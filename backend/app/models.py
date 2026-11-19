"""Database models — §27. Time-series snapshots are the backbone (§33)."""
from sqlalchemy import (
    Column, Integer, BigInteger, String, Float, DateTime, ForeignKey, JSON, Text,
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
    token_created_at = Column(BigInteger)    # epoch ms from provider (needs 64-bit on Postgres)
    new_token_evented = Column(Boolean, default=False)  # v3: "new token" event emitted once ever
    discovery_source = Column(String(16))    # new_pool | boost | profile (first source wins)
    launch_alert = Column(String(8))         # launch-safety alert: None | sent | skipped | failed

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


class SignalEvent(Base):
    """Notable market-intelligence events for the per-token signal timeline,
    the global live-intelligence feed, chains and alert/rules (v2/v3).
    Enriched rows (importance/confidence/checklist) are written by engine.intel;
    write path is cooldown-deduped with escalation keys (§43)."""
    __tablename__ = "signal_events"
    id = Column(Integer, primary_key=True)
    token_id = Column(Integer, ForeignKey("tokens.id"), index=True)
    ts = Column(Integer, index=True)
    kind = Column(String(32), index=True)
    key = Column(String(64), default="")
    title = Column(String(200))
    detail = Column(String(600))
    severity = Column(String(12), default="info")
    metrics_json = Column(JSON)
    # v3 enrichment (additive, nullable for backward compat)
    category = Column(String(16), index=True)          # new|momentum|volume|flow|whales|holders|liquidity|ath|reversal|distribution|risk|pattern
    importance = Column(String(8), index=True)         # LOW|MEDIUM|HIGH|CRITICAL
    importance_score = Column(Integer, default=0)
    confidence = Column(Integer, default=0)
    signal_text = Column(String(300))
    why_json = Column(JSON)
    concern = Column(String(300))
    chain_json = Column(JSON)
    checklist_json = Column(JSON)
    __table_args__ = (Index("ix_sigev_dedupe", "token_id", "kind", "key", "ts"),
                      Index("ix_sigev_feed", "ts", "importance_score"),)


class SignalEventOutcome(Base):
    """Forward returns after intelligence events (v3 §49 validation)."""
    __tablename__ = "signal_event_outcomes"
    id = Column(Integer, primary_key=True)
    event_id = Column(Integer, ForeignKey("signal_events.id"), index=True)
    token_id = Column(Integer, ForeignKey("tokens.id"), index=True)
    ts = Column(Integer, index=True)
    horizon_secs = Column(Integer)
    kind = Column(String(32), index=True)
    category = Column(String(16))
    importance = Column(String(8))
    fwd_ret_pct = Column(Float)
    max_dd_pct = Column(Float)
    max_up_pct = Column(Float)
    __table_args__ = (Index("ix_evout_unique", "event_id", "horizon_secs"),)


class TokenRiskCheck(Base):
    """Real on-chain risk facts from Solana RPC (Helius). One row per check;
    latest row per token is what the API serves."""
    __tablename__ = "token_risk_checks"
    id = Column(Integer, primary_key=True)
    token_id = Column(Integer, ForeignKey("tokens.id"), index=True, nullable=False)
    checked_at = Column(DateTime(timezone=True), server_default=func.now(), index=True)
    ok = Column(Boolean, default=False)
    error = Column(String(200))
    mint_authority = Column(String(64))      # None/"" = renounced (good)
    freeze_authority = Column(String(64))    # None/"" = none (good)
    supply = Column(Float)
    decimals = Column(Integer)
    top1_pct = Column(Float)
    top10_pct = Column(Float)
    holders_sampled = Column(Integer)
    token_2022 = Column(Boolean, default=False)
    holders_count = Column(Integer)          # REAL distinct holder wallets (None = unknown)
    holders_capped = Column(Boolean, default=False)   # True => "at least" (page cap hit)
    top1_wallet_pct = Column(Float)          # largest REAL wallet % (pools/PDAs/burn excluded)
    top10_wallet_pct = Column(Float)         # top-10 REAL wallets %
    excluded_pct = Column(Float)             # % held by pools / program accounts / burn


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


# ---------------------------------------------------------------- v4 paper trading
class StrategyVersion(Base):
    """Immutable strategy configuration snapshot (v4 §22). Trades link here so
    historical trades always keep the exact config that produced them."""
    __tablename__ = "strategy_versions"
    id = Column(Integer, primary_key=True)
    name = Column(String(64))                # e.g. "Momentum"
    version = Column(String(16))             # e.g. "4.0"
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    config_json = Column(JSON)               # full frozen configuration


class DemoAccount(Base):
    """Virtual-funds demo account (v4 §2). No path from here touches a chain,
    wallet, key or exchange — execution is virtual accounting only (§26)."""
    __tablename__ = "demo_accounts"
    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id"), index=True)
    label = Column(String(64), default="AI Paper Trader")
    starting_cash = Column(Float, default=10000.0)
    cash = Column(Float, default=10000.0)
    active = Column(Boolean, default=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    last_event_id = Column(Integer, default=0)     # idempotent signal cursor (§31)
    settings_json = Column(JSON)                    # §3 user-configurable settings
    strategy_version_id = Column(Integer, ForeignKey("strategy_versions.id"))
    peak_equity = Column(Float, default=10000.0)    # for drawdown tracking
    day_ts = Column(Integer, default=0)             # UTC day bucket for daily loss
    day_realized = Column(Float, default=0.0)
    consec_losses = Column(Integer, default=0)
    consec_wins = Column(Integer, default=0)
    halt_until = Column(Integer)                    # unix ts: no NEW buys until then (auto-resume)
    halt_reason = Column(String(16))                # loss_streak | drawdown


class PaperSignal(Base):
    """AI signal handed to the risk engine (v4 §4). Deduped on
    (account, source_event) — reprocessing the same event can never create a
    second signal/position (§31)."""
    __tablename__ = "paper_signals"
    id = Column(Integer, primary_key=True)
    account_id = Column(Integer, ForeignKey("demo_accounts.id"), index=True)
    token_id = Column(Integer, ForeignKey("tokens.id"), index=True)
    ts = Column(Integer, index=True)
    action = Column(String(8))               # BUY | SELL
    kind = Column(String(32))                # source event kind
    source_event_id = Column(Integer)        # signal_events.id — dedupe anchor
    price = Column(Float)                    # price at signal time
    confidence = Column(Float)               # 0–100
    importance = Column(String(8))
    importance_score = Column(Integer)
    risk_score = Column(Float)
    market_regime = Column(String(24))
    metrics_json = Column(JSON)              # §4 signal factor metrics
    why_json = Column(JSON)                  # §5 structured explanation
    concern = Column(String(300))
    status = Column(String(12), default="pending", index=True)  # approved | rejected
    reject_reason = Column(String(200))
    risk_decision_json = Column(JSON)        # every check the risk engine ran (§6)
    __table_args__ = (
        UniqueConstraint("account_id", "source_event_id", name="uq_signal_source"),
        Index("ix_papersig_feed", "account_id", "ts"),
    )


class PaperPosition(Base):
    """Position lifecycle = the trade ledger (v4 §9)."""
    __tablename__ = "paper_positions"
    id = Column(Integer, primary_key=True)
    account_id = Column(Integer, ForeignKey("demo_accounts.id"), index=True)
    signal_id = Column(Integer, ForeignKey("paper_signals.id"))
    strategy_version_id = Column(Integer, ForeignKey("strategy_versions.id"))
    token_id = Column(Integer, ForeignKey("tokens.id"), index=True)
    status = Column(String(8), default="open", index=True)      # open | closed
    entry_ts = Column(Integer, index=True)
    signal_price = Column(Float)
    entry_price = Column(Float)              # after slippage (§7)
    qty = Column(Float)
    size_usd = Column(Float)                 # notional requested
    cost_usd = Column(Float)                 # size − entry fee
    entry_fee = Column(Float)
    slippage_pct = Column(Float)
    stop_price = Column(Float)
    target_price = Column(Float)
    trail_pct = Column(Float)                # 0/None = trailing disabled
    peak_price = Column(Float)
    ai_confidence = Column(Float)
    risk_score = Column(Float)
    market_regime = Column(String(24))
    metrics_json = Column(JSON)              # signal factors at entry (§13)
    why_json = Column(JSON)                  # decision explanation (§5)
    max_hold_secs = Column(Integer)
    # close side
    exit_ts = Column(Integer)
    exit_price = Column(Float)
    exit_reason = Column(String(32), index=True)   # STOP_LOSS | TAKE_PROFIT | TRAILING_STOP |
                                                   # SELL_SIGNAL | CONFIDENCE_COLLAPSE |
                                                   # RISK_EXIT | LIQUIDITY_EXIT | MAX_HOLD |
                                                   # MANUAL | SLIPPAGE_EXEC
    exit_fee = Column(Float)
    realized_pnl = Column(Float)
    pnl_pct = Column(Float)
    hold_secs = Column(Integer)


class PaperTradeEvent(Base):
    """Chronological event stream per account (v4 §14)."""
    __tablename__ = "paper_trade_events"
    id = Column(Integer, primary_key=True)
    account_id = Column(Integer, ForeignKey("demo_accounts.id"), index=True)
    position_id = Column(Integer, ForeignKey("paper_positions.id"), index=True)
    signal_id = Column(Integer, ForeignKey("paper_signals.id"))
    ts = Column(Integer, index=True)
    kind = Column(String(12), index=True)    # SIGNAL RISK BUY SELL POSITION PROFIT LOSS SYSTEM
    title = Column(String(200))
    detail = Column(String(500))
    data_json = Column(JSON)


class EquitySnapshot(Base):
    """Portfolio equity time series (§16 charts, §30 PerformanceSnapshot)."""
    __tablename__ = "equity_snapshots"
    id = Column(Integer, primary_key=True)
    account_id = Column(Integer, ForeignKey("demo_accounts.id"), index=True)
    ts = Column(Integer, index=True)
    equity = Column(Float)
    cash = Column(Float)
    invested = Column(Float)
    unrealized = Column(Float)
    realized_cum = Column(Float)
    drawdown_pct = Column(Float)
    __table_args__ = (Index("ix_equity_acc_ts", "account_id", "ts", unique=True),)


class BacktestRun(Base):
    """Strategy backtests over recorded signal+price history (§20) and the
    frame series for historical replay (§21)."""
    __tablename__ = "backtest_runs"
    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id"), index=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    mode = Column(String(8), default="backtest")   # backtest | replay
    start_ts = Column(Integer)
    end_ts = Column(Integer)
    params_json = Column(JSON)                      # capital + strategy overrides
    result_json = Column(JSON)                      # summary stats
    frames_json = Column(JSON)                      # replay mode: per-tick frames
    status = Column(String(12), default="done")
    note = Column(String(300))
