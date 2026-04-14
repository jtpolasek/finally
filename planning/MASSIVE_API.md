# Massive API Reference (formerly Polygon.io)

Reference documentation for the Massive (formerly Polygon.io) REST API as used in FinAlly.

> **Rebrand note**: Polygon.io became Massive.com on October 30, 2025. All existing API keys, accounts, and integrations continue to work without interruption. The legacy base URL `https://api.polygon.io` remains supported alongside `https://api.massive.com`.

---

## Overview

| Item | Value |
|---|---|
| Base URL | `https://api.massive.com` (legacy `https://api.polygon.io` supported) |
| Python package | `massive` — install via `pip install -U massive` or `uv add massive` |
| Min Python version | 3.9+ |
| Auth | API key via `MASSIVE_API_KEY` env var or `RESTClient(api_key=...)` |
| Auth header | `Authorization: Bearer <API_KEY>` (client handles automatically) |

---

## Pricing Tiers & Rate Limits

| Plan | Price | Rate Limit | Data Access |
|---|---|---|---|
| **Free** | $0 | 5 req/min | EOD prices only, 2 years history |
| **Starter** | $29/mo | Unlimited | 15-min delayed, minute bars, 5 years history |
| **Developer** | $79/mo | Unlimited | Second-level aggs, WebSocket, trades, 10 years |
| **Advanced** | $199/mo | Unlimited | Real-time, tick-level data, 20+ years |
| **Business** | $1,999/mo | Unlimited | Real-time FMV, no exchange fees, full access |

**FinAlly polling strategy**:
- Free tier: poll every **15 seconds** (stays safely under 5 req/min)
- Paid tiers: poll every **2–5 seconds**

---

## Client Initialization

```python
from massive import RESTClient

# Reads MASSIVE_API_KEY from environment automatically
client = RESTClient()

# Or pass explicitly
client = RESTClient(api_key="your_key_here")
```

The `RESTClient` is **synchronous**. In async FastAPI contexts, run it in a thread pool:

```python
import asyncio

# Wrap synchronous calls to avoid blocking the event loop
result = await asyncio.to_thread(client.get_snapshot_all, ...)
```

---

## Endpoints Used in FinAlly

### 1. Full Market Snapshot — Multiple Tickers (Primary Endpoint)

Gets current prices for a list of tickers in **one API call**. This is the main polling endpoint.

**REST**: `GET /v2/snapshot/locale/us/markets/stocks/tickers`

| Parameter | Type | Required | Description |
|---|---|---|---|
| `tickers` | string | No | Comma-separated list of tickers (e.g. `AAPL,GOOGL,MSFT`). Omit for all tickers. |
| `include_otc` | boolean | No | Include OTC securities. Default: `false` |

**Python client**:
```python
from massive import RESTClient
from massive.rest.models import SnapshotMarketType

client = RESTClient()

# Fetch snapshots for a specific set of tickers — ONE API call
snapshots = client.get_snapshot_all(
    market_type=SnapshotMarketType.STOCKS,
    tickers=["AAPL", "GOOGL", "MSFT", "AMZN", "TSLA"],
)

for snap in snapshots:
    print(f"{snap.ticker}: ${snap.last_trade.price}")
    print(f"  Today's change: {snap.day.change_percent:.2f}%")
    print(f"  Day OHLC: O={snap.day.open} H={snap.day.high} L={snap.day.low} C={snap.day.close}")
    print(f"  Volume: {snap.day.volume:,}")
    # Timestamp is Unix milliseconds
    ts_seconds = snap.last_trade.timestamp / 1000.0
```

**Response shape** (per ticker):
```json
{
  "ticker": "AAPL",
  "day": {
    "open": 129.61,
    "high": 130.15,
    "low": 125.07,
    "close": 125.07,
    "volume": 111237700,
    "volume_weighted_average_price": 127.35,
    "previous_close": 129.61,
    "change": -4.54,
    "change_percent": -3.50
  },
  "last_trade": {
    "price": 125.07,
    "size": 100,
    "exchange": "XNYS",
    "timestamp": 1675190399000
  },
  "last_quote": {
    "bid_price": 125.06,
    "ask_price": 125.08,
    "bid_size": 500,
    "ask_size": 1000,
    "spread": 0.02,
    "timestamp": 1675190399500
  },
  "prev_day": {
    "open": 130.10,
    "high": 131.00,
    "low": 128.50,
    "close": 129.61,
    "volume": 95000000
  },
  "min": { "...": "most recent minute bar" },
  "todays_change": -4.54,
  "todays_change_percent": -3.50,
  "updated": 1675190399000
}
```

**Fields we extract in FinAlly**:
- `snap.last_trade.price` — current price for trading and SSE broadcast
- `snap.last_trade.timestamp` — Unix milliseconds, converted to seconds for the cache
- `snap.day.change_percent` — day change % (informational; session % is computed client-side)

---

### 2. Single Ticker Snapshot

Same data as the bulk endpoint but for one ticker. Useful for a detail view or one-off lookups.

**REST**: `GET /v2/snapshot/locale/us/markets/stocks/tickers/{ticker}`

**Python client**:
```python
from massive.rest.models import SnapshotMarketType

snapshot = client.get_snapshot_ticker(
    market_type=SnapshotMarketType.STOCKS,
    ticker="AAPL",
)

print(f"Price:      ${snapshot.last_trade.price}")
print(f"Bid/Ask:    ${snapshot.last_quote.bid_price} / ${snapshot.last_quote.ask_price}")
print(f"Day range:  ${snapshot.day.low} – ${snapshot.day.high}")
print(f"Change:     {snapshot.day.change_percent:+.2f}%")
```

---

### 3. Previous Day Bar (End-of-Day)

Previous trading day's OHLCV for a single ticker. Useful for seeding close prices or displaying EOD context.

**REST**: `GET /v2/aggs/ticker/{ticker}/prev`

| Parameter | Type | Required | Description |
|---|---|---|---|
| `adjusted` | boolean | No | Split-adjusted. Default: `true` |

**Python client**:
```python
# Returns an iterator; typically just one result
for bar in client.get_previous_close_agg(ticker="AAPL"):
    print(f"Previous close:  ${bar.close}")
    print(f"OHLC:            O={bar.open} H={bar.high} L={bar.low} C={bar.close}")
    print(f"Volume:          {bar.volume:,}")
    print(f"VWAP:            ${bar.vwap}")
    print(f"Timestamp (ms):  {bar.timestamp}")
```

**Response**:
```json
{
  "ticker": "AAPL",
  "adjusted": true,
  "resultsCount": 1,
  "results": [
    {
      "o": 150.00,
      "h": 155.00,
      "l": 149.00,
      "c": 154.50,
      "v": 1000000,
      "vw": 152.30,
      "n": 42000,
      "t": 1672531200000
    }
  ]
}
```

Field key: `o`=open, `h`=high, `l`=low, `c`=close, `v`=volume, `vw`=VWAP, `n`=transaction count, `t`=Unix milliseconds.

---

### 4. Daily Open/Close (Specific Date)

OHLCV plus pre/after-hours prices for a specific calendar date.

**REST**: `GET /v1/open-close/{ticker}/{date}`

| Parameter | Type | Required | Description |
|---|---|---|---|
| `date` | string | Yes | `YYYY-MM-DD` |
| `adjusted` | boolean | No | Split-adjusted. Default: `true` |

**Python client** (direct HTTP, no SDK wrapper):
```python
import httpx

resp = httpx.get(
    f"https://api.massive.com/v1/open-close/AAPL/2024-01-15",
    headers={"Authorization": f"Bearer {api_key}"},
)
data = resp.json()
# data keys: open, close, high, low, volume, preMarket, afterHours, from, symbol, status
```

**Response**:
```json
{
  "open": 185.50,
  "close": 186.20,
  "high": 187.10,
  "low": 184.80,
  "volume": 55000000,
  "preMarket": 185.10,
  "afterHours": 186.00,
  "from": "2024-01-15",
  "symbol": "AAPL",
  "status": "OK"
}
```

---

### 5. Aggregates (Historical Bars)

Historical OHLCV bars over a date range with configurable interval. Not used for live polling but useful for chart history.

**REST**: `GET /v2/aggs/ticker/{ticker}/range/{multiplier}/{timespan}/{from}/{to}`

**Python client**:
```python
bars = []
for bar in client.list_aggs(
    ticker="AAPL",
    multiplier=1,
    timespan="day",       # "minute", "hour", "day", "week", "month"
    from_="2024-01-01",
    to="2024-03-31",
    limit=50000,          # Max page size; SDK handles pagination automatically
):
    bars.append(bar)

for bar in bars:
    print(f"ts={bar.timestamp}  O={bar.open} H={bar.high} L={bar.low} C={bar.close} V={bar.volume}")
```

---

### 6. Last Trade / Last Quote

Point-in-time lookup for a single ticker. Use `get_snapshot_all()` in production (one call vs. N calls).

```python
# Most recent trade
trade = client.get_last_trade(ticker="AAPL")
print(f"Last trade: ${trade.price} × {trade.size} shares at exchange {trade.exchange}")

# Most recent NBBO quote
quote = client.get_last_quote(ticker="AAPL")
print(f"Bid: ${quote.bid} × {quote.bid_size}")
print(f"Ask: ${quote.ask} × {quote.ask_size}")
```

---

## How FinAlly Uses the API

The `MassiveDataSource` runs as a background asyncio task:

```python
import asyncio
from massive import RESTClient
from massive.rest.models import SnapshotMarketType
from app.market.cache import PriceCache

async def poll_once(client: RESTClient, tickers: list[str], cache: PriceCache) -> None:
    """Execute one poll cycle. Runs the synchronous client in a thread."""
    snapshots = await asyncio.to_thread(
        client.get_snapshot_all,
        market_type=SnapshotMarketType.STOCKS,
        tickers=tickers,
    )
    for snap in snapshots:
        try:
            price = snap.last_trade.price
            timestamp = snap.last_trade.timestamp / 1000.0  # ms → seconds
            cache.update(ticker=snap.ticker, price=price, timestamp=timestamp)
        except (AttributeError, TypeError):
            pass  # Skip tickers with incomplete data (e.g., no trades yet today)
```

**Poll cycle**:
1. Read current tickers list (`self._tickers`)
2. Call `get_snapshot_all()` with the full list — **one API call**
3. For each snapshot: extract `last_trade.price`, convert timestamp, write to `PriceCache`
4. Sleep for `poll_interval` seconds, then repeat
5. On any exception: log the error, continue the loop (next poll will retry)

---

## Error Handling

The `massive` client raises exceptions for HTTP errors:

| HTTP Status | Meaning | Action |
|---|---|---|
| `401` | Invalid API key | Check `MASSIVE_API_KEY` env var |
| `403` | Plan doesn't include endpoint | Upgrade plan or use a different endpoint |
| `429` | Rate limit exceeded | Increase poll interval; free tier: ≥15s |
| `5xx` | Server error | Built-in retry (3 attempts); log and continue |

```python
from massive.exceptions import AuthError, NoResultsError

try:
    snapshots = client.get_snapshot_all(market_type=SnapshotMarketType.STOCKS, tickers=tickers)
except AuthError:
    logger.error("Invalid Massive API key — check MASSIVE_API_KEY")
except Exception as e:
    logger.error("Massive poll failed: %s", e)
    # Loop continues — next interval will retry
```

---

## Notes

- The snapshot endpoint returns data for **all requested tickers in one call** — critical for staying within free-tier rate limits
- All timestamps from the API are **Unix milliseconds** — divide by 1000 before storing
- During market closed hours, `last_trade.price` reflects the last traded price (may include after-hours)
- The `day` bar resets at market open; during pre-market, values may be from the previous session
- The backend accepts any ticker string the user enters without validating it against Massive — if Massive has no data for a ticker, `get_snapshot_all()` will simply not include it in the response
- `api.polygon.io` continues to work alongside `api.massive.com` for an extended transition period
