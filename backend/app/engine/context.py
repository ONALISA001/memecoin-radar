"""TokenContext — the single, fully-traceable metric context consumed by the
Trend, FOMO and Risk engines, the explainability payload and the AI composer
(§17: 'AI analyzes structured data produced by the analytics engine').

Every number here is derived from stored snapshots, so every claim the UI
makes can be traced to source rows (§30).
"""
from __future__ import annotations

from dataclasses import dataclass, field

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import (
    HolderSnapshot, LiquiditySnapshot, PriceSnapshot, SocialSnapshot, Token,
    VolumeSnapshot, WalletActivity,
)
from . import timeseries as tsx
from .. import config


def _rows(db: Session, model, token_id: int, since: int):
    return db.execute(
        select(model).where(model.token_id == token_id, model.ts >= since)
    ).scalars().all()


@dataclass
class TokenContext:
    token: Token
    now: int
    sources: dict[str, str] = field(default_factory=dict)
    available: dict[str, bool] = field(default_factory=dict)

    # raw series
    price: dict[int, float] = field(default_factory=dict)
    mcap: dict[int, float] = field(default_factory=dict)
    fdv: dict[int, float] = field(default_factory=dict)
    volume: dict[int, float] = field(default_factory=dict)
    txns: dict[int, float] = field(default_factory=dict)
    buys: dict[int, float] = field(default_factory=dict)
    sells: dict[int, float] = field(default_factory=dict)
    liquidity: dict[int, float] = field(default_factory=dict)
    holders: dict[int, float] = field(default_factory=dict)
    top10: dict[int, float] = field(default_factory=dict)
    deployer_pct: dict[int, float] = field(default_factory=dict)
    new_wallets: dict[int, float] = field(default_factory=dict)
    active_wallets: dict[int, float] = field(default_factory=dict)
    whale_net: dict[int, float] = field(default_factory=dict)
    mentions: dict[int, float] = field(default_factory=dict)
    engagement: dict[int, float] = field(default_factory=dict)
    uniques: dict[int, float] = field(default_factory=dict)
    sentiment: dict[int, float] = field(default_factory=dict)
    influencers: dict[int, float] = field(default_factory=dict)
    lowq: dict[int, float] = field(default_factory=dict)

    # derived accelerations & levels
    d: dict[str, float] = field(default_factory=dict)
    anomalies: dict[str, float] = field(default_factory=dict)

    @classmethod
    def build(cls, db: Session, token: Token, now: int, hours: int = 48) -> "TokenContext":
        since = now - hours * 3600
        ctx = cls(token=token, now=now)
        ctx.sources = {"market": "dexscreener", "social": "mock:x-style-sim",
                       "onchain": "mock:chain-sim",
                       "news": "rss" if config.NEWS_RSS_ENABLED else "mock:news-sim"}

        for r in _rows(db, PriceSnapshot, token.id, since):
            ctx.price[r.ts] = r.price_usd or 0.0
            ctx.mcap[r.ts] = r.mcap or 0.0
            ctx.fdv[r.ts] = r.fdv or 0.0
        for r in _rows(db, VolumeSnapshot, token.id, since):
            ctx.volume[r.ts] = r.volume or 0.0
            ctx.txns[r.ts] = r.txns or 0.0
            ctx.buys[r.ts] = r.buys or 0.0
            ctx.sells[r.ts] = r.sells or 0.0
        for r in _rows(db, LiquiditySnapshot, token.id, since):
            ctx.liquidity[r.ts] = r.liquidity_usd or 0.0
        for r in _rows(db, HolderSnapshot, token.id, since):
            # NULL = not measured (real-data mode): skip it rather than treating it as 0
            if r.holders is not None:
                ctx.holders[r.ts] = r.holders
            if r.top10_pct is not None:
                ctx.top10[r.ts] = r.top10_pct
            if r.deployer_pct is not None:
                ctx.deployer_pct[r.ts] = r.deployer_pct
            if r.new_wallets is not None:
                ctx.new_wallets[r.ts] = r.new_wallets
            if r.active_wallets is not None:
                ctx.active_wallets[r.ts] = r.active_wallets
            if r.whale_net_usd is not None:
                ctx.whale_net[r.ts] = r.whale_net_usd
        for r in _rows(db, SocialSnapshot, token.id, since):
            ctx.mentions[r.ts] = r.mentions or 0.0
            ctx.engagement[r.ts] = r.engagement or 0.0
            ctx.uniques[r.ts] = r.unique_accounts or 0.0
            ctx.sentiment[r.ts] = r.sentiment or 0.0
            ctx.influencers[r.ts] = r.influencer_posts or 0.0
            ctx.lowq[r.ts] = r.low_quality_share or 0.0

        ctx.available = {
            "market": bool(ctx.price and ctx.volume and ctx.liquidity),
            "social": bool(ctx.mentions),
            "onchain": bool(ctx.holders),
            "news": None,  # filled by caller per narrative
        }
        ctx._derive()
        return ctx

    # ------------------------------------------------------------------
    def _derive(self) -> None:
        now = self.now
        d = self.d
        # social (§5)
        d["mentions_1h"], d["mentions_prev_1h"], d["mentions_g_1h"] = tsx.window_growth(self.mentions, now, 3600)
        d["mentions_15m"], d["mentions_prev_15m"], d["mentions_g_15m"] = tsx.window_growth(self.mentions, now, 900)
        d["mentions_24h"], _, d["mentions_g_24h"] = tsx.window_growth(self.mentions, now, 86400)
        _, _, d["engagement_g_1h"] = tsx.window_growth(self.engagement, now, 3600)
        _, _, d["engagement_g_15m"] = tsx.window_growth(self.engagement, now, 900)
        d["uniques_1h"], d["uniques_prev_1h"], d["uniques_g_1h"] = tsx.window_growth(self.uniques, now, 3600)
        d["influencers_1h"] = tsx.ssum(self.influencers, now - 3600, now)
        d["influencers_24h"] = tsx.ssum(self.influencers, now - 86400, now)
        d["sentiment_avg_6h"] = tsx.ema([v for t, v in tsx.series_list(self.sentiment, now - 21600)]) \
            if self.sentiment else 0.0
        d["lowq_24h"] = (tsx.rolling_stats(list(self.lowq.values()))[0] if self.lowq else 0.5)
        # market
        d["history_h"] = tsx.history_hours(self.price, now)   # real-data coverage
        d["volume_1h"], d["volume_prev_1h"], d["volume_g_1h"] = tsx.window_growth(self.volume, now, 3600)
        d["volume_15m"], d["volume_prev_15m"], d["volume_g_15m"] = tsx.window_growth(self.volume, now, 900)
        d["volume_24h"], d["volume_prev_24h"], d["volume_g_24h"] = tsx.window_growth(self.volume, now, 86400)
        d["txns_1h"], d["txns_prev_1h"], d["txns_g_1h"] = tsx.window_growth(self.txns, now, 3600)
        d["txns_15m"], _, d["txns_g_15m"] = tsx.window_growth(self.txns, now, 900)
        d["buys_1h"] = tsx.ssum(self.buys, now - 3600, now)
        d["sells_1h"] = tsx.ssum(self.sells, now - 3600, now)
        d["buy_share_1h"] = d["buys_1h"] / d["txns_1h"] if d["txns_1h"] > 0 else 0.5
        d["price_now"], d["price_1h_ago"], d["price_chg_1h"] = tsx.level_growth(self.price, now, 3600)
        _, d["price_15m_ago"], d["price_chg_15m"] = tsx.level_growth(self.price, now, 900)
        _, _, d["price_chg_24h"] = tsx.level_growth(self.price, now, 86400)
        d["mcap_now"] = tsx.value_at_or_before(self.mcap, now)
        d["fdv_now"] = tsx.value_at_or_before(self.fdv, now)
        # liquidity
        d["liq_now"], d["liq_3h_ago"], d["liq_chg_3h"] = tsx.level_growth(self.liquidity, now, 10800)
        _, d["liq_24h_ago"], d["liq_chg_24h"] = tsx.level_growth(self.liquidity, now, 86400)
        d["vol_liq_ratio"] = d["volume_1h"] / d["liq_now"] if d["liq_now"] > 0 else 99.0
        liq_levels = [v for _, v in tsx.series_list(self.liquidity, now - 10800)]
        chgs = [abs(b - a) / max(a, 1e-9) for a, b in zip(liq_levels, liq_levels[1:])]
        d["liq_instability"] = sum(chgs) / len(chgs) * 100 if chgs else 0.0
        # on-chain
        d["holders_now"], d["holders_1h_ago"], d["holders_g_1h"] = tsx.level_growth(self.holders, now, 3600)
        _, _, d["holders_g_24h"] = tsx.level_growth(self.holders, now, 86400)
        _, _, d["holders_g_15m"] = tsx.level_growth(self.holders, now, 900)
        d["new_wallets_1h"], _, d["new_wallets_g_1h"] = tsx.window_growth(self.new_wallets, now, 3600)
        d["active_wallets_1h"], _, d["active_wallets_g_1h"] = tsx.window_growth(self.active_wallets, now, 3600)
        d["whale_net_24h"] = tsx.ssum(self.whale_net, now - 86400, now)
        d["top10_now"] = tsx.value_at_or_before(self.top10, now)
        d["deployer_pct_now"] = tsx.value_at_or_before(self.deployer_pct, now)

        # anomalies (§20): newest window vs trailing 24h baseline of same window
        def az(series: dict[int, float], win: int) -> float:
            base = [tsx.ssum(series, now - (i + 2) * win, now - (i + 1) * win) for i in range(0, 288 * 300 // max(win, 300))]
            return tsx.zscore(base, tsx.ssum(series, now - win, now))

        self.anomalies = {
            "mentions": az(self.mentions, 900),
            "volume": az(self.volume, 900),
            "holders": tsx.zscore(
                [tsx.value_at_or_before(self.holders, now - i * 3600) for i in range(24, 1, -1)],
                d["holders_now"]),
            "price": tsx.zscore(
                [tsx.value_at_or_before(self.price, now - i * 900) for i in range(96, 1, -1)],
                d["price_now"]),
        }

    # convenience ---------------------------------------------------------
    def whale_events(self, db: Session, limit: int = 20):
        rows = db.execute(
            select(WalletActivity).where(WalletActivity.token_id == self.token.id)
            .order_by(WalletActivity.ts.desc()).limit(limit)
        ).scalars().all()
        return [{"ts": r.ts, "wallet": r.wallet, "side": r.side,
                 "amount_usd": r.amount_usd, "kind": r.kind} for r in rows]
