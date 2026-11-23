"""Track record of the Priority and Early lists: every coin that enters a list is recorded with
its price, then labelled with the real price 1h and 4h later. The panel shows the hit rate, so
the list's accuracy is measured instead of assumed.

Honest by construction (a pick that went badly must not quietly drop out of the numbers):
* A horizon price must be a real snapshot within TOLERANCE of the horizon, never "the last
  price we happened to have" (that froze coins at their price when tracking stopped).
* No snapshot at 4h (the coin dropped off tracking): ask DexScreener for its price now, within
  FALLBACK_WINDOW of the horizon. No pool left, or liquidity under RUG_LIQ_USD => RUGGED and
  counted as -100%. A last tracked price 80%+ below entry also counts as rugged.
* Still nothing after GIVE_UP => no_data, and stats() shows how many picks that is (coverage).
* stats() also reports returns NET of ROUND_TRIP_COST_PCT (fees + slippage + spread, both sides),
  which is what a real buyer would have kept.

* record(): each tick, coins newly on a list (not picked on that list in the last 6 h).
* label():  fills ret_1h / ret_4h / max_up_4h / max_dd_4h; returns picks needing a fallback.
* stats():  last 7 days per list.
"""
from __future__ import annotations

import asyncio
import statistics
import time

import httpx
from sqlalchemy import select

from .. import config
from ..models import PriceSnapshot, PriorityPick, SystemLog, Token
from ..providers.dex_limiter import dex_get

REPICK_H = 6
HORIZONS = {"ret_1h": 3600, "ret_4h": 4 * 3600}
LABEL_BATCH = 200
TOLERANCE = 15 * 60            # a snapshot within 15 min of the horizon counts
FALLBACK_WINDOW = 2 * 3600     # DexScreener "price now" accepted up to 2h after the 4h mark
GIVE_UP = 6 * 3600             # after 4h + this, an unlabelled pick becomes no_data
RUG_LIQ_USD = 1000.0           # a pool this thin can't be sold into: the coin is dead
RUG_DROP_PCT = -80.0           # last tracked price this far below entry => rugged
ROUND_TRIP_COST_PCT = 3.6      # paper execution model: 1.8% per side (fee + slippage + spread)


def record(db, now: int | None = None, prio: dict | None = None) -> int:
    from . import market_views as MV
    now = now or int(time.time())
    prio = prio or MV.priority_now(db, "4h", limit=50)
    recent = {(tid, kind) for tid, kind in db.execute(select(PriorityPick.token_id, PriorityPick.list_kind).where(
        PriorityPick.ts >= now - REPICK_H * 3600)).all()}
    n = 0
    for kind, items in (("passing", prio.get("items") or []), ("early", prio.get("early") or [])):
        for it in items:
            tid = it.get("token_id")
            if tid is None or not it.get("price") or (tid, kind) in recent:
                continue
            db.add(PriorityPick(token_id=tid, list_kind=kind, ts=now, score=it.get("score"),
                                price=float(it["price"]), status="open"))
            recent.add((tid, kind))
            n += 1
    db.flush()
    return n


def _pct(a: float | None, b: float | None) -> float | None:
    return None if not a or not b else round((b / a - 1) * 100, 2)


def _near(path: list[tuple[int, float]], t: int) -> float | None:
    """Price of the snapshot nearest to t, if within TOLERANCE."""
    best, gap = None, TOLERANCE + 1
    for ts, px in path:
        if abs(ts - t) < gap:
            best, gap = px, abs(ts - t)
    return best


def _rug(p: PriorityPick) -> None:
    p.ret_4h, p.status = -100.0, "rugged"
    p.max_dd_4h = -100.0


def label(db, now: int | None = None) -> list[PriorityPick]:
    """Label what the stored snapshots can; return picks that need a DexScreener lookup."""
    now = now or int(time.time())
    picks = db.execute(select(PriorityPick).where(PriorityPick.status == "open",
                                                  PriorityPick.ts <= now - HORIZONS["ret_1h"])
                       .order_by(PriorityPick.ts).limit(LABEL_BATCH)).scalars().all()
    need = []
    for p in picks:
        end = p.ts + HORIZONS["ret_4h"]
        path = [(ts, px) for ts, px in db.execute(select(PriceSnapshot.ts, PriceSnapshot.price_usd).where(
            PriceSnapshot.token_id == p.token_id, PriceSnapshot.ts > p.ts,
            PriceSnapshot.ts <= min(end, now) + TOLERANCE).order_by(PriceSnapshot.ts)).all() if px]
        inside = [px for ts, px in path if ts <= end]
        if inside:
            p.max_up_4h, p.max_dd_4h = _pct(p.price, max(inside)), _pct(p.price, min(inside))
        if p.ret_1h is None and now >= p.ts + HORIZONS["ret_1h"]:
            p.ret_1h = _pct(p.price, _near(path, p.ts + HORIZONS["ret_1h"]))
        if now < end:
            continue
        v = _near(path, end)
        if v is not None:
            p.ret_4h, p.status = _pct(p.price, v), "done"
        elif inside and (_pct(p.price, inside[-1]) or 0) <= RUG_DROP_PCT:
            _rug(p)                                   # collapsed, then fell off tracking
        elif now <= end + FALLBACK_WINDOW:
            need.append(p)
        elif now >= end + GIVE_UP:
            p.status = "no_data"
    db.flush()
    return need


async def _dex_now(addresses: list[str], transport=None) -> dict[str, dict] | None:
    """{address: {price, liquidity}} for the deepest pool. A queried address that is missing
    has no pool. None = the lookup failed (don't conclude anything)."""
    out: dict[str, dict] = {}
    async with httpx.AsyncClient(timeout=10.0, transport=transport) as c:
        for i in range(0, len(addresses), 30):
            try:
                r = await dex_get(c, f"{config.DEXSCREENER_BASE}/tokens/v1/solana/{','.join(addresses[i:i + 30])}")
                r.raise_for_status()
                body = r.json()
            except Exception:  # noqa: BLE001
                return None
            for pr in body if isinstance(body, list) else (body or {}).get("pairs") or []:
                addr = (pr.get("baseToken") or {}).get("address")
                liq = float((pr.get("liquidity") or {}).get("usd") or 0)
                if addr and (addr not in out or liq > out[addr]["liquidity"]):
                    out[addr] = {"price": float(pr.get("priceUsd") or 0), "liquidity": liq}
    return out


def apply_fallback(db, need: list[PriorityPick], live: dict[str, dict]) -> dict:
    addr = {t.id: t.address for t in db.execute(select(Token).where(
        Token.id.in_({p.token_id for p in need}))).scalars().all()}
    res = {"fallback": 0, "rugged": 0}
    for p in need:
        m = live.get(addr.get(p.token_id, ""))
        if not m or m["liquidity"] < RUG_LIQ_USD or m["price"] <= 0:
            _rug(p)
            res["rugged"] += 1
        else:
            p.ret_4h, p.status = _pct(p.price, m["price"]), "done"
            res["fallback"] += 1
    db.flush()
    return res


def _stats(xs: list[float]) -> tuple:
    if not xs:
        return None, None, None
    return (round(100 * sum(1 for x in xs if x > 0) / len(xs), 1), round(statistics.mean(xs), 2),
            round(statistics.median(xs), 2))


def stats(db, days: int = 7, now: int | None = None) -> dict:
    now = now or int(time.time())
    rows = db.execute(select(PriorityPick).where(PriorityPick.ts >= now - days * 86400)).scalars().all()
    out = {}
    for kind in ("passing", "early"):
        ps = [p for p in rows if p.list_kind == kind]
        due = [p for p in ps if now >= p.ts + HORIZONS["ret_4h"]]
        r4 = [p.ret_4h for p in ps if p.ret_4h is not None]          # includes rugs at -100
        net = [x - ROUND_TRIP_COST_PCT for x in r4]
        r1 = [p.ret_1h for p in ps if p.ret_1h is not None]
        hit10 = [p for p in ps if p.max_up_4h is not None]
        up, avg, med = _stats(r4)
        up_n, avg_n, med_n = _stats(net)
        out[kind] = {
            "picks": len(ps), "judged_4h": len(r4),
            "rugged": sum(1 for p in ps if p.status == "rugged"),
            "no_data": sum(1 for p in ps if p.status == "no_data"),
            # share of picks old enough to judge that actually got a result
            "coverage_pct": round(100 * len(r4) / len(due), 1) if due else None,
            "up_after_4h_pct": up, "avg_ret_4h": avg, "median_ret_4h": med,
            "net_up_after_4h_pct": up_n, "net_avg_ret_4h": avg_n, "net_median_ret_4h": med_n,
            "avg_ret_1h": round(statistics.mean(r1), 2) if r1 else None,
            "reached_10pct_pct": round(100 * sum(1 for p in hit10 if p.max_up_4h >= 10) / len(hit10), 1)
            if hit10 else None,
        }
    out["days"] = days
    out["cost_pct"] = ROUND_TRIP_COST_PCT
    out["note"] = (f"Each coin is counted once per 6 h when it enters a list; returns use real prices. "
                   f"Coins that lost their pool count as -100%. 'Net' subtracts a {ROUND_TRIP_COST_PCT}% "
                   f"round trip (fees, slippage, spread).")
    return out


def _phase1(session_factory) -> tuple[dict, dict[int, str]]:
    with session_factory() as db:
        relabelled = _relabel_once(db)
        added = record(db)
        need = label(db)
        addr = {t.id: t.address for t in db.execute(select(Token).where(
            Token.id.in_({p.token_id for p in need}))).scalars().all()} if need else {}
        want = {p.id: addr.get(p.token_id, "") for p in need}
        db.commit()
    return {"recorded": added, "fallback": 0, "rugged": 0, "relabelled": relabelled}, want


def _phase2(session_factory, want: dict[int, str], live: dict[str, dict]) -> dict:
    with session_factory() as db:
        need = db.execute(select(PriorityPick).where(PriorityPick.id.in_(list(want)),
                                                     PriorityPick.status == "open")).scalars().all()
        res = apply_fallback(db, need, live)
        db.commit()
    return res


async def run(session_factory, transport=None) -> dict:
    """Ingestion hook: record new picks, label old ones, look up the ones that fell off tracking.
    Database work runs in a worker thread so it never blocks the event loop (health checks)."""
    res, want = await asyncio.to_thread(_phase1, session_factory)
    if want:
        live = await _dex_now(sorted({a for a in want.values() if a}), transport=transport)
        if live is not None:
            res.update(await asyncio.to_thread(_phase2, session_factory, want, live))
    return res


RELABEL_MARK = "honest-relabel-v1"


def _relabel_once(db) -> int:
    """Run rebuild_history() once per database (marker row in system_logs)."""
    if db.execute(select(SystemLog.id).where(SystemLog.source == "priority_track",
                                             SystemLog.message == RELABEL_MARK)).first():
        return 0
    n = rebuild_history(db)
    db.add(SystemLog(ts=int(time.time()), level="info", source="priority_track", message=RELABEL_MARK))
    db.flush()
    return n


def rebuild_history(db, now: int | None = None) -> int:
    """Re-label past picks with the honest rules (a one-off after the upgrade): reopen every
    labelled pick whose 4h mark is still inside the snapshot history and label it again.
    Picks too old for the DexScreener fallback that no longer have a 4h snapshot go to
    no_data (or rugged, if they collapsed first) instead of keeping a stale price."""
    now = now or int(time.time())
    rows = db.execute(select(PriorityPick).where(PriorityPick.status != "open")).scalars().all()
    for p in rows:
        p.status, p.ret_1h, p.ret_4h, p.max_up_4h, p.max_dd_4h = "open", None, None, None, None
    db.flush()
    n = 0
    while True:
        before = n
        label(db, now)
        n = sum(1 for p in rows if p.status != "open")
        if n == before:
            break
    return n
