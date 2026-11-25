"""Market-structure facts for the third batch of paper rules, each computed once per tick:

- narrative_flow: which narratives money is rotating into, ranked from REAL market data only
  (median 1h volume growth and price change of each narrative's tracked coins). Narrative
  scores also mix in social-mention growth, which can be simulated, so they're not used here.
- telegram_calls: which coins public Telegram call channels posted recently.
- graduations: pump.fun coins that just migrated from the bonding curve to a DEX
  (services/launch_radar.py keeps them in memory from the live launch stream).
"""
from __future__ import annotations

import statistics

from sqlalchemy import select

from ..models import Narrative, TelegramPost, Token, TokenNarrative

_CACHE: dict[tuple, object] = {}


def _cached(key: tuple, build):
    if key not in _CACHE:
        if len(_CACHE) > 32:
            _CACHE.clear()
        _CACHE[key] = build()
    return _CACHE[key]


# ------------------------------------------------------------------ narratives
def narrative_flow(db, market: dict, now: int, min_members: int = 3) -> list[dict]:
    """Narratives with ≥min_members tracked coins, best money flow first:
    [{name, members, vol_g_1h, chg_1h, token_ids}]. Ranked by median 1h volume growth;
    only narratives whose median 1h price change is positive count as 'rotating in'."""
    def build():
        rows = db.execute(select(TokenNarrative.token_id, Narrative.name)
                          .join(Narrative, Narrative.id == TokenNarrative.narrative_id)).all()
        by: dict[str, list[int]] = {}
        for tid, name in rows:
            if tid in market:
                by.setdefault(name, []).append(tid)
        out = []
        for name, ids in by.items():
            vg = [market[t].get("volume_g_1h") for t in ids if market[t].get("volume_g_1h") is not None]
            pc = [market[t].get("price_chg_1h") for t in ids if market[t].get("price_chg_1h") is not None]
            if len(ids) < min_members or not vg or not pc:
                continue
            out.append({"name": name, "members": len(ids), "vol_g_1h": round(statistics.median(vg), 1),
                        "chg_1h": round(statistics.median(pc), 1), "token_ids": sorted(ids)})
        out.sort(key=lambda n: (n["chg_1h"] > 0, n["vol_g_1h"]), reverse=True)
        return out
    return _cached(("narr", now, min_members, id(market)), build)


def hot_narratives(db, market: dict, now: int, top_k: int, min_members: int) -> list[dict]:
    """The top_k narratives money is rotating into (rising volume AND rising price)."""
    return [n for n in narrative_flow(db, market, now, min_members) if n["chg_1h"] > 0 and n["vol_g_1h"] > 0][:top_k]


def narrative_check(db, token_id: int, market: dict, now: int, top_k: int, min_members: int):
    hot = hot_narratives(db, market, now, top_k, min_members)
    mine = [n for n in hot if token_id in n["token_ids"]]
    if mine:
        n = mine[0]
        return ("hot narrative", True, f"{n['name']}: rank {hot.index(n) + 1} of {len(hot)}, "
                                       f"volume {n['vol_g_1h']:+.0f}% / price {n['chg_1h']:+.1f}% 1h (median of {n['members']})")
    names = ", ".join(n["name"] for n in hot) or "none rotating in"
    return ("hot narrative", False, f"not in a top-{top_k} narrative ({names})")


# ------------------------------------------------------------------ telegram
def telegram_calls(db, now: int, window_min: float) -> dict[str, dict]:
    """{mint: {channels, posts, first_ts}} for coins posted in the last window_min minutes."""
    def build():
        rows = db.execute(select(TelegramPost).where(TelegramPost.ts >= now - int(window_min * 60))).scalars().all()
        out: dict[str, dict] = {}
        for r in rows:
            for m in r.mints_json or []:
                d = out.setdefault(m, {"channels": set(), "posts": 0, "first_ts": r.ts})
                d["channels"].add((r.channel or "").lower())
                d["posts"] += 1
                d["first_ts"] = min(d["first_ts"], r.ts)
        return {m: {"channels": len(d["channels"]), "channel_names": sorted(d["channels"]),
                    "posts": d["posts"], "first_ts": d["first_ts"]} for m, d in out.items()}
    return _cached(("tg", now, float(window_min)), build)


def telegram_check(mode: int, call: dict | None, min_channels: int, window_min: float, now: int):
    """mode 1 = skip coins called recently (fade the call) · 2 = only coins called by ≥min_channels."""
    if mode == 1:
        if call:
            return ("no fresh Telegram call", False,
                    f"called by {call['channels']} channel(s) {(now - call['first_ts']) / 60:.0f} min ago")
        return ("no fresh Telegram call", True, f"no call in the last {window_min:g} min")
    ok = bool(call) and call["channels"] >= min_channels
    return ("Telegram call", ok, f"{call['channels'] if call else 0} channel(s) in {window_min:g} min "
                                 f"vs min {min_channels}")


# ------------------------------------------------------------------ graduations
def graduations(db, now: int, window_min: float) -> list[tuple[int, float]]:
    """[(token_id, minutes since graduation)] for tracked coins that left the bonding curve
    within the window. Empty when the launch stream is off."""
    from ..services import launch_radar as LR
    recent = {l.mint: (now - l.graduated_ts) / 60.0 for l in list(LR.BOOK.values())
              if l.graduated and l.graduated_ts and 0 <= now - l.graduated_ts <= window_min * 60}
    if not recent:
        return []
    rows = db.execute(select(Token.id, Token.address).where(Token.address.in_(list(recent)))).all()
    return sorted(((tid, round(recent[addr], 1)) for tid, addr in rows), key=lambda x: x[1])
