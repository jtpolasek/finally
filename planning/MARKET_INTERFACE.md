# Market Data Interface Design

Unified Python interface for market data in FinAlly. Two implementations — `SimulatorDataSource` and `MassiveDataSource` — sit behind one abstract interface. All downstream code (SSE streaming, portfolio valuation, trade execution) is source-agnostic and reads from a shared `PriceCache`.

---

## Module Layout

```
backend/app/market/
├── __init__.py          # Public re-exports
├── models.py            # PriceUpdate dataclass
├── cache.py             # PriceCache
├── interface.py         # MarketDataSource ABC
├── factory.py           # create_market_data_source()
├── massive_client.py    # MassiveDataSource
├── simulator.py         # SimulatorDataSource + GBMSimulator
└── seed_prices.py       # SEED_PRICES, TICKER_PARAMS, DEFAULT_PARAMS, correlation constants
```

Public API (import from `app.market`):

```python
from app.market import PriceCache, PriceUpdate, MarketDataSource, create_market_data_source
```

---

## Core Data Model: `PriceUpdate`

The **only** data structure that leaves the market data layer. Every downstream consumer works with `PriceUpdate` objects.

```python
# models.py
from dataclasses import dataclass, field
import time

@dataclass(frozen=True, slots=True)
class PriceUpdate:
    """Immutable snapshot of a single ticker's price at a point in time."""
    ticker: str
    price: float          # Current price, rounded to 2 decimal places
    previous_price: float # Price from the preceding update
    timestamp: float = field(default_factory=time.time)  # Unix seconds

    @property
    def change(self) -> float:
        """Absolute price change from previous update."""
        return round(self.price - self.previous_price, 4)

    @property
    def change_percent(self) -> float:
        """Percentage change from previous update."""
        if self.previous_price == 0:
            return 0.0
        return round((self.price - self.previous_price) / self.previous_price * 100, 4)

    @property
    def direction(self) -> str:
        """'up', 'down', or 'flat'."""
        if self.price > self.previous_price:
            return "up"
        elif self.price < self.previous_price:
            return "down"
        return "flat"

    def to_dict(self) -> dict:
        """Serialize for JSON / SSE transmission."""
        return {
            "ticker": self.ticker,
            "price": self.price,
            "previous_price": self.previous_price,
            "timestamp": self.timestamp,
            "change": self.change,
            "change_percent": self.change_percent,
            "direction": self.direction,
        }
```

---

## Price Cache: `PriceCache`

Thread-safe in-memory store. The single source of truth for current prices at runtime.

**Writers**: one `MarketDataSource` (simulator or Massive), writing periodically from a background task.  
**Readers**: SSE streaming endpoint, portfolio valuation, trade execution — all read concurrently.

```python
# cache.py
from threading import Lock

class PriceCache:
    """Thread-safe in-memory cache of the latest price for each ticker."""

    def __init__(self) -> None:
        self._prices: dict[str, PriceUpdate] = {}
        self._lock = Lock()
        self._version: int = 0  # Monotonically increasing; bumped on every update

    def update(self, ticker: str, price: float, timestamp: float | None = None) -> PriceUpdate:
        """Record a new price. Returns the PriceUpdate.

        On first update for a ticker: previous_price == price (direction='flat').
        """
        with self._lock:
            ts = timestamp or time.time()
            prev = self._prices.get(ticker)
            previous_price = prev.price if prev else price
            update = PriceUpdate(
                ticker=ticker,
                price=round(price, 2),
                previous_price=round(previous_price, 2),
                timestamp=ts,
            )
            self._prices[ticker] = update
            self._version += 1
            return update

    def get(self, ticker: str) -> PriceUpdate | None:
        with self._lock:
            return self._prices.get(ticker)

    def get_price(self, ticker: str) -> float | None:
        """Convenience: just the price float, or None."""
        update = self.get(ticker)
        return update.price if update else None

    def get_all(self) -> dict[str, PriceUpdate]:
        """Shallow copy of all current prices."""
        with self._lock:
            return dict(self._prices)

    def remove(self, ticker: str) -> None:
        """Remove a ticker (called when removed from watchlist)."""
        with self._lock:
            self._prices.pop(ticker, None)

    @property
    def version(self) -> int:
        """Monotonic counter. Useful for SSE change detection."""
        return self._version
```

---

## Abstract Interface: `MarketDataSource`

```python
# interface.py
from abc import ABC, abstractmethod

class MarketDataSource(ABC):
    """Contract for market data providers.

    Implementations push price updates into a shared PriceCache on their own
    schedule. Downstream code never calls the data source directly for prices —
    it reads from the cache.

    Lifecycle:
        source = create_market_data_source(cache)
        await source.start(["AAPL", "GOOGL", ...])   # starts background task
        await source.add_ticker("TSLA")               # dynamically extend watchlist
        await source.remove_ticker("GOOGL")           # dynamically shrink watchlist
        await source.stop()                           # graceful shutdown
    """

    @abstractmethod
    async def start(self, tickers: list[str]) -> None:
        """Begin producing price updates for the given tickers.
        Starts a background task. Must be called exactly once."""

    @abstractmethod
    async def stop(self) -> None:
        """Stop the background task and release resources.
        Safe to call multiple times."""

    @abstractmethod
    async def add_ticker(self, ticker: str) -> None:
        """Add a ticker to the active set. No-op if already present.
        The next update cycle will include this ticker."""

    @abstractmethod
    async def remove_ticker(self, ticker: str) -> None:
        """Remove a ticker from the active set. No-op if not present.
        Also removes the ticker from the PriceCache."""

    @abstractmethod
    def get_tickers(self) -> list[str]:
        """Return the current list of actively tracked tickers."""
```

---

## Factory: `create_market_data_source`

Selects the implementation at startup based on `MASSIVE_API_KEY`:

```python
# factory.py
import os

def create_market_data_source(price_cache: PriceCache) -> MarketDataSource:
    """Returns an unstarted MarketDataSource. Caller must await source.start(tickers)."""
    api_key = os.environ.get("MASSIVE_API_KEY", "").strip()
    if api_key:
        from .massive_client import MassiveDataSource
        return MassiveDataSource(api_key=api_key, price_cache=price_cache)
    else:
        from .simulator import SimulatorDataSource
        return SimulatorDataSource(price_cache=price_cache)
```

---

## Massive Implementation: `MassiveDataSource`

Polls `GET /v2/snapshot/locale/us/markets/stocks/tickers` for all watched tickers in **one API call** per interval.

```python
# massive_client.py
import asyncio
import logging
from massive import RESTClient
from massive.rest.models import SnapshotMarketType

class MassiveDataSource(MarketDataSource):
    def __init__(
        self,
        api_key: str,
        price_cache: PriceCache,
        poll_interval: float = 15.0,  # 15s default → safe for free tier (5 req/min)
    ) -> None:
        self._client: RESTClient | None = None
        self._api_key = api_key
        self._cache = price_cache
        self._interval = poll_interval
        self._tickers: list[str] = []
        self._task: asyncio.Task | None = None

    async def start(self, tickers: list[str]) -> None:
        self._client = RESTClient(api_key=self._api_key)
        self._tickers = list(tickers)
        await self._poll_once()  # Immediate first poll — cache has data right away
        self._task = asyncio.create_task(self._poll_loop(), name="massive-poller")

    async def stop(self) -> None:
        if self._task and not self._task.done():
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        self._task = None
        self._client = None

    async def add_ticker(self, ticker: str) -> None:
        ticker = ticker.upper().strip()
        if ticker not in self._tickers:
            self._tickers.append(ticker)

    async def remove_ticker(self, ticker: str) -> None:
        ticker = ticker.upper().strip()
        self._tickers = [t for t in self._tickers if t != ticker]
        self._cache.remove(ticker)

    def get_tickers(self) -> list[str]:
        return list(self._tickers)

    async def _poll_loop(self) -> None:
        while True:
            await asyncio.sleep(self._interval)
            await self._poll_once()

    async def _poll_once(self) -> None:
        if not self._tickers or not self._client:
            return
        try:
            # RESTClient is synchronous — run in thread pool to avoid blocking
            snapshots = await asyncio.to_thread(self._fetch_snapshots)
            for snap in snapshots:
                try:
                    price = snap.last_trade.price
                    timestamp = snap.last_trade.timestamp / 1000.0  # ms → seconds
                    self._cache.update(ticker=snap.ticker, price=price, timestamp=timestamp)
                except (AttributeError, TypeError):
                    pass  # Skip tickers with no trade data (e.g., pre-market)
        except Exception as e:
            logger.error("Massive poll failed: %s", e)
            # Don't re-raise — loop retries on next interval

    def _fetch_snapshots(self) -> list:
        return self._client.get_snapshot_all(
            market_type=SnapshotMarketType.STOCKS,
            tickers=self._tickers,
        )
```

---

## Simulator Implementation: `SimulatorDataSource`

Wraps `GBMSimulator` in an async loop. See `MARKET_SIMULATOR.md` for GBM math and parameters.

```python
# simulator.py (outer wrapper)
class SimulatorDataSource(MarketDataSource):
    def __init__(
        self,
        price_cache: PriceCache,
        update_interval: float = 0.5,      # 2 ticks/second
        event_probability: float = 0.001,  # 0.1% random shock per tick per ticker
    ) -> None:
        self._cache = price_cache
        self._interval = update_interval
        self._event_prob = event_probability
        self._sim: GBMSimulator | None = None
        self._task: asyncio.Task | None = None

    async def start(self, tickers: list[str]) -> None:
        self._sim = GBMSimulator(tickers=tickers, event_probability=self._event_prob)
        # Seed cache immediately so SSE has prices on first connect
        for ticker in tickers:
            price = self._sim.get_price(ticker)
            if price is not None:
                self._cache.update(ticker=ticker, price=price)
        self._task = asyncio.create_task(self._run_loop(), name="simulator-loop")

    async def stop(self) -> None:
        if self._task and not self._task.done():
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        self._task = None

    async def add_ticker(self, ticker: str) -> None:
        if self._sim:
            self._sim.add_ticker(ticker)
            price = self._sim.get_price(ticker)
            if price is not None:
                self._cache.update(ticker=ticker, price=price)  # seed immediately

    async def remove_ticker(self, ticker: str) -> None:
        if self._sim:
            self._sim.remove_ticker(ticker)
        self._cache.remove(ticker)

    def get_tickers(self) -> list[str]:
        return self._sim.get_tickers() if self._sim else []

    async def _run_loop(self) -> None:
        while True:
            try:
                if self._sim:
                    prices = self._sim.step()  # dict[str, float]
                    for ticker, price in prices.items():
                        self._cache.update(ticker=ticker, price=price)
            except Exception:
                logger.exception("Simulator step failed")
            await asyncio.sleep(self._interval)
```

---

## Integration: SSE Streaming

The SSE endpoint reads from `PriceCache` and pushes to connected clients. The `version` counter enables efficient change detection — the streamer only broadcasts when prices have actually changed.

```python
# stream.py (simplified)
import asyncio
import json
from fastapi import APIRouter
from fastapi.responses import StreamingResponse

def create_stream_router(price_cache: PriceCache) -> APIRouter:
    router = APIRouter()

    @router.get("/api/stream/prices")
    async def stream_prices():
        async def event_generator():
            last_version = -1
            while True:
                current_version = price_cache.version
                if current_version != last_version:
                    last_version = current_version
                    prices = price_cache.get_all()
                    for update in prices.values():
                        data = json.dumps(update.to_dict())
                        yield f"event: price\ndata: {data}\n\n"
                else:
                    yield ": keepalive\n\n"  # Prevent client timeout
                await asyncio.sleep(0.5)

        return StreamingResponse(event_generator(), media_type="text/event-stream")

    return router
```

---

## Integration: Trade Execution

Trade routes read current price from the cache before filling an order:

```python
# In trade execution handler
current_price = price_cache.get_price(ticker)
if current_price is None:
    raise HTTPException(400, detail="Ticker not found in price cache")
# Proceed with fill at current_price
```

---

## Application Lifecycle

In `main.py` using FastAPI's `lifespan` event:

```python
from contextlib import asynccontextmanager
from app.market import PriceCache, create_market_data_source

price_cache = PriceCache()

@asynccontextmanager
async def lifespan(app: FastAPI):
    # --- Startup ---
    initial_tickers = await db_get_watchlist_tickers()          # Read from DB
    source = create_market_data_source(price_cache)
    await source.start(initial_tickers)                         # Begins background task

    # Wire watchlist change handlers
    app.state.market_source = source
    app.state.price_cache = price_cache

    yield  # App is running

    # --- Shutdown ---
    await source.stop()
```

**Watchlist change handlers** (called from watchlist router):
```python
await app.state.market_source.add_ticker("PYPL")
await app.state.market_source.remove_ticker("NFLX")
```

---

## Data Flow Diagram

```
MarketDataSource (background task)
  │
  │  writes PriceUpdate every 500ms (sim) or 15s (Massive)
  ▼
PriceCache (in-memory, thread-safe)
  ├── ← read by SSE endpoint      → broadcasts event: price to all clients
  ├── ← read by trade execution   → fills market orders at current price
  └── ← read by portfolio         → computes unrealized P&L
```

---

## Behavioral Guarantees

| Behavior | Detail |
|---|---|
| Cache is pre-seeded on start | `start()` populates the cache before the background loop begins. SSE clients get prices on first connect. |
| New tickers have prices immediately | `add_ticker()` seeds the cache with the current simulator price or waits for the next Massive poll. |
| Removed tickers are evicted | `remove_ticker()` clears the cache entry synchronously. |
| Loop never crashes | Both `_run_loop()` and `_poll_loop()` catch all exceptions and continue. Individual tick failures are logged but don't stop the stream. |
| Timestamps are Unix seconds | `PriceUpdate.timestamp` is always Unix seconds, regardless of source. Massive API returns milliseconds; the client divides by 1000 before storing. |
