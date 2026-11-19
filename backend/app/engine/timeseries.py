"""Time-series primitives (§5, §20, §33).

All metrics live as 5-minute-bucketed snapshots; these helpers compute
windowed sums, growth percentages, EMAs and z-score anomalies from them.
Pure functions, no I/O — easy to unit-test and backtest.
"""
from __future__ import annotations

import math
from typing import Iterable, Sequence

BUCKET = 300


def ssum(series: dict[int, float], start: int, end: int) -> float:
    """Sum of bucket values in [start, end)."""
    return sum(v for t, v in series.items() if start <= t < end)


def has_points(series: dict[int, float], start: int, end: int) -> bool:
    """True if at least one bucket was recorded in [start, end)."""
    return any(start <= t < end for t in series)


def history_hours(series: dict[int, float], now: int) -> float:
    """How far back recorded data reaches (hours)."""
    return max(0.0, (now - min(series)) / 3600) if series else 0.0


def value_at_or_before(series: dict[int, float], ts: int, default: float = 0.0) -> float:
    best_t = max((t for t in series if t <= ts), default=None)
    return series[best_t] if best_t is not None else default


def growth_pct(cur: float, prev: float, cap: float = 2000.0) -> float:
    """Percentage growth with sane handling when the baseline is ~0 (§5)."""
    if prev <= 1e-9:
        return min(cap, cur * 100.0) if cur > 0 else 0.0
    return min(cap, (cur - prev) / prev * 100.0)


def window_growth(series: dict[int, float], now: int, window_secs: int) -> tuple[float, float, float]:
    """(current_window_sum, previous_window_sum, growth_%) — the core
    'acceleration vs baseline' measurement from §5."""
    cur = ssum(series, now - window_secs, now)
    prev = ssum(series, now - 2 * window_secs, now - window_secs)
    if not has_points(series, now - 2 * window_secs, now - window_secs):
        return cur, prev, 0.0       # no baseline recorded yet → unknown, NOT a spike
    return cur, prev, growth_pct(cur, prev)


def level_growth(series: dict[int, float], now: int, window_secs: int) -> tuple[float, float, float]:
    """For cumulative levels (holders, price, liquidity): (now, then, growth%)."""
    cur = value_at_or_before(series, now)
    prev = value_at_or_before(series, now - window_secs)
    if not any(t <= now - window_secs for t in series):
        return cur, prev, 0.0       # history doesn't reach back that far yet
    return cur, prev, growth_pct(cur, prev)


def ema(values: Sequence[float], alpha: float = 0.3) -> float:
    if not values:
        return 0.0
    e = values[0]
    for v in values[1:]:
        e = alpha * v + (1 - alpha) * e
    return e


def rolling_stats(values: Iterable[float]) -> tuple[float, float]:
    vs = list(values)
    if len(vs) < 2:
        return (vs[0] if vs else 0.0), 0.0
    mean = sum(vs) / len(vs)
    var = sum((v - mean) ** 2 for v in vs) / (len(vs) - 1)
    return mean, math.sqrt(var)


def zscore(baseline: Iterable[float], x: float) -> float:
    """Z of the newest value against a trailing baseline (§20 anomaly rule)."""
    mean, sd = rolling_stats(baseline)
    if sd <= 1e-9:
        return 0.0
    return (x - mean) / sd


def sat_log(x: float, scale: float) -> float:
    """Log-saturating map → [0,1). Keeps whales from dominating §5."""
    if x <= 0:
        return 0.0
    return min(1.0, math.log1p(x) / math.log1p(scale))


def series_list(series: dict[int, float], since: int) -> list[tuple[int, float]]:
    return sorted(((t, v) for t, v in series.items() if t >= since), key=lambda p: p[0])
