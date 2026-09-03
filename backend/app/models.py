"""Database models — §27. Time-series snapshots are the backbone (§33)."""
from sqlalchemy import (
    Column, Integer, String, Float, DateTime, ForeignKey, JSON, Text,
    UniqueConstraint, Index, Boolean,
)
from sqlalchemy.sql import func

from .database import Base


class User(Base):
    __tablename__ = "users"
    id = Column(Integer, primary_key=True)
    email = Column(String(255), unique=True, nullable=False)
    password_hash = Column(String(255), nullable=False)
    api_token = Column(String(128), unique=True, index=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())


class Token(Base):
    __tablename__ = "tokens"
    id = Column(Integer, primary_key=True)
    address = Column(String(128), unique=True, nullable=False, index=True)
    chain = Column(String(32), index=True, default="solana")
    name = Column(String(160))
    symbol = Column(String(32), index=True)
    image_url = Column(String(512))
    description = Column(Text)
    deployer = Column(String(128))
    pair_address = Column(String(128))
    dex = Column(String(64))
    url = Column(String(512))
    archetype = Column(String(32))           # simulation archetype label (mock providers)
    state_json = Column(JSON)                # persisted simulator state (deterministic mocks)
    first_seen_at = Column(DateTime(timezone=True), server_default=func.now())
    token_created_at = Column(Integer)       # epoch ms from provider

    __table_args__ = (Index("ix_tokens_chain_addr", "chain", "address"),)


class TokenPair(Base):
    __tablename__ = "token_pairs"
    id = Column(Integer, primary_key=True)
    token_id = Column(Integer, ForeignKey("tokens.id"), index=True)
    dex = Column(String(64))
    pair_address = Column(String(128), unique=True)
    liquidity_usd = Column(Float)
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


class PriceSnapshot(Base):
    __tablename__ = "price_snapshots"
    id = Column(Integer, primary_key=True)
    token_id = Column(Integer, ForeignKey("tokens.id"))
    ts = Column(Integer)                     # epoch seconds, 5-min bucket
    price_usd = Column(Float)
    mcap = Column(Float)
    fdv = Column(Float)
    __table_args__ = (Index("ix_price_tok_ts", "token_id", "ts"),)


class VolumeSnapshot(Base):
    __tablename__ = "volume_snapshots"
    id = Column(Integer, primary_key=True)
    token_id = Column(Integer, ForeignKey("tokens.id"))
    ts = Column(Integer)
    volume = Column(Float)                   # USD traded within the bucket
    txns = Column(Integer)
    buys = Column(Integer)
    sells = Column(Integer)
    __table_args__ = (Index("ix_vol_tok_ts", "token_id", "ts"),)


class LiquiditySnapshot(Base):
    __tablename__ = "liquidity_snapshots"
    id = Column(Integer, primary_key=True)
    token_id = Column(Integer, ForeignKey("tokens.id"))
    ts = Column(Integer)
    liquidity_usd = Column(Float)
    __table_args__ = (Index("ix_liq_tok_ts", "token_id", "ts"),)


class HolderSnapshot(Base):
    __tablename__ = "holder_snapshots"
    id = Column(Integer, primary_key=True)
    token_id = Column(Integer, ForeignKey("tokens.id"))
    ts = Column(Integer)
    holders = Column(Integer)
    top10_pct = Column(Float)
    deployer_pct = Column(Float)
    new_wallets = Column(Integer)            # within bucket
    active_wallets = Column(Integer)
    whale_net_usd = Column(Float)            # net whale flow within bucket
    __table_args__ = (Index("ix_hold_tok_ts", "token_id", "ts"),)


class SocialSnapshot(Base):
    __tablename__ = "social_snapshots"
    id = Column(Integer, primary_key=True)
    token_id = Column(Integer, ForeignKey("tokens.id"))
    ts = Column(Integer)
    mentions = Column(Integer)               # within bucket
    engagement = Column(Integer)
    unique_accounts = Column(Integer)
    likes = Column(Integer)
    reposts = Column(Integer)
    replies = Column(Integer)
    sentiment = Column(Float)                # -1..1
    influencer_posts = Column(Integer)
    low_quality_share = Column(Float)        # 0..1 share of bot-like accounts
    __table_args__ = (Index("ix_soc_tok_ts", "token_id", "ts"),)


class SocialPost(Base):
    __tablename__ = "social_posts"
    id = Column(Integer, primary_key=True)
    token_id = Column(Integer, ForeignKey("tokens.id"))
    ts = Column(Integer)
    author = Column(String(64))
    text = Column(Text)
    likes = Column(Integer, default=0)
    reposts = Column(Integer, default=0)
    quality = Column(Float, default=0.5)     # account quality 0..1
    __table_args__ = (Index("ix_post_tok_ts", "token_id", "ts"),)


class NewsArticle(Base):
    __tablename__ = "news_articles"
    id = Column(Integer, primary_key=True)
    ts = Column(Integer, index=True)
    source = Column(String(64))
    title = Column(String(300))
    url = Column(String(512))
    narrative = Column(String(120), index=True)
    sentiment = Column(Float)
    relevance = Column(Float)
    entities_json = Column(JSON)             # ["SYM", ...]


class Narrative(Base):
    __tablename__ = "narratives"
    id = Column(Integer, primary_key=True)
    name = Column(String(120), unique=True)
    kind = Column(String(16), default="seeded")   # seeded | auto
    keywords_json = Column(JSON)
    score = Column(Float, default=0)
    mentions_growth = Column(Float, default=0)
    token_count = Column(Integer, default=0)
    abnormal_volume_tokens = Column(Integer, default=0)
    strong_candidates = Column(Integer, default=0)
    cross_platform = Column(String(8), default="LOW")  # HIGH/MED/LOW
    first_seen_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


class TokenNarrative(Base):
    __tablename__ = "token_narratives"
    token_id = Column(Integer, ForeignKey("tokens.id"), primary_key=True)
    narrative_id = Column(Integer, ForeignKey("narratives.id"), primary_key=True)
    relevance = Column(Float, default=0.5)


class WalletActivity(Base):
    __tablename__ = "wallet_activity"
    id = Column(Integer, primary_key=True)
    token_id = Column(Integer, ForeignKey("tokens.id"))
    ts = Column(Integer)
    wallet = Column(String(128))
    side = Column(String(8))                 # buy | sell
    amount_usd = Column(Float)
    kind = Column(String(24), default="whale")  # whale | deployer | fresh_wallet
    __table_args__ = (Index("ix_wa_tok_ts", "token_id", "ts"),)


class RiskEvent(Base):
    __tablename__ = "risk_events"
    id = Column(Integer, primary_key=True)
    token_id = Column(Integer, ForeignKey("tokens.id"), index=True)
    ts = Column(Integer, index=True)
    category = Column(String(48))
    severity = Column(String(12))            # info | warn | high
    message = Column(String(400))


class TrendScore(Base):
    __tablename__ = "trend_scores"
    id = Column(Integer, primary_key=True)
    token_id = Column(Integer, ForeignKey("tokens.id"))
    ts = Column(Integer, index=True)
    trend = Column(Float)
    fomo = Column(Float)
    fomo_class = Column(String(16))
    risk = Column(Float)
    risk_class = Column(String(16))
    confidence = Column(String(8))           # HIGH | MEDIUM | LOW (§19)
    emerging = Column(Boolean, default=False)
    kind = Column(String(8), default="live", index=True)  # live | retro (backtest)
    components_json = Column(JSON)           # full explainability payload (§35)
    __table_args__ = (
        Index("ix_ts_tok_ts", "token_id", "ts"),
        Index("ix_ts_ts_trend", "ts", "trend"),
        Index("ix_ts_ts_risk", "ts", "risk"),
    )


class Alert(Base):
    __tablename__ = "alerts"
    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id"), index=True)
    name = Column(String(160))
    condition_json = Column(JSON)            # {metric, op, value, scope, address?}
    channel_json = Column(JSON)              # {type: browser|webhook, url?}
    enabled = Column(Boolean, default=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())


class Watchlist(Base):
    __tablename__ = "watchlists"
    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id"), index=True)
    token_id = Column(Integer, ForeignKey("tokens.id"), index=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    __table_args__ = (UniqueConstraint("user_id", "token_id", name="uq_watch"),)


class AlertEvent(Base):
    __tablename__ = "alert_events"
    id = Column(Integer, primary_key=True)
    alert_id = Column(Integer, ForeignKey("alerts.id"), index=True)
    token_id = Column(Integer, ForeignKey("tokens.id"), nullable=True)
    ts = Column(Integer, index=True)
    payload_json = Column(JSON)
    delivered_json = Column(JSON)            # per-channel delivery result
    read = Column(Boolean, default=False)


class SignalOutcome(Base):
    """Forward outcomes attached to score snapshots (backtesting, §21).
    Labels are produced lazily once the horizon has elapsed."""
    __tablename__ = "signal_outcomes"
    id = Column(Integer, primary_key=True)
    token_id = Column(Integer, ForeignKey("tokens.id"), index=True)
    ts = Column(Integer, index=True)                    # score snapshot time
    horizon_secs = Column(Integer)
    trend = Column(Float)
    fomo = Column(Float)
    risk = Column(Float)
    emerging = Column(Boolean, default=False)
    kind = Column(String(8), default="live")            # live | retro
    fwd_ret_pct = Column(Float)
    max_dd_pct = Column(Float)
    outcome = Column(String(16))                        # continued | faded | reversal | rug | neutral
    __table_args__ = (Index("ix_outcomes_unique", "token_id", "ts", "horizon_secs"),)


class DataSource(Base):
    __tablename__ = "data_sources"
    id = Column(Integer, primary_key=True)
    name = Column(String(64), unique=True)
    kind = Column(String(24))                # market | social | onchain | news
    status = Column(String(16), default="ok")  # ok | mock | degraded | down
    last_ok_at = Column(DateTime(timezone=True), server_default=func.now())
    meta_json = Column(JSON)


class SystemLog(Base):
    __tablename__ = "system_logs"
    id = Column(Integer, primary_key=True)
    ts = Column(Integer, index=True)
    level = Column(String(8))
    source = Column(String(64))
    message = Column(String(500))
