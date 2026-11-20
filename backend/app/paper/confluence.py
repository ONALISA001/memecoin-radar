"""Momentum-confluence rules: one implementation for live paper trading AND backtests.

A *snapshot* is what the market looked like for one coin at decision time:

    momentum          0–100 (market-intel momentum score)
    momentum_hist     recent readings, oldest → newest, newest == momentum
    volume_g_1h       1h volume growth, %
    regime            BULLISH | NEUTRAL | DISTRIBUTION (coin-level confluence)
    sellp             sell pressure 0–100
    price_chg_1h      %
    breadth           share of tracked coins up over 24h (0–1), market-wide

Live trading builds it from the ingestion market map + stored score history; the
backtester rebuilds it from stored 5-min score snapshots at time t (no look-ahead).

The four confluence factors (what the paper analytics showed working):
    strong momentum · accelerating volume · bullish regime · low sell pressure
Every rule is OFF unless its setting is switched on (0 / False = off).
"""
from __future__ import annotations

FACTOR_DEFAULTS = {"momentum": 65.0, "volume": 40.0, "sellp": 40.0}


def _f(v):
    try:
        return None if v is None else float(v)
    except (TypeError, ValueError):
        return None


def factors(s: dict, snap: dict) -> dict[str, bool]:
    """Which of the 4 confluence factors this snapshot meets (thresholds follow the
    account's own filter settings when set, else the analytics defaults)."""
    mom, vol, sp = _f(snap.get("momentum")), _f(snap.get("volume_g_1h")), _f(snap.get("sellp"))
    m_min = float(s.get("min_momentum") or FACTOR_DEFAULTS["momentum"])
    v_min = float(s.get("min_volume_accel_pct") or FACTOR_DEFAULTS["volume"])
    sp_max = float(s.get("max_entry_sell_pressure") or FACTOR_DEFAULTS["sellp"])
    return {"momentum": mom is not None and mom >= m_min,
            "volume": vol is not None and vol >= v_min,
            "regime": snap.get("regime") == "BULLISH",
            "low_sell_pressure": sp is not None and sp < sp_max}


def entry_checks(s: dict, snap: dict) -> list[tuple[str, bool, str]]:
    """[(name, passed, detail)] for every momentum-confluence entry rule switched on."""
    out: list[tuple[str, bool, str]] = []
    mom = _f(snap.get("momentum"))
    hist = [x for x in (snap.get("momentum_hist") or []) if x is not None]
    mn = float(s.get("min_momentum") or 0)
    if mn > 0:
        out.append(("momentum strong", mom is not None and mom >= mn,
                    f"momentum {mom:.0f} vs min {mn:g}" if mom is not None else "no momentum reading"))
        need = int(s.get("momentum_confirm_readings") or 0)
        if need > 1:
            tail = hist[-need:]
            ok = len(tail) == need and all(x >= mn for x in tail)
            out.append(("momentum confirmed", ok,
                        f"last {need} readings {' → '.join(f'{x:.0f}' for x in tail) or 'n/a'} all ≥ {mn:g}"
                        if tail else "not enough readings yet"))
    if s.get("momentum_rising"):
        prev = hist[-2] if len(hist) >= 2 else None
        ok = mom is not None and prev is not None and mom > prev
        out.append(("momentum rising", ok, f"{prev:.0f} → {mom:.0f}" if ok or (prev is not None and mom is not None)
                    else "need two readings"))
    mv = float(s.get("min_volume_accel_pct") or 0)
    if mv > 0:
        v = _f(snap.get("volume_g_1h"))
        out.append(("volume accelerating", v is not None and v >= mv,
                    f"1h volume {v:+.0f}% vs min +{mv:g}%" if v is not None else "no volume data"))
    regime = snap.get("regime") or "NEUTRAL"
    if s.get("require_bullish_regime"):
        out.append(("bullish regime", regime == "BULLISH", f"regime {regime}"))
    elif s.get("block_distribution_regime"):
        out.append(("not distributing", regime != "DISTRIBUTION", f"regime {regime}"))
    sp_max = float(s.get("max_entry_sell_pressure") or 0)
    if sp_max > 0:
        sp = _f(snap.get("sellp"))
        out.append(("low sell pressure", sp is not None and sp < sp_max,
                    f"sell pressure {sp:.0f} vs max {sp_max:g}" if sp is not None else "no data"))
    br_min = float(s.get("min_market_breadth_pct") or 0)
    if br_min > 0:
        br = _f(snap.get("breadth"))
        out.append(("market breadth", br is not None and br * 100 >= br_min,
                    f"{br * 100:.0f}% of coins up 24h vs min {br_min:g}%" if br is not None else "no market data"))
    return out


def size_multiplier(s: dict, snap: dict) -> tuple[float | None, int]:
    """Confluence sizing: (multiplier or None = skip, factors met). Off => (1.0, n)."""
    n = sum(factors(s, snap).values())
    if not s.get("confluence_sizing"):
        return 1.0, n
    min_n = int(s.get("confluence_min_factors") or 3)
    if n < min_n:
        return None, n
    return (1.0 if n >= 4 else float(s.get("confluence_size_3") or 0.6) if n == 3 else 0.35), n


def exit_reason(s: dict, snap: dict, entry_ts: int, now: int, entry_price: float, price: float) -> str | None:
    """Momentum-strategy exits (each OFF unless set): momentum fade, regime flip, time stop."""
    below = float(s.get("momentum_exit_below") or 0)
    mom = _f(snap.get("momentum"))
    if below > 0 and mom is not None and mom < below:
        return "MOMENTUM_EXIT"
    if s.get("regime_exit") and snap.get("regime") == "DISTRIBUTION":
        return "REGIME_EXIT"
    th = float(s.get("time_stop_hours") or 0)
    if th > 0 and now - entry_ts >= th * 3600 and entry_price > 0:
        gain = (price / entry_price - 1) * 100
        if gain < float(s.get("time_stop_min_gain_pct") or 0):
            return "TIME_STOP"
    return None


def breadth(market: dict) -> float | None:
    """Share of coins in the live market map that are up over 24h."""
    ch = [m.get("price_chg_24h") for m in market.values() if isinstance(m, dict) and m.get("price_chg_24h") is not None]
    return (sum(1 for c in ch if c > 0) / len(ch)) if ch else None
