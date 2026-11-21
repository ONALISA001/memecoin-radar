"""Track record of the Priority and Early lists: every coin that enters a list is recorded with
its price, then labelled with the real price 1h and 4h later. The panel shows the hit rate, so
the list's accuracy is measured instead of assumed.

* record(): each tick, coins newly on a list (not picked on that list in the last 6 h).
* label():  fills ret_1h / ret_4h / max_up_4h / max_dd_4h from stored 5-min price snapshots.
* stats():  last 7 days per list: picks, % up after 4h, average and median 4h return, % that
            reached +10% at some point within 4 h.
"""
from __future__ import annotations

import statistics
import time

from sqlalchemy import select

from ..models import PriceSnapshot, PriorityPick

REPICK_H = 6
HORIZONS = {"ret_1h": 3600, "ret_4h": 4 * 3600}
LABEL_BATCH = 200


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


def label(db, now: int | None = None) -> int:
    now = now or int(time.time())
    picks = db.execute(select(PriorityPick).where(PriorityPick.status == "open",
                                                  PriorityPick.ts <= now - HORIZONS["ret_1h"])
                       .order_by(PriorityPick.ts).limit(LABEL_BATCH)).scalars().all()
    done = 0
    for p in picks:
        end = p.ts + HORIZONS["ret_4h"]
        rows = db.execute(select(PriceSnapshot.ts, PriceSnapshot.price_usd).where(
            PriceSnapshot.token_id == p.token_id, PriceSnapshot.ts > p.ts,
            PriceSnapshot.ts <= min(end, now) + 300).order_by(PriceSnapshot.ts)).all()
        path = [(ts, px) for ts, px in rows if px]

        def at(t):
            before = [px for ts, px in path if ts <= t]
            return before[-1] if before else None
        if p.ret_1h is None and now >= p.ts + HORIZONS["ret_1h"]:
            p.ret_1h = _pct(p.price, at(p.ts + HORIZONS["ret_1h"]))
        if now >= end:
            p.ret_4h = _pct(p.price, at(end))
            inside = [px for ts, px in path if ts <= end]
            if inside:
                p.max_up_4h = _pct(p.price, max(inside))
                p.max_dd_4h = _pct(p.price, min(inside))
            p.status = "done" if p.ret_4h is not None else "no_data"
            done += 1
        elif now >= p.ts + HORIZONS["ret_4h"] + 6 * 3600:      # coin stopped being tracked
            p.status = "no_data"
    db.flush()
    return done


def stats(db, days: int = 7, now: int | None = None) -> dict:
    now = now or int(time.time())
    rows = db.execute(select(PriorityPick).where(PriorityPick.ts >= now - days * 86400)).scalars().all()
    out = {}
    for kind in ("passing", "early"):
        ps = [p for p in rows if p.list_kind == kind]
        r4 = [p.ret_4h for p in ps if p.ret_4h is not None]
        r1 = [p.ret_1h for p in ps if p.ret_1h is not None]
        hit10 = [p for p in ps if p.max_up_4h is not None]
        out[kind] = {
            "picks": len(ps), "judged_4h": len(r4),
            "up_after_4h_pct": round(100 * sum(1 for x in r4 if x > 0) / len(r4), 1) if r4 else None,
            "avg_ret_4h": round(statistics.mean(r4), 2) if r4 else None,
            "median_ret_4h": round(statistics.median(r4), 2) if r4 else None,
            "avg_ret_1h": round(statistics.mean(r1), 2) if r1 else None,
            "reached_10pct_pct": round(100 * sum(1 for p in hit10 if p.max_up_4h >= 10) / len(hit10), 1)
            if hit10 else None,
        }
    out["days"] = days
    out["note"] = "Each coin is counted once per 6 h when it enters a list; returns use real prices."
    return out


def run(session_factory) -> dict:
    """Ingestion hook: record new picks and label old ones."""
    with session_factory() as db:
        added = record(db)
        labelled = label(db)
        db.commit()
    return {"recorded": added, "labelled": labelled}
