"""Derived market views for the dashboard and the intelligence page.

* priority_coins() — coins that PASS a fixed set of quality gates right now (risk, liquidity,
  no distribution, no bad verdict, momentum or constructive events), ranked by a transparent
  priority score. Every gate is returned with its value so the UI can show why a coin passed.
* distribution_watch() — tokens under sell pressure, filterable by momentum band, whale flow,
  liquidity and a look-back window (1h … 24h) with the real price change over that window.

Pure functions over the `card()` dicts used by the data API, so they are unit-testable
without a database. Research views, never buy or sell signals.
"""
from __future__ import annotations

WINDOWS_S = {"1h": 3600, "2h": 7200, "4h": 14400, "6h": 21600, "12h": 43200, "24h": 86400}
MOMENTUM_BANDS = ("extreme", "strong", "developing", "weakening", "low")
DISTRIBUTION_BANDS = ("distribution", "heavy_distribution")
BAD_VERDICTS = ("AVOID", "RUG_RISK")
VERDICT_BONUS = {"STRONG_WATCH": 15.0, "WATCH": 8.0, "NEUTRAL": 0.0}

# priority gates (kept here so tests and the UI legend use the same numbers)
P_MAX_RISK = 50.0
P_MIN_LIQ = 15_000.0
P_MIN_MOMENTUM_BANDS = ("developing", "strong", "extreme")

# red flags: (id, label, score penalty, severe = excludes the coin outright)
CHASE_H1, CHASE_H1_SEVERE = 60.0, 150.0         # % price rise in 1h
WASH_RATIO, WASH_RATIO_SEVERE = 15.0, 30.0      # 24h volume ÷ liquidity
EARLY_MAX_AGE_H = 24.0
EARLY_MIN_LIQ = 8_000.0
EARLY_MIN_SLOPE = 6.0                            # momentum points gained over the last readings


def _num(v, default=0.0) -> float:
    try:
        return float(v) if v is not None else default
    except (TypeError, ValueError):
        return default


def momentum_slope(hist: list[float] | None) -> float | None:
    """Momentum gained over the recent readings (newest − oldest), None without 2+ readings."""
    h = [x for x in (hist or []) if x is not None]
    return None if len(h) < 2 else round(h[-1] - h[0], 1)


def red_flags(c: dict) -> list[dict]:
    """Warning signs that make a pick less likely to work, each with a score penalty.
    Severe ones exclude the coin from the Priority list."""
    out = []
    h1 = _num(c.get("price_change_h1"))
    liq, vol24 = _num(c.get("liquidity")), _num(c.get("volume_24h"))
    ratio = vol24 / liq if liq > 0 else 0.0
    if h1 >= CHASE_H1:
        out.append({"id": "chase", "label": f"already +{h1:.0f}% in 1h (late)", "penalty": 15.0,
                    "severe": h1 >= CHASE_H1_SEVERE})
    if ratio >= WASH_RATIO:
        out.append({"id": "wash", "label": f"volume {ratio:.0f}× liquidity (wash trading?)", "penalty": 12.0,
                    "severe": ratio >= WASH_RATIO_SEVERE})
    if c.get("ath_status") in ("extended", "overheated"):
        out.append({"id": "overheated", "label": f"price {c['ath_status']}", "penalty": 8.0, "severe": False})
    if c.get("ath_status") == "reversal_risk":
        out.append({"id": "reversal", "label": "reversal risk at the high", "penalty": 12.0, "severe": False})
    if c.get("whale_class") in ("distributing", "heavy_distribution"):
        out.append({"id": "whales", "label": "whales selling", "penalty": 12.0,
                    "severe": c.get("whale_class") == "heavy_distribution"})
    org = c.get("organic")
    if org is not None and _num(org) < 40:
        out.append({"id": "manufactured", "label": f"activity looks manufactured ({_num(org):.0f})", "penalty": 10.0,
                    "severe": False})
    bs = c.get("buy_share")
    if bs is not None and _num(bs) < 0.45:
        out.append({"id": "sellers", "label": f"sellers in control ({_num(bs) * 100:.0f}% buys)", "penalty": 8.0,
                    "severe": False})
    if c.get("liq_band") in ("thin", "critical"):
        out.append({"id": "thin", "label": f"liquidity health {c['liq_band']}", "penalty": 6.0, "severe": False})
    return out


def priority_checks(c: dict, verdict: str | None, ev: dict | None) -> list[dict]:
    """The gates a coin must pass to be a priority coin (all must pass)."""
    ev = ev or {}
    risk, liq = _num(c.get("risk"), 100.0), _num(c.get("liquidity"))
    sellp_band = c.get("sellp_band") or "neutral"
    mom_band = c.get("momentum_band")
    constructive = ev.get("positive", 0) > ev.get("danger", 0) and ev.get("positive", 0) >= 1
    severe = [f["label"] for f in red_flags(c) if f["severe"]] if c else []
    return [
        {"id": "risk", "label": f"Risk below {P_MAX_RISK:.0f}", "value": f"{risk:.0f}", "passed": risk < P_MAX_RISK},
        {"id": "liquidity", "label": f"Liquidity ≥ ${P_MIN_LIQ / 1000:.0f}K", "value": f"${liq:,.0f}",
         "passed": liq >= P_MIN_LIQ},
        {"id": "flow", "label": "No distribution", "value": sellp_band.replace("_", " "),
         "passed": sellp_band not in DISTRIBUTION_BANDS},
        {"id": "verdict", "label": "Verdict not Avoid / Rug risk", "value": verdict or "none yet",
         "passed": verdict not in BAD_VERDICTS},
        {"id": "momentum", "label": "Momentum developing+ or constructive events",
         "value": f"{mom_band or '?'} · {ev.get('positive', 0)}↑ {ev.get('danger', 0)}↓",
         "passed": mom_band in P_MIN_MOMENTUM_BANDS or constructive},
        {"id": "red_flags", "label": "No severe red flags", "value": "; ".join(severe) or "none",
         "passed": not severe},
    ]


def score_factors(c: dict, verdict: str | None, ev: dict | None, hist: list[float] | None = None) -> list[dict]:
    """What the priority score is made of: weighted 0–100 sub-scores, verdict bonus, penalties."""
    ev = ev or {}
    clamp = lambda v: max(0.0, min(100.0, v))  # noqa: E731
    mom = _num(c.get("momentum"))
    slope = momentum_slope(hist)
    bs = c.get("buy_share")
    flow = min(100.0, ev.get("positive_importance", 0) / 2.0) - min(50.0, ev.get("danger", 0) * 15.0)
    parts = [
        ("momentum", "Momentum level", 0.25, clamp(mom)),
        ("trend", "Momentum rising", 0.15, 50.0 if slope is None else clamp(50 + slope * 5)),
        ("buyers", "Buyers in control", 0.10, 50.0 if bs is None else clamp((_num(bs) - 0.4) / 0.35 * 100)),
        ("sellp", "Low sell pressure", 0.10, clamp(100 - _num(c.get("sellp"), 50.0))),
        ("volume", "Volume accelerating", 0.10, clamp(_num(c.get("vol_accel_h1")))),
        ("holders", "Holders growing", 0.05, clamp(_num(c.get("holder_growth")) * 10)),
        ("risk", "Low risk", 0.15, clamp(100 - _num(c.get("risk"), 100.0))),
        ("events", "Constructive events", 0.10, clamp(flow)),
    ]
    out = [{"id": k, "label": l, "weight": w, "value": round(v, 1), "points": round(w * v, 1)} for k, l, w, v in parts]
    bonus = VERDICT_BONUS.get(verdict or "", 0.0)
    if bonus:
        out.append({"id": "verdict", "label": f"Verdict {verdict.replace('_', ' ').title()}", "weight": None,
                    "value": None, "points": bonus})
    for f in red_flags(c):
        out.append({"id": f["id"], "label": f["label"], "weight": None, "value": None, "points": -f["penalty"]})
    return out


def priority_score(c: dict, verdict: str | None, ev: dict | None, hist: list[float] | None = None) -> float:
    """0–100 from score_factors(): momentum level 25% · momentum rising 15% · buyers 10% · low sell
    pressure 10% · volume acceleration 10% · holders 5% · low risk 15% · events 10%, plus the
    verdict bonus, minus red-flag penalties."""
    return round(max(0.0, min(100.0, sum(f["points"] for f in score_factors(c, verdict, ev, hist)))), 1)


def early_reasons(c: dict, hist: list[float] | None, ev: dict | None) -> list[str] | None:
    """A coin building momentum BEFORE it meets the full Priority bar: young, momentum climbing
    fast, real buying and accelerating volume, no distribution and no severe red flags.
    Returns the reasons, or None if it isn't an early candidate."""
    ev = ev or {}
    age = c.get("age_hours")
    slope = momentum_slope(hist)
    if age is None or _num(age) > EARLY_MAX_AGE_H or slope is None or slope < EARLY_MIN_SLOPE:
        return None
    if _num(c.get("risk"), 100.0) >= P_MAX_RISK or _num(c.get("liquidity")) < EARLY_MIN_LIQ:
        return None
    if (c.get("sellp_band") or "neutral") in DISTRIBUTION_BANDS or any(f["severe"] for f in red_flags(c)):
        return None
    bs, vol, hg = c.get("buy_share"), _num(c.get("vol_accel_h1")), _num(c.get("holder_growth"))
    confirms = []
    if bs is not None and _num(bs) >= 0.55:
        confirms.append(f"{_num(bs) * 100:.0f}% buys")
    if vol >= 40:
        confirms.append(f"volume +{vol:.0f}%")
    if hg >= 3:
        confirms.append(f"holders +{hg:.1f}%")
    if ev.get("positive", 0) > ev.get("danger", 0):
        confirms.append(f"{ev['positive']} constructive events")
    if len(confirms) < 2:
        return None
    return [f"momentum +{slope:.0f} pts recently", f"{_num(age):.1f}h old"] + confirms


def _row(c: dict, v: str | None, ev: dict | None, hist: list[float] | None) -> dict:
    flags = red_flags(c)
    return {"address": c["address"], "symbol": c.get("symbol"), "name": c.get("name"),
            "image_url": c.get("image_url"), "chain": c.get("chain"),
            "price": c.get("price"), "price_change_h1": c.get("price_change_h1"),
            "price_change_h24": c.get("price_change_h24"), "liquidity": c.get("liquidity"),
            "volume_1h": c.get("volume_1h"), "momentum": c.get("momentum"),
            "momentum_band": c.get("momentum_band"), "momentum_slope": momentum_slope(hist),
            "sellp": c.get("sellp"), "sellp_band": c.get("sellp_band"), "risk": c.get("risk"),
            "age_hours": c.get("age_hours"), "verdict": v,
            "events_up": (ev or {}).get("positive", 0), "events_down": (ev or {}).get("danger", 0),
            "score": priority_score(c, v, ev, hist), "factors": score_factors(c, v, ev, hist),
            "flags": [f["label"] for f in flags]}


def priority_coins(cards: list[dict], verdicts: dict[str, str], events: dict[str, dict],
                   limit: int = 8, hist: dict[str, list[float]] | None = None) -> dict:
    """cards: card() dicts; verdicts: address -> latest rating; events: address ->
    {positive, danger, positive_importance} over the window; hist: address -> recent momentum
    readings (oldest → newest). Returns the passing coins, early candidates and near misses."""
    hist = hist or {}
    passed, near, early = [], [], []
    for c in cards:
        a = c["address"]
        v, ev, h = verdicts.get(a), events.get(a), hist.get(a)
        checks = priority_checks(c, v, ev)
        fails = [k for k in checks if not k["passed"]]
        row = {**_row(c, v, ev, h), "checks": checks}
        if not fails:
            passed.append(row)
            continue
        reasons = early_reasons(c, h, ev)
        if reasons and v not in BAD_VERDICTS:
            early.append({**row, "early_reasons": reasons})
        elif len(fails) == 1 and fails[0]["id"] in ("momentum", "liquidity"):
            row["missed"] = fails[0]["label"]
            near.append(row)
    passed.sort(key=lambda r: r["score"], reverse=True)
    near.sort(key=lambda r: r["score"], reverse=True)
    early.sort(key=lambda r: ((r["momentum_slope"] or 0), r["score"]), reverse=True)
    return {"items": passed[:limit], "early": early[:limit], "near_misses": near[:4],
            "passed": len(passed), "early_count": len(early), "checked": len(cards),
            "gates": [k["label"] for k in priority_checks({}, None, None)]}


def distribution_watch(cards: list[dict], window_change: dict[str, float | None], *,
                       momentum: set[str] | None = None, whale: set[str] | None = None,
                       min_liquidity: float = 3000.0, min_sellp: float = 55.0,
                       sort: str = "sellp", limit: int = 12) -> dict:
    """Tokens under sell pressure. `window_change` = address -> % price change over the
    chosen window (None when the history doesn't reach back that far).

    min_sellp defaults to 55: the sell-pressure engine reports a neutral 50 when it has no
    flow history yet, and that must not flood the watch with every freshly tracked coin."""
    out = []
    for c in cards:
        sellp = _num(c.get("sellp"))
        in_dist = (c.get("sellp_band") in DISTRIBUTION_BANDS or c.get("ath_status") == "reversal_risk"
                   or c.get("whale_class") in ("distributing", "heavy_distribution"))
        if not in_dist or sellp < min_sellp:
            continue
        if _num(c.get("liquidity")) < min_liquidity:
            continue
        if momentum and (c.get("momentum_band") or "") not in momentum:
            continue
        if whale and (c.get("whale_class") or "neutral") not in whale:
            continue
        out.append({**{k: c.get(k) for k in (
            "address", "symbol", "name", "image_url", "chain", "price", "liquidity", "volume_1h",
            "momentum", "momentum_band", "sellp", "sellp_band", "whale_class", "risk",
            "price_change_h1", "price_change_h24", "ath_status")},
            "window_change": window_change.get(c["address"])})
    keys = {
        "sellp": lambda r: _num(r["sellp"]),
        "price_drop": lambda r: -_num(r["window_change"], 0.0),
        "momentum_low": lambda r: -_num(r["momentum"], 100.0),
        "liquidity": lambda r: _num(r["liquidity"]),
        "volume": lambda r: _num(r["volume_1h"]),
    }
    out.sort(key=keys.get(sort, keys["sellp"]), reverse=True)
    return {"items": out[:limit], "total": len(out)}


def momentum_history(db, token_ids: list[int], newest_ts: int, readings: int = 4) -> dict[int, list[float]]:
    """token_id -> its last few momentum readings (oldest → newest), from stored score snapshots.
    Only the momentum field is read (the database extracts it), not the ~11 KB snapshot."""
    from sqlalchemy import select
    from ..models import TrendScore
    if not token_ids:
        return {}
    window = newest_ts - (readings + 1) * 300
    mom = TrendScore.components_json[("summary", "momentum")].as_float()
    try:
        with db.begin_nested():
            rows = db.execute(select(TrendScore.token_id, TrendScore.ts, mom).where(
                TrendScore.token_id.in_(token_ids), TrendScore.ts >= window)
                .order_by(TrendScore.ts.asc())).all()
    except Exception:  # noqa: BLE001  unexpected JSON: rank without the trend rather than fail
        return {}
    out: dict[int, list[float]] = {}
    for tid, _ts, m in rows:
        if m is not None:
            out.setdefault(tid, []).append(float(m))
    return {tid: v[-readings:] for tid, v in out.items()}


def priority_now(db, window: str = "4h", limit: int = 20) -> dict:
    """Priority coins (+ early candidates) straight from the database. Used by /signals/priority,
    the paper trader's priority-coin entries and the pick tracker."""
    from ..routers.data_api import _event_flow, _latest_ratings, card, latest_score_rows
    rows = latest_score_rows(db)
    tokens = [t for _, t in rows]
    flow = _event_flow(db, [t.id for t in tokens], WINDOWS_S.get(window, 14400))
    events = {t.address: flow[t.id] for t in tokens if t.id in flow}
    newest = max((sc.ts for sc, _ in rows), default=0)
    mh = momentum_history(db, [t.id for t in tokens], newest)
    hist = {t.address: mh[t.id] for t in tokens if t.id in mh}
    out = priority_coins([card(t, sc) for sc, t in rows], _latest_ratings(db, tokens), events, limit, hist)
    by_addr = {t.address: t.id for t in tokens}
    for it in out["items"] + out["early"]:
        it["token_id"] = by_addr.get(it["address"])
    return out
