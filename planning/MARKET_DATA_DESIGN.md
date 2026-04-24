# Market Data Backend — Design Document

This document describes the as-built market data subsystem in `backend/app/market/`. All code snippets reflect the actual implementation.

---

## Table of Contents

1. [Architecture Overview](#1-architecture-overview)
2. [File Structure](#2-file-structure)
3. [Data Model — `models.py`](#3-data-model)
4. [Price Cache — `cache.py`](#4-price-cache)
5. [Abstract Interface — `interface.py`](#5-abstract-interface)
6. [Seed Prices & Parameters — `seed_prices.py`](#6-seed-prices--parameters)
7. [GBM Simulator — `simulator.py`](#7-gbm-simulator)
8. [Massive API Client — `massive_client.py`](#8-massive-api-client)
9. [Factory — `factory.py`](#9-factory)
10. [SSE Streaming — `stream.py`](#10-sse-streaming)
11. [FastAPI Lifecycle Integration](#11-fastapi-lifecycle-integration)
12. [Watchlist Coordination](#12-watchlist-coordination)
13. [Testing Strategy](#13-testing-strategy)
14. [Error Handling & Edge Cases](#14-error-handling--edge-cases)
15. [Configuration Summary](#15-configuration-summary)

---

## 1. Architecture Overview

```
MarketDataSource (ABC)
├── SimulatorDataSource  →  GBM simulator (default — no API key needed)
└── MassiveDataSource    →  Polygon.io REST polling (when MASSIVE_API_KEY set)
        │
        ▼ writes on every tick
PriceCache (thread-safe, in-memory)
        │
        ├──→  GET /api/stream/prices  (SSE: version-diff push to browser)
        ├──→  POST /api/portfolio/trade  (fill market orders at current price)
        └──→  GET /api/portfolio  (unrealized P&L valuation)
```

**Strategy pattern** — both data sources implement the same abstract interface. All downstream code reads from `PriceCache` and is source-agnostic.

**Push model** — the data source writes to the cache on its own schedule (simulator: ~500ms, Massive: ~15s). The SSE endpoint reads from the cache at its own cadence. No coupling between the two.

**Public API** — import everything from `app.market`:

```python
from app.market import PriceCache, PriceUpdate, MarketDataSource, create_market_data_source, create_stream_router
```

---

## 2. File Structure

```
backend/app/market/
├── __init__.py          # Public re-exports
├── models.py            # PriceUpdate — immutable frozen dataclass
├── cache.py             # PriceCache — thread-safe in-memory store
├── interface.py         # MarketDataSource — abstract base class
├── seed_prices.py       # SEED_PRICES, TICKER_PARAMS, correlation constants
├── simulator.py         # GBMSimulator (math engine) + SimulatorDataSource (async wrapper)
├── massive_client.py    # MassiveDataSource — REST polling client
├── factory.py           # create_market_data_source() — env-var dispatch
└── stream.py            # create_stream_router() — FastAPI SSE endpoint factory
```

Each file has a single responsibility. `__init__.py` re-exports the public surface so the rest of the backend never imports from submodules directly.

---

## 3. Data Model

**`backend/app/market/models.py`**

`PriceUpdate` is the only data structure that leaves the market data layer. Every consumer (SSE, portfolio, trade execution) works with this type exclusively.

```python
from __future__ import annotations

import time
from dataclasses import dataclass, field


@dataclass(frozen=True, slots=True)
class PriceUpdate:
    """Immutable snapshot of a single ticker's price at a point in time."""

    ticker: str
    price: float           # Current price, rounded to 2 decimal places
    previous_price: float  # Price from the preceding update
    timestamp: float = field(default_factory=time.time)  # Unix seconds

    @property
    def change(self) -> float:
        return round(self.price - self.previous_price, 4)

    @property
    def change_percent(self) -> float:
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

**Design decisions:**
- `frozen=True` — price updates are immutable value objects, safe to share across async tasks without copying.
- `slots=True` — minor memory optimization; many instances are created per second.
- Computed properties (`change`, `direction`, `change_percent`) are derived from stored fields — they can never be inconsistent with each other.
- `to_dict()` is the single serialization point used by both SSE and REST responses.

---

## 4. Price Cache

**`backend/app/market/cache.py`**

The price cache is the central hub. Data sources write to it; SSE streaming and portfolio valuation read from it. Uses `threading.Lock` because the Massive client's synchronous REST calls run in `asyncio.to_thread()` (a real OS thread), which `asyncio.Lock` would not protect against.

```python
from __future__ import annotations

import time
from threading import Lock

from .models import PriceUpdate


class PriceCache:
    """Thread-safe in-memory cache of the latest price for each ticker."""

    def __init__(self) -> None:
        self._prices: dict[str, PriceUpdate] = {}
        self._lock = Lock()
        self._version: int = 0  # Monotonically increasing; bumped on every update

    def update(self, ticker: str, price: float, timestamp: float | None = None) -> PriceUpdate:
        """Record a new price. Returns the PriceUpdate.

        First update for a ticker: previous_price == price (direction='flat').
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
        update = self.get(ticker)
        return update.price if update else None

    def get_all(self) -> dict[str, PriceUpdate]:
        """Shallow copy of all current prices."""
        with self._lock:
            return dict(self._prices)

    def remove(self, ticker: str) -> None:
        with self._lock:
            self._prices.pop(ticker, None)

    @property
    def version(self) -> int:
        """Monotonic counter. Useful for SSE change detection."""
        return self._version

    def __len__(self) -> int:
        with self._lock:
            return len(self._prices)

    def __contains__(self, ticker: str) -> bool:
        with self._lock:
            return ticker in self._prices
```

**Version counter** — the SSE loop polls every 500ms. The version counter lets it skip sending when nothing has changed. This matters for Massive (free tier only updates every 15s): without it, the SSE loop would serialize and transmit identical data 30 times between Massive polls.

```python
# SSE loop pattern
last_version = -1
while True:
    current_version = price_cache.version
    if current_version != last_version:
        last_version = current_version
        yield format_sse(price_cache.get_all())
    await asyncio.sleep(0.5)
```

---

## 5. Abstract Interface

**`backend/app/market/interface.py`**

```python
from __future__ import annotations

from abc import ABC, abstractmethod


class MarketDataSource(ABC):
    """Contract for market data providers.

    Implementations push price updates into a shared PriceCache.
    Downstream code never calls the source directly for prices — it reads
    from the cache.

    Lifecycle:
        source = create_market_data_source(cache)
        await source.start(["AAPL", "GOOGL", ...])   # starts background task
        await source.add_ticker("TSLA")               # dynamically extend
        await source.remove_ticker("GOOGL")           # dynamically shrink
        await source.stop()                           # graceful shutdown
    """

    @abstractmethod
    async def start(self, tickers: list[str]) -> None:
        """Begin producing price updates. Starts a background task.
        Must be called exactly once."""

    @abstractmethod
    async def stop(self) -> None:
        """Stop the background task. Safe to call multiple times."""

    @abstractmethod
    async def add_ticker(self, ticker: str) -> None:
        """Add a ticker to the active set. No-op if already present."""

    @abstractmethod
    async def remove_ticker(self, ticker: str) -> None:
        """Remove a ticker. Also removes it from the PriceCache."""

    @abstractmethod
    def get_tickers(self) -> list[str]:
        """Return the current list of actively tracked tickers."""
```

The push model decouples timing: the simulator ticks at 500ms, Massive polls at 15s, but SSE always reads from the cache at its own 500ms cadence. No layer needs to know what the active data source is.

---

## 6. Seed Prices & Parameters

**`backend/app/market/seed_prices.py`**

Constants only — no logic. Shared by the simulator for initial prices and GBM parameters.

```python
# Realistic starting prices for the default watchlist
SEED_PRICES: dict[str, float] = {
    "AAPL":  190.00,
    "GOOGL": 175.00,
    "MSFT":  420.00,
    "AMZN":  185.00,
    "TSLA":  250.00,
    "NVDA":  800.00,
    "META":  500.00,
    "JPM":   195.00,
    "V":     280.00,
    "NFLX":  600.00,
}

# Per-ticker GBM parameters
# sigma: annualized volatility   mu: annualized drift / expected return
TICKER_PARAMS: dict[str, dict[str, float]] = {
    "AAPL":  {"sigma": 0.22, "mu": 0.05},   # Large-cap, moderate vol
    "GOOGL": {"sigma": 0.25, "mu": 0.05},
    "MSFT":  {"sigma": 0.20, "mu": 0.05},   # Lowest vol of the tech group
    "AMZN":  {"sigma": 0.28, "mu": 0.05},
    "TSLA":  {"sigma": 0.50, "mu": 0.03},   # High vol, lower expected return
    "NVDA":  {"sigma": 0.40, "mu": 0.08},   # High vol, strong growth drift
    "META":  {"sigma": 0.30, "mu": 0.05},
    "JPM":   {"sigma": 0.18, "mu": 0.04},   # Bank: lower vol
    "V":     {"sigma": 0.17, "mu": 0.04},   # Payments: lowest vol
    "NFLX":  {"sigma": 0.35, "mu": 0.05},
}

DEFAULT_PARAMS: dict[str, float] = {"sigma": 0.25, "mu": 0.05}  # Unknown tickers

# Correlation groups for Cholesky decomposition
CORRELATION_GROUPS: dict[str, set[str]] = {
    "tech":    {"AAPL", "GOOGL", "MSFT", "AMZN", "META", "NVDA", "NFLX"},
    "finance": {"JPM", "V"},
}

INTRA_TECH_CORR    = 0.6   # Tech stocks move together (macro driven)
INTRA_FINANCE_CORR = 0.5   # Finance stocks move together
CROSS_GROUP_CORR   = 0.3   # Cross-sector baseline (positive market correlation)
TSLA_CORR          = 0.3   # TSLA is in tech set but overridden — does its own thing
```

Dynamically-added tickers not in `SEED_PRICES` start at a random price between $50–$300. They use `DEFAULT_PARAMS` for GBM and `CROSS_GROUP_CORR` against all existing tickers.

---

## 7. GBM Simulator

**`backend/app/market/simulator.py`**

Two classes: `GBMSimulator` (the math engine) and `SimulatorDataSource` (the async wrapper that implements `MarketDataSource`).

### 7.1 GBM Mathematics

At each time step, a stock price evolves as:

```
S(t + dt) = S(t) × exp( (μ - σ²/2) × dt + σ × √dt × Z )
```

| Symbol | Meaning |
|---|---|
| `S(t)` | Current price |
| `μ` | Annualized drift (expected return) |
| `σ` | Annualized volatility |
| `dt` | Time step as a fraction of a trading year |
| `Z` | Standard normal random variable from N(0,1) |

**Time step for 500ms ticks:**
```
Trading seconds/year = 252 days × 6.5 hours/day × 3600 sec/hour = 5,896,800
dt = 0.5 / 5,896,800 ≈ 8.48e-8
```

This tiny `dt` produces sub-cent moves per tick. At σ = 0.25 (25% annual vol):
```
Expected per-tick move = σ × √dt ≈ 0.25 × √(8.48e-8) ≈ 0.0073%
                       ≈ 0.73 cents on a $100 stock
```

Properties of GBM:
- **Always positive** — `exp()` can never be ≤ 0
- **Log-normal returns** — matches empirical market data
- **Scalable** — just `μ` and `σ` per ticker, no other parameters

### 7.2 Correlated Moves

Real stocks don't move independently. The simulator uses a **Cholesky decomposition** of a sector-based correlation matrix to produce correlated moves.

**Method:**
1. Build n×n correlation matrix `C` using sector rules
2. Compute lower triangular `L = cholesky(C)`
3. Each tick: draw `Z_independent ~ N(0, Iₙ)`, then `Z_correlated = L @ Z_independent`
4. Use `Z_correlated[i]` as the random draw for ticker `i`

This preserves the correct covariance: `E[LZ (LZ)ᵀ] = LILᵀ = C`.

The matrix is rebuilt (O(n²), negligible at n < 50) whenever tickers are added or removed.

### 7.3 Random Shock Events

Each tick, each ticker has a 0.1% chance of a sudden 2–5% move for visual drama:

```python
if random.random() < event_probability:  # 0.001 default
    shock_magnitude = random.uniform(0.02, 0.05)
    shock_sign = random.choice([-1, 1])
    price *= (1 + shock_magnitude * shock_sign)
```

With 10 tickers at 2 ticks/sec: roughly one event somewhere in the watchlist every ~50 seconds.

### 7.4 GBMSimulator Implementation

```python
import math
import random
import numpy as np
from .seed_prices import (
    SEED_PRICES, TICKER_PARAMS, DEFAULT_PARAMS,
    CORRELATION_GROUPS, INTRA_TECH_CORR, INTRA_FINANCE_CORR,
    CROSS_GROUP_CORR, TSLA_CORR,
)


class GBMSimulator:
    """Geometric Brownian Motion simulator for correlated stock prices."""

    TRADING_SECONDS_PER_YEAR = 252 * 6.5 * 3600   # 5,896,800
    DEFAULT_DT = 0.5 / TRADING_SECONDS_PER_YEAR    # ~8.48e-8

    def __init__(
        self,
        tickers: list[str],
        dt: float = DEFAULT_DT,
        event_probability: float = 0.001,
    ) -> None:
        self._dt = dt
        self._event_prob = event_probability
        self._tickers: list[str] = []
        self._prices: dict[str, float] = {}
        self._params: dict[str, dict[str, float]] = {}
        self._cholesky: np.ndarray | None = None

        for ticker in tickers:
            self._add_ticker_internal(ticker)
        self._rebuild_cholesky()

    def step(self) -> dict[str, float]:
        """Advance all tickers by one time step. Returns {ticker: new_price}."""
        n = len(self._tickers)
        if n == 0:
            return {}

        z_independent = np.random.standard_normal(n)
        z_correlated = self._cholesky @ z_independent if self._cholesky is not None else z_independent

        result: dict[str, float] = {}
        for i, ticker in enumerate(self._tickers):
            mu = self._params[ticker]["mu"]
            sigma = self._params[ticker]["sigma"]

            drift = (mu - 0.5 * sigma ** 2) * self._dt
            diffusion = sigma * math.sqrt(self._dt) * z_correlated[i]
            self._prices[ticker] *= math.exp(drift + diffusion)

            if random.random() < self._event_prob:
                shock = random.uniform(0.02, 0.05) * random.choice([-1, 1])
                self._prices[ticker] *= (1 + shock)

            result[ticker] = round(self._prices[ticker], 2)

        return result

    def add_ticker(self, ticker: str) -> None:
        if ticker in self._prices:
            return
        self._add_ticker_internal(ticker)
        self._rebuild_cholesky()

    def remove_ticker(self, ticker: str) -> None:
        if ticker not in self._prices:
            return
        self._tickers.remove(ticker)
        del self._prices[ticker]
        del self._params[ticker]
        self._rebuild_cholesky()

    def get_price(self, ticker: str) -> float | None:
        return self._prices.get(ticker)

    def get_tickers(self) -> list[str]:
        return list(self._tickers)

    def _add_ticker_internal(self, ticker: str) -> None:
        self._tickers.append(ticker)
        self._prices[ticker] = SEED_PRICES.get(ticker, random.uniform(50.0, 300.0))
        self._params[ticker] = TICKER_PARAMS.get(ticker, dict(DEFAULT_PARAMS))

    def _rebuild_cholesky(self) -> None:
        n = len(self._tickers)
        if n <= 1:
            self._cholesky = None
            return
        corr = np.eye(n)
        for i in range(n):
            for j in range(i + 1, n):
                rho = self._pairwise_correlation(self._tickers[i], self._tickers[j])
                corr[i, j] = rho
                corr[j, i] = rho
        self._cholesky = np.linalg.cholesky(corr)

    @staticmethod
    def _pairwise_correlation(t1: str, t2: str) -> float:
        tech = CORRELATION_GROUPS["tech"]
        finance = CORRELATION_GROUPS["finance"]
        if t1 == "TSLA" or t2 == "TSLA":
            return TSLA_CORR
        if t1 in tech and t2 in tech:
            return INTRA_TECH_CORR
        if t1 in finance and t2 in finance:
            return INTRA_FINANCE_CORR
        return CROSS_GROUP_CORR
```

### 7.5 SimulatorDataSource — Async Wrapper

```python
import asyncio
import logging
from .cache import PriceCache
from .interface import MarketDataSource

logger = logging.getLogger(__name__)


class SimulatorDataSource(MarketDataSource):
    """Drives GBMSimulator in a background asyncio task, writing to PriceCache."""

    def __init__(
        self,
        price_cache: PriceCache,
        update_interval: float = 0.5,       # 500ms between ticks
        event_probability: float = 0.001,
    ) -> None:
        self._cache = price_cache
        self._interval = update_interval
        self._event_prob = event_probability
        self._sim: GBMSimulator | None = None
        self._task: asyncio.Task | None = None

    async def start(self, tickers: list[str]) -> None:
        self._sim = GBMSimulator(tickers=tickers, event_probability=self._event_prob)
        # Pre-seed cache so SSE clients have prices immediately on first connect
        for ticker in tickers:
            price = self._sim.get_price(ticker)
            if price is not None:
                self._cache.update(ticker=ticker, price=price)
        self._task = asyncio.create_task(self._run_loop(), name="simulator-loop")
        logger.info("Simulator started with %d tickers", len(tickers))

    async def stop(self) -> None:
        if self._task and not self._task.done():
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        self._task = None
        logger.info("Simulator stopped")

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
                    for ticker, price in self._sim.step().items():
                        self._cache.update(ticker=ticker, price=price)
            except Exception:
                logger.exception("Simulator step failed")
            await asyncio.sleep(self._interval)
```

**Key behaviors:**
- `start()` pre-seeds the cache before the loop begins — SSE has data on first connect.
- `stop()` cancels and awaits the task, catching `CancelledError` for clean FastAPI shutdown.
- The loop catches all exceptions per-step so one bad tick doesn't kill the data feed.
- `add_ticker()` seeds the cache immediately so the new ticker has a price before the next loop tick.

---

## 8. Massive API Client

**`backend/app/market/massive_client.py`**

Polls `GET /v2/snapshot/locale/us/markets/stocks/tickers` for all watched tickers in **one API call** per interval. The Massive `RESTClient` is synchronous and runs in `asyncio.to_thread()` to avoid blocking the event loop.

```python
from __future__ import annotations

import asyncio
import logging
from typing import Any

from .cache import PriceCache
from .interface import MarketDataSource

logger = logging.getLogger(__name__)


class MassiveDataSource(MarketDataSource):
    """MarketDataSource backed by the Massive (Polygon.io) REST API.

    Rate limits:
      - Free tier: 5 req/min → poll every 15s (default)
      - Paid tiers: poll every 2-5s
    """

    def __init__(
        self,
        api_key: str,
        price_cache: PriceCache,
        poll_interval: float = 15.0,
    ) -> None:
        self._api_key = api_key
        self._cache = price_cache
        self._interval = poll_interval
        self._tickers: list[str] = []
        self._task: asyncio.Task | None = None
        self._client: Any = None

    async def start(self, tickers: list[str]) -> None:
        from massive import RESTClient

        self._client = RESTClient(api_key=self._api_key)
        self._tickers = list(tickers)
        await self._poll_once()  # Immediate first poll — cache has data right away
        self._task = asyncio.create_task(self._poll_loop(), name="massive-poller")
        logger.info("Massive poller started: %d tickers, %.1fs interval", len(tickers), self._interval)

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
            logger.info("Massive: added ticker %s (appears on next poll)", ticker)

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
            snapshots = await asyncio.to_thread(self._fetch_snapshots)
            processed = 0
            for snap in snapshots:
                try:
                    price = snap.last_trade.price
                    timestamp = snap.last_trade.timestamp / 1000.0  # ms → seconds
                    self._cache.update(ticker=snap.ticker, price=price, timestamp=timestamp)
                    processed += 1
                except (AttributeError, TypeError) as e:
                    logger.warning("Skipping snapshot for %s: %s", getattr(snap, "ticker", "???"), e)
            logger.debug("Massive poll: updated %d/%d tickers", processed, len(self._tickers))
        except Exception as e:
            logger.error("Massive poll failed: %s", e)
            # Don't re-raise — loop retries on next interval

    def _fetch_snapshots(self) -> list:
        """Synchronous Massive API call. Runs in a thread via asyncio.to_thread()."""
        from massive.rest.models import SnapshotMarketType

        return self._client.get_snapshot_all(
            market_type=SnapshotMarketType.STOCKS,
            tickers=self._tickers,
        )
```

**Snapshot response fields used:**
- `snap.ticker` — ticker symbol
- `snap.last_trade.price` — current price for cache and SSE
- `snap.last_trade.timestamp` — Unix milliseconds (divided by 1000 before storing)

**Error handling:**

| Error | Behavior |
|-------|----------|
| 401 Unauthorized | Logged. Poller continues retrying. |
| 429 Rate Limited | Logged. Next poll retries after `poll_interval`. |
| Network timeout | Logged. Retries automatically on next cycle. |
| Malformed snapshot | Individual ticker skipped with warning; others succeed. |
| All tickers fail | Cache retains last-known prices. SSE streams stale data. |

**Lazy import:** `from massive import RESTClient` happens inside `start()`, not at module import time. Students using the simulator don't need the `massive` package installed.

---

## 9. Factory

**`backend/app/market/factory.py`**

```python
from __future__ import annotations

import logging
import os

from .cache import PriceCache
from .interface import MarketDataSource

logger = logging.getLogger(__name__)


def create_market_data_source(price_cache: PriceCache) -> MarketDataSource:
    """Create the appropriate market data source based on environment variables.

    - MASSIVE_API_KEY set and non-empty → MassiveDataSource (real market data)
    - Otherwise → SimulatorDataSource (GBM simulation, no API key needed)

    Returns an unstarted source. Caller must: await source.start(tickers)
    """
    api_key = os.environ.get("MASSIVE_API_KEY", "").strip()

    if api_key:
        from .massive_client import MassiveDataSource
        logger.info("Market data source: Massive API (real data)")
        return MassiveDataSource(api_key=api_key, price_cache=price_cache)
    else:
        from .simulator import SimulatorDataSource
        logger.info("Market data source: GBM Simulator")
        return SimulatorDataSource(price_cache=price_cache)
```

---

## 10. SSE Streaming

**`backend/app/market/stream.py`**

The SSE endpoint holds open a long-lived HTTP connection and pushes all ticker prices to the client as a single JSON payload each tick. Uses version-based change detection to avoid redundant sends.

```python
from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import AsyncGenerator

from fastapi import APIRouter, Request
from fastapi.responses import StreamingResponse

from .cache import PriceCache

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/stream", tags=["streaming"])


def create_stream_router(price_cache: PriceCache) -> APIRouter:
    """Factory: returns an APIRouter with the cache injected."""

    @router.get("/prices")
    async def stream_prices(request: Request) -> StreamingResponse:
        return StreamingResponse(
            _generate_events(price_cache, request),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "Connection": "keep-alive",
                "X-Accel-Buffering": "no",  # Disable nginx buffering if proxied
            },
        )

    return router


async def _generate_events(
    price_cache: PriceCache,
    request: Request,
    interval: float = 0.5,
) -> AsyncGenerator[str, None]:
    yield "retry: 1000\n\n"  # Browser reconnects after 1s if connection drops

    last_version = -1
    client_ip = request.client.host if request.client else "unknown"
    logger.info("SSE client connected: %s", client_ip)

    try:
        while True:
            if await request.is_disconnected():
                logger.info("SSE client disconnected: %s", client_ip)
                break

            current_version = price_cache.version
            if current_version != last_version:
                last_version = current_version
                prices = price_cache.get_all()
                if prices:
                    data = {ticker: update.to_dict() for ticker, update in prices.items()}
                    payload = json.dumps(data)
                    yield f"data: {payload}\n\n"

            await asyncio.sleep(interval)
    except asyncio.CancelledError:
        logger.info("SSE stream cancelled for: %s", client_ip)
```

**Wire format** — each SSE event is a single `data:` message (unnamed — the browser `onmessage` handler receives it) containing all tickers as one JSON object:

```
retry: 1000

data: {"AAPL":{"ticker":"AAPL","price":190.50,"previous_price":190.42,"timestamp":1707580800.5,"change":0.08,"change_percent":0.042,"direction":"up"},"GOOGL":{...}}

```

**Frontend usage:**

```javascript
const es = new EventSource('/api/stream/prices');
es.onmessage = (event) => {
    const prices = JSON.parse(event.data);
    // prices: { "AAPL": { ticker, price, previous_price, change, ... }, ... }
    for (const [ticker, update] of Object.entries(prices)) {
        flashPrice(ticker, update.direction);
        updateSparkline(ticker, update.price);
    }
};
```

**Why poll-and-push, not event-driven?** The SSE generator polls the cache at a fixed interval rather than being notified on each write. This produces evenly-spaced updates regardless of data source cadence, which is better for sparkline chart rendering (the frontend accumulates data points at regular intervals).

---

## 11. FastAPI Lifecycle Integration

**`backend/app/main.py`** — the market data system starts and stops with the FastAPI application.

```python
from contextlib import asynccontextmanager

from fastapi import FastAPI, Depends

from app.market import PriceCache, MarketDataSource, create_market_data_source, create_stream_router


price_cache = PriceCache()


@asynccontextmanager
async def lifespan(app: FastAPI):
    # --- Startup ---

    # 1. Read initial watchlist from DB
    initial_tickers = await db_get_watchlist_tickers()

    # 2. Create and start the market data source
    source = create_market_data_source(price_cache)
    app.state.market_source = source
    app.state.price_cache = price_cache
    await source.start(initial_tickers)

    # 3. Register the SSE router
    stream_router = create_stream_router(price_cache)
    app.include_router(stream_router)

    yield  # App is running

    # --- Shutdown ---
    await source.stop()


app = FastAPI(title="FinAlly", lifespan=lifespan)


# Dependency injection helpers
def get_price_cache() -> PriceCache:
    return app.state.price_cache


def get_market_source() -> MarketDataSource:
    return app.state.market_source
```

**Startup sequence** (order matters):
1. DB init — create tables, seed data
2. Market data source start — reads watchlist from DB, passes tickers to `source.start()`
3. Portfolio snapshot background task — starts 30s snapshot loop

---

## 12. Watchlist Coordination

When the watchlist changes via REST or LLM chat, the market data source must be notified.

### Adding a Ticker

```python
@router.post("/api/watchlist")
async def add_to_watchlist(
    payload: WatchlistAdd,
    source: MarketDataSource = Depends(get_market_source),
    db: aiosqlite.Connection = Depends(get_db),
):
    # 1. Insert into DB (validates cap, duplicates)
    await db.execute("INSERT INTO watchlist (user_id, ticker, added_at) VALUES (?, ?, ?)", ...)

    # 2. Tell source to start tracking
    await source.add_ticker(payload.ticker)
    #    Simulator: adds to GBMSimulator, rebuilds Cholesky, seeds cache immediately
    #    Massive: appends to ticker list, appears on next poll cycle

    return {"ticker": payload.ticker, "price": price_cache.get_price(payload.ticker)}
```

### Removing a Ticker

```python
@router.delete("/api/watchlist/{ticker}")
async def remove_from_watchlist(
    ticker: str,
    source: MarketDataSource = Depends(get_market_source),
    db: aiosqlite.Connection = Depends(get_db),
):
    await db.execute("DELETE FROM watchlist WHERE user_id = ? AND ticker = ?", ...)

    # Only evict from cache if no open position (portfolio valuation needs prices)
    position = await db.execute_fetchone(
        "SELECT quantity FROM positions WHERE user_id = ? AND ticker = ?", ...
    )
    if position is None or position["quantity"] == 0:
        await source.remove_ticker(ticker)
        #    Simulator: removes from GBMSimulator, rebuilds Cholesky, removes from cache
        #    Massive: removes from ticker list, evicts from cache

    return {"status": "ok"}
```

### Trade Execution

```python
@router.post("/api/portfolio/trade")
async def execute_trade(
    trade: TradeRequest,
    price_cache: PriceCache = Depends(get_price_cache),
    db: aiosqlite.Connection = Depends(get_db),
):
    current_price = price_cache.get_price(trade.ticker)
    if current_price is None:
        raise HTTPException(400, detail="Ticker not found in price cache")
    # Fill at current_price — instant market order
    ...
```

---

## 13. Testing Strategy

### Running the Tests

```bash
cd backend
uv run --extra dev pytest tests/market/ -v         # All market tests
uv run --extra dev pytest tests/market/ --cov=app  # With coverage
```

**73 tests, 84% overall coverage.** All passing.

### 13.1 Unit Tests — GBMSimulator

```python
# backend/tests/market/test_simulator.py
import pytest
from app.market.simulator import GBMSimulator
from app.market.seed_prices import SEED_PRICES


class TestGBMSimulator:

    def test_step_returns_all_tickers(self):
        sim = GBMSimulator(tickers=["AAPL", "GOOGL"])
        assert set(sim.step().keys()) == {"AAPL", "GOOGL"}

    def test_prices_are_always_positive(self):
        sim = GBMSimulator(tickers=["AAPL"])
        for _ in range(10_000):
            assert sim.step()["AAPL"] > 0

    def test_initial_prices_match_seeds(self):
        sim = GBMSimulator(tickers=["AAPL"])
        assert sim.get_price("AAPL") == SEED_PRICES["AAPL"]

    def test_add_ticker_included_in_next_step(self):
        sim = GBMSimulator(tickers=["AAPL"])
        sim.add_ticker("TSLA")
        assert "TSLA" in sim.step()

    def test_remove_ticker_excluded_from_next_step(self):
        sim = GBMSimulator(tickers=["AAPL", "GOOGL"])
        sim.remove_ticker("GOOGL")
        result = sim.step()
        assert "GOOGL" not in result
        assert "AAPL" in result

    def test_add_duplicate_is_noop(self):
        sim = GBMSimulator(tickers=["AAPL"])
        sim.add_ticker("AAPL")
        assert len(sim._tickers) == 1

    def test_remove_nonexistent_is_noop(self):
        sim = GBMSimulator(tickers=["AAPL"])
        sim.remove_ticker("ZZZZ")  # Must not raise

    def test_unknown_ticker_gets_random_seed_price(self):
        sim = GBMSimulator(tickers=["UNKN"])
        assert 50.0 <= sim.get_price("UNKN") <= 300.0

    def test_empty_step_returns_empty_dict(self):
        sim = GBMSimulator(tickers=[])
        assert sim.step() == {}

    def test_cholesky_exists_for_multiple_tickers(self):
        sim = GBMSimulator(tickers=["AAPL"])
        assert sim._cholesky is None        # 1 ticker: no matrix
        sim.add_ticker("GOOGL")
        assert sim._cholesky is not None    # 2 tickers: matrix built
```

### 13.2 Unit Tests — PriceCache

```python
# backend/tests/market/test_cache.py
from app.market.cache import PriceCache


class TestPriceCache:

    def test_update_and_get(self):
        cache = PriceCache()
        update = cache.update("AAPL", 190.50)
        assert update.ticker == "AAPL"
        assert update.price == 190.50
        assert cache.get("AAPL") == update

    def test_first_update_is_flat_direction(self):
        cache = PriceCache()
        update = cache.update("AAPL", 190.50)
        assert update.direction == "flat"
        assert update.previous_price == 190.50

    def test_direction_up(self):
        cache = PriceCache()
        cache.update("AAPL", 190.00)
        update = cache.update("AAPL", 191.00)
        assert update.direction == "up"
        assert update.change == 1.00

    def test_direction_down(self):
        cache = PriceCache()
        cache.update("AAPL", 190.00)
        update = cache.update("AAPL", 189.00)
        assert update.direction == "down"
        assert update.change == -1.00

    def test_remove_clears_ticker(self):
        cache = PriceCache()
        cache.update("AAPL", 190.00)
        cache.remove("AAPL")
        assert cache.get("AAPL") is None

    def test_version_increments_on_each_update(self):
        cache = PriceCache()
        v0 = cache.version
        cache.update("AAPL", 190.00)
        assert cache.version == v0 + 1
        cache.update("AAPL", 191.00)
        assert cache.version == v0 + 2

    def test_get_all_returns_copy(self):
        cache = PriceCache()
        cache.update("AAPL", 190.00)
        cache.update("GOOGL", 175.00)
        assert set(cache.get_all().keys()) == {"AAPL", "GOOGL"}
```

### 13.3 Integration Tests — SimulatorDataSource

```python
# backend/tests/market/test_simulator_source.py
import asyncio
import pytest
from app.market.cache import PriceCache
from app.market.simulator import SimulatorDataSource


@pytest.mark.asyncio
class TestSimulatorDataSource:

    async def test_start_populates_cache_immediately(self):
        cache = PriceCache()
        source = SimulatorDataSource(price_cache=cache, update_interval=0.1)
        await source.start(["AAPL", "GOOGL"])
        # Prices available before any loop ticks
        assert cache.get("AAPL") is not None
        assert cache.get("GOOGL") is not None
        await source.stop()

    async def test_stop_is_idempotent(self):
        cache = PriceCache()
        source = SimulatorDataSource(price_cache=cache, update_interval=0.1)
        await source.start(["AAPL"])
        await source.stop()
        await source.stop()  # Must not raise

    async def test_add_and_remove_ticker(self):
        cache = PriceCache()
        source = SimulatorDataSource(price_cache=cache, update_interval=0.1)
        await source.start(["AAPL"])

        await source.add_ticker("TSLA")
        assert "TSLA" in source.get_tickers()
        assert cache.get("TSLA") is not None  # Seeded immediately

        await source.remove_ticker("TSLA")
        assert "TSLA" not in source.get_tickers()
        assert cache.get("TSLA") is None     # Evicted from cache

        await source.stop()
```

### 13.4 Unit Tests — MassiveDataSource (mocked)

```python
# backend/tests/market/test_massive.py
from unittest.mock import MagicMock, patch
import pytest
from app.market.cache import PriceCache
from app.market.massive_client import MassiveDataSource


def _make_snapshot(ticker: str, price: float, timestamp_ms: int) -> MagicMock:
    snap = MagicMock()
    snap.ticker = ticker
    snap.last_trade.price = price
    snap.last_trade.timestamp = timestamp_ms
    return snap


@pytest.mark.asyncio
class TestMassiveDataSource:

    async def test_poll_updates_cache(self):
        cache = PriceCache()
        source = MassiveDataSource(api_key="test", price_cache=cache, poll_interval=60.0)
        source._tickers = ["AAPL", "GOOGL"]
        source._client = MagicMock()

        with patch.object(source, "_fetch_snapshots", return_value=[
            _make_snapshot("AAPL", 190.50, 1707580800000),
            _make_snapshot("GOOGL", 175.25, 1707580800000),
        ]):
            await source._poll_once()

        assert cache.get_price("AAPL") == 190.50
        assert cache.get_price("GOOGL") == 175.25

    async def test_malformed_snapshot_is_skipped(self):
        cache = PriceCache()
        source = MassiveDataSource(api_key="test", price_cache=cache, poll_interval=60.0)
        source._tickers = ["AAPL", "BAD"]
        source._client = MagicMock()

        bad_snap = MagicMock()
        bad_snap.ticker = "BAD"
        bad_snap.last_trade = None  # Causes AttributeError

        with patch.object(source, "_fetch_snapshots", return_value=[
            _make_snapshot("AAPL", 190.50, 1707580800000),
            bad_snap,
        ]):
            await source._poll_once()

        assert cache.get_price("AAPL") == 190.50
        assert cache.get_price("BAD") is None  # Skipped cleanly

    async def test_api_error_does_not_crash_loop(self):
        cache = PriceCache()
        source = MassiveDataSource(api_key="test", price_cache=cache, poll_interval=60.0)
        source._tickers = ["AAPL"]
        source._client = MagicMock()

        with patch.object(source, "_fetch_snapshots", side_effect=Exception("network error")):
            await source._poll_once()  # Must not raise

        assert cache.get_price("AAPL") is None
```

---

## 14. Error Handling & Edge Cases

### Empty Watchlist on Startup

Both data sources handle `start([])` gracefully. The simulator produces no prices; the Massive poller skips its API call. SSE streams no data. When the user adds a ticker, the source starts tracking it immediately.

### Price Cache Miss During Trade

The simulator avoids this by seeding the cache in `add_ticker()`. Massive may have a brief gap (new ticker won't appear until the next poll interval):

```python
price = price_cache.get_price(ticker)
if price is None:
    raise HTTPException(
        status_code=400,
        detail="Price not yet available for this ticker. Please wait a moment and try again.",
    )
```

### Invalid Massive API Key

If `MASSIVE_API_KEY` is set but invalid, the first poll fails with a 401. The poller logs the error and keeps retrying. The SSE stream stays open but sends empty data. The user sees prices as `—`. Fix: correct the key and restart the container.

### Ticker Removed but Position Still Open

When the user removes a ticker from the watchlist, the watchlist route checks for an open position before calling `remove_ticker()`. If `quantity > 0`, the source keeps tracking it so portfolio valuation stays accurate. The ticker is evicted from the cache only when both conditions are true: removed from watchlist AND no open position.

### Thread Safety Under Load

`threading.Lock` is a mutex — one holder at a time. Under normal load (20 tickers, 2 updates/sec), lock contention is negligible. The critical section is a single dict assignment. If this ever became a bottleneck (hundreds of tickers, many concurrent readers), the fix would be a `ReadWriteLock`, but that's unnecessary for this project.

---

## 15. Configuration Summary

| Parameter | Location | Default | Description |
|-----------|----------|---------|-------------|
| `MASSIVE_API_KEY` | Environment | `""` | If set and non-empty → Massive API; else → simulator |
| `update_interval` | `SimulatorDataSource.__init__` | `0.5s` | Time between simulator ticks |
| `poll_interval` | `MassiveDataSource.__init__` | `15.0s` | Time between Massive API polls (free tier safe) |
| `event_probability` | `GBMSimulator.__init__` | `0.001` | Chance of random shock per ticker per tick |
| `dt` | `GBMSimulator.__init__` | `~8.48e-8` | GBM time step (fraction of a trading year) |
| SSE push interval | `_generate_events()` | `0.5s` | How often SSE loop checks for cache changes |
| SSE retry directive | `_generate_events()` | `1000ms` | Browser `EventSource` reconnection delay |

### `__init__.py` Public API

```python
# backend/app/market/__init__.py
from .cache import PriceCache
from .factory import create_market_data_source
from .interface import MarketDataSource
from .models import PriceUpdate
from .stream import create_stream_router

__all__ = [
    "PriceUpdate",
    "PriceCache",
    "MarketDataSource",
    "create_market_data_source",
    "create_stream_router",
]
```

### Quick-Start Usage for Downstream Code

```python
from app.market import PriceCache, create_market_data_source, create_stream_router

# Application startup
cache = PriceCache()
source = create_market_data_source(cache)   # reads MASSIVE_API_KEY
await source.start(["AAPL", "GOOGL", "MSFT", ...])

# Read prices anywhere
update = cache.get("AAPL")           # PriceUpdate | None
price  = cache.get_price("AAPL")     # float | None
all_px = cache.get_all()             # dict[str, PriceUpdate]

# Dynamic watchlist management
await source.add_ticker("PYPL")
await source.remove_ticker("NFLX")

# Create SSE router and register with FastAPI
router = create_stream_router(cache)   # GET /api/stream/prices
app.include_router(router)

# Application shutdown
await source.stop()
```
