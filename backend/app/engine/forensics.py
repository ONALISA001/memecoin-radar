"""Signal forensics (Tier 2/3): combines stats2 detectors into a per-token
verdict on whether momentum looks ORGANIC or MANUFACTURED.

The headline metric is `organic_score` (0–100): high momentum with a low
organic score is exactly the "High momentum ≠ low risk" lesson (§2),
made quantitative: wash-trading, sybil accounts, coordinated posting,
price-led pumps and distribution regimes all drag it down.
"""
from __future__ import annotations

import math
from collections import Counter

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..models import SocialPost
from . import stats2
from .context import TokenContext
from .timeseries import series_list, ssum


def compute_forensics(db: Session, ctx: TokenContext) -> dict:
    d = ctx.d
    now = ctx.now

    # --- wash-trading heuristics (aggregate-level, honest limits noted) ---
    txns_24h = ssum(ctx.txns, now - 86400, now)
    size_series = [ctx.volume.get(t, 0) / ctx.txns[t] for t in ctx.txns
                   if ctx.txns[t] > 0 and t >= now - 86400]
    size_cv = stats2.uniformity_cv(size_series)
    trades_per_wallet = txns_24h / max(ssum(ctx.active_wallets, now - 86400, now), 1)
    buys_24h = ssum(ctx.buys, now - 86400, now)
    buy_skew_24h = (buys_24h / txns_24h) if txns_24h > 0 else 0.5
    w_parts = [
        0.4 * min(1.0, max(0.0, (trades_per_wallet - 3) / 12)),        # wallets trading like machines
        0.3 * (0.0 if math.isnan(size_cv) else min(1.0, max(0.0, (0.35 - size_cv) / 0.35))),
        0.3 * min(1.0, max(0.0, (buy_skew_24h - 0.75) / 0.2)),         # relentless one-sided flow
    ]
    wash = round(min(1.0, sum(w_parts)), 3)

    # --- sybil / fresh-account pressure ---
    uniq_ratio = d["uniques_1h"] / d["mentions_1h"] if d["mentions_1h"] > 5 else 1.0
    new_share = d["new_wallets_1h"] / d["holders_now"] if d["holders_now"] > 50 else 0.0
    sybil = round(min(1.0, 0.45 * (1 - min(1.0, uniq_ratio / 0.6))
                      + 0.30 * min(1.0, d["lowq_24h"] * 1.4)
                      + 0.25 * min(1.0, new_share / 0.25)), 3)

    # --- coordination across posts (same authors across tokens / templates) ---
    posts = db.execute(
        select(SocialPost).where(SocialPost.token_id == ctx.token.id,
                                 SocialPost.ts >= now - 86400)
    ).scalars().all()
    coord = 0.0
    repeated_authors = 0
    if len(posts) >= 5:
        texts = Counter(p.text.split("🚀")[0] for p in posts)         # template reuse
        template_reuse = 1 - len(texts) / len(posts)
        authors = [p.author for p in posts]
        row = db.execute(
            select(SocialPost.author, func.count(func.distinct(SocialPost.token_id)))
            .where(SocialPost.author.in_(authors), SocialPost.ts >= now - 86400)
            .group_by(SocialPost.author)
        ).all()
        repeated_authors = sum(1 for _, c in row if c >= 2)
        author_share = repeated_authors / max(len(set(authors)), 1)
        influ_density = min(1.0, d["influencers_1h"] / max(d["mentions_1h"], 1) * 20)
        coord = round(min(1.0, 0.45 * template_reuse + 0.35 * author_share
                          + 0.20 * influ_density), 3)

    # --- cascade structure / lead-lag / regime / change-points ---
    exc = stats2.excitation_index(ctx.mentions)
    ll = stats2.lead_lag(ctx.mentions, ctx.price)
    regime = stats2.classify_regime(ctx.price, ctx.volume, ctx.holders, now)
    since_24h = {t: v for t, v in ctx.price.items() if t >= now - 86400}
    cps = stats2.cusum_changepoints([v for _, v in sorted(since_24h.items())])

    # --- retention: are older holders staying? ---
    hs = [(t, v) for t, v in series_list(ctx.holders, now - 21600)]
    peak = max((v for _, v in hs), default=0)
    retention = (hs[-1][1] / peak) if peak > 0 else 1.0

    # --- composite organic score ---
    price_led_penalty = 1.0 if (ll.get("best_lag") is not None and ll["best_lag"] < 0
                                and abs(ll.get("strength") or 0) > 0.25) else 0.35
    regime_penalty = {"distribution": 1.0, "markup": 0.0, "accumulation": 0.0,
                      "chop": 0.4, "dormant": 0.3, "unknown": 0.4}[regime["regime"]]
    manufact_share = min(1.0, 0.28 * wash + 0.22 * sybil + 0.22 * coord
                         + 0.13 * price_led_penalty + 0.15 * regime_penalty)
    organic = round((1 - manufact_share) * 100, 1)

    return {
        "organic_score": organic,
        "verdict": ("Likely organic" if organic >= 70 else
                    "Mostly organic" if organic >= 55 else
                    "Mixed signals" if organic >= 40 else
                    "Possibly manufactured" if organic >= 25 else "Likely manufactured"),
        "components": {
            "wash_trading": {"score": wash, "trades_per_wallet_24h": round(trades_per_wallet, 2),
                             "trade_size_cv": None if math.isnan(size_cv) else round(size_cv, 3),
                             "buy_share_24h": round(buy_skew_24h, 3)},
            "sybil_pressure": {"score": sybil, "unique_ratio_1h": round(uniq_ratio, 3),
                               "low_quality_share_24h": round(d["lowq_24h"], 3),
                               "new_wallet_share": round(new_share, 3)},
            "coordination": {"score": coord, "posts_24h": len(posts),
                             "cross_token_authors": repeated_authors},
            "excitation": exc,
            "lead_lag": ll,
            "regime": regime,
            "holder_retention_6h": round(retention, 3),
        },
        "changepoints_24h": cps[-3:],
        "notes": [
            "Wash/sybil/coordination indexes are heuristics over aggregate buckets — they "
            "flag suspicion, they do not prove manipulation.",
            "With real trade-level and graph data (Phase 3) these become wallet-precise.",
        ],
    }
