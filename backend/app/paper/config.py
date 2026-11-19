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
    "loss_streak_cooldown_hours": 3.0,  # pause after the streak, then auto-resume (streak reset)
    # max_drawdown_pct pause lasts until the next UTC day, then drawdown is re-measured
    # from current equity (otherwise an idle account could never trade again)
    "max_hold_hours": 24.0,
    "confidence_collapse_pts": 25.0,  # exit if momentum falls this far vs entry
    "sell_pressure_exit": 60.0,       # exit if sell pressure crosses this
    "liquidity_exit_pct": -20.0,      # exit if pool 3h change worse than this
    # ---- strategy-lab rules (all OFF by default: 0 / False / empty = not applied) ----
    "allowed_kinds": [],              # buy-event kinds to act on; [] = all BUY_KINDS
    "min_token_age_h": 0.0,           # skip coins younger than this (most launches die day 1)
    "max_token_age_h": 0.0,
    "fresh_pool_only": False,         # only coins found as fresh pools that PASSED launch checks
    "require_verified_contract": False,   # Helius: mint + freeze authority off, standard SPL
    "max_top10_wallet_pct": 0.0,      # real-wallet concentration cap (needs Helius data)
    "max_vol_liq_ratio": 0.0,         # wash-trading guard: 24h volume / liquidity
    "max_chase_pct_1h": 0.0,          # don't buy after a 1h pump bigger than this
    "min_buy_share_pct": 0.0,         # buys as % of 1h trades
    "breakeven_at_pct": 0.0,          # once up this much, stop moves to break-even (+fees)
    "trail_activate_pct": 0.0,        # trailing stop only arms after this gain (0 = +2%)
    # pullback entries: generated from market data, not from intel events
    "pullback_entries": False,
    "pullback_min_chg_24h": 40.0,     # established uptrend
    "pullback_dip_1h": [-20.0, -6.0], # 1h dip range (%)
    "pullback_min_chg_15m": 0.0,      # bounce has started (15m change ≥ this)
    "pullback_cooldown_h": 6.0,       # max one pullback entry per coin per N hours
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


# ---------------------------------------------------------------- strategy lab
# Each preset runs in its own $10k virtual account next to the main one, on the
# same live market, so strategies are compared under identical conditions.
# Grounding (see PR): most launches die within a day; most >100% runners show
# artificial volume; short-horizon prices tend to revert after aggressive buying;
# memecoin returns are fat-tailed (fixed small take-profits cut the winners).
_EXITS_LET_RUN = {"take_profit_pct": 0.0, "breakeven_at_pct": 20.0,
                  "trailing_stop_pct": 20.0, "trail_activate_pct": 30.0}
_SAFETY = {"require_verified_contract": True, "max_top10_wallet_pct": 35.0, "max_vol_liq_ratio": 15.0}

PRESETS: dict[str, dict] = {
    "baseline": {
        "name": "Baseline (current rules)",
        "summary": "The current MemeAI Momentum rules, unchanged — the yardstick.",
        "settings": {},
    },
    "verified_momentum": {
        "name": "Verified Momentum",
        "summary": "Momentum signals only on coins ≥1h old with clean contracts and spread wallets; "
                   "skips wash-traded volume and 1h pumps >60%; no fixed take-profit — break-even "
                   "at +20%, then a 20% trailing stop lets winners run.",
        "settings": {**_SAFETY, **_EXITS_LET_RUN, "min_token_age_h": 1.0, "max_chase_pct_1h": 60.0,
                     "min_buy_share_pct": 52.0, "stop_loss_pct": 15.0, "max_hold_hours": 12.0,
                     "max_loss_per_trade_pct": 1.0, "min_confidence": 65.0},
    },
    "launch_survivor": {
        "name": "Launch Survivor",
        "summary": "Only fresh launches that passed the launch safety checks AND survived their first "
                   "hour (1–6h old) with real buying; small size, wide stop, trailing exit.",
        "settings": {**_SAFETY, "fresh_pool_only": True, "min_token_age_h": 1.0, "max_token_age_h": 6.0,
                     "min_liquidity_usd": 20000.0, "min_buy_share_pct": 55.0, "max_chase_pct_1h": 80.0,
                     "stop_loss_pct": 20.0, "take_profit_pct": 0.0, "breakeven_at_pct": 25.0,
                     "trailing_stop_pct": 25.0, "trail_activate_pct": 40.0, "max_hold_hours": 6.0,
                     "max_position_usd": 250.0, "max_loss_per_trade_pct": 0.75, "min_confidence": 55.0},
    },
    "pullback": {
        "name": "Trend Pullback",
        "summary": "Doesn't chase: buys coins in a 24h uptrend (+40%) after a 6–20% 1h dip once the "
                   "15m bounce starts, with clean contracts; quick exits (+25% target, 12% stop, 8h max).",
        "settings": {**_SAFETY, "allowed_kinds": ["__none__"], "pullback_entries": True,
                     "min_token_age_h": 6.0, "stop_loss_pct": 12.0, "take_profit_pct": 25.0,
                     "breakeven_at_pct": 12.0, "max_hold_hours": 8.0, "min_confidence": 60.0,
                     "max_loss_per_trade_pct": 1.0},
    },
}


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
