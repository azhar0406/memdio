from datetime import UTC, datetime, timedelta

import httpx
import pytest

from integrations.bridge import BridgeService, EventGate, MemdioPoster, SoundEvent


class FakePoster:
    def __init__(self):
        self.events = []

    async def post_event(self, event: SoundEvent) -> None:
        self.events.append(event)


class RecordingClient:
    """Stand-in for httpx.AsyncClient that replays queued responses."""

    def __init__(self, responses):
        self._responses = list(responses)
        self.calls = 0

    async def post(self, url, json):
        self.calls += 1
        return self._responses[min(self.calls - 1, len(self._responses) - 1)]


def _poster_with(responses, retries=3):
    poster = MemdioPoster(
        api_url="http://x/memories", api_key=None, timeout_seconds=1, retries=retries
    )
    poster._client = RecordingClient(responses)
    return poster


def _resp(status):
    return httpx.Response(status, request=httpx.Request("POST", "http://x/memories"))


@pytest.mark.asyncio
async def test_bridge_skips_events_below_confidence_threshold():
    poster = FakePoster()
    service = BridgeService(
        source=None,
        poster=poster,
        gate=EventGate(threshold=0.7, debounce_seconds=5),
    )

    stored = await service.handle_event(
        SoundEvent(tag="fan", confidence=0.4, ts=datetime.now(UTC))
    )

    assert stored is False
    assert poster.events == []


@pytest.mark.asyncio
async def test_bridge_debounces_duplicate_tags_within_window():
    poster = FakePoster()
    service = BridgeService(
        source=None,
        poster=poster,
        gate=EventGate(threshold=0.7, debounce_seconds=5),
    )
    start = datetime(2026, 7, 5, tzinfo=UTC)

    first = SoundEvent(tag="rooster", confidence=0.9, ts=start)
    second = SoundEvent(tag="rooster", confidence=0.95, ts=start + timedelta(seconds=3))
    third = SoundEvent(tag="rooster", confidence=0.96, ts=start + timedelta(seconds=6))

    assert await service.handle_event(first) is True
    assert await service.handle_event(second) is False
    assert await service.handle_event(third) is True
    assert poster.events == [first, third]


@pytest.mark.asyncio
async def test_poster_does_not_retry_permanent_4xx():
    # A 401 (bad key) is permanent — must fail fast after a single attempt.
    poster = _poster_with([_resp(401)])
    event = SoundEvent(tag="rooster", confidence=0.9, ts=datetime.now(UTC))
    with pytest.raises(httpx.HTTPStatusError):
        await poster.post_event(event)
    assert poster._client.calls == 1


@pytest.mark.asyncio
async def test_poster_retries_transient_5xx_then_succeeds(monkeypatch):
    # 503 is transient — retry (no real backoff sleep) until a 2xx arrives.
    monkeypatch.setattr("integrations.bridge.asyncio.sleep", _noop_sleep)
    poster = _poster_with([_resp(503), _resp(503), _resp(200)])
    event = SoundEvent(tag="thunder", confidence=0.9, ts=datetime.now(UTC))
    await poster.post_event(event)
    assert poster._client.calls == 3


async def _noop_sleep(_seconds):
    return None
