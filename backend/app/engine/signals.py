"""Signal confirmation (§11), data-confidence grading (§19), contradiction
detection (§36) and emerging-signal classification (§10)."""
from __future__ import annotations

from ..config import (MIN_HISTORY_HOURS, EMERGING_MIN_FOMO, EMERGING_MIN_SOCIAL_G, EMERGING_MIN_TREND,
                      EMERGING_MIN_VOLUME_G)
from .context import TokenContext


def confirmation(ctx: TokenContext, news_active: bool) -> dict:
    d = ctx.d
    conf = {
        "social": d["mentions_g_1h"] >= 50,
        "market": d["volume_g_1h"] >= 50,
        "onchain": d["holders_g_1h"] >= 3,
        "news": bool(news_active),
    }
    count = sum(1 for v in conf.values() if v)
    # §19 confidence: availability × independence × agreement. Mock providers
    # carry reduced weight, so Phase-1 confidence is capped accordingly.
    avail = 0.0
    avail += 1.0 if ctx.available.get("market") else 0.0
    avail += 0.7 if ctx.available.get("social") else 0.0
    avail += 0.7 if ctx.available.get("onchain") else 0.0
    avail += 0.6 if news_active else 0.0
    score = avail + 0.4 * count
    level = "HIGH" if score >= 3.6 else "MEDIUM" if score >= 2.4 else "LOW"
    if count < 3 and level == "HIGH":     # independent agreement is required for HIGH
        level = "MEDIUM"
    return {
        "checks": conf,
        "confirmed": count,
        "confidence": level,
        "availability": {k: bool(v) for k, v in ctx.available.items() if v is not None},
        "note": ("Multiple independent signals agree." if count >= 3
                 else "Limited cross-source confirmation — treat with extra caution."),
    }


def detect_conflicts(ctx: TokenContext, fomo: float) -> list[dict]:
    """§36 false-signal protection: surface contradictory evidence."""
    d = ctx.d
    out = []
    social_hi = d["mentions_g_1h"] >= 150
    onchain_lo = d["holders_g_1h"] < 2
    liq_falling = d["liq_chg_3h"] <= -10
    if social_hi and onchain_lo:
        out.append({
            "title": "SIGNAL CONFLICT — social vs on-chain",
            "detail": "Momentum is concentrated in social activity with limited "
                      "on-chain confirmation (holder growth low).",
            "evidence": {"social_momentum": "HIGH", "onchain_growth": "LOW"},
        })
    if fomo >= 70 and liq_falling:
        out.append({
            "title": "SIGNAL CONFLICT — high FOMO with declining liquidity",
            "detail": "Attention is accelerating while liquidity is being withdrawn — "
                      "historically a fragile combination.",
            "evidence": {"fomo": f"{fomo:.0f}", "liquidity_3h": f"{d['liq_chg_3h']:+.1f}%"},
        })
    if d["price_chg_1h"] > 25 and d["holders_g_1h"] < -1:
        out.append({
            "title": "SIGNAL CONFLICT — price up, holders falling",
            "detail": "Price is rising while the holder base shrinks, suggesting "
                      "distribution rather than adoption.",
            "evidence": {"price_1h": f"{d['price_chg_1h']:+.1f}%",
                         "holders_1h": f"{d['holders_g_1h']:+.1f}%"},
        })
    if d["mentions_1h"] > 20 and d["uniques_1h"] / max(d["mentions_1h"], 1) < 0.25 \
            and d["influencers_1h"] >= 1:
        out.append({
            "title": "SIGNAL CONFLICT — promoted vs organic activity",
            "detail": "Influencer/promoted activity present with very few unique "
                      "accounts — organic engagement is weak.",
            "evidence": {"unique_ratio": f"{d['uniques_1h'] / max(d['mentions_1h'], 1):.0%}"},
        })
    return out


def is_emerging(ctx: TokenContext, trend: float, fomo: float, age_hours: float,
                trend_delta_30m: float) -> bool:
    d = ctx.d
    if d.get("history_h", 0.0) < MIN_HISTORY_HOURS:
        return False            # too little recorded history to call acceleration
    return (trend >= EMERGING_MIN_TREND and fomo >= EMERGING_MIN_FOMO and fomo < 81
            and d["mentions_g_1h"] >= EMERGING_MIN_SOCIAL_G
            and d["volume_g_1h"] >= EMERGING_MIN_VOLUME_G
            and d["holders_g_1h"] > 0 and trend_delta_30m > -3
            and age_hours <= 24 * 7)
