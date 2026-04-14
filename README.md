# FinAlly — AI Trading Workstation

An AI-powered trading workstation that streams live market data, simulates a $10,000 portfolio, and integrates an LLM chat assistant that can analyze positions and execute trades via natural language. Built with a Bloomberg-inspired dark terminal aesthetic.

Capstone project for an agentic AI coding course — the entire codebase is produced by orchestrated coding agents.

## Features

- **Live price streaming** — SSE-driven price updates with green/red flash animations and sparkline mini-charts
- **Simulated trading** — $10,000 virtual cash, fractional shares, market orders with instant fills, no fees
- **Portfolio visualizations** — treemap heatmap (sized by weight, colored by P&L), P&L line chart, positions table with unrealized and realized P&L
- **AI chat assistant** — analyzes holdings and P&L, suggests and auto-executes trades, manages the watchlist via natural language
- **Watchlist management** — up to 20 tickers, add/remove manually or via AI, session change % since page load
- **Persistent sessions** — chat history and portfolio state survive page refreshes; portfolio reset restores to $10,000

## Architecture

Single Docker container on port 8000:

```
┌─────────────────────────────────────────────────┐
│  Docker Container (port 8000)                   │
│                                                 │
│  FastAPI (Python/uv)                            │
│  ├── /api/*          REST endpoints             │
│  ├── /api/stream/*   SSE streaming              │
│  └── /*              Static files (Next.js)     │
│                                                 │
│  SQLite  ·  background market data task         │
└─────────────────────────────────────────────────┘
```

| Layer | Technology |
|---|---|
| Frontend | Next.js (static export), TypeScript, Tailwind CSS, TradingView Lightweight Charts |
| Backend | FastAPI, Python 3.12, uv |
| Database | SQLite (auto-initialized on startup) |
| Real-time | Server-Sent Events (SSE) |
| AI | LiteLLM → OpenRouter (Cerebras) with structured outputs |
| Market data | Built-in GBM simulator (default) or Massive/Polygon.io REST API |

## Prerequisites

- Docker
- An [OpenRouter](https://openrouter.ai) API key (for AI chat)
- Optionally: a [Massive](https://massive.io) API key for real market data (simulator used by default)

## Quick Start

```bash
# 1. Clone the repo
git clone <repo-url> && cd finally

# 2. Configure environment
cp .env.example .env
# Edit .env and set OPENROUTER_API_KEY

# 3. Build and run
docker build -t finally .
docker run -v finally-data:/app/db -p 8000:8000 --env-file .env finally
```

Open [http://localhost:8000](http://localhost:8000). No login required.

### Start/Stop Scripts

```bash
# macOS / Linux
./scripts/start_mac.sh       # build (if needed) + run
./scripts/stop_mac.sh        # stop + remove container (data volume preserved)

# Windows (PowerShell)
.\scripts\start_windows.ps1
.\scripts\stop_windows.ps1
```

## Environment Variables

| Variable | Required | Default | Description |
|---|---|---|---|
| `OPENROUTER_API_KEY` | Yes | — | OpenRouter API key for AI chat |
| `MASSIVE_API_KEY` | No | — | Massive/Polygon.io key; omit to use the built-in simulator |
| `LLM_MOCK` | No | `false` | Set `true` for deterministic mock LLM responses (E2E tests / CI) |
| `DB_PATH` | No | `/app/db/finally.db` | Override SQLite path (useful for local dev outside Docker) |

## Project Structure

```
finally/
├── frontend/                 # Next.js TypeScript project (static export)
├── backend/                  # FastAPI uv project
│   ├── app/
│   │   ├── main.py           # FastAPI entrypoint (uvicorn app.main:app)
│   │   ├── routers/          # Route handlers: portfolio, watchlist, chat, stream
│   │   ├── services/         # Business logic: portfolio, trade execution, chat
│   │   ├── market/           # Market data — abstract interface, GBM simulator, Massive client
│   │   └── db.py             # DB connection, init, helpers
│   └── schema/               # SQL schema definitions and seed data
├── planning/                 # Project documentation and agent contracts
├── test/                     # Playwright E2E tests + docker-compose.test.yml
├── scripts/                  # Start/stop helpers (macOS + Windows)
├── db/                       # Volume mount target (finally.db written here at runtime)
├── Dockerfile
├── .env.example
└── .gitignore
```

## Market Data

Two interchangeable implementations behind a common interface:

- **Simulator (default)** — Geometric Brownian Motion with correlated moves via a shared market factor and per-ticker β sensitivity, random shock events, realistic seed prices, ~500ms tick interval. No API key needed.
- **Massive client** — REST polling against Polygon.io via the `massive` package. Free tier polls every 15 seconds; paid tiers support 2–15 second intervals.

Run the terminal demo to see the simulator live:

```bash
cd backend
uv run market_data_demo.py
```

## Development

### Backend

```bash
cd backend
uv sync
uv run uvicorn app.main:app --reload --port 8000
```

Set `DB_PATH=./db/finally.db` in your shell (or export it) so the backend writes the SQLite file locally instead of the Docker path.

### Frontend

```bash
cd frontend
npm ci
npm run dev      # dev server on http://localhost:3000
npm run build    # static export to out/
```

### Running Tests

**Backend unit tests:**
```bash
cd backend
uv run pytest
uv run pytest --cov=app --cov-report=term-missing   # with coverage
```

**E2E tests (requires Docker):**
```bash
cd test
docker compose -f docker-compose.test.yml up --abort-on-container-exit
```

E2E tests run with `LLM_MOCK=true` by default and use a fresh anonymous volume per run.

## API Overview

| Method | Path | Description |
|---|---|---|
| GET | `/api/stream/prices` | SSE stream of live price updates |
| GET | `/api/watchlist` | Watchlist tickers with latest prices |
| POST | `/api/watchlist` | Add a ticker (max 20) |
| DELETE | `/api/watchlist/{ticker}` | Remove a ticker |
| GET | `/api/portfolio` | Positions, cash, P&L |
| POST | `/api/portfolio/trade` | Execute a market order `{ticker, quantity, side}` |
| GET | `/api/portfolio/history` | Portfolio value snapshots (last 500) |
| POST | `/api/portfolio/reset` | Reset to $10k, clear all state |
| POST | `/api/chat` | Send a chat message; returns AI response + executed actions |
| GET | `/api/chat/history` | Last 50 messages (for page-load restore) |
| GET | `/api/health` | Health check |

## License

See [LICENSE](LICENSE).
