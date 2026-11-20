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


def _num(v, default=0.0) -> float:
    try:
        return float(v) if v is not None else default
    except (TypeError, ValueError):
        return default


def priority_checks(c: dict, verdict: str | None, ev: dict | None) -> list[dict]:
    """The gates a coin must pass to be a priority coin (all must pass)."""
    ev = ev or {}
    risk, liq = _num(c.get("risk"), 100.0), _num(c.get("liquidity"))
    sellp_band = c.get("sellp_band") or "neutral"
    mom_band = c.get("momentum_band")
    constructive = ev.get("positive", 0) > ev.get("danger", 0) and ev.get("positive", 0) >= 1
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
    ]


def priority_score(c: dict, verdict: str | None, ev: dict | None) -> float:
    """0–100: momentum 35% · low sell pressure 15% · low risk 20% · event flow 20% · verdict bonus."""
    ev = ev or {}
    mom = _num(c.get("momentum"))
    sellp = _num(c.get("sellp"), 50.0)
    risk = _num(c.get("risk"), 100.0)
    flow = min(100.0, ev.get("positive_importance", 0) / 2.0) - min(50.0, ev.get("danger", 0) * 15.0)
    s = 0.35 * mom + 0.15 * (100 - sellp) + 0.20 * (100 - risk) + 0.20 * max(0.0, flow)
    s += VERDICT_BONUS.get(verdict or "", 0.0)
    return round(max(0.0, min(100.0, s)), 1)


def priority_coins(cards: list[dict], verdicts: dict[str, str], events: dict[str, dict],
                   limit: int = 8) -> dict:
    """cards: card() dicts; verdicts: address -> latest rating; events: address ->
    {positive, danger, positive_importance} over the window."""
    passed, near = [], []
    for c in cards:
        v, ev = verdicts.get(c["address"]), events.get(c["address"])
        checks = priority_checks(c, v, ev)
        fails = [k for k in checks if not k["passed"]]
        row = {"address": c["address"], "symbol": c.get("symbol"), "name": c.get("name"),
               "image_url": c.get("image_url"), "chain": c.get("chain"),
               "price": c.get("price"), "price_change_h1": c.get("price_change_h1"),
               "price_change_h24": c.get("price_change_h24"), "liquidity": c.get("liquidity"),
               "volume_1h": c.get("volume_1h"), "momentum": c.get("momentum"),
               "momentum_band": c.get("momentum_band"), "sellp": c.get("sellp"),
               "sellp_band": c.get("sellp_band"), "risk": c.get("risk"), "verdict": v,
               "events_up": (ev or {}).get("positive", 0), "events_down": (ev or {}).get("danger", 0),
               "score": priority_score(c, v, ev), "checks": checks}
        if not fails:
            passed.append(row)
        elif len(fails) == 1 and fails[0]["id"] in ("momentum", "liquidity"):
            row["missed"] = fails[0]["label"]
            near.append(row)
    passed.sort(key=lambda r: r["score"], reverse=True)
    near.sort(key=lambda r: r["score"], reverse=True)
    return {"items": passed[:limit], "near_misses": near[:4], "passed": len(passed),
            "checked": len(cards),
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


def priority_now(db, window: str = "4h", limit: int = 20) -> dict:
    """Priority coins straight from the database (same result as /signals/priority).
    Used by the paper trader's priority-coin entries."""
    from ..routers.data_api import _event_flow, _latest_ratings, card, latest_score_rows
    rows = latest_score_rows(db)
    tokens = [t for _, t in rows]
    flow = _event_flow(db, [t.id for t in tokens], WINDOWS_S.get(window, 14400))
    events = {t.address: flow[t.id] for t in tokens if t.id in flow}
    out = priority_coins([card(t, sc) for sc, t in rows], _latest_ratings(db, tokens), events, limit)
    by_addr = {t.address: t.id for t in tokens}
    for it in out["items"]:
        it["token_id"] = by_addr.get(it["address"])
    return out
