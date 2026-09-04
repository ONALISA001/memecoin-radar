"""Paper-trading configuration (v4 §3). Every knob lives here and is
user-editable via the settings API — no code changes required."""

STRATEGY_NAME = "MemeAI Momentum"
STRATEGY_BASE_VERSION = "4.0"

DEFAULT_SETTINGS: dict = {
    # capital (§2/§3)
    "starting_capital": 10000.0,
    # position management
    "max_position_usd": 500.0,
    "max_position_pct": 5.0,          # % of equity per trade
    "max_open_positions": 8,
    "max_exposure_pct": 40.0,         # % of equity invested max
    "min_cash_reserve_pct": 20.0,     # keep this % of equity in cash
    # strategy filters
    "min_confidence": 60.0,
    "min_liquidity_usd": 15000.0,
    "min_volume_24h_usd": 25000.0,
    "min_mcap_usd": 30000.0,
    "max_risk_score": 70.0,
    "min_bullish_signals": 2,         # supporting checklist checks that passed
    # risk management
    "stop_loss_pct": 12.0,
    "take_profit_pct": 35.0,
    "trailing_stop_pct": 0.0,         # 0 = disabled; e.g. 10 = trail from peak
    "max_daily_loss_pct": 8.0,
    "max_drawdown_pct": 25.0,
    "max_loss_per_trade_pct": 1.5,    # of equity
    "max_consecutive_losses": 5,
    "max_hold_hours": 24.0,
    "confidence_collapse_pts": 25.0,  # exit if momentum falls this far vs entry
    "sell_pressure_exit": 60.0,       # exit if sell pressure crosses this
    "liquidity_exit_pct": -20.0,      # exit if pool 3h change worse than this
    # execution simulation (§7/§8)
    "execution": {
        "fee_pct": 0.3,               # per side
        "base_slippage_pct": 0.35,    # high-liquidity baseline
        "impact_weight": 0.6,         # × (size / pool liquidity) price impact
        "impact_cap_pct": 6.0,
        "vol_weight": 0.02,           # × |1h price change| extra slip
        "vol_cap_pct": 2.0,
        "min_order_usd": 25.0,
    },
}

# buy-side event kinds the strategy converts into BUY signals (v3 categories).
# Important/danger events re-open nothing; sell-side kinds drive risk exits.
BUY_KINDS = (
    "momentum_acceleration", "unusual_volume", "buy_pressure_increasing",
    "holder_growth", "liquidity_increase", "new_high", "whale_accumulation",
    "pattern_emerging",
)
SELL_KINDS = (
    "sell_pressure_increasing", "distribution_warning", "liquidity_drop",
    "pattern_distribution", "momentum_reversal", "elevated_risk",
)


def merged_settings(overrides: dict | None) -> dict:
    """Deep-merge user overrides onto DEFAULT_SETTINGS."""
    import copy
    s = copy.deepcopy(DEFAULT_SETTINGS)
    def rec(dst, src):
        for k, v in (src or {}).items():
            if isinstance(v, dict) and isinstance(dst.get(k), dict):
                rec(dst[k], v)
            elif k in dst:
                dst[k] = v
    rec(s, overrides or {})
    return s
