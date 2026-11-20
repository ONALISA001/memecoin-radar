"""Schedules creator track records (providers/creator_history.py + engine/dev_score.py).

Who gets scored (credit guard — a free-plan Helius scan is ~1 call per transaction read):
  * creators found by launch forensics (status ok) of tokens that are actively tracked now
  * launch-alerted coins first, then by Trend Score
  * CREATOR_PER_TICK per tick and CREATOR_DAILY_MAX per 24 h
  * one profile per creator wallet, re-scored after CREATOR_TTL_H; errors retry after 1 h
"""
from __future__ import annotations

import datetime as dt
import logging
import time

from sqlalchemy import func, select

from .. import config
from ..engine import dev_score
from ..models import CreatorProfile, Token, TokenLaunchForensics, TrendScore
from ..providers.creator_history import CreatorHistoryProvider

log = logging.getLogger("radar.creator")
ACTIVE_WINDOW_S = 900
RETRY_AFTER_H = 1.0


def _naive(d):
    return d.replace(tzinfo=None) if d is not None and d.tzinfo else d


def scored_24h(db, now: dt.datetime) -> int:
    since = now - dt.timedelta(hours=24)
    return db.execute(select(func.count()).select_from(CreatorProfile)
                      .where(CreatorProfile.scored_at >= since)).scalar() or 0


def candidates(db, now: dt.datetime | None = None) -> list[tuple[str, set[str]]]:
    """[(creator, mints of his we track — excluded from his 'earlier launches')], best first."""
    now = now or dt.datetime.utcnow()
    left = config.CREATOR_DAILY_MAX - scored_24h(db, now)
    if left <= 0:
        return []
    cutoff = int(time.time()) - ACTIVE_WINDOW_S
    trend = dict(db.execute(select(TrendScore.token_id, func.max(TrendScore.trend))
                            .where(TrendScore.kind == "live", TrendScore.ts >= cutoff)
                            .group_by(TrendScore.token_id)).all())
    if not trend:
        return []
    rows = db.execute(select(TokenLaunchForensics, Token)
                      .join(Token, Token.id == TokenLaunchForensics.token_id)
                      .where(TokenLaunchForensics.status == "ok",
                             TokenLaunchForensics.creator.is_not(None))).all()
    mints: dict[str, set[str]] = {}
    prio: dict[str, tuple] = {}
    for lf, tok in rows:
        mints.setdefault(lf.creator, set()).add(tok.address)
        if tok.id not in trend:
            continue
        p = (0 if tok.launch_alert == "sent" else 1, -(trend[tok.id] or 0))
        if lf.creator not in prio or p < prio[lf.creator]:
            prio[lf.creator] = p
    if not prio:
        return []
    have = {p.creator: p for p in db.execute(select(CreatorProfile).where(
        CreatorProfile.creator.in_(list(prio)))).scalars().all()}
    due = []
    for c, p in prio.items():
        prev = have.get(c)
        if prev is not None and prev.scored_at is not None:
            age_h = (now - _naive(prev.scored_at)).total_seconds() / 3600
            if age_h < (RETRY_AFTER_H if prev.status == "error" else config.CREATOR_TTL_H):
                continue
        due.append((p, c))
    due.sort()
    return [(c, mints[c]) for _, c in due[:min(config.CREATOR_PER_TICK, left)]]


def store(db, hist, scored: dict | None) -> CreatorProfile:
    row = db.execute(select(CreatorProfile).where(
        CreatorProfile.creator == hist.creator)).scalar_one_or_none()
    if row is None:
        row = CreatorProfile(creator=hist.creator)
        db.add(row)
    row.scored_at = dt.datetime.utcnow()
    row.method = hist.method or None
    row.txs_scanned = hist.txs_scanned
    if hist.status != "ok" or scored is None:
        row.error = (hist.error or "unknown error")[:200]
        if row.band is None:          # never scored: retry in an hour
            row.status = "error"
        return row                    # otherwise the last good score stays visible until the TTL
    row.status, row.error = scored["status"], None
    row.band, row.total, row.conduct, row.power = (scored["band"], scored.get("total"),
                                                   scored.get("conduct"), scored.get("power"))
    row.dump_tier, row.launches = scored.get("dump_tier"), scored.get("launches")
    row.score_json = {**scored, "txs_scanned": hist.txs_scanned, "method": hist.method,
                      "calls": hist.calls}
    return row


async def run_creator_profiles(session_factory, provider: CreatorHistoryProvider | None = None) -> dict:
    if not config.CREATOR_SCORE_ENABLED:
        return {"status": "disabled"}
    with session_factory() as db:
        todo = candidates(db)
    if not todo:
        return {"scored": 0}
    prov = provider or CreatorHistoryProvider()
    res = {"scored": 0, "ok": 0, "error": 0}
    for creator, exclude in todo:
        hist = await prov.history(creator, exclude=exclude)
        scored = None
        if hist.status == "ok":
            scored = dev_score.score(hist.records, career_start_ts=hist.career_start_ts,
                                     history_complete=hist.history_complete)
        with session_factory() as db:
            store(db, hist, scored)
            db.commit()
        res["scored"] += 1
        res["ok" if scored else "error"] += 1
    res["calls"] = dict(prov.calls)
    return res


def profile_facts(row: CreatorProfile | None) -> dict | None:
    """Compact dict for verdict evidence and the API. None = not scored yet."""
    if row is None or row.status not in ("ok", "new"):
        return None
    s = row.score_json or {}
    return {"creator": row.creator, "status": row.status, "band": row.band, "label": s.get("label"),
            "total": row.total, "conduct": row.conduct, "power": row.power, "dump_tier": row.dump_tier,
            "launches": row.launches, "traded": s.get("traded"), "dumps": s.get("dumps"),
            "dump_rate": s.get("dump_rate"), "fastest_first_sell_s": s.get("fastest_first_sell_s"),
            "graduated": s.get("graduated"), "alive": s.get("alive"),
            "peak_mcap_usd": s.get("peak_mcap_usd"), "summary": s.get("summary"),
            "scored_at": row.scored_at.isoformat() if row.scored_at else None}


def profiles_for(db, creators: list[str]) -> dict[str, CreatorProfile]:
    creators = [c for c in set(creators) if c]
    if not creators:
        return {}
    return {p.creator: p for p in db.execute(select(CreatorProfile).where(
        CreatorProfile.creator.in_(creators))).scalars().all()}
