"""FOMO Index (§7). Detects RAPID acceleration of attention/participation.
Extreme FOMO ≠ buy signal — it carries an explicit overheating warning."""
from __future__ import annotations

from .context import TokenContext
from .timeseries import sat_log as sat

BANDS = [(81, "EXTREME FOMO"), (61, "STRONG FOMO"), (41, "MOMENTUM"),
         (21, "EMERGING"), (0, "DORMANT")]


def classify(score: float) -> str:
    for lo, name in BANDS:
        if score >= lo:
            return name
    return "DORMANT"


def calculate_fomo(ctx: TokenContext, news_active: bool = False) -> dict:
    d = ctx.d
    c_social = 20 * sat(max(d["mentions_g_15m"], 0), 250)
    c_vol = 20 * sat(max(d["volume_g_15m"], 0), 300)
    c_price = 10 * min(1.0, max(0.0, d["price_chg_15m"]) / 25.0)
    c_txn = 10 * sat(max(d["txns_g_15m"], 0), 250)
    c_holders = 15 * sat(max(d["holders_g_1h"], 0), 25)
    c_infl = 10 * min(1.0, d["influencers_1h"] / 3.0)
    c_eng = 10 * sat(max(d["engagement_g_15m"], 0), 250)
    c_news = 5.0 if news_active and d["mentions_g_1h"] > 50 else 0.0

    score = max(0.0, min(100.0, c_social + c_vol + c_price + c_txn + c_holders
                           + c_infl + c_eng + c_news))
    cls = classify(score)
    out = {
        "fomo": round(score, 1),
        "classification": cls,
        "components": [
            {"label": "Social acceleration (15m)", "points": round(c_social, 1), "max": 20,
             "value": f"{d['mentions_g_15m']:+.0f}%"},
            {"label": "Volume acceleration (15m)", "points": round(c_vol, 1), "max": 20,
             "value": f"{d['volume_g_15m']:+.0f}%"},
            {"label": "Price acceleration (15m)", "points": round(c_price, 1), "max": 10,
             "value": f"{d['price_chg_15m']:+.1f}%"},
            {"label": "Transaction acceleration (15m)", "points": round(c_txn, 1), "max": 10,
             "value": f"{d['txns_g_15m']:+.0f}%"},
            {"label": "New-holder acceleration (1h)", "points": round(c_holders, 1), "max": 15,
             "value": f"{d['holders_g_1h']:+.1f}%"},
            {"label": "Influencer activity", "points": round(c_infl, 1), "max": 10,
             "value": f"{d['influencers_1h']:.0f} posts"},
            {"label": "Engagement acceleration", "points": round(c_eng, 1), "max": 10,
             "value": f"{d['engagement_g_15m']:+.0f}%"},
            {"label": "News/search activity", "points": round(c_news, 1), "max": 5,
             "value": "active" if news_active else "quiet"},
        ],
    }
    if score >= 81:
        out["warning"] = ("Attention is accelerating rapidly. Extreme momentum can be "
                          "associated with elevated volatility and rapid reversals. "
                          "This is not a buy signal.")
    return out
