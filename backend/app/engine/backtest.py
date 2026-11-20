"""Backtesting & calibration engine (§21, Tier-4).

    Trades nothing; answers: 'when our signals looked like X historically,
    what happened over the next 1h/4h?' — reported as rates and distributions,
    never as promises. Per spec §21 we measure hit rates, false positives,
    persistence and drawdown — not just profitability.

Two jobs:
1. label_due()  — every tick: attach forward outcomes to score snapshots whose
   horizon has elapsed (lazy labeling against price/liquidity history).
2. calibration_report() — bucketed reliability statistics for the UI.
"""
from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import LiquiditySnapshot, PriceSnapshot, SignalOutcome, TrendScore
from .timeseries import series_list, value_at_or_before

HORIZONS = (3600, 7200, 14400)    # 1h, 2h, 4h
STRIDE = 5                        # label every 5th score snapshot (per token)


def _classify(fwd_ret: float, max_dd: float, liq_collapse: bool) -> str:
    if liq_collapse or (fwd_ret <= -70):
        return "rug"
    if max_dd <= -45:
        return "reversal"
    if fwd_ret >= 10 and max_dd > -30:
        return "continued"
    if fwd_ret <= -10:
        return "faded"
    return "neutral"


def label_due(db: Session, now: int, batch: int = 400) -> int:
    created = 0
    for horizon in HORIZONS:
        rows = db.execute(
            select(TrendScore).where(
                TrendScore.ts <= now - horizon,
                TrendScore.ts % (STRIDE * 300) < 300,
            ).order_by(TrendScore.ts.desc()).limit(batch)
        ).scalars().all()
        for sc in rows:
            exists = db.execute(
                select(SignalOutcome.id).where(
                    SignalOutcome.token_id == sc.token_id,
                    SignalOutcome.ts == sc.ts,
                    SignalOutcome.horizon_secs == horizon)
            ).first()
            if exists:
                continue
            prices = {r.ts: r.price_usd for r in db.execute(
                select(PriceSnapshot).where(
                    PriceSnapshot.token_id == sc.token_id,
                    PriceSnapshot.ts >= sc.ts - 300,
                    PriceSnapshot.ts <= sc.ts + horizon + 300)
                .order_by(PriceSnapshot.ts)).scalars().all()}
            if len(prices) < 4:
                continue
            entry = value_at_or_before(prices, sc.ts)
            exitp = value_at_or_before(prices, sc.ts + horizon)
            if entry <= 0 or exitp <= 0:
                continue
            path = [v for _, v in series_list(prices, sc.ts) if _ <= sc.ts + horizon]
            trough = min(path) if path else entry
            fwd_ret = (exitp / entry - 1) * 100
            max_dd = (trough / entry - 1) * 100
            liq = {r.ts: r.liquidity_usd for r in db.execute(
                select(LiquiditySnapshot).where(
                    LiquiditySnapshot.token_id == sc.token_id,
                    LiquiditySnapshot.ts >= sc.ts - 300,
                    LiquiditySnapshot.ts <= sc.ts + horizon)).scalars().all()}
            liq0 = value_at_or_before(liq, sc.ts)
            liqT = value_at_or_before(liq, sc.ts + horizon)
            collapse = liq0 > 0 and liqT < 0.3 * liq0
            db.add(SignalOutcome(
                token_id=sc.token_id, ts=sc.ts, horizon_secs=horizon,
                trend=sc.trend, fomo=sc.fomo, risk=sc.risk, emerging=sc.emerging,
                kind=sc.kind or "live", fwd_ret_pct=round(fwd_ret, 2),
                max_dd_pct=round(max_dd, 2), outcome=_classify(fwd_ret, max_dd, collapse)))
            created += 1
        db.flush()
    return created


def _bucket_stats(v: list[float]) -> dict:
    if not v:
        return {"n": 0}
    s = sorted(v)
    med = s[len(s) // 2]
    return {"n": len(s), "median": round(med, 2),
            "p25": round(s[int(len(s) * 0.25)], 2), "p75": round(s[int(len(s) * 0.75)], 2)}


def _curve(rows: list[SignalOutcome], attr: str, horizon: int) -> list[dict]:
    edges = [(0, 20), (20, 40), (40, 60), (60, 80), (80, 101)]
    out = []
    for lo, hi in edges:
        seg = [r for r in rows if r.horizon_secs == horizon and
               (getattr(r, attr) or 0) >= lo and (getattr(r, attr) or 0) < hi]
        if not seg:
            out.append({"bucket": f"{lo}-{hi if hi < 101 else 100}", "n": 0})
            continue
        cont = sum(1 for r in seg if r.outcome == "continued")
        rugs = sum(1 for r in seg if r.outcome in ("rug", "reversal"))
        out.append({
            "bucket": f"{lo}-{hi if hi < 101 else 100}",
            "n": len(seg),
            "continuation_rate": round(cont / len(seg) * 100, 1),
            "reversal_rate": round(rugs / len(seg) * 100, 1),
            "median_fwd_ret": _bucket_stats([r.fwd_ret_pct for r in seg]).get("median"),
            "median_max_dd": _bucket_stats([r.max_dd_pct for r in seg]).get("median"),
        })
    return out


def _summary(seg: list[SignalOutcome]) -> dict:
    """Headline numbers for one horizon: how often signals continued / reversed."""
    if not seg:
        return {"n": 0}
    n = len(seg)
    outcomes: dict[str, int] = {}
    for r in seg:
        outcomes[r.outcome or "neutral"] = outcomes.get(r.outcome or "neutral", 0) + 1
    rets = [r.fwd_ret_pct for r in seg if r.fwd_ret_pct is not None]
    dds = [r.max_dd_pct for r in seg if r.max_dd_pct is not None]
    return {"n": n,
            "continuation_rate": round(100 * outcomes.get("continued", 0) / n, 1),
            "reversal_rate": round(100 * (outcomes.get("reversal", 0) + outcomes.get("rug", 0)) / n, 1),
            "median_fwd_ret": _bucket_stats(rets).get("median"),
            "p25_fwd_ret": _bucket_stats(rets).get("p25"), "p75_fwd_ret": _bucket_stats(rets).get("p75"),
            "median_max_dd": _bucket_stats(dds).get("median"),
            "outcomes": outcomes}


RULES = [
    # (name, predicate, outcome that counts as a hit, adverse outcomes)
    ("trend ≥ 70", lambda r: (r.trend or 0) >= 70, "continued", ("rug", "reversal")),
    ("trend ≥ 70 & risk < 50", lambda r: (r.trend or 0) >= 70 and (r.risk or 100) < 50,
     "continued", ("rug", "reversal")),
    ("fomo ≥ 80", lambda r: (r.fomo or 0) >= 80, "continued", ("rug", "reversal")),
    ("fomo 40–80 (building)", lambda r: 40 <= (r.fomo or 0) < 80, "continued", ("rug", "reversal")),
    ("emerging flag", lambda r: bool(r.emerging), "continued", ("rug", "reversal")),
    ("emerging & risk < 50", lambda r: bool(r.emerging) and (r.risk or 100) < 50,
     "continued", ("rug", "reversal")),
    ("risk ≥ 70", lambda r: (r.risk or 0) >= 70, "rug", ("rug", "reversal")),
    ("fomo ≥ 80 & risk ≥ 70", lambda r: (r.fomo or 0) >= 80 and (r.risk or 0) >= 70,
     "rug", ("rug", "reversal")),
]


def calibration_report(db: Session, days: float | None = None, now: int | None = None) -> dict:
    """`days` limits the report to score snapshots from the last N days (None = all)."""
    q = select(SignalOutcome)
    if days:
        import time as _t
        q = q.where(SignalOutcome.ts >= (now or int(_t.time())) - int(days * 86400))
    rows = db.execute(q).scalars().all()
    n = len(rows)
    horizons = {}
    for h in HORIZONS:
        sub = [r for r in rows if r.horizon_secs == h]
        horizons[str(h)] = {
            "trend_curve": _curve(rows, "trend", h),
            "fomo_curve": _curve(rows, "fomo", h),
            "risk_curve": _curve(rows, "risk", h),
            "n": len(sub),
            "summary": _summary(sub),
        }
    rules = []
    for name, fn, good, bad in RULES:
        for h in HORIZONS:
            seg = [r for r in rows if r.horizon_secs == h and fn(r)]
            if not seg:
                continue
            target = sum(1 for r in seg if r.outcome == good) if isinstance(good, str) else 0
            adverse = sum(1 for r in seg if r.outcome in bad)
            if good == "rug":         # risk rules: a "hit" is the adverse outcome they warn about
                metric_val = round(adverse / len(seg) * 100, 1)
            else:
                metric_val = round(target / len(seg) * 100, 1)
            rets = [r.fwd_ret_pct for r in seg if r.fwd_ret_pct is not None]
            rules.append({"rule": name, "horizon_secs": h, "n": len(seg),
                          "hit_rate_pct": metric_val, "target": good,
                          "kind": "risk" if good == "rug" else "signal",
                          "median_fwd_ret": _bucket_stats(rets).get("median"),
                          "adverse_rate_pct": round(adverse / len(seg) * 100, 1)})
    retro = sum(1 for r in rows if r.kind == "retro")
    return {
        "days": days,
        "horizon_list": list(HORIZONS),
        "total_outcomes": n,
        "retro_share": round(retro / n, 3) if n else 0.0,
        "data_sufficiency": ("GOOD" if n >= 400 else "BUILDING" if n >= 80 else "INSUFFICIENT"),
        "horizons": horizons,
        "signal_rules": rules,
        "honesty": ("Statistics are historical relationships over a short, partly simulated "
                    "training window — not predictions. Expect regime dependence and "
                    "selection bias (we track boosted/trending coins). Rates update every tick."),
    }
