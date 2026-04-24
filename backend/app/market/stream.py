"""SSE streaming endpoint for live price updates."""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import AsyncGenerator
from datetime import datetime, timezone

from fastapi import APIRouter, Request
from fastapi.responses import StreamingResponse

from .cache import PriceCache

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/stream", tags=["streaming"])

# Seconds between keepalive comments when the watchlist is empty.
# Prevents browser EventSource from timing out the idle connection.
KEEPALIVE_INTERVAL = 15.0


def create_stream_router(price_cache: PriceCache) -> APIRouter:
    """Create the SSE streaming router with a reference to the price cache.

    This factory pattern lets us inject the PriceCache without globals.
    """

    @router.get("/prices")
    async def stream_prices(request: Request) -> StreamingResponse:
        """SSE endpoint for live price updates.

        Emits one named 'price' event per ticker whenever the price cache
        updates. When the watchlist is empty, sends a keepalive comment every
        15 seconds to prevent browser EventSource timeouts.

        Event format:
            event: price
            data: {"ticker": "AAPL", "price": 192.50, "prev_price": 191.80,
                   "change": 0.37, "timestamp": "2026-04-10T10:00:00.123Z"}
        """
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


def _unix_to_iso(unix_seconds: float) -> str:
    """Convert a Unix timestamp (seconds) to ISO 8601 with millisecond precision.

    Example output: "2026-04-10T10:00:00.123Z"
    """
    dt = datetime.fromtimestamp(unix_seconds, tz=timezone.utc)
    ms = dt.microsecond // 1000
    return dt.strftime(f"%Y-%m-%dT%H:%M:%S.{ms:03d}Z")


async def _generate_events(
    price_cache: PriceCache,
    request: Request,
    interval: float = 0.5,
) -> AsyncGenerator[str, None]:
    """Async generator that yields SSE-formatted price events.

    Polls the PriceCache version every `interval` seconds. When the version
    changes, emits one 'price' event per updated ticker. When the watchlist
    is empty and no events have been sent for KEEPALIVE_INTERVAL seconds,
    emits a keepalive comment to prevent connection timeouts.
    """
    # Tell the client to retry after 1 second if the connection drops
    yield "retry: 1000\n\n"

    last_version = -1
    loop = asyncio.get_running_loop()
    last_send_time = loop.time()
    client_ip = request.client.host if request.client else "unknown"
    logger.info("SSE client connected: %s", client_ip)

    try:
        while True:
            # Check for client disconnect
            if await request.is_disconnected():
                logger.info("SSE client disconnected: %s", client_ip)
                break

            current_version = price_cache.version
            if current_version != last_version:
                last_version = current_version
                prices = price_cache.get_all()

                if prices:
                    for update in prices.values():
                        payload = json.dumps({
                            "ticker": update.ticker,
                            "price": update.price,
                            "prev_price": update.previous_price,
                            "change": update.change,
                            "timestamp": _unix_to_iso(update.timestamp),
                        })
                        yield f"event: price\ndata: {payload}\n\n"
                    last_send_time = loop.time()

            # Send keepalive comment when no data has been sent recently
            # (covers the empty-watchlist case per PLAN.md)
            now = loop.time()
            if now - last_send_time >= KEEPALIVE_INTERVAL:
                yield ": keepalive\n\n"
                last_send_time = now

            await asyncio.sleep(interval)
    except asyncio.CancelledError:
        logger.info("SSE stream cancelled for: %s", client_ip)
