# PredictEdge

PredictEdge is a live quant-style dashboard and automation stack for Polymarket crypto direction markets (`BTC`, `ETH`, `SOL`, `XRP`) across `5m`, `15m`, and `1h` windows.

## What it does

- Streams live market/streak data over WebSocket
- Ingests active + historical market data from **Gamma** and **CLOB** APIs into SQLite
- Runs configurable strategies (default: **streak reversal** — 6× UP/DOWN + USD momentum reversal)
- Auto-claims winnings (Safe-based claim flow)
- Quant cockpit: regime, edge health, signal tape, execution tracker
- Real-time React dashboard

## Project structure

- `backend/` — FastAPI, ingestion (Gamma + CLOB), analytics, trading, auto-claimer
- `frontend/` — React + Vite dashboard (streaks, Quant Cockpit, Execution Tracker)
- `start.sh` — starts backend + frontend (ports configurable via `.env`), optional full historical sync

## Data and architecture

- **Resolutions / winner**: From **Gamma API** (`/events` by tag or slug, `/markets`). `outcomePrices` → UP/DOWN.
- **Spot open/close** (for `spot_change_usd`): **Gamma only** — `eventMetadata.priceToBeat` when present; previous window’s close = next market’s `price_to_beat`. No Binance or other backfill; CLOB `/prices-history` is token price, not underlying spot.
- **Historical sync**: Every 5 min: primary Gamma `/markets?closed=true&tag_slug=crypto` plus **tag-based** sync (events pages 1–8) so BTC 5m/15m resolutions are always present.
- **Live**: CLOB orderbook/midpoint, spot feed (Binance) for live ticks; strategy logic reads from DB (`market_resolutions`, `auto_trade_orders`).
- **Strategy mode** (`STRATEGY_MODE`): `streak_reversal` (default), `all`, or `pattern`. Streak reversal uses only streak + USD reversal triggers; `all` runs pattern + fast_reversal + streak_reversal.

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

# Strategy (default: streak_reversal)
STRATEGY_MODE=streak_reversal
STREAK_REVERSAL_SIZE=100
STREAK_REVERSAL_ORDER_PRICE=0.50

DEFAULT_ORDER_SIZE=10
AUTO_TRADE_LOOP_SECONDS=30
TOP_PATTERN_COUNT=10
MIN_PATTERN_WIN_RATE_PCT=55
AUTO_CLAIM_INTERVAL_SECONDS=600
```

Notes:
- CLOB API credentials can be auto-generated from `PRIVATE_KEY` on startup if missing.
- `POLYGON_RPC_URL` is required for claim execution.
- `WALLET_PROXY_SAFE` is the proxy/Safe address used for orders and claiming.

### 4) Run app

From repo root:

```bash
./start.sh
```

App URLs (default ports; override via `.env`):
- Frontend: `http://localhost:3000` (or `FRONTEND_PORT`)
- Backend: `http://localhost:8888` (or `PORT`)
- API docs: `http://localhost:8888/docs`

**Production**: Set `HOST=0.0.0.0`, `PUBLIC_HOST=<your-server-ip>` in `.env` so URLs reflect the server address.

## Optional: full historical sync before startup

```bash
./start.sh --full-clob-sync --clob-pages=1-100
```

You can run additional bounded chunks:

```bash
./start.sh --full-clob-sync --clob-pages=101-200
```

## Key runtime behaviors

- Backend starts spot feed, CLOB ingestion, **historical sync loop** (Gamma every 5 min), then traders and auto-claimer based on `STRATEGY_MODE`.
- With `STRATEGY_MODE=streak_reversal` (default): only **StreakReversalTrader** and **AutoClaimer** run; no pattern or fast_reversal.
- Streak reversal: 6× consecutive UP → buy DOWN (and vice versa); or previous market |spot_change_usd| > $200 (5m) / $400 (15m) → contrarian order on next market.
- Dashboard: live streaks, Quant Cockpit (regime, edge health, signal tape), Execution Tracker (orders placed/executed/wins/losses).
- WebSocket broadcasts live market/streak data; API docs at `/docs`.

## Troubleshooting

- If ports are busy, `start.sh` prompts to kill existing services.
- If claims fail, verify:
  - `POLYGON_RPC_URL` is reachable
  - `PRIVATE_KEY` owner matches configured Safe owner
  - wallet has claimable winning token balances
- If WebSocket shows disconnected, ensure backend is healthy at `/health`.

## Verification

- **Resolution/strategy data**: `PYTHONPATH=backend python backend/scripts/verify_btc_resolutions.py` — shows recent BTC 5m/15m resolutions, streak check, USD reversal check, and strategy data checklist (Gamma-only spot; no backfill).
- **Price-to-close logic**: `PYTHONPATH=backend python backend/scripts/verify_gamma_price_to_beat.py` — checks that next market’s `price_to_beat` is used as previous market’s close.

## Notes for roadmap

- Quant signal ideation and roadmap are tracked in `QUANT_SIGNALS_NOTES.md`.
