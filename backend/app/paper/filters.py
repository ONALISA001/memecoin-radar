"""Strategy-lab entry filters and pullback entries (all driven by settings).

Every filter is OFF at its default value, so an account without overrides
trades exactly as before. Each one is grounded in a failure mode we observe in
memecoins: launch-day deaths, unverifiable contracts, concentrated supply,
wash-traded volume and chasing short-lived pumps.
"""
from __future__ import annotations

import time

from ..models import Token
from ..services.contract_facts import contract_facts_for


def token_age_h(token: Token, now: int | None = None) -> float | None:
    if not token or not token.token_created_at:
        return None
    return max(0.0, ((now or time.time()) * 1000 - token.token_created_at) / 3.6e6)


def strategy_checks(db, settings: dict, token: Token, mkt: dict, now: int) -> list[tuple[str, bool, str]]:
    """[(name, passed, detail)] for every rule that is switched on."""
    s, out = settings, []
    age = token_age_h(token, now)
    if s.get("min_token_age_h", 0) > 0:
        out.append(("coin age", age is not None and age >= s["min_token_age_h"],
                    f"{age:.1f}h vs min {s['min_token_age_h']:g}h" if age is not None else "launch time unknown"))
    if s.get("max_token_age_h", 0) > 0:
        out.append(("coin age (max)", age is not None and age <= s["max_token_age_h"],
                    f"{age:.1f}h vs max {s['max_token_age_h']:g}h" if age is not None else "launch time unknown"))
    if s.get("fresh_pool_only"):
        ok = token.discovery_source == "new_pool" and token.launch_alert == "sent"
        out.append(("passed launch checks", ok,
                    "fresh pool, launch checks passed" if ok else
                    f"source={token.discovery_source or 'n/a'}, launch={token.launch_alert or 'pending'}"))
    if s.get("require_verified_contract") or s.get("max_top10_wallet_pct", 0) > 0:
        f = contract_facts_for(db, token)
        if s.get("require_verified_contract"):
            clean = (f.get("source") == "helius" and f.get("mint_authority") is False
                     and f.get("freeze_authority") is False and not f.get("token_2022"))
            out.append(("verified contract", clean,
                        "mint+freeze off (Helius)" if clean else
                        "not verified yet" if f.get("source") != "helius" else "authority active / Token-2022"))
        cap = s.get("max_top10_wallet_pct", 0)
        if cap > 0:
            w10 = f.get("top10_wallet_pct")
            out.append(("wallet concentration", w10 is not None and w10 <= cap,
                        f"top-10 wallets {w10:.0f}% vs max {cap:g}%" if w10 is not None else "no wallet data yet"))
    cap = s.get("max_vol_liq_ratio", 0)
    if cap > 0:
        r = mkt.get("vol_liq_ratio")
        out.append(("volume vs liquidity", r is not None and r <= cap,
                    f"{r:.1f}x vs max {cap:g}x (wash-trading guard)" if r is not None else "no data"))
    cap = s.get("max_chase_pct_1h", 0)
    if cap > 0:
        c = mkt.get("price_chg_1h") or 0.0
        out.append(("not chasing", c <= cap, f"1h {c:+.0f}% vs max +{cap:g}%"))
    mn = s.get("min_buy_share_pct", 0)
    if mn > 0:
        b = mkt.get("buy_share_1h")
        out.append(("buyers in control", b is not None and b >= mn,
                    f"buys {b:.0f}% of 1h trades vs min {mn:g}%" if b is not None else "no data"))
    return out


def pullback_candidate(settings: dict, mkt: dict) -> tuple[bool, list[dict]]:
    """Uptrend + 1h dip + 15m bounce. Returns (all passed, checklist)."""
    lo, hi = settings["pullback_dip_1h"]
    c24, c1, c15 = mkt.get("price_chg_24h"), mkt.get("price_chg_1h"), mkt.get("price_chg_15m")
    checks = [
        ("24h uptrend", c24 is not None and c24 >= settings["pullback_min_chg_24h"],
         f"{(c24 or 0):+.0f}% vs min +{settings['pullback_min_chg_24h']:g}%"),
        ("1h pullback", c1 is not None and lo <= c1 <= hi, f"{(c1 or 0):+.1f}% in [{lo:g}%, {hi:g}%]"),
        ("bounce started", c15 is not None and c15 >= settings["pullback_min_chg_15m"], f"15m {(c15 or 0):+.1f}%"),
        ("liquidity holding", (mkt.get("liq_chg_3h") or 0) >= -5, f"3h {(mkt.get('liq_chg_3h') or 0):+.0f}%"),
        ("sell pressure moderate", (mkt.get("sellp") or 0) < 55, f"{(mkt.get('sellp') or 0):.0f}/100"),
    ]
    return all(ok for _, ok, _ in checks), [{"label": l, "passed": bool(ok), "value": v} for l, ok, v in checks]
