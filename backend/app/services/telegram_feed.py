"""Store Telegram call posts and turn them into REAL per-coin social signals.

Per coin (by contract address), from posts in the watched channels:
  tg_mentions_1h / tg_mentions_prev_1h — posts naming the coin
  tg_channels_24h                        — distinct channels calling it
  tg_views_1h                            — views on those posts
  tg_coordinated                         — ≥ COORD_CHANNELS different channels posting it
                                           within COORD_WINDOW_S (a coordinated push)
Evidence from solana-social-engine: buying on calls lost money on average, so these
count as attention and as a shill-risk signal, never as a buy signal by themselves.
"""
from __future__ import annotations

import time

from sqlalchemy import delete, select

from ..models import TelegramPost

COORD_CHANNELS = 3
COORD_WINDOW_S = 600


def store_posts(db, posts, now: int, retention_days: int = 3) -> dict:
    ids = [p.post_id for p in posts]
    existing = {r.post_id: r for r in db.execute(select(TelegramPost).where(TelegramPost.post_id.in_(ids))).scalars()} \
        if ids else {}
    added = 0
    for p in posts:
        row = existing.get(p.post_id)
        if row is not None:
            row.views = max(row.views or 0, p.views)          # views keep growing
            continue
        db.add(TelegramPost(channel=p.channel[:64], post_id=p.post_id[:96], ts=p.ts or now,
                            views=p.views, text=p.text[:500], mints_json=p.mints))
        existing[p.post_id] = True
        added += 1
    pruned = db.execute(delete(TelegramPost).where(TelegramPost.ts < now - retention_days * 86400)).rowcount
    return {"added": added, "pruned": pruned or 0}


def coin_stats(db, addresses: list[str], now: int) -> dict[str, dict]:
    """{address: stats} for coins with at least one post in the last 24h."""
    rows = db.execute(select(TelegramPost).where(TelegramPost.ts >= now - 86400)).scalars().all()
    want = set(addresses)
    by: dict[str, list] = {}
    for r in rows:
        for m in r.mints_json or []:
            if m in want:
                by.setdefault(m, []).append(r)
    out = {}
    for addr, ps in by.items():
        cur = [p for p in ps if p.ts >= now - 3600]
        prev = [p for p in ps if now - 7200 <= p.ts < now - 3600]
        coord, chans = False, []
        ps_sorted = sorted(ps, key=lambda p: p.ts)
        for i, p in enumerate(ps_sorted):
            window = {q.channel.lower() for q in ps_sorted[i:] if q.ts - p.ts <= COORD_WINDOW_S}
            if len(window) >= COORD_CHANNELS:
                coord, chans = True, sorted(window)
                break
        out[addr] = {"tg_mentions_1h": len(cur), "tg_mentions_prev_1h": len(prev),
                     "tg_channels_24h": len({p.channel.lower() for p in ps}),
                     "tg_views_1h": sum(p.views or 0 for p in cur),
                     "tg_coordinated": coord, "tg_coord_channels": chans}
    return out


def recent_calls(db, now: int, minutes: int = 30) -> list[tuple[str, int]]:
    """[(mint, distinct channels)] called in the last N minutes, most-called first."""
    rows = db.execute(select(TelegramPost).where(TelegramPost.ts >= now - minutes * 60)).scalars().all()
    chans: dict[str, set] = {}
    for r in rows:
        for m in r.mints_json or []:
            chans.setdefault(m, set()).add(r.channel.lower())
    return sorted(((m, len(c)) for m, c in chans.items()), key=lambda x: -x[1])


class Schedule:
    def __init__(self):
        self.last = 0.0

    def due(self, minutes: float) -> bool:
        return time.time() - self.last >= minutes * 60

    def mark(self):
        self.last = time.time()


SCHEDULE = Schedule()
