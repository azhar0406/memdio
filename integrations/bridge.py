"""Bridge external sound-tag events into Memdio memories via the REST API.

The bridge is source-agnostic. It ships with a mock source for local testing and
with a stdin JSON-lines source for real integrations. To connect a websocket or
another live producer, implement the EventSource protocol and register it in
create_event_source().
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import signal
import sys
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Protocol

try:
    import httpx
except ImportError:  # pragma: no cover - exercised only when the extra is missing.
    httpx = None


LOGGER = logging.getLogger("memdio.bridge")
DEFAULT_API_URL = os.getenv("MEMDIO_API_URL", "http://localhost:8000/memories")
DEFAULT_THRESHOLD = float(os.getenv("MEMDIO_CONF_THRESHOLD", "0.7"))
DEFAULT_DEBOUNCE_SECONDS = float(os.getenv("MEMDIO_DEBOUNCE_SECONDS", "5"))
DEFAULT_TIMEOUT_SECONDS = float(os.getenv("MEMDIO_HTTP_TIMEOUT", "5"))
DEFAULT_RETRIES = int(os.getenv("MEMDIO_HTTP_RETRIES", "3"))


@dataclass(slots=True, frozen=True)
class SoundEvent:
    """A normalized sound-tag event."""

    tag: str
    confidence: float
    ts: datetime = field(default_factory=lambda: datetime.now(UTC))


class EventSource(Protocol):
    """Pluggable source of sound-tag events."""

    async def events(self, stop_event: asyncio.Event) -> AsyncIterator[SoundEvent]:
        """Yield normalized sound events until shutdown is requested."""


class MockEventSource:
    """Emit sample tags forever for local validation."""

    def __init__(self, interval_seconds: float = 1.0) -> None:
        self._interval_seconds = interval_seconds
        self._samples = (
            SoundEvent(tag="rooster", confidence=0.92),
            SoundEvent(tag="thunder", confidence=0.88),
            SoundEvent(tag="guitar", confidence=0.95),
            SoundEvent(tag="fan", confidence=0.41),
        )

    async def events(self, stop_event: asyncio.Event) -> AsyncIterator[SoundEvent]:
        while not stop_event.is_set():
            for sample in self._samples:
                if stop_event.is_set():
                    break
                yield SoundEvent(
                    tag=sample.tag,
                    confidence=sample.confidence,
                    ts=datetime.now(UTC),
                )
                await asyncio.sleep(self._interval_seconds)


class StdInJsonEventSource:
    """Read newline-delimited JSON events from stdin.

    Expected format per line:
    {"tag": "rooster", "confidence": 0.92, "ts": "2026-07-05T10:00:00Z"}
    """

    async def events(self, stop_event: asyncio.Event) -> AsyncIterator[SoundEvent]:
        while not stop_event.is_set():
            line = await asyncio.to_thread(sys.stdin.readline)
            if not line:
                await asyncio.sleep(0.1)
                continue

            payload = json.loads(line)
            yield SoundEvent(
                tag=str(payload["tag"]),
                confidence=float(payload["confidence"]),
                ts=parse_timestamp(payload.get("ts")),
            )


def parse_timestamp(value: object | None) -> datetime:
    """Parse an optional ISO timestamp, defaulting to the current UTC time."""

    if value is None:
        return datetime.now(UTC)
    if isinstance(value, datetime):
        return value.astimezone(UTC)
    if not isinstance(value, str):
        raise TypeError("timestamp must be an ISO-8601 string")

    normalized = value.replace("Z", "+00:00")
    return datetime.fromisoformat(normalized).astimezone(UTC)


@dataclass(slots=True)
class EventGate:
    """Apply confidence and debounce filtering to events."""

    threshold: float
    debounce_seconds: float
    last_seen: dict[str, datetime] = field(default_factory=dict)

    def should_store(self, event: SoundEvent) -> bool:
        if event.confidence < self.threshold:
            return False

        previous = self.last_seen.get(event.tag)
        if previous is not None:
            elapsed = (event.ts - previous).total_seconds()
            if elapsed < self.debounce_seconds:
                return False

        self.last_seen[event.tag] = event.ts
        return True


class Poster(Protocol):
    """Abstract memory poster for dependency injection."""

    async def post_event(self, event: SoundEvent) -> None:
        """Persist the event as a Memdio memory."""


class MemdioPoster:
    """Async HTTP client with bounded retries for transient failures."""

    def __init__(
        self,
        api_url: str,
        api_key: str | None,
        timeout_seconds: float,
        retries: int,
    ) -> None:
        if httpx is None:
            raise RuntimeError("httpx is required for bridge mode. Install with: pip install -e .[bridge]")

        headers = {"Content-Type": "application/json"}
        if api_key:
            headers["Authorization"] = f"Bearer {api_key}"

        self._api_url = api_url
        self._retries = retries
        self._client = httpx.AsyncClient(timeout=timeout_seconds, headers=headers)

    async def post_event(self, event: SoundEvent) -> None:
        payload = {
            "content": (
                f"Detected sound: {event.tag} "
                f"(conf {event.confidence:.2f}) at {event.ts.isoformat()}"
            ),
            "tags": event.tag,
        }

        for attempt in range(1, self._retries + 1):
            try:
                response = await self._client.post(self._api_url, json=payload)
            except (httpx.TransportError, httpx.TimeoutException) as exc:
                # Network-level failure — retryable.
                await self._retry_or_raise(event, attempt, exc)
                continue

            if response.is_success:
                LOGGER.info(
                    "stored_event tag=%s confidence=%.2f status=%s",
                    event.tag,
                    event.confidence,
                    response.status_code,
                )
                return

            # Only 5xx and 429 (rate limit) are transient. Every other 4xx
            # (401 bad key, 403, 404, ...) is permanent — fail fast, no retry.
            if response.status_code >= 500 or response.status_code == 429:
                await self._retry_or_raise(
                    event, attempt, RuntimeError(f"transient response {response.status_code}")
                )
                continue

            response.raise_for_status()

    async def _retry_or_raise(self, event: SoundEvent, attempt: int, exc: Exception) -> None:
        """Back off before the next attempt, or raise once retries are exhausted."""
        if attempt >= self._retries:
            raise RuntimeError(
                f"failed posting tag={event.tag} after {self._retries} attempts"
            ) from exc
        delay = min(2 ** (attempt - 1), 8)
        LOGGER.warning(
            "post_retry tag=%s attempt=%s delay=%ss error=%s",
            event.tag,
            attempt,
            delay,
            exc,
        )
        await asyncio.sleep(delay)

    async def aclose(self) -> None:
        await self._client.aclose()


@dataclass(slots=True)
class BridgeService:
    """Coordinate event ingestion, filtering, and posting."""

    source: EventSource
    poster: Poster
    gate: EventGate
    logger: logging.Logger = LOGGER

    async def run(self, stop_event: asyncio.Event) -> None:
        async for event in self.source.events(stop_event):
            if stop_event.is_set():
                break
            await self.handle_event(event)

    async def handle_event(self, event: SoundEvent) -> bool:
        if not self.gate.should_store(event):
            self.logger.info(
                "skipped_event tag=%s confidence=%.2f threshold=%.2f",
                event.tag,
                event.confidence,
                self.gate.threshold,
            )
            return False

        await self.poster.post_event(event)
        return True


def create_event_source(name: str) -> EventSource:
    """Resolve a built-in event source name to an implementation."""

    if name == "mock":
        return MockEventSource()
    if name == "stdin-json":
        return StdInJsonEventSource()
    raise ValueError(f"unsupported source '{name}'")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", default=os.getenv("MEMDIO_EVENT_SOURCE", "mock"))
    parser.add_argument("--threshold", type=float, default=DEFAULT_THRESHOLD)
    parser.add_argument("--api-url", default=DEFAULT_API_URL)
    parser.add_argument("--debounce", type=float, default=DEFAULT_DEBOUNCE_SECONDS)
    parser.add_argument("--timeout", type=float, default=DEFAULT_TIMEOUT_SECONDS)
    parser.add_argument("--retries", type=int, default=DEFAULT_RETRIES)
    return parser


def configure_logging() -> None:
    logging.basicConfig(
        level=os.getenv("MEMDIO_LOG_LEVEL", "INFO").upper(),
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )


def install_signal_handlers(stop_event: asyncio.Event) -> None:
    """Request a graceful shutdown on SIGINT/SIGTERM when supported."""

    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, stop_event.set)
        except NotImplementedError:  # pragma: no cover - platform dependent.
            signal.signal(sig, lambda _signum, _frame: stop_event.set())


async def run_bridge(args: argparse.Namespace) -> None:
    configure_logging()

    source = create_event_source(args.source)
    stop_event = asyncio.Event()
    install_signal_handlers(stop_event)

    poster = MemdioPoster(
        api_url=args.api_url,
        api_key=os.getenv("MEMDIO_API_KEY"),
        timeout_seconds=args.timeout,
        retries=args.retries,
    )
    service = BridgeService(
        source=source,
        poster=poster,
        gate=EventGate(threshold=args.threshold, debounce_seconds=args.debounce),
    )

    try:
        await service.run(stop_event)
    finally:
        await poster.aclose()


def main() -> None:
    args = build_parser().parse_args()
    asyncio.run(run_bridge(args))


if __name__ == "__main__":
    main()
