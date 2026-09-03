"""Advanced time-series forensics (Tier-2/3 upgrade).

Pure functions over bucketed series already stored in the DB — no new data
sources required. Detectors:

- CUSUM change-point detection (regime breaks, §20++ style)
- Excitation index (Hawkes-inspired: is activity a self-sustaining cascade
  — organic virality — or a single shill trigger?)
- Cross-lag analysis: does social activity LEAD or LAG price?
- Market regime classification (markup / distribution / accumulation / dead)
"""
from __future__ import annotations

import math
from typing import Sequence


def _pearson(x: Sequence[float], y: Sequence[float]) -> float:
    n = len(x)
    if n < 8:
        return float("nan")
    mx, my = sum(x) / n, sum(y) / n
    sx = math.sqrt(sum((v - mx) ** 2 for v in x))
    sy = math.sqrt(sum((v - my) ** 2 for v in y))
    if sx <= 1e-12 or sy <= 1e-12:
        return 0.0
    return sum((a - mx) * (b - my) for a, b in zip(x, y)) / (sx * sy)


def cusum_changepoints(values: Sequence[float], min_gap: int = 6) -> list[dict]:
    """Online-mean CUSUM over standardized magnitudes. Returns detected
    regime-shift points (index, direction, magnitude in sigma)."""
    xs = [float(v) for v in values]
    if len(xs) < 30:
        return []
    pts: list[dict] = []
    sp = sm = 0.0
    for i in range(12, len(xs)):
        base = xs[max(0, i - 24):i]
        mu = sum(base) / len(base)
        sd = math.sqrt(sum((v - mu) ** 2 for v in base) / max(len(base) - 1, 1)) or 1e-9
        z = (xs[i] - mu) / sd
        sp = max(0.0, sp + z - 0.5)
        sm = min(0.0, sm + z + 0.5)
        if sp > 6 or sm < -6:
            if not pts or i - pts[-1]["i"] >= min_gap:
                pts.append({"i": i, "side": "up" if sp > 6 else "down",
                            "mag": round(abs(sp if sp > 6 else sm), 1)})
            sp = sm = 0.0
    return pts


def excitation_index(series: dict[int, float]) -> dict:
    """Hawkes-flavoured cascade test on a bucket count series.
    High = events cluster and self-propagate (cascade, often manufactured);
    Low = arrivals resemble background Poisson (organic accumulation)."""
    xs = [v for _, v in sorted(series.items()) if True]
    if len(xs) < 24 or sum(xs) <= 0:
        return {"index": None, "burst_share": None, "lag1_corr": None}
    mean = sum(xs) / len(xs)
    total = sum(xs)
    k = max(1, len(xs) // 10)
    burst_share = sum(sorted(xs, reverse=True)[:k]) / total
    lag1 = _pearson(xs[1:], xs[:-1])
    lag1 = 0.0 if math.isnan(lag1) else max(-1.0, min(1.0, lag1))
    # Poisson background ⇒ burst_share ≈ 10%; concentrated ⇒ ≫ 10%
    burst_norm = min(1.0, max(0.0, (burst_share - 0.1) / 0.5))
    corr_norm = (lag1 + 1) / 2
    idx = round(min(1.0, 0.55 * corr_norm + 0.45 * burst_norm), 3)
    return {"index": idx, "burst_share": round(burst_share, 3), "lag1_corr": round(lag1, 3)}


def lead_lag(x: dict[int, float], y: dict[int, float], max_lag: int = 12) -> dict:
    """Cross-correlation between first differences of two series.
    Positive best_lag  ⇒ x LEADS y by that many buckets (x is the driver).
    Negative best_lag  ⇒ x REACTS to y. Used for social-vs-price direction."""
    ts = sorted(set(x) & set(y))
    if len(ts) < 40:
        return {"best_lag": None, "strength": None, "note": "insufficient overlap"}
    dx = [x[ts[i]] - x[ts[i - 1]] for i in range(1, len(ts))]
    dy = [y[ts[i]] - y[ts[i - 1]] for i in range(1, len(ts))]
    best_lag, best_corr = 0, 0.0
    for lag in range(-max_lag, max_lag + 1):
        if lag >= 0:
            a, b = dx[:-lag - 1 or None], dy[lag + 1:]
        else:
            a, b = dx[-lag + 1:], dy[:lag - 1 or None]
        m = min(len(a), len(b))
        if m < 30:
            continue
        c = _pearson(a[:m], b[:m])
        if not math.isnan(c) and abs(c) > abs(best_corr):
            best_corr, best_lag = c, lag
    buckets_to_min = 5
    note = ("social leads price" if best_lag > 0 else
            "price leads social" if best_lag < 0 else "no lead/lag structure")
    return {"best_lag": best_lag, "lead_minutes": best_lag * buckets_to_min,
            "strength": round(best_corr, 3), "note": note}


def classify_regime(price: dict[int, float], volume: dict[int, float],
                    holders: dict[int, float], now: int) -> dict:
    """Rule-based regime from the last ~6h of structure (HMM-lite)."""
    win = 6 * 3600
    pts = [(t, v) for t, v in sorted(price.items()) if now - win <= t <= now]
    vols = [v for t, v in sorted(volume.items()) if now - win <= t <= now]
    hs = [(t, v) for t, v in sorted(holders.items()) if now - win <= t <= now]
    if len(pts) < 20:
        return {"regime": "unknown", "detail": "insufficient history"}
    p0, p1 = pts[0][1], pts[-1][1]
    ret = (p1 / p0 - 1) * 100 if p0 > 0 else 0.0
    half = len(vols) // 2 or 1
    vol_trend = (sum(vols[half:]) / max(half, 1)) / max(sum(vols[:half]) / max(half, 1), 1e-9)
    hg = (hs[-1][1] / hs[0][1] - 1) * 100 if len(hs) > 1 and hs[0][1] > 0 else 0.0
    if ret > 8 and vol_trend >= 1.0 and hg > 1:
        regime, detail = "markup", "price rising with expanding volume and holders"
    elif ret < -4 and vol_trend > 1.2:
        regime, detail = "distribution", "volume expanding while price stalls/falls"
    elif abs(ret) <= 8 and vol_trend < 1.0 and hg > 0.5:
        regime, detail = "accumulation", "quiet price with growing holder base"
    elif sum(vols) < 1 and hg <= 0.5:
        regime, detail = "dormant", "no meaningful activity"
    else:
        regime, detail = "chop", "mixed structure"
    return {"regime": regime, "detail": detail,
            "ret_6h": round(ret, 2), "vol_trend": round(vol_trend, 2), "holder_growth_6h": round(hg, 2)}


def uniformity_cv(values: Sequence[float]) -> float:
    """Coefficient of variation; ~0 ⇒ suspiciously uniform sizes (wash-like)."""
    xs = [v for v in values if v and v > 0]
    if len(xs) < 8:
        return float("nan")
    m = sum(xs) / len(xs)
    sd = math.sqrt(sum((v - m) ** 2 for v in xs) / len(xs))
    return sd / m
