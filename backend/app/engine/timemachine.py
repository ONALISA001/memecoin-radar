"""Time Machine — replay a trade before you take it.

Ported from the Day Trader skill's trader-timemachine and adapted to memecoins
(24/7 markets, so no sessions or time-of-day pools):

1. **Monte Carlo.** The token's own recent 5-minute price snapshots become a
   pool of returns. Gaps in the data are never bridged: only pairs of buckets
   exactly 5 minutes apart count. Each replay starts at the entry price and
   stitches together random 30-minute blocks of real returns (a block
   bootstrap, so quiet and wild stretches stay together) until the hold time
   runs out or the stop or target is hit.
2. **History.** The same stop/target/hold rule is re-run on every past signal
   of the same kind, on real snapshots. Data gaps are counted, not filled.
3. **Verdict.** Among outcomes that hit a side, how often was it the target,
   against the break-even rate 1 / (1 + R)?

Snapshots are 5-minute closes, so moves inside a bucket are not seen and a
stop can fill worse than shown. This is a simulation from real past prices,
not a prediction.
"""
from __future__ import annotations

import math
from typing import Iterable, Optional, Sequence

import numpy as np

BUCKET_SECS = 300
BLOCK_BARS = 6              # 30-minute bootstrap blocks
MIN_RETURNS = 36            # need >= 3h of contiguous 5-min data
MAX_REPLAYS = 20000
MAX_HOURS = 72.0
GAP_TOLERANCE_SECS = 900    # history walk: a hole longer than this = data gap
FAN_POINTS = 48             # fan-chart samples returned to the UI
CHUNK_CELLS = 400_000
MIN_HISTORY_DECIDED = 10     # past signals that hit a side before a history verdict       # replays x steps simulated at once (bounds memory)


def contiguous_log_returns(points: Sequence[tuple[int, float]]) -> np.ndarray:
    """Log returns between snapshots exactly one bucket apart. Missing or
    non-positive prices break the chain instead of being interpolated."""
    out: list[float] = []
    prev_ts, prev_px = None, None
    for ts, px in points:
        if px is None or px <= 0 or not math.isfinite(px):
            prev_ts, prev_px = None, None
            continue
        if prev_ts is not None and ts - prev_ts == BUCKET_SECS:
            out.append(math.log(px / prev_px))
        prev_ts, prev_px = ts, px
    return np.asarray(out, dtype=float)


def breakeven_rate(r_multiple: float) -> float:
    return 1.0 / (1.0 + r_multiple)


def verdict(target_first: int, stop_first: int, r_multiple: float, min_decided: int = 1) -> dict:
    """One honest line from the decided outcomes (the skill's rule)."""
    decided = target_first + stop_first
    be = breakeven_rate(r_multiple)
    if 0 < decided < min_decided:
        return {"label": "Too few to judge",
                "text": (f"Only {decided} past signal(s) hit a side; at least {min_decided} are "
                         f"needed before reading anything into the split."),
                "win_rate_decided": round(target_first * 100 / decided, 1),
                "breakeven": round(be * 100, 1)}
    if decided == 0:
        return {"label": "Undecided", "text": "Neither side was hit in the hold time, so there is "
                "no target-vs-stop read. The result rides on where the price drifts.",
                "win_rate_decided": None, "breakeven": round(be * 100, 1)}
    wr = target_first / decided
    diff = (wr - be) * 100
    side = "target" if diff > 0 else "stop"
    if abs(diff) < 5:
        label = "Close to a coin flip" if abs(r_multiple - 1) < 1e-6 else "Close to break-even"
    elif abs(diff) < 15:
        label = f"Leans toward the {side}"
    else:
        label = f"Strongly toward the {side}"
    return {"label": label,
            "text": (f"When a side was hit, the target came first {wr * 100:.1f}% of the time. "
                     f"At {r_multiple:.2f}R you need {be * 100:.1f}% to break even."),
            "win_rate_decided": round(wr * 100, 1), "breakeven": round(be * 100, 1)}


def simulate(returns: np.ndarray, stop_pct: float, target_pct: float, steps: int,
             replays: int = 10000, seed: int = 19) -> dict:
    """Block-bootstrap Monte Carlo over close-to-close log returns."""
    n = len(returns)
    rng = np.random.default_rng(seed)
    block = min(BLOCK_BARS, n)
    n_blocks = math.ceil(steps / block)
    rets = returns.astype(np.float32)
    stop_lvl = 1 - stop_pct / 100.0
    tgt_lvl = 1 + target_pct / 100.0
    pick = np.linspace(0, steps - 1, min(FAN_POINTS, steps)).astype(int)
    offs = np.arange(block, dtype=np.int32)

    # Chunked so memory stays ~CHUNK_CELLS floats however long the hold is.
    chunk = max(1, CHUNK_CELLS // steps)
    fs_parts, ft_parts, fin_parts, fan_parts = [], [], [], []
    for lo in range(0, replays, chunk):
        m = min(chunk, replays - lo)
        starts = rng.integers(0, n - block + 1, size=(m, n_blocks), dtype=np.int32)
        idx = (starts[:, :, None] + offs[None, None, :]).reshape(m, -1)[:, :steps]
        paths = np.exp(np.cumsum(rets[idx], axis=1))           # price / entry, per step
        hit_stop = paths <= stop_lvl
        hit_tgt = paths >= tgt_lvl
        fs_parts.append(np.where(hit_stop.any(1), hit_stop.argmax(1), steps))
        ft_parts.append(np.where(hit_tgt.any(1), hit_tgt.argmax(1), steps))
        fin_parts.append(paths[:, -1].astype(float))
        fan_parts.append(paths[:, pick])
    first_stop, first_tgt = np.concatenate(fs_parts), np.concatenate(ft_parts)
    final = np.concatenate(fin_parts)
    # Close-only data: one bucket can't touch both, but keep the skill's rule anyway.
    stop_won = (first_stop <= first_tgt) & (first_stop < steps)
    tgt_won = (first_tgt < first_stop)
    neither = ~(stop_won | tgt_won)

    r_mult = target_pct / stop_pct
    r = np.where(tgt_won, r_mult, np.where(stop_won, -1.0, (final - 1) * 100 / stop_pct))
    exit_ret = np.where(tgt_won, target_pct, np.where(stop_won, -stop_pct, (final - 1) * 100))

    tf, sf, nn = int(tgt_won.sum()), int(stop_won.sum()), int(neither.sum())
    green = int(tgt_won.sum() + (neither & (final > 1)).sum())
    hit_steps = np.minimum(first_stop, first_tgt)[~neither]
    # Fan shows where unexited paths would sit (the price, ignoring exits).
    bands = np.percentile(np.concatenate(fan_parts), [10, 25, 50, 75, 90], axis=0)
    pct = lambda q: round(float(np.percentile(exit_ret, q)), 2)
    return {
        "replays": replays, "seed": seed, "steps": steps,
        "target_first_pct": round(tf * 100 / replays, 1),
        "stop_first_pct": round(sf * 100 / replays, 1),
        "neither_pct": round(nn * 100 / replays, 1),
        "green_pct": round(green * 100 / replays, 1),
        "avg_r": round(float(r.mean()), 3),
        "exit_return_pct": {"p5": pct(5), "p25": pct(25), "p50": pct(50),
                            "p75": pct(75), "p95": pct(95)},
        "median_minutes_to_exit": (int(np.median(hit_steps) + 1) * 5) if len(hit_steps) else None,
        "fan": [{"min": int(s + 1) * 5,
                 **{k: round((float(v) - 1) * 100, 2)
                    for k, v in zip(("p10", "p25", "p50", "p75", "p90"), bands[:, j])}}
                for j, s in enumerate(pick)],
        "verdict": verdict(tf, sf, r_mult),
    }


def replay_history(event_points: Iterable[tuple[int, Sequence[tuple[int, float]]]],
                   stop_pct: float, target_pct: float, horizon_secs: int, now: int) -> dict:
    """Re-run the rule on real snapshots after each past signal.

    event_points: (event_ts, [(ts, price), ...] for that token from event_ts on).
    """
    stop_lvl, tgt_lvl = 1 - stop_pct / 100.0, 1 + target_pct / 100.0
    r_mult = target_pct / stop_pct
    tally = {"target": 0, "stop": 0, "neither": 0, "data_gap": 0, "too_recent": 0, "no_entry": 0}
    rs: list[float] = []
    for ev_ts, pts in event_points:
        if ev_ts + horizon_secs > now:
            tally["too_recent"] += 1
            continue
        pts = [(t, p) for t, p in pts if p and p > 0]
        if not pts or pts[0][0] - ev_ts > 2 * BUCKET_SECS:
            tally["no_entry"] += 1
            continue
        entry_ts, entry = pts[0]
        end = ev_ts + horizon_secs
        prev_t, outcome, last_px = entry_ts, None, entry
        for t, p in pts[1:]:
            if t > end:
                break
            if t - prev_t > GAP_TOLERANCE_SECS:
                outcome = "data_gap"
                break
            prev_t, last_px = t, p
            if p / entry <= stop_lvl:
                outcome = "stop"; break
            if p / entry >= tgt_lvl:
                outcome = "target"; break
        if outcome is None:
            outcome = "neither" if end - prev_t <= GAP_TOLERANCE_SECS else "data_gap"
        tally[outcome] += 1
        if outcome == "target":
            rs.append(r_mult)
        elif outcome == "stop":
            rs.append(-1.0)
        elif outcome == "neither":
            rs.append((last_px / entry - 1) * 100 / stop_pct)
    counted = tally["target"] + tally["stop"] + tally["neither"]
    return {"counted": counted, **tally,
            "target_first_pct": round(tally["target"] * 100 / counted, 1) if counted else None,
            "stop_first_pct": round(tally["stop"] * 100 / counted, 1) if counted else None,
            "avg_r": round(sum(rs) / len(rs), 3) if rs else None,
            "verdict": verdict(tally["target"], tally["stop"], r_mult, MIN_HISTORY_DECIDED)
            if counted else None}


def validate_plan(stop_pct: float, target_pct: float, hours: float, replays: int) -> Optional[str]:
    if not (0.5 <= stop_pct <= 95):
        return "stop_pct must be between 0.5 and 95"
    if not (0.5 <= target_pct <= 1000):
        return "target_pct must be between 0.5 and 1000"
    if not (0.25 <= hours <= MAX_HOURS):
        return f"hours must be between 0.25 and {MAX_HOURS:g}"
    if not (100 <= replays <= MAX_REPLAYS):
        return f"replays must be between 100 and {MAX_REPLAYS}"
    return None
