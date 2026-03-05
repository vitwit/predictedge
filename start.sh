#!/bin/bash
set -e

ROOT="$(cd "$(dirname "$0")" && pwd)"

echo "🚀 Starting PredictEdge..."
BACKEND_PID=""
FRONTEND_PID=""
STARTED_BACKEND=0
EXISTING_PIDS=""
FULL_CLOB_SYNC=0
CLOB_START_PAGE=1
CLOB_END_PAGE=100

for arg in "$@"; do
    case "$arg" in
        --full-clob-sync|--sync-all-clob)
            FULL_CLOB_SYNC=1
            ;;
        --clob-pages=*)
            FULL_CLOB_SYNC=1
            RANGE="${arg#*=}"
            if [[ "$RANGE" =~ ^([0-9]+)-([0-9]+)$ ]]; then
                CLOB_START_PAGE="${BASH_REMATCH[1]}"
                CLOB_END_PAGE="${BASH_REMATCH[2]}"
            else
                echo "❌ Invalid --clob-pages format. Use --clob-pages=1-100"
                exit 1
            fi
            ;;
    esac
done

cleanup() {
    if [ -n "$FRONTEND_PID" ]; then
        kill "$FRONTEND_PID" 2>/dev/null || true
    fi
    if [ "$STARTED_BACKEND" -eq 1 ] && [ -n "$BACKEND_PID" ]; then
        kill "$BACKEND_PID" 2>/dev/null || true
    fi
    if [ -n "$RESYNC_PID" ]; then
        kill "$RESYNC_PID" 2>/dev/null || true
    fi
    exit 0
}

append_pid() {
    local pid="$1"
    if [ -z "$pid" ]; then
        return
    fi
    case " $EXISTING_PIDS " in
        *" $pid "*) ;;
        *) EXISTING_PIDS="$EXISTING_PIDS $pid" ;;
    esac
}

collect_existing_services() {
    local port
    for port in 8000 3000 3001 3002; do
        for pid in $(lsof -nP -iTCP:"$port" -sTCP:LISTEN -t 2>/dev/null || true); do
            append_pid "$pid"
        done
    done
}

confirm_and_kill_existing_services() {
    collect_existing_services
    if [ -z "$EXISTING_PIDS" ]; then
        return
    fi

    echo "⚠️  Existing services detected:"
    for pid in $EXISTING_PIDS; do
        ps -p "$pid" -o pid=,command= 2>/dev/null || true
    done
    echo ""
    printf "Kill these processes and start fresh? [y/N]: "
    read -r reply
    case "$reply" in
        y|Y|yes|YES)
            for pid in $EXISTING_PIDS; do
                kill "$pid" 2>/dev/null || true
            done
            sleep 1
            for pid in $EXISTING_PIDS; do
                if kill -0 "$pid" 2>/dev/null; then
                    kill -9 "$pid" 2>/dev/null || true
                fi
            done
            echo "✅ Existing processes stopped"
            ;;
        *)
            echo "ℹ️  Start aborted. Existing services were not stopped."
            exit 0
            ;;
    esac
}

confirm_and_kill_existing_services

# Backend
cd "$ROOT/backend"
if [ "$FULL_CLOB_SYNC" -eq 1 ]; then
    echo "📥 Running full CLOB historical sync (pages ${CLOB_START_PAGE}-${CLOB_END_PAGE})..."
    python3 -u -c "from ingestion.polymarket import sync_all_historical_markets; sync_all_historical_markets(start_page=${CLOB_START_PAGE}, end_page=${CLOB_END_PAGE}, show_progress=True)"
fi

echo "🔧 Starting backend on http://localhost:8000 ..."
if lsof -nP -iTCP:8000 -sTCP:LISTEN >/dev/null 2>&1; then
    echo "❌ Port 8000 is still in use after cleanup"
    exit 1
fi

python3 main.py &
BACKEND_PID=$!
STARTED_BACKEND=1

# Wait up to 20s for backend to become healthy or crash.
READY=0
for _ in $(seq 1 40); do
    if ! kill -0 "$BACKEND_PID" 2>/dev/null; then
        echo "❌ Backend process exited during startup"
        exit 1
    fi
    if curl -sf http://localhost:8000/health > /dev/null; then
        READY=1
        break
    fi
    sleep 0.5
done

if [ "$READY" -ne 1 ]; then
    echo "❌ Backend failed to start (health check timed out)"
    exit 1
fi
echo "✅ Backend ready"

# Frontend
cd "$ROOT/frontend"
echo "🎨 Starting frontend on http://localhost:3000 ..."
npm run dev &
FRONTEND_PID=$!

sleep 2

echo ""
echo "============================================"
echo "  PredictEdge is running!"
echo "  Frontend:   http://localhost:3000"
echo "  Backend:    http://localhost:8000"
echo "  API Docs:   http://localhost:8000/docs"
echo "============================================"
echo ""
echo "Press Ctrl+C to stop all services"

trap cleanup SIGINT SIGTERM

# Background spot-data refresh: runs every 6 hours to keep historical_spot current
(
  while true; do
    sleep 21600  # 6 hours
    echo "⟳  Refreshing spot data (delta fetch)..."
    cd "$ROOT/backend" && python scripts/resync_spot.py >> "$ROOT/backend/logs/resync_spot.log" 2>&1
    echo "✅ Spot data refresh complete"
  done
) &
RESYNC_PID=$!

wait
