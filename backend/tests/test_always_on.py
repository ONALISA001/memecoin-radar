"""Always-on hosting: shared DexScreener pacing, no API keys in logs, stale /health."""
import asyncio
import logging
import time

import httpx
from fastapi.testclient import TestClient

from app.providers import dex_limiter


def _client(handler):
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


def test_dex_get_retries_once_after_429_and_pauses_everyone(monkeypatch):
    dex_limiter.reset_for_tests()
    calls = []

    def handler(req):
        calls.append(time.monotonic())
        if len(calls) == 1:
            return httpx.Response(429, headers={"retry-after": "1"})
        return httpx.Response(200, json=[])

    async def run():
        async with _client(handler) as c:
            r = await dex_limiter.dex_get(c, "https://api.dexscreener.com/x")
            return r.status_code

    assert asyncio.run(run()) == 200
    assert len(calls) == 2
    assert calls[1] - calls[0] >= 0.9                 # waited out Retry-After
    assert dex_limiter.STATS["rate_limited"] == 1


def test_dex_get_gives_up_after_second_429():
    dex_limiter.reset_for_tests()
    monkey_cool = dex_limiter.COOLDOWN_S
    dex_limiter.COOLDOWN_S = 0.01
    try:
        async def run():
            async with _client(lambda req: httpx.Response(429)) as c:
                return (await dex_limiter.dex_get(c, "https://api.dexscreener.com/x")).status_code
        assert asyncio.run(run()) == 429
        assert dex_limiter.STATS["requests"] == 2
    finally:
        dex_limiter.COOLDOWN_S = monkey_cool
        dex_limiter.reset_for_tests()


def test_dex_get_spaces_requests(monkeypatch):
    dex_limiter.reset_for_tests()
    monkeypatch.setattr(dex_limiter, "MIN_INTERVAL_S", 0.1)
    seen = []

    def handler(req):
        seen.append(time.monotonic())
        return httpx.Response(200, json=[])

    async def run():
        async with _client(handler) as c:
            await asyncio.gather(*(dex_limiter.dex_get(c, "https://api.dexscreener.com/x") for _ in range(3)))
    asyncio.run(run())
    gaps = [b - a for a, b in zip(seen, seen[1:])]
    assert all(g >= 0.09 for g in gaps), gaps
    dex_limiter.reset_for_tests()


def test_httpx_request_urls_are_not_logged():
    import app.main  # noqa: F401  sets the logger levels
    assert not logging.getLogger("httpx").isEnabledFor(logging.INFO)


def test_health_reports_stale_without_scores():
    from app.main import app
    with TestClient(app) as c:
        body = c.get("/health").json()
    assert "stale" in body
    if body.get("last_score_age_s") is None:
        assert body["stale"] is True
