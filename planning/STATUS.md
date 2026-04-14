# FinAlly — Project Status
**Date:** 2026-04-14

---

## What's Done

### Market Data Backend — Complete
- `backend/app/market/` — 8 modules, ~500 lines, 73 tests, 84% coverage
- GBM simulator with correlated moves (sector-based Cholesky decomposition)
- Massive/Polygon.io REST polling client (activated via `MASSIVE_API_KEY`)
- Thread-safe `PriceCache` as single source of truth
- SSE stream endpoint factory (`/api/stream/prices`)
- Factory pattern selects simulator vs. real data based on env var
- Full code review completed — all 7 issues resolved
- Rich terminal demo: `cd backend && uv run market_data_demo.py`

---

## What's Left to Build

Everything below is unstarted. Build order matters — database and core API must come before frontend.

### 1. Database Layer
- `backend/app/db.py` — aiosqlite connection, lifespan init, schema creation
- `backend/schema/` — SQL schema (6 tables: user_profile, watchlist, positions, trades, portfolio_snapshots, chat_messages)
- Seed data: default user ($10k cash), 10 default tickers

### 2. Backend Routers & Services
- **Portfolio** — `GET /api/portfolio`, `POST /api/portfolio/trade`, `GET /api/portfolio/history`, `POST /api/portfolio/reset`
- **Watchlist** — `GET /api/watchlist`, `POST /api/watchlist`, `DELETE /api/watchlist/{ticker}`
- **Chat** — `POST /api/chat`, `GET /api/chat/history`
- **System** — `GET /api/health`
- Trade execution logic: buy/sell validation, avg cost calc, realized P&L
- Portfolio snapshot background task (every 30s)

### 3. LLM Integration
- LiteLLM → OpenRouter (Cerebras), model: `openrouter/openai/gpt-oss-120b`
- Structured output schema: `message`, `trades[]`, `watchlist_changes[]`
- Auto-execute trades from LLM response
- LLM mock mode (`LLM_MOCK=true`) for tests
- 30-second timeout → HTTP 504

### 4. Frontend (Next.js + TypeScript)
- Watchlist panel with price flash animations and sparklines (TradingView Lightweight Charts)
- Main chart area for selected ticker
- Portfolio heatmap (treemap) with CASH block
- P&L chart (line chart from snapshot history)
- Positions table
- Trade bar (ticker + quantity + Buy/Sell)
- AI chat panel (collapsible, expanded by default, history restored on load)
- Header (total value, cash, connection status, Reset button)
- Tailwind CSS dark theme (`#0d1117` / `#1a1a2e`)

### 5. Docker & Scripts
- Multi-stage Dockerfile (Node 20 → Python 3.12)
- `.env.example`
- `scripts/start_mac.sh`, `scripts/stop_mac.sh`
- `scripts/start_windows.ps1`, `scripts/stop_windows.ps1`

### 6. E2E Tests
- `test/docker-compose.test.yml` + Playwright test suite
- Key scenarios: streaming, trading, portfolio reset, chat, history restore

---

## Known Issues to Fix Before Next Build Pass

From `planning/REVIEW.md`:

1. **Stop hook loop** (High) — `.claude/settings.json` wires `Stop` to a Codex review command that re-triggers the same hook. Fix or remove before next session.
2. **README ahead of reality** (Medium) — README documents the full app but only market data exists. Update README to reflect current state.
3. **Interface mismatch** (Medium) — `PLAN.md` references `backend/app/market_data/` and a DB-read pattern, but the actual code lives at `backend/app/market/` and uses `add_ticker()`/`remove_ticker()`. Reconcile before wiring the watchlist router to the market data source.

---

## Repo
`https://github.com/jtpolasek/finally`
