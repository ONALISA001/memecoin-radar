"""SOL market regime from Hyperliquid's public API (no key): price vs its 24h average,
24h change and perp funding. Memecoins trade against SOL and tend to fall with it, so
paper strategies can stand aside while SOL is in a downtrend (paper/config.py
sol_regime_filter).

refresh() runs from the async ingestion tick at most every REFRESH_S; current() is read
by the paper engine. A failed fetch keeps the last good reading; readings older than
STALE_S count as unknown, and the paper rules then let trades through (noted on the
trade) instead of halting the account because a third-party API is down.
"""
from __future__ import annotations

import logging
import time

import httpx

log = logging.getLogger("radar.sol")
HL = "https://api.hyperliquid.xyz/info"
REFRESH_S = 600
STALE_S = 3600
DOWN_CHG_24H = -3.0          # % — below its 24h average AND down at least this much = DOWN

STATE: dict = {"ts": 0, "regime": None}
_last_try = {"ts": 0.0}


def classify(price: float, avg_24h: float, chg_24h: float) -> str:
    if price >= avg_24h and chg_24h >= 0:
        return "UP"
    if price < avg_24h and chg_24h <= DOWN_CHG_24H:
        return "DOWN"
    return "FLAT"


def from_candles(closes: list[float], funding_1h: float | None, now: int) -> dict | None:
    """Regime from hourly closes (oldest first, ≥25 of them)."""
    if len(closes) < 25:
        return None
    last24 = closes[-24:]
    price, avg = closes[-1], sum(last24) / 24
    chg = 100.0 * (price / closes[-25] - 1.0) if closes[-25] else 0.0
    return {"ts": now, "price": round(price, 4), "avg_24h": round(avg, 4), "chg_24h": round(chg, 2),
            "funding_1h_pct": None if funding_1h is None else round(funding_1h * 100, 5),
            "regime": classify(price, avg, chg)}


async def refresh(force: bool = False, transport: httpx.AsyncBaseTransport | None = None) -> dict | None:
    now = time.time()
    if not force and now - _last_try["ts"] < REFRESH_S:
        return current()
    _last_try["ts"] = now
    try:
        async with httpx.AsyncClient(timeout=10.0, transport=transport) as client:
            r = await client.post(HL, json={"type": "candleSnapshot", "req": {
                "coin": "SOL", "interval": "1h", "startTime": int((now - 30 * 3600) * 1000),
                "endTime": int(now * 1000)}})
            r.raise_for_status()
            closes = [float(c["c"]) for c in r.json()]
            funding = None
            m = await client.post(HL, json={"type": "metaAndAssetCtxs"})
            if m.status_code == 200:
                meta, ctxs = m.json()
                for u, c in zip(meta.get("universe", []), ctxs):
                    if u.get("name") == "SOL":
                        funding = float(c.get("funding")) if c.get("funding") is not None else None
                        break
        reading = from_candles(closes, funding, int(now))
        if reading:
            STATE.clear()
            STATE.update(reading)
    except Exception as exc:  # noqa: BLE001 — a third-party outage never breaks the tick
        log.warning("SOL regime refresh failed (keeping last reading): %s", exc)
    return current()


def current(now: float | None = None) -> dict | None:
    """The latest reading, or None if there is none or it is stale."""
    if not STATE.get("regime") or (now or time.time()) - STATE.get("ts", 0) > STALE_S:
        return None
    return dict(STATE)
