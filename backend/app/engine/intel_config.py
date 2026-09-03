"""Central detection thresholds for the intelligence engine (v3 §35).

Every threshold lives here so the detection model can be tuned/backtested
without touching engine code. False-positive control philosophy: no important
signal from a single weak indicator — thresholds demand magnitude + corroboration.
"""

# activity floors — below these, token is too thin for meaningful flow events
MIN_TXNS_1H_FOR_FLOW = 25
MIN_VOLUME_1H_FOR_FLOW_USD = 2500.0

# momentum acceleration (§6)
MOMENTUM_JUMP_MIN = 12.0            # points vs previous live score (≤30 min)
MOMENTUM_BANDS = (50, 65, 80, 90)   # crossings worth an event

# unusual volume (§7) — relative to the token's OWN 24h baseline
UNUSUAL_VOL_RATIO_MIN = 2.0         # 15m cur vs avg-15m
UNUSUAL_VOL_Z_MIN = 2.5
VOL_ESCALATION_BUCKETS = (2.0, 3.0, 5.0)   # 2x → 3x → 5x ladder (§43)

# flow (§8–9)
BUY_PRESSURE_MIN_SHARE = 0.60
BUY_PRESSURE_DELTA_PP = 0.06        # vs 4h-ago share
SELL_PRESSURE_MAX_SHARE = 0.46
SELL_SURGE_MIN = 1.0                # sell-side 15m count ≥ +100% vs prior 15m

# whales (§10)
WHALE_NET_MIN_OF_VOL = 0.06         # |net flow 24h| ≥ 6% of 24h volume
WHALE_HEAVY_OF_VOL = 0.15

# holders (§11)
HOLDER_GROWTH_MIN = 12.0            # % 1h
HOLDER_WEAKENING_MAX = 2.0          # % 1h while price rising

# liquidity (§12)
LIQ_INCREASE_MIN = 18.0             # % 3h
LIQ_DROP_MIN = -12.0                # % 3h
LIQ_DROP_CRITICAL = -30.0

# elevated structural risk (feed "risk" category)
ELEVATED_RISK_MIN = 80

# data-quality confidence caps by token age (§37)
CONF_CAPS = ((0.5, 58), (1.0, 68), (3.0, 80), (24.0, 95), (1e9, 100))

# cooldowns by kind (seconds) — escalation beats cooldown via ordered keys
COOLDOWNS = {
    "new_token": 86400 * 365,        # once ever
    "momentum_acceleration": 1800,
    "unusual_volume": 3600,
    "buy_pressure_increasing": 3600,
    "sell_pressure_increasing": 3600,
    "whale_accumulation": 3600,
    "whale_distribution": 3600,
    "holder_growth": 3600,
    "holder_weakening": 3600,
    "liquidity_increase": 3600,
    "liquidity_drop": 1800,
    "new_high": 1800,
    "momentum_reversal": 1800,
    "momentum_divergence": 1800,
    "abnormal_volume": 3600,
    "distribution_warning": 1800,
    "elevated_risk": 21600,
    "pattern_emerging": 3600,
    "pattern_distribution": 3600,
}

# chain detection (§16)
CHAIN_WINDOW_SECS = 5400  # 90 minutes
