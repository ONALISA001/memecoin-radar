"""One shared pace for every DexScreener request in this process.

Four loops call DexScreener (ingestion, launch-radar enrich, outcomes, creator history). Each
stayed under the published limit on its own, but together, all firing at once from one IP,
they drew "429 Too Many Requests" and many ticks scored only a handful of coins.

`dex_get()` spaces requests at least DEX_MIN_INTERVAL_S apart (default 0.4 s = 150/min, half
the published 300/min) and, on a 429, pauses ALL DexScreener calls for the Retry-After time
(or DEX_COOLDOWN_S) and retries that one request once.
"""
from __future__ import annotations

import asyncio
import logging
import os
import time

import httpx

log = logging.getLogger("dexscreener")

MIN_INTERVAL_S = float(os.getenv("DEX_MIN_INTERVAL_S", "0.4"))
COOLDOWN_S = float(os.getenv("DEX_COOLDOWN_S", "10"))

_lock: asyncio.Lock | None = None
_lock_loop = None
_next_ok = 0.0          # monotonic time before which nobody may send
STATS = {"requests": 0, "rate_limited": 0}


def _get_lock() -> asyncio.Lock:
    # A Lock is bound to the loop it was first used on; tests run several loops.
    global _lock, _lock_loop
    loop = asyncio.get_running_loop()
    if _lock is None or _lock_loop is not loop:
        _lock, _lock_loop = asyncio.Lock(), loop
    return _lock


async def _wait_turn() -> None:
    global _next_ok
    async with _get_lock():
        delay = _next_ok - time.monotonic()
        if delay > 0:
            await asyncio.sleep(delay)
        _next_ok = time.monotonic() + MIN_INTERVAL_S


def _cooldown(resp: httpx.Response) -> float:
    try:
        return min(60.0, max(1.0, float(resp.headers.get("retry-after", ""))))
    except ValueError:
        return COOLDOWN_S


async def dex_get(client: httpx.AsyncClient, url: str, **kw) -> httpx.Response:
    """GET a DexScreener URL at the shared pace; one retry after a 429."""
    global _next_ok
    for attempt in (1, 2):
        await _wait_turn()
        STATS["requests"] += 1
        r = await client.get(url, **kw)
        if r.status_code != 429:
            return r
        STATS["rate_limited"] += 1
        pause = _cooldown(r)
        _next_ok = max(_next_ok, time.monotonic() + pause)
        if attempt == 1:
            log.warning("DexScreener rate limit: pausing all DexScreener calls %.0fs", pause)
    return r


def reset_for_tests() -> None:
    global _next_ok
    _next_ok = 0.0
    STATS.update(requests=0, rate_limited=0)
