"""Trend Score engine (§6, §34, §35). Every sub-score is an isolated pure
function returning (points, detail) so the model can be tuned, explained
and backtested. The UI always receives the full component breakdown."""
from __future__ import annotations

from ..config import ANOMALY_Z
from .context import TokenContext
from .timeseries import sat_log as sat


def calculate_social_score(ctx: TokenContext) -> tuple[float, dict]:
    d = ctx.d
    p_growth = 8 * sat(d["mentions_g_1h"], 300)
    p_vel = 5 * sat(max(d["mentions_g_15m"], 0), 250)
    p_eng = 4 * sat(max(d["engagement_g_1h"], 0), 300)
    diversity = d["uniques_1h"] / d["mentions_1h"] if d["mentions_1h"] > 0 else 0.0
    p_uniq = 4 * sat(max(d["uniques_g_1h"], 0), 300) * min(1.0, 0.3 + 1.4 * diversity)
    p_infl = min(2.0, d["influencers_1h"] * 0.7)
    p_cross = 2.0 if (d["mentions_24h"] > 0 and (d["influencers_24h"] > 0 or ctx.available.get("news"))) \
        else (1.0 if d["mentions_24h"] > 10 else 0.0)
    detail = {
        "max": 25,
        "drivers": [
            {"label": "Mention growth (1h)", "points": round(p_growth, 1), "max": 8,
             "value": f"{d['mentions_1h']:.0f} vs {d['mentions_prev_1h']:.0f} ({d['mentions_g_1h']:+.0f}%)"},
            {"label": "Mention velocity (15m)", "points": round(p_vel, 1), "max": 5,
             "value": f"{d['mentions_g_15m']:+.0f}%"},
            {"label": "Engagement growth", "points": round(p_eng, 1), "max": 4,
             "value": f"{d['engagement_g_1h']:+.0f}%"},
            {"label": "Unique-account growth × diversity", "points": round(p_uniq, 1), "max": 4,
             "value": f"{d['uniques_g_1h']:+.0f}% · diversity {diversity:.0%}"},
            {"label": "Influencer activity", "points": round(p_infl, 1), "max": 2,
             "value": f"{d['influencers_1h']:.0f} posts (1h)"},
            {"label": "Cross-source presence", "points": round(p_cross, 1), "max": 2,
             "value": "multi-source" if p_cross >= 2 else "single-source"},
        ],
    }
    return round(p_growth + p_vel + p_eng + p_uniq + p_infl + p_cross, 1), detail


def calculate_market_score(ctx: TokenContext) -> tuple[float, dict]:
    d = ctx.d
    p_vol = 9 * sat(d["volume_g_1h"], 350)
    p_price = 5 * min(1.0, max(0.0, d["price_chg_1h"]) / 60.0)
    p_txn = 5 * sat(d["txns_g_1h"], 250)
    share = d["buy_share_1h"]
    p_bs = 3.0 if 0.50 <= share <= 0.75 else (1.5 if 0.42 <= share <= 0.85 else
                                              (0.5 if share <= 0.9 else 0.0))
    ratio = d["vol_liq_ratio"]
    p_vl = 3.0 if 0.2 <= ratio <= 2.5 else (1.5 if 0.05 <= ratio <= 5.0 else 0.5)
    detail = {
        "max": 25,
        "drivers": [
            {"label": "Volume acceleration (1h)", "points": round(p_vol, 1), "max": 9,
             "value": f"${d['volume_1h']:,.0f} vs ${d['volume_prev_1h']:,.0f} ({d['volume_g_1h']:+.0f}%)"},
            {"label": "Price momentum (1h)", "points": round(p_price, 1), "max": 5,
             "value": f"{d['price_chg_1h']:+.2f}%"},
            {"label": "Transaction growth", "points": round(p_txn, 1), "max": 5,
             "value": f"{d['txns_1h']:.0f} txns ({d['txns_g_1h']:+.0f}%)"},
            {"label": "Buy/sell balance", "points": round(p_bs, 1), "max": 3,
             "value": f"{share:.0%} buys"},
            {"label": "Volume/liquidity ratio", "points": round(p_vl, 1), "max": 3,
             "value": f"{ratio:.2f}"},
        ],
    }
    return round(p_vol + p_price + p_txn + p_bs + p_vl, 1), detail


def calculate_onchain_score(ctx: TokenContext) -> tuple[float, dict]:
    d = ctx.d
    p_h1 = 6 * sat(max(d["holders_g_1h"], 0), 30)
    p_h24 = 4 * sat(max(d["holders_g_24h"], 0), 150)
    p_nw = 3 * sat(max(d["new_wallets_g_1h"], 0), 250)
    whale = d["whale_net_24h"]
    p_whale = 2.0 if 1_000 < whale < 200_000 else (1.0 if -30_000 <= whale <= 1_000 else 0.2)
    detail = {
        "max": 15,
        "drivers": [
            {"label": "Holder growth (1h)", "points": round(p_h1, 1), "max": 6,
             "value": f"{d['holders_now']:.0f} holders ({d['holders_g_1h']:+.1f}%)"},
            {"label": "Holder growth (24h)", "points": round(p_h24, 1), "max": 4,
             "value": f"{d['holders_g_24h']:+.1f}%"},
            {"label": "New wallets", "points": round(p_nw, 1), "max": 3,
             "value": f"{d['new_wallets_1h']:.0f}/h ({d['new_wallets_g_1h']:+.0f}%)"},
            {"label": "Whale flow (24h)", "points": round(p_whale, 1), "max": 2,
             "value": f"${whale:+,.0f} net"},
        ],
    }
    return round(p_h1 + p_h24 + p_nw + p_whale, 1), detail


def calculate_liquidity_score(ctx: TokenContext) -> tuple[float, dict]:
    d = ctx.d
    liq = d["liq_now"]
    p_abs = 6.0 if liq >= 10e6 else 5.0 if liq >= 2e6 else 4.0 if liq >= 5e5 else \
        3.0 if liq >= 1e5 else 2.0 if liq >= 25_000 else 1.0 if liq >= 5_000 else 0.0
    p_chg = 4 * sat(max(d["liq_chg_3h"], 0), 50)
    inst = d["liq_instability"]
    p_stab = 3.0 if inst < 1.5 else 2.0 if inst < 3 else 1.0 if inst < 6 else 0.0
    ratio = d["vol_liq_ratio"]
    p_vl = 2.0 if 0.2 <= ratio <= 2.5 else (1.0 if 0.05 <= ratio <= 5.0 else 0.0)
    detail = {
        "max": 15,
        "drivers": [
            {"label": "Absolute liquidity", "points": p_abs, "max": 6, "value": f"${liq:,.0f}"},
            {"label": "Liquidity change (3h)", "points": round(p_chg, 1), "max": 4,
             "value": f"{d['liq_chg_3h']:+.1f}%"},
            {"label": "Liquidity stability", "points": p_stab, "max": 3,
             "value": f"{inst:.1f}% mean step"},
            {"label": "Volume/liquidity health", "points": p_vl, "max": 2, "value": f"{ratio:.2f}"},
        ],
    }
    return round(p_abs + p_chg + p_stab + p_vl, 1), detail


def calculate_narrative_score(narr_best: float, narr_count: int, cross_platform: bool) -> tuple[float, dict]:
    p_best = 6 * min(1.0, narr_best / 100.0)
    p_cnt = min(2.0, narr_count * 0.7)
    p_cross = 2.0 if cross_platform else 0.5
    detail = {
        "max": 10,
        "drivers": [
            {"label": "Strongest narrative", "points": round(p_best, 1), "max": 6,
             "value": f"score {narr_best:.0f}/100"},
            {"label": "Narrative breadth", "points": round(p_cnt, 1), "max": 2,
             "value": f"{narr_count} narratives"},
            {"label": "Cross-platform narrative", "points": p_cross, "max": 2,
             "value": "confirmed" if cross_platform else "single platform"},
        ],
    }
    return round(p_best + p_cnt + p_cross, 1), detail


def calculate_market_context_score(market_ctx: dict) -> tuple[float, dict]:
    mom = market_ctx.get("sector_momentum", 50.0)
    breadth = market_ctx.get("breadth", 0.5)
    p_mom = 6 * min(1.0, max(0.0, mom) / 100.0)
    p_breadth = 4 * min(1.0, max(0.0, breadth))
    detail = {
        "max": 10,
        "drivers": [
            {"label": "Meme-sector momentum", "points": round(p_mom, 1), "max": 6,
             "value": f"{mom:.0f}/100"},
            {"label": "Market breadth (24h advancers)", "points": round(p_breadth, 1), "max": 4,
             "value": f"{breadth:.0%}"},
        ],
    }
    return round(p_mom + p_breadth, 1), detail


def calculate_trend_score(ctx: TokenContext, narrative_info: dict, market_ctx: dict) -> dict:
    social, social_d = calculate_social_score(ctx)
    market, market_d = calculate_market_score(ctx)
    onchain, onchain_d = calculate_onchain_score(ctx)
    liq, liq_d = calculate_liquidity_score(ctx)
    narr, narr_d = calculate_narrative_score(
        narrative_info.get("best_score", 0.0),
        narrative_info.get("count", 0),
        narrative_info.get("cross_platform", False),
    )
    mctx, mctx_d = calculate_market_context_score(market_ctx)
    total = max(0.0, min(100.0, social + market + onchain + liq + narr + mctx))
    anomaly_hits = [k for k, z in ctx.anomalies.items() if abs(z) >= ANOMALY_Z]
    return {
        "trend": round(total, 1),
        "anomalies": anomaly_hits,
        "components": {
            "social": {"points": social, **social_d, "source": ctx.sources["social"]},
            "market": {"points": market, **market_d, "source": ctx.sources["market"]},
            "onchain": {"points": onchain, **onchain_d, "source": ctx.sources["onchain"]},
            "liquidity": {"points": liq, **liq_d, "source": ctx.sources["market"]},
            "narrative": {"points": narr, **narr_d, "source": "narrative-engine"},
            "market_context": {"points": mctx, **mctx_d, "source": "internal-aggregate"},
        },
    }
