# FinAlly — AI Trading Workstation

## Project Specification

## 1. Vision

FinAlly (Finance Ally) is a visually stunning AI-powered trading workstation that streams live market data, lets users trade a simulated portfolio, and integrates an LLM chat assistant that can analyze positions and execute trades on the user's behalf. It looks and feels like a modern Bloomberg terminal with an AI copilot.

This is the capstone project for an agentic AI coding course. It is built entirely by Coding Agents demonstrating how orchestrated AI agents can produce a production-quality full-stack application. Agents interact through files in `planning/`.

## 2. User Experience

### First Launch

The user runs a single Docker command (or a provided start script). A browser opens to `http://localhost:8000`. No login, no signup. They immediately see:

- A watchlist of 10 default tickers with live-updating prices in a grid
- $10,000 in virtual cash
- A dark, data-rich trading terminal aesthetic
- An AI chat panel ready to assist

### What the User Can Do

- **Watch prices stream** — prices flash green (uptick) or red (downtick) with subtle CSS animations that fade
- **View sparkline mini-charts** — price action beside each ticker in the watchlist, accumulated on the frontend from the SSE stream since page load (sparklines fill in progressively; render an empty container until the first SSE price arrives, then begin plotting)
- **Click a ticker** to see a larger detailed chart in the main chart area (shows "Waiting for price data…" overlay until the first data point arrives for that ticker)
- **Buy and sell shares** — market orders only, instant fill at current price, no fees, no confirmation dialog; quantity field accepts up to 4 decimal places (fractional shares)
- **Monitor their portfolio** — a heatmap (treemap) showing positions sized by portfolio weight and colored by P&L, plus a "CASH" block sized by uninvested cash weight (neutral gray); shows "No open positions" when portfolio is empty
- **View a positions table** — ticker, quantity, average cost, current price, unrealized P&L, unrealized % change, realized P&L; shows "No open positions" when empty
- **Chat with the AI assistant** — ask about their portfolio, get analysis, and have the AI execute trades and manage the watchlist through natural language; conversation history restores on page refresh
- **Manage the watchlist** — add/remove tickers manually or via the AI chat; hard cap of 20 tickers (backend returns an error if exceeded)
- **Reset the portfolio** — a "Reset Portfolio" button in the header restores cash to $10,000 and clears all positions, trades, snapshots, and chat history

### Session Change %

The watchlist shows each ticker's % change vs. the price at page load. On an EventSource reconnect, the baseline resets to the first price received after reconnection.

### Visual Design

- **Dark theme**: backgrounds around `#0d1117` or `#1a1a2e`, muted gray borders, no pure black
- **Price flash animations**: brief green/red background highlight on price change, fading over ~500ms via CSS transitions
- **Connection status indicator**: a small colored dot (green = connected, yellow = reconnecting, red = disconnected) visible in the header
- **Professional, data-dense layout**: inspired by Bloomberg/trading terminals — every pixel earns its place
- **Responsive but desktop-first**: optimized for wide screens, functional on tablet

### Color Scheme
- Accent Yellow: `#ecad0a`
- Blue Primary: `#209dd7`
- Purple Secondary: `#753991` (Buy/Sell buttons and other primary actions)

---

## 3. Architecture Overview

### Single Container, Single Port

```
┌─────────────────────────────────────────────────┐
│  Docker Container (port 8000)                   │
│                                                 │
│  FastAPI (Python/uv)                            │
│  ├── /api/*          REST endpoints             │
│  ├── /api/stream/*   SSE streaming              │
│  └── /*              Static file serving         │
│                      (Next.js export)            │
│                                                 │
│  SQLite database (volume-mounted)               │
│  Background task: market data polling/sim        │
└─────────────────────────────────────────────────┘
```

- **Frontend**: Next.js with TypeScript, built as a static export (`output: 'export'`), served by FastAPI as static files
- **Backend**: FastAPI (Python), managed as a `uv` project
- **Database**: SQLite, single file at `db/finally.db`, volume-mounted for persistence
- **Real-time data**: Server-Sent Events (SSE) — simpler than WebSockets, one-way server→client push, works everywhere
- **AI integration**: LiteLLM → OpenRouter (Cerebras for fast inference), with structured outputs for trade execution
- **Market data**: Environment-variable driven — simulator by default, real data via Massive API if key provided

See the [Architecture Decision Log](#appendix-architecture-decision-log) for rationale behind these choices.

---

## 4. Directory Structure

```
finally/
├── frontend/                 # Next.js TypeScript project (static export)
├── backend/                  # FastAPI uv project (Python)
│   ├── app/
│   │   ├── main.py           # FastAPI app entrypoint (uvicorn app.main:app)
│   │   ├── routers/          # FastAPI route handlers (portfolio, watchlist, chat, stream)
│   │   ├── services/         # Business logic (portfolio, trade execution, chat)
│   │   ├── market_data/      # Abstract base + simulator + Massive client
│   │   └── db.py             # DB connection, initialization, and helpers
│   └── schema/               # Schema SQL definitions and seed data
├── planning/                 # Project-wide documentation for agents
│   ├── PLAN.md               # This document
│   └── ...                   # Additional agent reference docs
├── scripts/
│   ├── start_mac.sh          # Launch Docker container (macOS/Linux)
│   ├── stop_mac.sh           # Stop Docker container (macOS/Linux)
│   ├── start_windows.ps1     # Launch Docker container (Windows PowerShell)
│   └── stop_windows.ps1      # Stop Docker container (Windows PowerShell)
├── test/                     # Playwright E2E tests + docker-compose.test.yml
├── db/                       # Volume mount target (SQLite file lives here at runtime)
│   └── .gitkeep              # Directory exists in repo; finally.db is gitignored
├── Dockerfile                # Multi-stage build (Node → Python)
├── .dockerignore             # Excludes node_modules, __pycache__, .env, db/*.db, etc.
├── .env                      # gitignored — copy from .env.example and fill in keys
├── .env.example              # committed — template with placeholder values
└── .gitignore
```

### Key Boundaries

- **`backend/schema/`** contains schema SQL definitions and seed logic. Note the distinction: `backend/schema/` holds source-controlled schema files; `db/` at the project root is the runtime volume mount where the live SQLite file is written.
- The **frontend static export** (Next.js build output) is copied into the Python Docker stage at `/app/static/` and served by FastAPI at `/*`. The frontend has no knowledge of Python and communicates only via `/api/*` and `/api/stream/*`.

### Database Initialization

The backend initializes the SQLite database on **application startup** via FastAPI's `lifespan` event — not lazily on the first request. If the database file doesn't exist or tables are missing, it creates the schema and seeds default data. A startup failure surfaces immediately rather than on the first user request.

---

## 5. Environment Variables

```bash
# Required: OpenRouter API key for LLM chat functionality
OPENROUTER_API_KEY=your-openrouter-api-key-here

# Optional: Massive (Polygon.io) API key for real market data
# If not set, the built-in market simulator is used (recommended for most users)
MASSIVE_API_KEY=

# Optional: Set to "true" for deterministic mock LLM responses (testing)
LLM_MOCK=false

# Optional: Override SQLite database path (default: /app/db/finally.db)
DB_PATH=/app/db/finally.db
```

### Behavior

- If `MASSIVE_API_KEY` is set and non-empty → backend uses Massive REST API for market data
- If `MASSIVE_API_KEY` is absent or empty → backend uses the built-in market simulator
- If `LLM_MOCK=true` → backend returns deterministic mock LLM responses (for E2E tests)
- `DB_PATH` defaults to `/app/db/finally.db`; set to a local path (e.g., `./db/finally.db`) for development outside Docker.
- Inside Docker, `--env-file .env` injects variables at `docker run` time — the backend reads from `os.environ` directly (`python-dotenv` is not required). Outside Docker (local dev), export variables in your shell or add `python-dotenv` as a dev dependency.
- `LLM_MOCK` is a string in the environment; compare with `os.environ.get("LLM_MOCK", "false").lower() == "true"`.

---

## 6. Market Data

### Two Implementations, One Interface

Both the simulator and the Massive client implement the same abstract interface. The backend selects which to use based on the environment variable. All downstream code (SSE streaming, price cache, frontend) is agnostic to the source.

```python
from abc import ABC, abstractmethod

class MarketDataSource(ABC):
    @abstractmethod
    async def start(self, tickers: list[str]) -> None:
        """Start the data source. `tickers` is the initial list from the DB (used for the
        first tick only); subsequent ticks re-read the watchlist from the DB directly."""
        ...

    @abstractmethod
    async def stop(self) -> None:
        """Stop the background task cleanly."""
        ...
```

Each implementation calls the shared `update_price_cache(ticker, price, prev_price, timestamp)` helper on each tick. That function writes to the in-memory cache and triggers an SSE broadcast.

### Simulator (Default)

- Generates prices using geometric Brownian motion (GBM) with configurable drift and volatility per ticker
- Updates at ~500ms intervals
- Correlated moves across tickers via a shared market factor — on each tick, sample a market return `m ~ N(0, σ_market)` (σ_market ≈ 0.002), then for each ticker sample an idiosyncratic return `ε ~ N(0, σ_ticker)`. Log return = `β * m + ε` where β is a per-ticker sensitivity (default 1.0; high-beta stocks like TSLA use ~1.5). No full covariance matrix needed.
- Occasional random "events" — sudden 2-5% moves on a ticker for drama
- Starts from realistic seed prices (e.g., AAPL ~$190, GOOGL ~$175, etc.)
- Runs as an in-process background task — no external dependencies

### Massive API (Optional)

- REST API polling (not WebSocket) — simpler, works on all tiers
- Re-queries the watchlist from the database each poll interval to pick up any additions or removals
- Polls for the union of all watched tickers on a configurable interval
- Free tier (5 calls/min): poll every 15 seconds
- Paid tiers: poll every 2-15 seconds depending on tier
- Parses REST response into the same format as the simulator

### Shared Price Cache

- A single background task (simulator or Massive poller) writes to an in-memory price cache
- The cache is a module-level dict: `price_cache: dict[str, dict] = {}`. Each entry: `{"price": float, "prev_price": float, "timestamp": str}`. CPython's GIL makes individual dict reads and writes atomic — no explicit lock is required for the simple get/set operations used here.
- **Each write to the cache triggers an immediate SSE broadcast** — the SSE cadence therefore matches the data source (simulator: ~500ms, Massive free tier: ~15s)
- If a ticker has no cache entry yet (e.g., just added to the watchlist before the next poll), `GET /api/watchlist` returns `null` for `price` and `prev_price`; the frontend renders `—`

### SSE Streaming

- Endpoint: `GET /api/stream/prices`
- Long-lived SSE connection; client uses native `EventSource` API
- **Broadcast mechanism**: each connected SSE client gets a dedicated `asyncio.Queue`. A module-level set (`sse_clients: set[asyncio.Queue]`) is populated on connect and cleaned up on disconnect. When `update_price_cache()` is called, it puts the updated price data onto every queue in `sse_clients`. The SSE endpoint awaits its queue and streams each item as a `price` event.
- Server broadcasts a price update event for all tickers whenever the price cache is updated; the background task re-reads the watchlist from the DB each cycle, so newly-added tickers appear in the stream without a client reconnect
- Each SSE event is named `price` and carries a JSON data payload:
  ```
  event: price
  data: {"ticker": "AAPL", "price": 192.50, "prev_price": 191.80, "change": 0.37, "timestamp": "2026-04-10T10:00:00.123Z"}
  ```
  Fields: `ticker` (string), `price` (float), `prev_price` (float — the price from the immediately preceding tick, same value stored in the cache and returned by `GET /api/watchlist`; used for flash direction), `change` (float — absolute price delta, not percent), `timestamp` (ISO 8601). The frontend computes session change % itself using the page-load baseline price captured from the first SSE tick.
- When the watchlist is empty, the server sends an SSE comment line (`: keepalive`) every 15 seconds to prevent client-side connection timeout
- Client handles reconnection automatically (EventSource has built-in retry)

---

## 7. Database

### SQLite with Startup Initialization

The backend initializes the SQLite database on application startup via FastAPI's `lifespan` event. If the file doesn't exist or tables are missing, it creates the schema and seeds default data. This means:

- No separate migration step
- No manual database setup
- Fresh Docker volumes start with a clean, seeded database automatically

Use `aiosqlite` for all database access. All route handlers and the snapshot background task run in the async event loop; `aiosqlite` ensures non-blocking I/O and prevents `database is locked` errors from concurrent writes.

### Startup Sequence

The `lifespan` event handler runs these steps in order:

1. **DB init** — create tables and seed default data if not present
2. **Market data source start** — query the current watchlist from the DB; pass the ticker list to `source.start(tickers)`. The source uses this list for the first tick only; subsequent ticks re-read the watchlist from the DB directly.
3. **Portfolio snapshot task** — start a separate background task that records a `portfolio_snapshots` row every 30 seconds. It reads cash from `user_profile` and computes `total_value = cash + sum(quantity * current_price)` for all open positions using the live price cache. If the price cache is empty when a snapshot fires (e.g., before the first data tick arrives), the snapshot is **skipped** — no row is recorded.

Startup failures surface immediately — if the DB cannot be initialized or the market data source fails to start, the application exits rather than silently degrading.

### Schema

All tables include a `user_id` column defaulting to `"default"`. This is hardcoded for now (single-user) but enables future multi-user support without schema migration.

**user_profile** — User state (cash balance)

| Column | Type | Notes |
|---|---|---|
| `id` | TEXT PK | default `"default"` |
| `cash_balance` | REAL | default `10000.0` |
| `created_at` | TEXT | ISO timestamp |

**watchlist** — Tickers the user is watching

| Column | Type | Notes |
|---|---|---|
| `id` | INTEGER PK | auto-assigned rowid alias |
| `user_id` | TEXT | default `"default"` |
| `ticker` | TEXT | |
| `added_at` | TEXT | ISO timestamp |
| — | UNIQUE | `(user_id, ticker)` |

**positions** — Current holdings (one row per ticker per user)

| Column | Type | Notes |
|---|---|---|
| `id` | TEXT PK | UUID |
| `user_id` | TEXT | default `"default"` |
| `ticker` | TEXT | |
| `quantity` | REAL | fractional shares; set to `0` when position fully closed (row is never deleted) |
| `avg_cost` | REAL | weighted average cost basis |
| `realized_pnl` | REAL | default `0.0`; cumulative realized gain/loss from sells; preserved when position closes |
| `updated_at` | TEXT | ISO timestamp |
| — | UNIQUE | `(user_id, ticker)` |

On sell: `realized_pnl += (sell_price - avg_cost) * quantity_sold`. Rows are never deleted — zeroing `quantity` preserves `realized_pnl` across round trips on the same ticker.

On buy: `avg_cost = (old_avg_cost * old_quantity + new_price * new_quantity) / (old_quantity + new_quantity)`. When `old_quantity == 0` (position fully closed and re-opened), this simplifies to `new_price` — no special-case code required, but implementations must handle this edge case correctly.

**trades** — Trade history (append-only log)

| Column | Type | Notes |
|---|---|---|
| `id` | INTEGER PK | auto-assigned rowid alias |
| `user_id` | TEXT | default `"default"` |
| `ticker` | TEXT | |
| `side` | TEXT | `"buy"` or `"sell"` |
| `quantity` | REAL | fractional shares |
| `price` | REAL | fill price |
| `executed_at` | TEXT | ISO timestamp |

**portfolio_snapshots** — Portfolio value over time (for P&L chart). Recorded every 30 seconds. `total_value` = cash balance + market value of all positions with `quantity > 0`.

| Column | Type | Notes |
|---|---|---|
| `id` | TEXT PK | UUID |
| `user_id` | TEXT | default `"default"` |
| `total_value` | REAL | cash + market value of open positions |
| `recorded_at` | TEXT | ISO timestamp; **indexed** |

**chat_messages** — Conversation history with LLM

| Column | Type | Notes |
|---|---|---|
| `id` | TEXT PK | UUID |
| `user_id` | TEXT | default `"default"` |
| `role` | TEXT | `"user"` or `"assistant"` |
| `content` | TEXT | |
| `actions` | TEXT | JSON — trades executed + watchlist changes; `null` for user messages |
| `created_at` | TEXT | ISO timestamp |

The backend must call `json.dumps()` when writing `actions` and `json.loads()` when reading it. The `GET /api/chat/history` endpoint must return `actions` as a deserialized JSON object, never as a raw string.

### Default Seed Data

- One user_profile row: `id="default"`, `cash_balance=10000.0`
- Ten watchlist entries: AAPL, GOOGL, MSFT, AMZN, TSLA, NVDA, META, JPM, V, NFLX

---

## 8. API Endpoints

### Market Data
| Method | Path | Description |
|--------|------|-------------|
| GET | `/api/stream/prices` | SSE stream of live price updates |

### Portfolio
| Method | Path | Description |
|--------|------|-------------|
| GET | `/api/portfolio` | Current positions, cash balance, total value, unrealized and realized P&L |
| POST | `/api/portfolio/trade` | Execute a trade: `{ticker, quantity, side}` |
| GET | `/api/portfolio/history` | Portfolio value snapshots over time (for P&L chart) |
| POST | `/api/portfolio/reset` | Reset portfolio to $10k; clears positions, trades, snapshots, and chat history |

### Watchlist
| Method | Path | Description |
|--------|------|-------------|
| GET | `/api/watchlist` | Current watchlist tickers with latest prices (from price cache) |
| POST | `/api/watchlist` | Add a ticker: `{ticker}`; returns 400 if at cap or already present |
| DELETE | `/api/watchlist/{ticker}` | Remove a ticker; returns 404 if ticker is not in watchlist |

### Chat
| Method | Path | Description |
|--------|------|-------------|
| POST | `/api/chat` | Send a message; receive complete JSON response (message + executed actions) |
| GET | `/api/chat/history` | Return last 50 messages from `chat_messages` for page-load restore (50 for UI display; the LLM backend uses only the last 20 for context) |

### System
| Method | Path | Description |
|--------|------|-------------|
| GET | `/api/health` | Health check (for Docker/deployment) |

### Response Shapes

**`GET /api/watchlist`**
```json
[
  {
    "ticker": "AAPL",
    "price": 192.50,
    "prev_price": 191.80,
    "added_at": "2026-04-10T10:00:00Z"
  }
]
```
`price` and `prev_price` are `null` when the ticker has no cache entry yet; the frontend renders `—`. Session change % (vs. page-load baseline) is computed entirely on the frontend from SSE data — the REST watchlist endpoint does not return a `change_pct` field. The backend accepts any ticker string without validating it against an external source.

**`POST /api/watchlist` — error (cap reached)**
```json
{"error": "Watchlist limit reached (max 20 tickers)"}
```
HTTP 400. Also returns 400 with `{"error": "Ticker already in watchlist"}` for duplicates.

**`DELETE /api/watchlist/{ticker}` — error (not found)**
```json
{"error": "Ticker not in watchlist"}
```
HTTP 404.

**`GET /api/portfolio`**
```json
{
  "cash_balance": 7500.00,
  "total_value": 10250.00,
  "total_unrealized_pnl": 125.00,
  "total_realized_pnl": 650.00,
  "positions": [
    {
      "ticker": "AAPL",
      "quantity": 10,
      "avg_cost": 190.00,
      "current_price": 192.50,
      "market_value": 1925.00,
      "unrealized_pnl": 25.00,
      "unrealized_pnl_pct": 1.32,
      "realized_pnl": 625.00
    }
  ]
}
```
`total_realized_pnl` is the sum of `realized_pnl` across all positions (including closed ones with `quantity == 0`). It will differ from any single position's `realized_pnl` whenever there are multiple tickers with realized gains/losses.
Only positions with `quantity > 0` are returned. `current_price` is `null` if not in the price cache.

**`POST /api/portfolio/trade` — success**
```json
{"ticker": "AAPL", "side": "buy", "quantity": 10, "price": 192.50, "executed_at": "2026-04-10T10:00:00Z"}
```
HTTP 200.

**`POST /api/portfolio/trade` — error**
```json
{"error": "Insufficient cash"}
```
HTTP 400. Other error values: `"Insufficient shares"`, `"Ticker not found in price cache"`.

**`GET /api/portfolio/history`**
```json
[
  {"recorded_at": "2026-04-10T10:00:00Z", "total_value": 10000.00},
  {"recorded_at": "2026-04-10T10:00:30Z", "total_value": 10125.50}
]
```
Returns the most recent **500** snapshots for the default user ordered by `recorded_at` ascending. This cap prevents unbounded response sizes after long sessions.

**`POST /api/portfolio/reset` — success**
```json
{"status": "ok"}
```
HTTP 200.

**`GET /api/chat/history`**
```json
[
  {
    "id": "uuid",
    "role": "user",
    "content": "Buy 5 shares of AAPL",
    "actions": null,
    "created_at": "2026-04-10T10:00:00Z"
  },
  {
    "id": "uuid",
    "role": "assistant",
    "content": "Done! I've bought 5 shares of AAPL at $192.50.",
    "actions": {
      "trades_executed": [{"ticker": "AAPL", "side": "buy", "quantity": 5, "price": 192.50}],
      "trades_failed": [],
      "watchlist_changes_executed": [],
      "watchlist_changes_failed": []
    },
    "created_at": "2026-04-10T10:00:01Z"
  }
]
```
Returns up to 50 messages ordered by `created_at` ascending. The `actions` field is returned as a deserialized JSON object (not a raw string) for assistant messages; `null` for user messages.

---

## 9. LLM Integration

> _[Agent note: when implementing LLM calls in this section, use the `cerebras` skill to configure LiteLLM via OpenRouter with the Cerebras inference provider.]_

There is an `OPENROUTER_API_KEY` in the `.env` file in the project root. Use the model `openrouter/openai/gpt-oss-120b` with Cerebras as the inference provider. Use Structured Outputs to parse the response.

### How It Works

When the user sends a chat message, the backend:

1. Loads the user's current portfolio context (cash, positions with P&L, watchlist with live prices, total portfolio value)
2. Loads the last 20 messages from `chat_messages` as conversation history (hard limit to stay within model context)
3. Constructs a prompt with a system message, portfolio context, conversation history, and the user's new message
4. Calls the LLM via LiteLLM → OpenRouter with a **30-second timeout**; on timeout returns HTTP 504 with `{"error": "The AI assistant is taking too long to respond. Please try again."}`
5. Parses the structured response; auto-executes any trades and watchlist changes (same validation as manual operations); persists the full exchange (user message, assistant message, executed actions) to `chat_messages`
6. Returns a response envelope to the frontend (no token-by-token streaming — Cerebras inference is fast enough that a loading indicator is sufficient)

### Structured Output Schema

The LLM is instructed to respond with JSON matching this schema:

```json
{
  "message": "Your conversational response to the user",
  "trades": [
    {"ticker": "AAPL", "side": "buy", "quantity": 10}
  ],
  "watchlist_changes": [
    {"ticker": "PYPL", "action": "add"},
    {"ticker": "TSLA", "action": "remove"}
  ]
}
```

- `message` (required): The conversational text shown to the user
- `trades` (optional): Array of trades to auto-execute. Each trade goes through the same validation as manual trades (sufficient cash for buys, sufficient shares for sells)
- `watchlist_changes` (optional): Array of watchlist modifications. `action` must be `"add"` or `"remove"` — no other values are valid.

### API Response to Frontend

The `/api/chat` endpoint returns an envelope (not the raw LLM JSON) so the frontend knows what actually succeeded:

```json
{
  "message": "I've bought 5 shares of AAPL for you.",
  "trades_executed": [
    {"ticker": "AAPL", "side": "buy", "quantity": 5, "price": 192.50}
  ],
  "trades_failed": [],
  "watchlist_changes_executed": [
    {"ticker": "PYPL", "action": "add"}
  ],
  "watchlist_changes_failed": []
}
```

- `trades_executed`: trades that passed validation and were committed
- `trades_failed`: trades that were requested but failed (e.g., insufficient cash), with a `reason` string
- `watchlist_changes_executed`: watchlist operations that succeeded
- `watchlist_changes_failed`: watchlist operations that failed (e.g., cap reached), with a `reason` string

### Auto-Execution

Trades specified by the LLM execute automatically — no confirmation dialog. This is a deliberate design choice:
- It's a simulated environment with fake money, so the stakes are zero
- It creates an impressive, fluid demo experience
- It demonstrates agentic AI capabilities — the core theme of the course

If a trade fails validation (e.g., insufficient cash), the error is included in the chat response so the LLM can inform the user.

### System Prompt Guidance

The LLM should be prompted as "FinAlly, an AI trading assistant" with instructions to:
- Analyze portfolio composition, risk concentration, and P&L
- Suggest trades with reasoning
- Execute trades when the user asks or agrees
- Manage the watchlist proactively — add tickers the user mentions that aren't already watched; remove tickers the user says they're no longer interested in; suggest adding correlated tickers when the user builds a new position
- Be concise and data-driven in responses
- Always respond with valid structured JSON

### LLM Mock Mode

When `LLM_MOCK=true`, the backend returns the following deterministic mock response instead of calling OpenRouter. This enables fast, free, reproducible E2E tests and CI/CD pipelines without an API key.

**Canonical mock response** (every chat message returns this):
```json
{
  "message": "I've analyzed your portfolio. Your positions look balanced. Let me know if you'd like to make any trades.",
  "trades": [],
  "watchlist_changes": []
}
```
The mock response intentionally contains no trades so that E2E tests sending multiple chat messages do not deplete cash.

---

## 10. Frontend Design

### Layout

The frontend is a single-page application with a dense, terminal-inspired layout. The specific component architecture and layout system is up to the Frontend Engineer, but the UI must include these elements:

- **Watchlist panel** — grid/table of watched tickers with: ticker symbol, current price (flashing green/red on change), session change % (vs. price at page load; resets on SSE reconnect), and a sparkline mini-chart (empty container until first SSE tick, then plots progressively)
- **Main chart area** — larger chart for the currently selected ticker; shows "Waiting for price data…" overlay until the first data point arrives; data accumulates from SSE since page load (no server-side price history)
- **Portfolio heatmap** — treemap where each rectangle is a position (sized by portfolio weight, colored by P&L: green = profit, red = loss) plus a "CASH" block (sized by cash weight, neutral gray); shows "No open positions" when portfolio is empty
- **P&L chart** — line chart showing total portfolio value over time, using data from `GET /api/portfolio/history`; render a "Waiting for data…" placeholder when the endpoint returns an empty array (no snapshots recorded yet)
- **Positions table** — ticker, quantity, avg cost, current price, unrealized P&L, unrealized % change, realized P&L; shows "No open positions" when empty
- **Trade bar** — ticker field, quantity field (accepts up to 4 decimal places), Buy button, Sell button; market orders, instant fill; on success: clear the quantity field and refresh the portfolio immediately; on error: display the `error` field from the response inline below the trade bar (no modal)
- **AI chat panel** — collapsible sidebar docked to the right edge; **expanded by default**; a toggle button in the header collapses/expands it; message input, scrolling conversation history (restored from `GET /api/chat/history` on page load), loading indicator while waiting for LLM response; trade executions and watchlist changes shown inline as confirmations; on a non-200 response from `/api/chat`, display the `error` field from the response body as an assistant message in the chat; a successful `/api/chat` response (one that returns `trades_executed`) must trigger an immediate portfolio refresh (same as a manual trade)
- **Header** — portfolio total value (updating live), cash balance, connection status indicator, "Reset Portfolio" button

### Technical Notes

- Use `EventSource` for SSE connection to `/api/stream/prices`
- Use **TradingView Lightweight Charts** for all price charts (main chart area and sparklines) — canvas-based, purpose-built for financial data, MIT-licensed. For sparklines, configure each chart instance with no axes, no grid, and a fixed pixel height of ~40px. With up to 20 tickers, this creates up to 20 canvas elements — verify render performance before shipping.
- Price flash effect: on receiving a new price, briefly apply a CSS class with background color transition, then remove it
- All API calls go to the same origin (`/api/*`) — no CORS configuration needed
- Tailwind CSS for styling with a custom dark theme
- For frontend unit tests, mock `EventSource` with a manual class exposing `onmessage` and `onerror` callbacks — no third-party SSE mock library needed

---

## 11. Docker & Deployment

### Multi-Stage Dockerfile

Two stages: **Node 20** builds the frontend static export; **Python 3.12** runs the backend and serves everything.

Key details:
- Use `npm ci` (not `npm install`) in the Node stage for reproducible builds
- The frontend static export is copied from the Node stage into `/app/static/` in the Python stage
- FastAPI mounts the static directory and serves it at `/*`. For any request path that does not match `/api/*` and does not match a static file, FastAPI must fall back to serving `index.html` (standard SPA pattern — prevents 404 on direct URL access or browser refresh)
- The backend entrypoint is `backend/app/main.py`; the FastAPI app object is `app`
- Expose port 8000
- CMD: `uvicorn app.main:app --host 0.0.0.0 --port 8000`

```dockerfile
HEALTHCHECK --interval=30s --timeout=5s --start-period=30s \
  CMD curl -f http://localhost:8000/api/health || exit 1
```

### .dockerignore

The `.dockerignore` must exclude at minimum: `node_modules/`, `frontend/.next/`, `__pycache__/`, `*.pyc`, `db/finally.db`, `.env`.

### Docker Volume

The SQLite database persists via a named Docker volume:

```bash
docker run -v finally-data:/app/db -p 8000:8000 --env-file .env finally
```

The `db/` directory in the project root maps to `/app/db` in the container. The backend writes `finally.db` to `/app/db/finally.db`. The database path defaults to `/app/db/finally.db` and can be overridden via the `DB_PATH` environment variable (useful for local development outside Docker).

### Start/Stop Scripts

**`scripts/start_mac.sh`** (macOS/Linux):
- Builds the Docker image if not already built (or if `--build` flag passed)
- If the container is already running, prints a message and exits without error (idempotent — does not restart or duplicate it)
- Runs the container with the volume mount, port mapping, and `.env` file
- Prints the URL to access the app
- Optionally opens the browser

**`scripts/stop_mac.sh`** (macOS/Linux):
- Stops and removes the running container
- Does NOT remove the volume (data persists)

**`scripts/start_windows.ps1`** / **`scripts/stop_windows.ps1`**: PowerShell equivalents for Windows.

All scripts should be idempotent — safe to run multiple times.

### Optional Cloud Deployment

The container is designed to deploy to AWS App Runner, Render, or any container platform. A Terraform configuration for App Runner may be provided in a `deploy/` directory as a stretch goal, but is not part of the core build.

---

## 12. Testing Strategy

### Unit Tests (within `frontend/` and `backend/`)

**Backend (pytest)**:
- Market data: simulator generates valid prices, GBM math is correct, Massive API response parsing works, both implementations conform to the abstract interface
- Portfolio: trade execution logic, P&L calculations (unrealized and realized), edge cases (selling more than owned, buying with insufficient cash, selling at a loss, round-trip realized P&L)
- LLM: structured output parsing handles all valid schemas, graceful handling of malformed responses, trade validation within chat flow
- API routes: correct status codes, response shapes, error handling

**Frontend (React Testing Library + Vitest)**:
- Component rendering with mock data
- Price flash animation triggers correctly on price changes
- Watchlist CRUD operations (mock `EventSource` with a manual class exposing `onmessage`/`onerror`)
- Portfolio display calculations (unrealized and realized P&L)
- Chat message rendering and loading state

### E2E Tests (in `test/`)

**Infrastructure**: A separate `docker-compose.test.yml` in `test/` that spins up the app container plus a Playwright container. This keeps browser dependencies out of the production image.

**DB isolation**: Each test run uses a fresh anonymous Docker volume — `docker-compose.test.yml` uses an anonymous volume that is destroyed on teardown. Test scenarios do not share database state.

**Environment**: Tests run with `LLM_MOCK=true` by default for speed and determinism.

**Key Scenarios**:
- Fresh start: default watchlist appears, $10k balance shown, prices are streaming
- Add and remove a ticker from the watchlist; verify 20-ticker cap enforced
- Buy shares: cash decreases, position appears, portfolio updates
- Sell shares: cash increases, realized P&L updates, position updates or shows quantity 0
- Portfolio visualization: heatmap renders with correct colors and CASH block, P&L chart has data points
- AI chat (mocked): send a message, receive a response, trade execution appears inline
- Chat history: refresh the page, verify previous messages reload
- Portfolio reset: click Reset, verify $10k restored and positions cleared
- SSE connectivity: verify the connection status indicator shows "connected" and price updates are arriving within 2 seconds of page load

---

## Appendix: Architecture Decision Log

| Decision | Rationale |
|---|---|
| SSE over WebSockets | One-way push is all we need; simpler, no bidirectional complexity, universal browser support |
| Static Next.js export | Single origin, no CORS issues, one port, one container, simple deployment |
| SQLite over Postgres | Single-user for now; self-contained, zero config. Schema includes `user_id` on every table for future multi-user extensibility without a schema migration. |
| Single Docker container | Students run one command; no docker-compose for production, no service orchestration |
| uv for Python | Fast, modern Python project management; reproducible lockfile; what students should learn |
| Market orders only | Eliminates order book, limit order logic, partial fills — dramatically simpler portfolio math |
| SSE push on cache write | Cadence adapts to data source automatically (simulator: ~500ms, Massive free tier: ~15s) without a separate timer |
| Positions rows never deleted | Preserves realized P&L across round trips on the same ticker without a separate closed-positions table |
| 20-ticker watchlist cap | Prevents Massive API free-tier rate limit issues; sufficient for a demo portfolio |

