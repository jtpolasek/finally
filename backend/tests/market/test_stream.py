"""Tests for SSE streaming endpoint (stream.py)."""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import pytest

from app.market.cache import PriceCache
from app.market.stream import KEEPALIVE_INTERVAL, _generate_events, _unix_to_iso


class TestUnixToIso:
    """Tests for the timestamp conversion helper."""

    def test_format_structure(self):
        """Converted timestamp follows ISO 8601 with milliseconds."""
        result = _unix_to_iso(1712750400.0)
        # e.g. "2024-04-10T12:00:00.000Z"
        assert result.endswith("Z")
        assert "T" in result
        assert len(result) == 24  # "YYYY-MM-DDTHH:MM:SS.mmmZ"

    def test_millisecond_precision(self):
        """Milliseconds are included and zero-padded."""
        # 0.123 seconds = 123ms
        result = _unix_to_iso(1712750400.123)
        ms_part = result[-4:-1]  # the 3 digits before 'Z'
        assert len(ms_part) == 3
        assert ms_part.isdigit()

    def test_zero_milliseconds(self):
        """Whole-second timestamps show .000 milliseconds."""
        result = _unix_to_iso(1712750400.0)
        assert result.endswith(".000Z")

    def test_returns_string(self):
        """Return type is always a string."""
        assert isinstance(_unix_to_iso(0.0), str)

    def test_utc_timezone(self):
        """Timestamps are in UTC (Z suffix)."""
        result = _unix_to_iso(0.0)  # Unix epoch
        assert result == "1970-01-01T00:00:00.000Z"


@pytest.mark.asyncio
class TestGenerateEvents:
    """Tests for the _generate_events async generator."""

    def _make_request(self, disconnected_after: int = 999) -> MagicMock:
        """Create a mock FastAPI Request that disconnects after N checks."""
        request = MagicMock()
        request.client = MagicMock()
        request.client.host = "127.0.0.1"
        call_count = [0]

        async def is_disconnected():
            call_count[0] += 1
            return call_count[0] > disconnected_after

        request.is_disconnected = is_disconnected
        return request

    async def _collect_events(
        self,
        cache: PriceCache,
        request: MagicMock,
        interval: float = 0.001,
        max_items: int = 50,
    ) -> list[str]:
        """Collect up to max_items SSE chunks from the generator."""
        chunks: list[str] = []
        gen = _generate_events(cache, request, interval=interval)
        async for chunk in gen:
            chunks.append(chunk)
            if len(chunks) >= max_items:
                break
        return chunks

    async def test_first_chunk_is_retry_directive(self):
        """Generator starts with a retry directive."""
        cache = PriceCache()
        request = self._make_request(disconnected_after=1)
        chunks = await self._collect_events(cache, request)
        assert chunks[0] == "retry: 1000\n\n"

    async def test_price_event_format_named_event(self):
        """Price events are named 'price' events."""
        cache = PriceCache()
        cache.update("AAPL", 190.50)

        request = self._make_request(disconnected_after=2)
        chunks = await self._collect_events(cache, request, max_items=5)

        price_chunks = [c for c in chunks if c.startswith("event: price")]
        assert len(price_chunks) > 0

        # Each price event has the right two-line structure
        event_chunk = price_chunks[0]
        lines = event_chunk.strip().split("\n")
        assert lines[0] == "event: price"
        assert lines[1].startswith("data: ")

    async def test_price_event_payload_fields(self):
        """Price event payloads contain the required PLAN.md fields."""
        cache = PriceCache()
        cache.update("AAPL", 190.00)
        cache.update("AAPL", 191.00)

        request = self._make_request(disconnected_after=2)
        chunks = await self._collect_events(cache, request, max_items=5)

        price_chunks = [c for c in chunks if c.startswith("event: price")]
        assert len(price_chunks) > 0

        data_line = price_chunks[0].strip().split("\n")[1]
        payload = json.loads(data_line[len("data: "):])

        assert "ticker" in payload
        assert "price" in payload
        assert "prev_price" in payload  # PLAN.md field name
        assert "change" in payload
        assert "timestamp" in payload

    async def test_prev_price_field_name(self):
        """SSE payload uses 'prev_price', not 'previous_price'."""
        cache = PriceCache()
        cache.update("AAPL", 190.00)
        cache.update("AAPL", 191.50)

        request = self._make_request(disconnected_after=2)
        chunks = await self._collect_events(cache, request, max_items=5)

        price_chunks = [c for c in chunks if c.startswith("event: price")]
        data_line = price_chunks[0].strip().split("\n")[1]
        payload = json.loads(data_line[len("data: "):])

        assert "prev_price" in payload
        assert "previous_price" not in payload

    async def test_timestamp_is_iso8601(self):
        """SSE payload timestamp is an ISO 8601 string, not a float."""
        cache = PriceCache()
        cache.update("AAPL", 190.00)

        request = self._make_request(disconnected_after=2)
        chunks = await self._collect_events(cache, request, max_items=5)

        price_chunks = [c for c in chunks if c.startswith("event: price")]
        data_line = price_chunks[0].strip().split("\n")[1]
        payload = json.loads(data_line[len("data: "):])

        ts = payload["timestamp"]
        assert isinstance(ts, str)
        assert ts.endswith("Z")
        assert "T" in ts

    async def test_one_event_per_ticker(self):
        """Each ticker produces its own separate event."""
        cache = PriceCache()
        cache.update("AAPL", 190.00)
        cache.update("GOOGL", 175.00)

        request = self._make_request(disconnected_after=2)
        chunks = await self._collect_events(cache, request, max_items=10)

        price_chunks = [c for c in chunks if c.startswith("event: price")]
        tickers_seen = set()
        for chunk in price_chunks:
            data_line = chunk.strip().split("\n")[1]
            payload = json.loads(data_line[len("data: "):])
            tickers_seen.add(payload["ticker"])

        assert "AAPL" in tickers_seen
        assert "GOOGL" in tickers_seen

    async def test_no_events_when_cache_empty(self):
        """No price events are emitted when the cache is empty."""
        cache = PriceCache()
        request = self._make_request(disconnected_after=1)

        chunks = await self._collect_events(cache, request, max_items=5)
        price_chunks = [c for c in chunks if c.startswith("event: price")]
        assert len(price_chunks) == 0

    async def test_keepalive_when_empty(self):
        """A keepalive comment is sent after KEEPALIVE_INTERVAL when cache is empty."""
        cache = PriceCache()
        request = self._make_request(disconnected_after=999)

        chunks: list[str] = []
        # Patch KEEPALIVE_INTERVAL to a very short duration so the test runs fast
        with patch("app.market.stream.KEEPALIVE_INTERVAL", 0.01):
            gen = _generate_events(cache, request, interval=0.001)
            async for chunk in gen:
                chunks.append(chunk)
                if chunk == ": keepalive\n\n":
                    break
                if len(chunks) > 100:
                    break

        assert ": keepalive\n\n" in chunks

    async def test_no_duplicate_events_on_same_version(self):
        """Price events are only emitted when the cache version changes."""
        cache = PriceCache()
        cache.update("AAPL", 190.00)

        request = self._make_request(disconnected_after=3)

        # Two iterations with the same version should only yield events once
        chunks = await self._collect_events(cache, request, max_items=20)
        price_chunks = [c for c in chunks if c.startswith("event: price")]

        # Should only see AAPL once (version didn't change after the initial update)
        aapl_events = [c for c in price_chunks if '"AAPL"' in c]
        assert len(aapl_events) == 1

    async def test_price_values_correct(self):
        """Price and prev_price values in event payload are correct."""
        cache = PriceCache()
        cache.update("AAPL", 190.00)
        cache.update("AAPL", 195.00)

        request = self._make_request(disconnected_after=2)
        chunks = await self._collect_events(cache, request, max_items=5)

        price_chunks = [c for c in chunks if '"AAPL"' in c and c.startswith("event: price")]
        assert len(price_chunks) > 0

        data_line = price_chunks[0].strip().split("\n")[1]
        payload = json.loads(data_line[len("data: "):])

        assert payload["ticker"] == "AAPL"
        assert payload["price"] == 195.00
        assert payload["prev_price"] == 190.00
        assert payload["change"] == 5.00


class TestKeepaliveInterval:
    """Test the module-level KEEPALIVE_INTERVAL constant."""

    def test_keepalive_interval_is_15_seconds(self):
        """KEEPALIVE_INTERVAL should be 15 seconds as specified in PLAN.md."""
        assert KEEPALIVE_INTERVAL == 15.0
