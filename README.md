# PredictEdge

PredictEdge is a live quant-style dashboard and automation stack for Polymarket crypto direction markets (`BTC`, `ETH`, `SOL`, `XRP`) across `5m`, `15m`, and `1h` windows.

## What it does

- Streams live market/streak data over WebSocket
- Ingests active + historical market data into SQLite
- Runs pattern-based auto-trading
- Auto-claims winnings (including Safe-based claim flow)
- Provides a real-time React dashboard

## Project structure

- `backend/` - FastAPI services, ingestion, analytics, trading, claiming
- `frontend/` - React + Vite live dashboard
- `start.sh` - starts backend + frontend with optional full historical sync

## Quick start

### 1) Backend dependencies

```bash
cd backend
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

### 2) Frontend dependencies

```bash
cd ../frontend
npm install
```

### 3) Configure environment

Create `.env` in repo root (or update existing) with at least:

```env
CLOB_HOST=https://clob.polymarket.com
GAMMA_HOST=https://gamma-api.polymarket.com
POLYGON_RPC_URL=<your_polygon_rpc_url>

PRIVATE_KEY=<your_private_key>
WALLET_PROXY_SAFE=<your_safe_or_wallet_address>

CLOB_API_KEY=<your_clob_api_key>
CLOB_SECRET=<your_clob_secret>
CLOB_PASS_PHRASE=<your_clob_passphrase>

DEFAULT_ORDER_SIZE=10
AUTO_TRADE_LOOP_SECONDS=30
TOP_PATTERN_COUNT=10
MIN_PATTERN_WIN_RATE_PCT=55
AUTO_CLAIM_INTERVAL_SECONDS=600
```

Notes:
- CLOB API credentials can be auto-generated from `PRIVATE_KEY` on startup if missing.
- `POLYGON_RPC_URL` is required for claim execution.

### 4) Run app

From repo root:

```bash
./start.sh
```

App URLs:
- Frontend: `http://localhost:3000`
- Backend: `http://localhost:8000`
- API docs: `http://localhost:8000/docs`

## Optional: full historical sync before startup

```bash
./start.sh --full-clob-sync --clob-pages=1-100
```

You can run additional bounded chunks:

```bash
./start.sh --full-clob-sync --clob-pages=101-200
```

## Key runtime behaviors

- Backend starts spot feed, CLOB ingestion, auto-trader, auto-claimer, and WebSocket broadcaster.
- Dashboard receives live updates via WebSocket and periodically refreshes table stats.
- Auto-trader uses top-N pattern signals with win-rate threshold from env.

## Troubleshooting

- If ports are busy, `start.sh` prompts to kill existing services.
- If claims fail, verify:
  - `POLYGON_RPC_URL` is reachable
  - `PRIVATE_KEY` owner matches configured Safe owner
  - wallet has claimable winning token balances
- If WebSocket shows disconnected, ensure backend is healthy at `/health`.

## Notes for roadmap

- Quant signal ideation and roadmap are tracked in `QUANT_SIGNALS_NOTES.md`.
