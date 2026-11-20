"""Schedules creator (dev) track records (providers/creator_history.py) and serves them.

Who gets a profile (credit guard):
  * creators found by a successful launch-forensics run on a real Solana token
  * never profiled, or the profile is older than CREATOR_REFRESH_HOURS (coins that
    were "young" last time may have died or graduated since); errors retry after 1h
  * creators of launch-alert candidates and the newest launches first,
    CREATOR_PROFILES_PER_TICK per tick
One profile per creator wallet; each token's counts exclude the token itself.
"""
from __future__ import annotations

import datetime as dt
import time

from sqlalchemy import select

from .. import config
from ..models import CreatorProfile, Token, TokenLaunchForensics
from ..providers.creator_history import CreatorHistoryProvider, creator_flags, summarise

RETRY_AFTER_H = 1.0


def _age_h(row: CreatorProfile, now: dt.datetime) -> float | None:
    seen = row.checked_at.replace(tzinfo=None) if row.checked_at else None
    return None if seen is None else (now - seen).total_seconds() / 3600


def candidates(db, now: dt.datetime | None = None) -> list[str]:
    now = now or dt.datetime.utcnow()
    max_age_ms = config.LAUNCH_FORENSICS_MAX_AGE_H * 3.6e6
    have = {p.creator: p for p in db.execute(select(CreatorProfile)).scalars().all()}
    rows = db.execute(select(TokenLaunchForensics, Token)
                      .join(Token, Token.id == TokenLaunchForensics.token_id)
                      .where(TokenLaunchForensics.status == "ok",
                             TokenLaunchForensics.creator.is_not(None))).all()
    best: dict[str, tuple] = {}
    for lf, tok in rows:
        if (tok.address or "").startswith("Mock"):
            continue
        if tok.token_created_at and time.time() * 1000 - tok.token_created_at > max_age_ms:
            continue
        prev = have.get(lf.creator)
        if prev is not None:
            age = _age_h(prev, now)
            limit = config.CREATOR_REFRESH_HOURS if prev.status == "ok" else RETRY_AFTER_H
            if age is not None and age < limit:
                continue
        key = (0 if tok.launch_alert is None and tok.discovery_source == "new_pool" else 1,
               0 if prev is None else 1, -(tok.token_created_at or 0))
        if lf.creator not in best or key < best[lf.creator]:
            best[lf.creator] = key
    return [c for c, _ in sorted(best.items(), key=lambda kv: kv[1])][:config.CREATOR_PROFILES_PER_TICK]


def store(db, creator: str, h) -> CreatorProfile:
    row = db.execute(select(CreatorProfile).where(CreatorProfile.creator == creator)).scalar_one_or_none()
    if row is None:
        row = CreatorProfile(creator=creator)
        db.add(row)
    if h.status != "ok" and row.status == "ok":
        # keep the last good history; only the timestamp moves so it retries later
        row.error = (h.error or h.status)[:200]
        row.checked_at = dt.datetime.utcnow() - dt.timedelta(hours=config.CREATOR_REFRESH_HOURS - RETRY_AFTER_H)
        return row
    row.status, row.error, row.method = h.status, (h.error or None), h.method or None
    row.txs_scanned, row.history_truncated = h.txs_scanned, h.history_truncated
    row.launches_json = h.launches[:100]
    row.checked_at = dt.datetime.utcnow()
    return row


async def run_creator_profiles(session_factory, provider: CreatorHistoryProvider | None = None) -> dict:
    if not config.CREATOR_PROFILES_ENABLED:
        return {"status": "disabled"}
    with session_factory() as db:
        todo = candidates(db)
    if not todo:
        return {"profiled": 0}
    prov = provider or CreatorHistoryProvider()
    res = {"profiled": 0, "ok": 0, "other": 0}
    for creator in todo:
        h = await prov.analyse(creator)
        with session_factory() as db:
            store(db, creator, h)
            db.commit()
        res["profiled"] += 1
        res["ok" if h.status == "ok" else "other"] += 1
    res["rpc_calls"] = prov.calls
    return res


def creator_facts(row: CreatorProfile | None, mint: str, now: float | None = None) -> dict | None:
    """Compact dict for the risk engine, verdicts and alerts. None = not profiled yet."""
    if row is None:
        return None
    if row.status != "ok":
        return {"status": row.status, "creator": row.creator}
    s = summarise(row.launches_json or [], exclude_mint=mint, now=now,
                  truncated=bool(row.history_truncated))
    return {"status": "ok", "creator": row.creator, **s, "flags": creator_flags(s),
            "checked_at": row.checked_at.isoformat() if row.checked_at else None}


def profiles_for(db, creators: list[str]) -> dict[str, CreatorProfile]:
    creators = [c for c in set(creators) if c]
    if not creators:
        return {}
    rows = db.execute(select(CreatorProfile).where(CreatorProfile.creator.in_(creators))).scalars().all()
    return {r.creator: r for r in rows}
