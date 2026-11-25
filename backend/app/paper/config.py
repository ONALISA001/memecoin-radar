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
    # AI verdict rules (bull / bear / judge verdicts). All OFF by default.
    "verdict_filter": 0,              # 0 off · 1 = only Watch or Strong watch · 2 = Strong watch only
    "verdict_max_age_h": 6.0,         # a verdict older than this doesn't count
    "block_bad_verdicts": 0,          # 1 = never buy a coin rated Avoid / Rug risk (even with filter off)
    "verdict_sizing": 0,              # 1 = size by conviction (Strong watch/high = full size ... low = 40%)
    "verdict_exit": 0,                # 1 = close if the coin is re-judged Avoid / Rug risk after entry
    # momentum-confluence rules (paper/confluence.py). All OFF by default.
    "min_momentum": 0.0,              # buy only if momentum ≥ this (e.g. 65)
    "momentum_confirm_readings": 0,   # …for this many consecutive 5-min readings (anti-chase)
    "momentum_rising": False,         # latest momentum reading above the previous one
    "min_volume_accel_pct": 0.0,      # 1h volume growth ≥ this % (e.g. 40)
    "require_bullish_regime": False,  # coin-level regime must be BULLISH
    "block_distribution_regime": False,  # never buy in a DISTRIBUTION regime
    "max_entry_sell_pressure": 0.0,   # sell pressure must be below this (e.g. 40)
    "min_market_breadth_pct": 0.0,    # % of tracked coins up over 24h (market-wide filter)
    "confluence_sizing": False,       # size by factors met: 4 → full, 3 → 60%, fewer → skip
    "confluence_min_factors": 3,
    "confluence_size_3": 0.6,
    "momentum_exit_below": 0.0,       # close when momentum falls below this (e.g. 50)
    "regime_exit": False,             # close when the coin's regime turns DISTRIBUTION
    "time_stop_hours": 0.0,           # close a trade that hasn't worked after N hours…
    "time_stop_min_gain_pct": 2.0,    # …i.e. isn't up at least this much
    "reentry_cooldown_h": 0.0,        # at most one entry per coin per N hours
    # priority-coin entries: buy coins on the Priority list (every gate passed) when the
    # account's own entry rules also pass. OFF by default.
    "priority_entries": False,
    "priority_window_h": 4,            # look-back for the list's event flow: 1, 2, 4, 6, 12, 24
    "priority_min_score": 55.0,        # priority score needed (0–100)
    "priority_signal_cooldown_h": 2.0,  # at most one priority signal per coin per N hours
    # learn-from-winners gate (paper/winners.py): buy only setups whose most similar past
    # trades — from every paper account — mostly won. OFF by default.
    "learn_from_winners": False,
    "winners_lookback_days": 14.0,     # history window
    "winners_min_history": 30,         # closed trades needed before the gate can approve anything
    "winners_neighbors": 20,           # compare with at most this many most-similar trades
    "winners_min_similar": 8,          # …and need at least this many
    "winners_min_match": 0.6,          # share of known entry facts that must match to count as similar
    "winners_min_win_rate": 55.0,      # % of similar trades that won
    "winners_min_avg_pct": 2.0,        # their average net return (after the ~3.6% round-trip costs)
    "winners_sizing": False,           # size by evidence: ≥70% winners full, ≥60% 75%, else 50%
    # pullback entries: generated from market data, not from intel events
    "pullback_entries": False,
    "pullback_min_chg_24h": 40.0,     # established uptrend
    "pullback_dip_1h": [-20.0, -6.0], # 1h dip range (%)
    "pullback_min_chg_15m": 0.0,      # bounce has started (15m change ≥ this)
    "pullback_cooldown_h": 6.0,       # max one pullback entry per coin per N hours
    # execution simulation (§7/§8)
    "execution": {
        "fee_pct": 0.3,               # per side
        # Realistic memecoin costs (per side): fee 0.3% + slippage 1.0% + half-spread 0.5%
        # = 1.8%, ~3.6% round trip — the cost model measured on Solana memecoins in the
        # solana-social-engine research. The old 0.35% baseline made paper results rosy.
        "base_slippage_pct": 1.0,
        "half_spread_pct": 0.5,
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

# Momentum-confluence family (A–F): built on what the paper analytics showed working —
# strong momentum (≥65), momentum + accelerating volume, bullish regime. Shared:
# let winners run (break-even at +15%, then a 20% trail), exit when momentum fades
# below 50 or the regime turns to distribution, a 3h time stop for trades that never
# worked, no chasing 1h pumps over 60%, one entry per coin per 4h.
_MOMENTUM_EXITS = {"take_profit_pct": 0.0, "breakeven_at_pct": 15.0, "trailing_stop_pct": 20.0,
                   "trail_activate_pct": 25.0, "momentum_exit_below": 50.0, "regime_exit": True,
                   "time_stop_hours": 3.0, "time_stop_min_gain_pct": 2.0, "max_hold_hours": 8.0,
                   "stop_loss_pct": 12.0, "max_loss_per_trade_pct": 1.0}
_NO_CHASE = {"max_chase_pct_1h": 60.0, "reentry_cooldown_h": 4.0, "max_vol_liq_ratio": 15.0,
             "block_distribution_regime": True}
_MOM65 = {"min_momentum": 65.0, "momentum_rising": True, "momentum_confirm_readings": 2}

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
    "ai_verdict": {
        "name": "AI Verdict Follower",
        "summary": "Trades only coins the FOMO verdict rates Watch or Strong watch (verdict under 6h old), "
                   "sizes by the verdict's conviction and sells if the coin is re-judged Avoid / Rug risk; "
                   "clean contracts, let-winners-run exits. Tests whether the verdict adds an edge.",
        "settings": {**_SAFETY, **_EXITS_LET_RUN, "verdict_filter": 1, "block_bad_verdicts": 1,
                     "verdict_sizing": 1, "verdict_exit": 1, "verdict_max_age_h": 6.0,
                     "min_confidence": 55.0, "stop_loss_pct": 15.0, "max_hold_hours": 12.0,
                     "max_loss_per_trade_pct": 1.0},
    },
    "liquid_scalper": {
        "name": "Liquid Scalper",
        "summary": "A different market: only established coins (≥24h old, liquidity ≥$150K, mcap ≥$1M) "
                   "with real buying; quick in and out — +10% target, 6% stop, 3h max hold, no chasing "
                   "1h pumps over 25%. Lower costs per trade, smaller swings.",
        "settings": {"require_verified_contract": True, "max_vol_liq_ratio": 15.0,
                     "min_token_age_h": 24.0, "min_liquidity_usd": 150000.0, "min_mcap_usd": 1_000_000.0,
                     "min_volume_24h_usd": 100000.0, "min_buy_share_pct": 55.0, "max_chase_pct_1h": 25.0,
                     "take_profit_pct": 10.0, "stop_loss_pct": 6.0, "breakeven_at_pct": 5.0,
                     "max_hold_hours": 3.0, "max_risk_score": 50.0, "min_confidence": 60.0,
                     "max_position_usd": 800.0, "max_position_pct": 8.0, "max_open_positions": 6,
                     "max_loss_per_trade_pct": 1.0},
    },
    "mom_a": {
        "name": "A · Momentum 65",
        "summary": "Buys only when momentum is ≥65 for two readings in a row and still rising. "
                   "Momentum exits, no chasing, one entry per coin per 4h.",
        "settings": {**_MOM65, **_MOMENTUM_EXITS, **_NO_CHASE},
    },
    "mom_b": {
        "name": "B · Momentum + Volume",
        "summary": "A, plus 1h volume must be up at least 40% — momentum confirmed by money flowing in.",
        "settings": {**_MOM65, **_MOMENTUM_EXITS, **_NO_CHASE, "min_volume_accel_pct": 40.0},
    },
    "mom_c": {
        "name": "C · Bullish Regime",
        "summary": "Your current rules, but only when the coin's regime is bullish (emerging momentum); "
                   "closes if the regime turns to distribution. Isolates the regime effect.",
        "settings": {"require_bullish_regime": True, "regime_exit": True},
    },
    "mom_d": {
        "name": "D · Full Confluence",
        "summary": "All of A + B + C, sell pressure under 40, and at least half the market up over 24h. "
                   "Fewest trades, highest bar.",
        "settings": {**_MOM65, **_MOMENTUM_EXITS, **_NO_CHASE, "min_volume_accel_pct": 40.0,
                     "require_bullish_regime": True, "max_entry_sell_pressure": 40.0,
                     "min_market_breadth_pct": 50.0},
    },
    "mom_e": {
        "name": "E · Confluence + AI Verdict",
        "summary": "D, plus the FOMO verdict must be Watch or Strong watch; sells if re-judged Avoid / Rug risk.",
        "settings": {**_MOM65, **_MOMENTUM_EXITS, **_NO_CHASE, "min_volume_accel_pct": 40.0,
                     "require_bullish_regime": True, "max_entry_sell_pressure": 40.0,
                     "min_market_breadth_pct": 50.0, "verdict_filter": 1, "block_bad_verdicts": 1,
                     "verdict_exit": 1, "verdict_max_age_h": 6.0},
    },
    "mom_f": {
        "name": "F · Confluence Sizing",
        "summary": "No hard momentum filter: counts the 4 factors (momentum ≥65, volume +40%, bullish regime, "
                   "sell pressure <40) — full size with 4, 60% with 3, skips 2 or fewer. More trades, "
                   "money weighted to the best setups.",
        "settings": {**_MOMENTUM_EXITS, **_NO_CHASE, "confluence_sizing": True, "confluence_min_factors": 3},
    },
}

PRESETS["priority"] = {
    "name": "G · Priority Coins",
    "summary": "Trades only coins on the Priority list (risk <50, liquidity ≥$15K, no distribution, no "
               "Avoid/Rug verdict, momentum or constructive events) with a priority score ≥55, when momentum "
               "is ≥60 and rising. Momentum exits, no chasing, one entry per coin per 4h.",
    "settings": {**_MOMENTUM_EXITS, **_NO_CHASE, "allowed_kinds": ["__none__"], "priority_entries": True,
                 "min_momentum": 60.0, "momentum_rising": True, "min_confidence": 55.0},
}

# Built from the radar's own measured signal outcomes (signal_event_outcomes, Nov 21-23 2026,
# ~1,900 labelled buy-side events). Per event kind, the 4h median return and the share that beat a
# 3.6% round trip: buy_pressure_increasing +10.4% / 57%, new_high +6.1% / 53%,
# momentum_acceleration +2.6% / 49% (break-even after costs), unusual_volume -1.1% / 43% (loses),
# liquidity_increase -13.8% / 25% (loses). Average 4h drawdown was 12-19%, so a 12% stop was
# hit 31-48% of the time: wider stop, smaller size. Average return was far above the median
# (fat right tail), so no fixed take-profit: lock in at +15% and trail. Gains were mostly made
# within 4h (4h and 24h medians are similar), so trades that haven't worked after 2h are cut.
# CAVEATS: under 3 days of data from one market; outcomes of coins that stopped being tracked
# (often rugs) are missing, so these numbers are optimistic. new_token is left out for that
# reason: only 162 of 778 launches have an outcome. Re-check once weeks of history exist.
PRESETS["evidence"] = {
    "name": "H · Measured Signals",
    "summary": "Trades only the signal kinds that beat costs in the radar's own outcome history "
               "(buy pressure rising, new highs, momentum acceleration); skips unusual-volume and "
               "liquidity-increase signals, which lost money. 18% stop with smaller size (avg 4h "
               "drawdown was 12-19%), no fixed target: break-even at +15%, 20% trail from +25%, "
               "2h time stop, 6h max hold. Clean contracts, no chasing.",
    "settings": {**_SAFETY, **_NO_CHASE,
                 "allowed_kinds": ["buy_pressure_increasing", "new_high", "momentum_acceleration"],
                 "min_token_age_h": 0.5, "min_confidence": 60.0,
                 "stop_loss_pct": 18.0, "max_loss_per_trade_pct": 0.75, "max_position_usd": 300.0,
                 "take_profit_pct": 0.0, "breakeven_at_pct": 15.0,
                 "trailing_stop_pct": 20.0, "trail_activate_pct": 25.0,
                 "time_stop_hours": 2.0, "time_stop_min_gain_pct": 3.0, "max_hold_hours": 6.0,
                 "regime_exit": True},
}

# Learns from the whole lab: every candidate is compared with the closed trades of ALL paper
# accounts (main, every preset, challengers) and bought only when its most similar past
# setups mostly won and beat costs on average. Takes every signal source the other accounts
# use, so it sees the same opportunities; the history decides. Needs 30+ closed trades in the
# last 14 days before it can buy anything. Exits copy the let-winners-run family.
PRESETS["winners"] = {
    "name": "W · Learn From Winners",
    "summary": "Studies every closed trade across all accounts and only buys setups whose most similar "
               "past trades mostly won: at least 8 look-alikes (signal, verdict, regime, momentum, 1h move, "
               "liquidity, buy share, sell pressure, confidence), 55%+ of them winners, average +2% after "
               "costs. Sizes by the evidence; clean contracts, no Avoid/Rug coins, let-winners-run exits.",
    "settings": {**_SAFETY, **_EXITS_LET_RUN, "learn_from_winners": True, "winners_sizing": True,
                 "block_bad_verdicts": 1, "priority_entries": True, "pullback_entries": True,
                 "min_confidence": 50.0, "stop_loss_pct": 15.0, "max_hold_hours": 12.0,
                 "max_loss_per_trade_pct": 1.0},
}

# the Strategy Lab's momentum family, compared side by side in the backtester
MOMENTUM_FAMILY = ("baseline", "mom_a", "mom_b", "mom_c", "mom_d", "mom_e", "mom_f")


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
