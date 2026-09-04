"""Risk engine (v4 §6). Every signal passes every check here before it may
become a simulated position; the decision record (checks + rejected reason)
is persisted so users can audit 'AI wanted to trade' vs 'risk engine allowed'.
"""
from __future__ import annotations


def evaluation_template() -> dict:
    return {"checks": [], "approved": True, "rejects": []}


def chk(rec: dict, name: str, passed: bool, detail: str, fatal: bool = True):
    rec["checks"].append({"name": name, "passed": bool(passed), "detail": detail})
    if fatal and not passed:
        rec["approved"] = False
        rec["rejects"].append(detail)


def evaluate_signal(settings: dict, sig: dict, portfolio: dict, acct_state: dict) -> dict:
    """sig: {confidence, risk_score, metrics, why_passed, importance}
    portfolio: {equity, cash, invested, open_count, drawdown_pct, prices...}
    acct_state: {day_realized, consecutive_losses, day_realized_limit...}
    Returns decision dict."""
    rec = evaluation_template()
    s = settings

    chk(rec, "trading allowed",
        acct_state.get("trading_allowed", True),
        "trading paused by settings" if not acct_state.get("trading_allowed", True) else "trading enabled")
    conf = float(sig.get("confidence") or 0)
    chk(rec, "minimum confidence", conf >= s["min_confidence"],
        f"confidence {conf:.0f} < required {s['min_confidence']:.0f}")
    m = sig.get("metrics") or {}
    liq = float(m.get("liquidity") or 0)
    chk(rec, "liquidity", liq >= s["min_liquidity_usd"],
        f"liquidity ${liq:,.0f} < minimum ${s['min_liquidity_usd']:,.0f}")
    vol24 = float(m.get("volume_24h") or 0)
    chk(rec, "volume", vol24 >= s["min_volume_24h_usd"],
        f"24h volume ${vol24:,.0f} < minimum ${s['min_volume_24h_usd']:,.0f}")
    mcap = float(m.get("mcap") or 0)
    chk(rec, "market cap", mcap >= s["min_mcap_usd"],
        f"market cap ${mcap:,.0f} < minimum ${s['min_mcap_usd']:,.0f}")
    risk = float(sig.get("risk_score") or 100)
    chk(rec, "risk score", risk <= s["max_risk_score"],
        f"risk {risk:.0f} > max {s['max_risk_score']:.0f}")
    bull = int(sig.get("why_passed") or 0)
    chk(rec, "bullish signals", bull >= s["min_bullish_signals"],
        f"only {bull} supporting checks passed < {s['min_bullish_signals']} required")

    chk(rec, "max open positions", portfolio["open_count"] < s["max_open_positions"],
        f"{portfolio['open_count']} open positions ≥ max {s['max_open_positions']}")
    eq = max(portfolio["equity"], 1e-9)
    exposure_pct = 100.0 * portfolio["invested"] / eq
    chk(rec, "portfolio exposure", exposure_pct < s["max_exposure_pct"],
        f"exposure {exposure_pct:.0f}% ≥ max {s['max_exposure_pct']:.0f}%")
    # daily loss + drawdown + consecutive losses
    day_limit = -s["max_daily_loss_pct"] / 100.0 * eq
    chk(rec, "daily loss limit", acct_state["day_realized"] > day_limit,
        f"daily realized {acct_state['day_realized']:+.2f} breached limit {day_limit:+.2f}")
    chk(rec, "max drawdown", portfolio["drawdown_pct"] < s["max_drawdown_pct"],
        f"drawdown {portfolio['drawdown_pct']:.1f}% ≥ max {s['max_drawdown_pct']:.0f}%")
    chk(rec, "consecutive losses", acct_state["consec_losses"] < s["max_consecutive_losses"],
        f"{acct_state['consec_losses']} consecutive losses ≥ max {s['max_consecutive_losses']}")
    return rec


def position_size(settings: dict, portfolio: dict) -> float:
    """Suggested position size (§4/§7): min of absolute cap, % of equity cap,
    available cash above the reserve requirement."""
    s = settings
    eq = max(portfolio["equity"], 0.0)
    by_abs = float(s["max_position_usd"])
    by_pct = eq * float(s["max_position_pct"]) / 100.0
    reserve = eq * float(s["min_cash_reserve_pct"]) / 100.0
    by_cash = max(0.0, portfolio["cash"] - reserve)
    # respect the exposure ceiling too
    exposure_room = eq * float(s["max_exposure_pct"]) / 100.0 - portfolio["invested"]
    size = min(by_abs, by_pct, by_cash, max(exposure_room, 0.0))
    return round(size, 2)


def loss_cap_size(settings: dict, portfolio: dict, size: float, stop_pct: float) -> float:
    """Shrink size so stop-out loss ≤ max_loss_per_trade (§3 risk mgmt)."""
    max_loss = portfolio["equity"] * float(settings["max_loss_per_trade_pct"]) / 100.0
    if stop_pct <= 0:
        return size
    size_cap = max_loss / (stop_pct / 100.0)
    return round(min(size, size_cap), 2)
