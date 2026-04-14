# Market Simulator Design

Approach and code structure for simulating realistic stock prices when no `MASSIVE_API_KEY` is configured. The simulator is the default for development, demos, and testing.

---

## Overview

The simulator uses **Geometric Brownian Motion (GBM)** to generate realistic stock price paths. GBM is the standard model underlying Black-Scholes option pricing — prices evolve with random noise proportional to the current price, can never go negative, and produce the log-normal distribution observed in real markets.

Prices update every **500ms** (~2 ticks/second), producing a continuous stream that makes the dashboard feel live. Correlated moves across tickers mean related stocks (e.g., AAPL and MSFT) tend to move together, as in real markets.

---

## GBM Mathematics

At each time step, a stock price evolves as:

```
S(t + dt) = S(t) × exp( (μ - σ²/2) × dt + σ × √dt × Z )
```

| Symbol | Meaning |
|---|---|
| `S(t)` | Current price |
| `μ` (mu) | Annualized drift / expected return (e.g. `0.05` = 5%/year) |
| `σ` (sigma) | Annualized volatility (e.g. `0.20` = 20%/year) |
| `dt` | Time step as a fraction of a trading year |
| `Z` | Standard normal random variable drawn from N(0,1) |

**Time step calibration** for 500ms ticks:
```
Trading seconds/year = 252 days × 6.5 hours/day × 3600 sec/hour = 5,896,800
dt = 0.5 / 5,896,800 ≈ 8.48e-8
```

This tiny `dt` produces sub-cent moves per tick that accumulate naturally over time. At `σ = 0.25` (25% annual vol), the expected per-tick move is:
```
σ × √dt ≈ 0.25 × √(8.48e-8) ≈ 0.0073%  (~0.73 cents on a $100 stock)
```

### Why GBM?

- **Multiplicative**: prices are always positive (`exp()` is never ≤ 0)
- **Log-normal**: log returns are normally distributed, matching empirical data
- **Scalable**: parameterized by just `μ` and `σ` per ticker
- **Analytically tractable**: well-understood behavior, easy to calibrate

---

## Correlated Moves

Real stocks don't move independently — tech stocks tend to move together during risk-on/off events. The simulator captures this using a **Cholesky decomposition** of a sector-based correlation matrix.

### Method

1. Build correlation matrix `C` (n×n) using pairwise sector rules (below)
2. Compute lower triangular `L = cholesky(C)`
3. On each tick: draw `Z_independent ~ N(0, I_n)`, then `Z_correlated = L @ Z_independent`
4. Use `Z_correlated[i]` as the random draw for ticker `i`

The Cholesky decomposition preserves the correct covariance structure: `E[L Z (L Z)ᵀ] = L I Lᵀ = C`.

### Sector Correlation Rules

| Pair | Correlation | Reason |
|---|---|---|
| Tech × Tech | **0.6** | AAPL, GOOGL, MSFT, AMZN, META, NVDA, NFLX — co-move on macro |
| Finance × Finance | **0.5** | JPM, V — correlated but less tightly than mega-cap tech |
| TSLA × anything | **0.3** | Idiosyncratic behavior, low market correlation |
| Cross-sector / unknown | **0.3** | Baseline positive correlation (market factor) |

The matrix must be positive semi-definite. With this structure it always is, but Cholesky will raise `LinAlgError` if it isn't — a useful sanity check.

### Rebuilding on Watchlist Changes

When tickers are added or removed, `_rebuild_cholesky()` recomputes the full matrix. This is O(n²) in the number of tickers, but n < 50, so it completes in microseconds.

---

## Random Events

Every tick, each ticker has a small probability (`event_probability = 0.001`, i.e. 0.1%) of a sudden shock — a 2–5% move in either direction. This adds drama and makes the dashboard visually interesting.

```python
if random.random() < event_probability:
    shock_magnitude = random.uniform(0.02, 0.05)   # 2–5%
    shock_sign = random.choice([-1, 1])             # up or down
    price *= (1 + shock_magnitude * shock_sign)
```

**Expected event frequency** with default settings (10 tickers, 2 ticks/sec):
- Per ticker: once every ~500 seconds (~8 minutes)
- Across the watchlist: roughly one event somewhere every ~50 seconds

---

## Seed Prices

Realistic starting prices for the default watchlist:

```python
# seed_prices.py
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
```

Tickers added dynamically that aren't in `SEED_PRICES` start at a random price between $50–$300.

---

## Per-Ticker Parameters

Each ticker has calibrated volatility and drift reflecting real-world behavior:

```python
TICKER_PARAMS: dict[str, dict[str, float]] = {
    "AAPL":  {"sigma": 0.22, "mu": 0.05},   # Large-cap, moderate vol
    "GOOGL": {"sigma": 0.25, "mu": 0.05},
    "MSFT":  {"sigma": 0.20, "mu": 0.05},   # Low vol for its size
    "AMZN":  {"sigma": 0.28, "mu": 0.05},
    "TSLA":  {"sigma": 0.50, "mu": 0.03},   # High vol, lower expected return
    "NVDA":  {"sigma": 0.40, "mu": 0.08},   # High vol, strong growth drift
    "META":  {"sigma": 0.30, "mu": 0.05},
    "JPM":   {"sigma": 0.18, "mu": 0.04},   # Bank: lower vol
    "V":     {"sigma": 0.17, "mu": 0.04},   # Payments: lowest vol
    "NFLX":  {"sigma": 0.35, "mu": 0.05},
}

DEFAULT_PARAMS: dict[str, float] = {"sigma": 0.25, "mu": 0.05}  # Unknown tickers
```

With `sigma=0.50` (TSLA), intraday range at `σ × √(1/252) ≈ 3.15%` — consistent with observed TSLA behavior.

---

## Correlation Groups

```python
CORRELATION_GROUPS: dict[str, set[str]] = {
    "tech":    {"AAPL", "GOOGL", "MSFT", "AMZN", "META", "NVDA", "NFLX"},
    "finance": {"JPM", "V"},
}

INTRA_TECH_CORR    = 0.6   # Tech stocks move together
INTRA_FINANCE_CORR = 0.5   # Finance stocks move together
CROSS_GROUP_CORR   = 0.3   # Between sectors / unknown tickers
TSLA_CORR          = 0.3   # TSLA has low correlation with everything
```

Note: TSLA is in the `tech` set for sector membership, but `_pairwise_correlation` overrides it with `TSLA_CORR` for all pairs.

---

## Implementation: `GBMSimulator`

```python
# simulator.py
import math
import random
import numpy as np
from .seed_prices import (
    SEED_PRICES, TICKER_PARAMS, DEFAULT_PARAMS,
    CORRELATION_GROUPS, INTRA_TECH_CORR, INTRA_FINANCE_CORR,
    CROSS_GROUP_CORR, TSLA_CORR,
)

class GBMSimulator:
    """Generates correlated GBM price paths for multiple tickers.

    Math:
        S(t+dt) = S(t) * exp((mu - sigma^2/2) * dt + sigma * sqrt(dt) * Z)

    The tiny dt (~8.5e-8 for 500ms ticks) produces sub-cent moves per tick
    that accumulate naturally over time.
    """

    TRADING_SECONDS_PER_YEAR = 252 * 6.5 * 3600  # 5,896,800
    DEFAULT_DT = 0.5 / TRADING_SECONDS_PER_YEAR   # ~8.48e-8

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
        """Advance all tickers by one time step. Returns {ticker: new_price}.

        Hot path — called every 500ms. O(n) where n = number of tickers.
        """
        n = len(self._tickers)
        if n == 0:
            return {}

        z_independent = np.random.standard_normal(n)
        z_correlated = self._cholesky @ z_independent if self._cholesky is not None else z_independent

        result: dict[str, float] = {}
        for i, ticker in enumerate(self._tickers):
            mu = self._params[ticker]["mu"]
            sigma = self._params[ticker]["sigma"]

            # GBM: S(t+dt) = S(t) * exp((mu - 0.5*sigma^2)*dt + sigma*sqrt(dt)*Z)
            drift = (mu - 0.5 * sigma**2) * self._dt
            diffusion = sigma * math.sqrt(self._dt) * z_correlated[i]
            self._prices[ticker] *= math.exp(drift + diffusion)

            # Random event: 0.1% chance per tick
            if random.random() < self._event_prob:
                shock = random.uniform(0.02, 0.05) * random.choice([-1, 1])
                self._prices[ticker] *= (1 + shock)

            result[ticker] = round(self._prices[ticker], 2)

        return result

    def add_ticker(self, ticker: str) -> None:
        """Add a ticker to the simulation. Rebuilds correlation matrix."""
        if ticker in self._prices:
            return
        self._add_ticker_internal(ticker)
        self._rebuild_cholesky()

    def remove_ticker(self, ticker: str) -> None:
        """Remove a ticker from the simulation. Rebuilds correlation matrix."""
        if ticker not in self._prices:
            return
        self._tickers.remove(ticker)
        del self._prices[ticker]
        del self._params[ticker]
        self._rebuild_cholesky()

    def get_price(self, ticker: str) -> float | None:
        """Current price for a ticker, or None if not tracked."""
        return self._prices.get(ticker)

    def get_tickers(self) -> list[str]:
        return list(self._tickers)

    # --- Internals ---

    def _add_ticker_internal(self, ticker: str) -> None:
        """Add without rebuilding Cholesky (used during batch init)."""
        self._tickers.append(ticker)
        self._prices[ticker] = SEED_PRICES.get(ticker, random.uniform(50.0, 300.0))
        self._params[ticker] = TICKER_PARAMS.get(ticker, dict(DEFAULT_PARAMS))

    def _rebuild_cholesky(self) -> None:
        """Rebuild Cholesky decomposition of the correlation matrix.

        Called whenever tickers are added or removed. O(n^2), but n < 50.
        """
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

---

## Implementation: `SimulatorDataSource`

Wraps `GBMSimulator` in the `MarketDataSource` async interface. See `MARKET_INTERFACE.md` for full context.

```python
class SimulatorDataSource(MarketDataSource):
    """Drives GBMSimulator in a background asyncio task, writing to PriceCache."""

    def __init__(
        self,
        price_cache: PriceCache,
        update_interval: float = 0.5,      # 500ms between ticks
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

    async def _run_loop(self) -> None:
        while True:
            try:
                if self._sim:
                    for ticker, price in self._sim.step().items():
                        self._cache.update(ticker=ticker, price=price)
            except Exception:
                logger.exception("Simulator step failed")
            await asyncio.sleep(self._interval)

    # add_ticker, remove_ticker, stop — see full implementation in simulator.py
```

---

## Behavioral Properties

| Property | Detail |
|---|---|
| **Prices never go negative** | GBM is multiplicative (`exp()` is always > 0) |
| **Realistic intraday ranges** | Sub-cent moves per tick accumulate to typical daily ranges |
| **Correlated sector moves** | Tech stocks co-move; finance stocks co-move; cross-sector less so |
| **Drama via random events** | ~1 event somewhere in a 10-ticker watchlist every 50 seconds |
| **Cache pre-seeded on start** | No delay on first SSE connect — prices available immediately |
| **New tickers seeded immediately** | `add_ticker()` writes a starting price before the next loop tick |
| **Cholesky rebuilt on changes** | O(n²) per add/remove, negligible at n < 50 |

---

## Testing the Simulator

The GBM math can be verified by checking that returns are normally distributed and that long-run drift matches `μ`. The test suite in `backend/tests/market/` covers:

- `test_simulator.py` — `GBMSimulator.step()` returns correct shape, prices stay positive, drift is approximately correct over many steps
- `test_simulator_source.py` — `SimulatorDataSource` lifecycle (start, add_ticker, remove_ticker, stop), cache population, graceful CancelledError handling

Run with:
```bash
cd backend
uv run --extra dev pytest tests/market/test_simulator.py tests/market/test_simulator_source.py -v
```
