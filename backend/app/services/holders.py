"""REAL holder statistics derived from stored Helius checks (TokenRiskCheck).

Nothing here is simulated: counts come from DAS getTokenAccounts, and growth is
computed only from our own earlier real checks (so it needs a few hours of history).
"""
from __future__ import annotations

import datetime as dt

from sqlalchemy import select

from ..models import TokenRiskCheck

MIN_SPAN_H = 5.0      # need at least this much real history before reporting growth
WINDOW_H = 24.0


def _naive(d: dt.datetime) -> dt.datetime:
    return d.replace(tzinfo=None) if d.tzinfo else d


def epoch(d: dt.datetime) -> int:
    """checked_at -> unix seconds (naive values from SQLite are UTC)."""
    return int((d if d.tzinfo else d.replace(tzinfo=dt.timezone.utc)).timestamp())


def real_holder_stats(db, token_ids=None) -> dict[int, dict]:
    """{token_id: {count, capped, checked_at, growth_pct|None, span_h|None}} for tokens
    with at least one successful holder count."""
    q = select(TokenRiskCheck).where(TokenRiskCheck.ok.is_(True),
                                     TokenRiskCheck.holders_count.isnot(None))
    if token_ids is not None:
        q = q.where(TokenRiskCheck.token_id.in_(list(token_ids)))
    rows = db.execute(q.order_by(TokenRiskCheck.token_id, TokenRiskCheck.checked_at)).scalars().all()
    by_tok: dict[int, list] = {}
    for r in rows:
        by_tok.setdefault(r.token_id, []).append(r)
    out: dict[int, dict] = {}
    for tid, lst in by_tok.items():
        last = lst[-1]
        t_last = _naive(last.checked_at)
        window = [r for r in lst if (t_last - _naive(r.checked_at)).total_seconds() <= WINDOW_H * 3600]
        ref = window[0]
        span_h = (t_last - _naive(ref.checked_at)).total_seconds() / 3600
        growth = None
        if (ref is not last and span_h >= MIN_SPAN_H and ref.holders_count > 0
                and not last.holders_capped and not ref.holders_capped):
            growth = round((last.holders_count - ref.holders_count) / ref.holders_count * 100, 2)
        out[tid] = {"count": last.holders_count, "capped": bool(last.holders_capped),
                    "checked_at": last.checked_at, "growth_pct": growth,
                    "span_h": round(span_h, 1) if ref is not last else None}
    return out
