"""Learn from winners: a buy gate built from the closed trades of EVERY paper account
(main, every Strategy Lab preset, supervisor challengers).

Each closed trade is described by what was true when it was opened (signal kind, AI
verdict, regime, momentum, 1h move, pool size, buy share, sell pressure, confidence),
bucketed so similar setups compare. For a new candidate the gate finds the most similar
past trades and buys only if most of them won AND their average return beat costs:
"12 of 16 similar past trades won, average +7.4%".

No look-ahead: only trades closed before `now` count. Rules are OFF by default
(learn_from_winners = False); the "winners" Strategy Lab preset switches them on.
Research only: a pattern that won on a few dozen paper trades can stop working.
"""
from __future__ import annotations

from sqlalchemy import select

from ..models import DemoAccount, PaperPosition

FEATURES = ("kind", "verdict", "regime", "momentum", "chg_1h", "liquidity", "buy_share", "sell_pressure",
            "confidence", "sol", "telegram")
LABELS = {"kind": "signal", "verdict": "AI verdict", "regime": "regime", "momentum": "momentum",
          "chg_1h": "1h move", "liquidity": "liquidity", "buy_share": "buy share",
          "sell_pressure": "sell pressure", "confidence": "confidence", "sol": "SOL",
          "telegram": "Telegram"}


def _bucket(v, edges: tuple, names: tuple):
    if v is None:
        return None
    try:
        v = float(v)
    except (TypeError, ValueError):
        return None
    for e, n in zip(edges, names):
        if v < e:
            return n
    return names[-1]


def features(kind, verdict_rating, regime, momentum, chg_1h, liq, buy_share, sellp, confidence,
             sol_regime=None, tg_channels=None) -> dict:
    """Bucketed description of a setup. Missing facts are None (never matched)."""
    return {
        "kind": kind or None,
        "verdict": (verdict_rating or "none"),
        "regime": regime or None,
        "momentum": _bucket(momentum, (50, 65, 80), ("<50", "50-65", "65-80", "80+")),
        "chg_1h": _bucket(chg_1h, (0, 10, 30, 60), ("down", "0-10%", "10-30%", "30-60%", "60%+")),
        "liquidity": _bucket(liq, (25e3, 75e3, 250e3), ("<$25K", "$25-75K", "$75-250K", "$250K+")),
        "buy_share": _bucket(buy_share, (50, 55, 60), ("<50%", "50-55%", "55-60%", "60%+")),
        "sell_pressure": _bucket(sellp, (30, 45, 60), ("<30", "30-45", "45-60", "60+")),
        "confidence": _bucket(confidence, (55, 65, 75), ("<55", "55-65", "65-75", "75+")),
        "sol": sol_regime or None,
        "telegram": None if tg_channels is None else ("called" if tg_channels > 0 else "not called"),
    }


def trade_features(p: PaperPosition) -> dict:
    m = p.metrics_json or {}
    em = m.get("entry_market") or {}
    v = m.get("verdict") or {}
    return features(m.get("signal_kind"), v.get("rating"), p.market_regime,
                    m.get("momentum"), em.get("price_chg_1h"), em.get("liq") or m.get("liquidity"),
                    em.get("buy_share_pct"), em.get("sellp") if em.get("sellp") is not None else m.get("sell_pressure"),
                    p.ai_confidence, (m.get("sol") or {}).get("regime"),
                    (m.get("telegram") or {}).get("channels"))


_CACHE: dict[tuple, list] = {}


def history(db, user_id: int, now: int, lookback_days: float) -> list[tuple[dict, float]]:
    """(features, pnl_pct) of every trade closed before `now` across the user's accounts.
    Cached per tick."""
    key = (user_id, now, float(lookback_days))
    if key not in _CACHE:
        _CACHE.clear()
        since = now - int(float(lookback_days) * 86400) if lookback_days else 0
        ids = [a for (a,) in db.execute(select(DemoAccount.id).where(DemoAccount.user_id == user_id)).all()]
        rows = db.execute(select(PaperPosition).where(
            PaperPosition.account_id.in_(ids), PaperPosition.status == "closed",
            PaperPosition.exit_ts < now, PaperPosition.exit_ts >= since,
            PaperPosition.pnl_pct.isnot(None))).scalars().all() if ids else []
        _CACHE[key] = [(trade_features(p), float(p.pnl_pct)) for p in rows]
    return _CACHE[key]


def _similarity(a: dict, b: dict) -> tuple[int, int]:
    """(features matched, features known on both sides)."""
    known = [f for f in FEATURES if a.get(f) is not None and b.get(f) is not None]
    return sum(1 for f in known if a[f] == b[f]), len(known)


def judge(settings: dict, cand: dict, hist: list[tuple[dict, float]]) -> dict:
    """Does history say this setup wins? Returns ok, the evidence, and a size multiplier."""
    min_hist = int(settings.get("winners_min_history") or 30)
    if len(hist) < min_hist:
        return {"ok": False, "reason": f"only {len(hist)} closed trades in history (need {min_hist})",
                "similar": 0, "size_mult": None}
    min_frac = float(settings.get("winners_min_match") or 0.6)
    scored = []
    for f, ret in hist:
        hit, known = _similarity(cand, f)
        if known >= 4 and hit / known >= min_frac:
            scored.append((hit / known, hit, ret))
    scored.sort(key=lambda x: (x[0], x[1]), reverse=True)
    near = scored[:int(settings.get("winners_neighbors") or 20)]
    need = int(settings.get("winners_min_similar") or 8)
    if len(near) < need:
        return {"ok": False, "reason": f"only {len(near)} similar past trades (need {need})",
                "similar": len(near), "size_mult": None}
    rets = [r for _, _, r in near]
    wins = sum(1 for r in rets if r > 0)
    wr = 100.0 * wins / len(rets)
    avg = sum(rets) / len(rets)
    min_wr = float(settings.get("winners_min_win_rate") or 55)
    min_avg = float(settings.get("winners_min_avg_pct") or 0)
    ok = wr >= min_wr and avg >= min_avg
    mult = 1.0 if wr >= 70 else 0.75 if wr >= 60 else 0.5
    return {"ok": ok, "similar": len(near), "wins": wins, "win_rate": round(wr, 1), "avg_pct": round(avg, 2),
            "size_mult": mult if ok else None,
            "reason": (f"{wins} of {len(near)} similar past trades won, avg {avg:+.1f}% "
                       f"(need ≥{min_wr:g}% winners and ≥{min_avg:+g}% avg)")}


def patterns(db, user_id: int, now: int, lookback_days: float = 14, min_n: int = 5) -> dict:
    """What winners have in common: win rate and average return for every feature value with
    at least `min_n` closed trades, best first. Feeds the Strategy Lab 'winning patterns' card."""
    hist = history(db, user_id, now, lookback_days)
    if not hist:
        return {"trades": 0, "win_rate": None, "avg_pct": None, "patterns": []}
    groups: dict[tuple, list[float]] = {}
    for f, r in hist:
        for k in FEATURES:
            if f.get(k) is not None:
                groups.setdefault((k, f[k]), []).append(r)
    rows = []
    for (k, v), rets in groups.items():
        if len(rets) < min_n:
            continue
        rows.append({"feature": k, "label": LABELS[k], "value": v, "trades": len(rets),
                     "win_rate": round(100.0 * sum(1 for r in rets if r > 0) / len(rets), 1),
                     "avg_pct": round(sum(rets) / len(rets), 2)})
    rows.sort(key=lambda r: (r["avg_pct"], r["win_rate"]), reverse=True)
    all_r = [r for _, r in hist]
    return {"trades": len(hist), "win_rate": round(100.0 * sum(1 for r in all_r if r > 0) / len(all_r), 1),
            "avg_pct": round(sum(all_r) / len(all_r), 2), "lookback_days": lookback_days, "patterns": rows}
