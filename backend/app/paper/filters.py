"""Strategy-lab entry filters and pullback entries (all driven by settings).

Every filter is OFF at its default value, so an account without overrides
trades exactly as before. Each one is grounded in a failure mode we observe in
memecoins: launch-day deaths, unverifiable contracts, concentrated supply,
wash-traded volume and chasing short-lived pumps.
"""
from __future__ import annotations

import time

from ..models import CreatorProfile, Token, TokenLaunchForensics, TokenVerdict
from ..providers import sol_market as SOL
from ..services.contract_facts import contract_facts_for
from . import execution as X

BAD_VERDICTS = ("AVOID", "RUG_RISK")
GOOD_VERDICTS = {1: ("WATCH", "STRONG_WATCH"), 2: ("STRONG_WATCH",)}
# size multiplier by (rating, confidence); never above 1.0 (the risk rules stay in charge)
VERDICT_SIZE = {("STRONG_WATCH", "high"): 1.0, ("STRONG_WATCH", "medium"): 0.85, ("STRONG_WATCH", "low"): 0.6,
                ("WATCH", "high"): 0.8, ("WATCH", "medium"): 0.6, ("WATCH", "low"): 0.4}


# verdict_ai_only: which verdict sources count. Picks (buy filter, sizing) need the AI
# debate itself; safety checks (block, sell on downgrade) also honour the deterministic
# hard-fail gate. Rules verdicts are ignored so they can't override the AI's call.
AI_PICK_SOURCES = ("llm",)
AI_SAFETY_SOURCES = ("llm", "gate")


def verdict_sources(settings: dict, purpose: str) -> tuple[str, ...] | None:
    """None = every source (default). purpose: 'pick' or 'safety'."""
    if not settings.get("verdict_ai_only"):
        return None
    return AI_PICK_SOURCES if purpose == "pick" else AI_SAFETY_SOURCES


def latest_verdict(db, token_id: int, now: int | None = None,
                   sources: tuple[str, ...] | None = None) -> dict | None:
    """The coin's most recent verdict as a small dict (rating, confidence, source, id,
    created_ts, age_h). REVIEW is skipped: it means "not enough data", not a call.
    sources: only verdicts from these sources (None = any)."""
    q = db.query(TokenVerdict).filter(TokenVerdict.token_id == token_id, TokenVerdict.rating != "REVIEW")
    if sources:
        q = q.filter(TokenVerdict.source.in_(sources))
    v = q.order_by(TokenVerdict.id.desc()).first()
    if v is None or v.created_at is None:
        return None
    import datetime as _dt
    c = v.created_at if v.created_at.tzinfo else v.created_at.replace(tzinfo=_dt.timezone.utc)
    ts = int(c.timestamp())
    return {"id": v.id, "rating": v.rating, "confidence": v.confidence or "low", "source": v.source,
            "created_ts": ts, "age_h": round(max(0.0, ((now or time.time()) - ts) / 3600), 2)}


def verdict_size_mult(v: dict | None) -> float:
    if not v:
        return 1.0
    return VERDICT_SIZE.get((v["rating"], v.get("confidence") or "low"), 0.4)


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
    vf, block = int(s.get("verdict_filter") or 0), bool(s.get("block_bad_verdicts"))
    if vf or block:
        max_age = float(s.get("verdict_max_age_h") or 0) or 1e9
        ai_only = bool(s.get("verdict_ai_only"))

        def label(v):
            src = " AI" if ai_only and v["source"] == "llm" else ""
            return f"{v['rating'].replace('_', ' ').title()}{src} ({v['confidence']}, {v['age_h']:.1f}h old)"
        if block:
            v = latest_verdict(db, token.id, now, verdict_sources(s, "safety"))
            fresh = v is not None and v["age_h"] <= max_age
            bad = fresh and v["rating"] in BAD_VERDICTS
            out.append(("AI verdict not negative", not bad, f"rated {label(v)}" if fresh else "no recent verdict"))
        if vf:
            v = latest_verdict(db, token.id, now, verdict_sources(s, "pick"))
            fresh = v is not None and v["age_h"] <= max_age
            want = GOOD_VERDICTS.get(vf, GOOD_VERDICTS[1])
            ok = fresh and v["rating"] in want
            none = "no AI-debate verdict yet" if ai_only else "no verdict yet"
            out.append(("AI verdict", ok, f"rated {label(v)}" if fresh else
                        (none if v is None else f"verdict too old ({v['age_h']:.1f}h > {max_age:g}h)")))
    cf = int(s.get("creator_filter") or 0)
    if cf:
        cr = creator_record(db, token.id)
        out.append(creator_check(cf, int(s.get("creator_min_launches") or 0), cr))
    if s.get("clean_launch"):
        f = db.query(TokenLaunchForensics).filter(TokenLaunchForensics.token_id == token.id).first()
        out.append(clean_launch_check(s, f))
    sf = int(s.get("sol_regime_filter") or 0)
    if sf:
        out.append(sol_check(sf, SOL.current()))
    cap = s.get("max_vol_liq_ratio", 0)
    if cap > 0:
        r = mkt.get("vol_liq_ratio")
        out.append(("volume vs liquidity", r is not None and r <= cap,
                    f"24h volume {r:.1f}x liquidity vs max {cap:g}x (wash-trading guard)"
                    if r is not None else "no data"))
    cap = s.get("max_chase_pct_1h", 0)
    if cap > 0:
        c = mkt.get("price_chg_1h") or 0.0
        out.append(("not chasing", c <= cap, f"1h {c:+.0f}% vs max +{cap:g}%"))
    mn = s.get("min_buy_share_pct", 0)
    if mn > 0:
        b = mkt.get("buy_share_pct")
        out.append(("buyers in control", b is not None and b >= mn,
                    f"buys {b:.0f}% of 1h trades vs min {mn:g}%" if b is not None else "no data"))
    return out


def creator_record(db, token_id: int) -> dict | None:
    """The coin creator's scored track record: {band, launches, total} or None if unknown."""
    f = db.query(TokenLaunchForensics).filter(TokenLaunchForensics.token_id == token_id).first()
    if f is None or not f.creator:
        return None
    p = db.query(CreatorProfile).filter(CreatorProfile.creator == f.creator).first()
    if p is None or p.status not in ("ok", "new"):
        return None
    return {"band": p.band, "launches": p.launches or 0, "total": p.total}


def creator_check(mode: int, min_launches: int, cr: dict | None) -> tuple[str, bool, str]:
    if cr is None:
        # mode 1 only blocks known-bad creators; mode 2 needs a proven record
        return ("creator record", mode == 1, "creator not scored yet")
    band = (cr["band"] or "new").replace("_", " ")
    what = f"{band}, {cr['launches']} earlier launch{'es' if cr['launches'] != 1 else ''}" + (
        f", {cr['total']:.0f}/100" if cr.get("total") is not None else "")
    if mode == 1:
        return ("creator record", cr["band"] not in ("avoid", "stay_away"), what)
    return ("creator record", cr["band"] == "clean" and cr["launches"] >= min_launches,
            f"{what} (need clean with ≥{min_launches})")


def clean_launch_check(s: dict, f) -> tuple[str, bool, str]:
    """Launch fairness from forensics. Unanalysed / failed analyses don't pass: 'clean' must be shown."""
    if f is None or f.status != "ok":
        return ("clean launch", False, f"launch not analysed ({getattr(f, 'status', None) or 'pending'})")
    linked = (f.shared_funder_wallets or 0) + (f.funded_by_creator or 0)
    facts = [("bundled", f.bundle_pct, s["launch_max_bundle_pct"]), ("sniped", f.sniper_pct, s["launch_max_sniper_pct"]),
             ("dev holds", f.dev_holds_pct, s["launch_max_dev_holds_pct"])]
    bad = [f"{n} {v:.1f}% > {cap:g}%" for n, v, cap in facts if (v or 0) > cap]
    if linked > int(s["launch_max_linked_wallets"]):
        bad.append(f"{linked} linked wallets > {int(s['launch_max_linked_wallets'])}")
    if bad:
        return ("clean launch", False, "; ".join(bad))
    return ("clean launch", True, ", ".join(f"{n} {(v or 0):.1f}%" for n, v, _ in facts) + f", {linked} linked")


def sol_check(mode: int, sol: dict | None) -> tuple[str, bool, str]:
    if sol is None:
        return ("SOL regime", True, "SOL data unavailable — not applied")
    what = f"SOL {sol['regime'].lower()} ({sol['chg_24h']:+.1f}% 24h, ${sol['price']:.2f} vs 24h avg ${sol['avg_24h']:.2f})"
    ok = sol["regime"] != "DOWN" if mode == 1 else sol["regime"] == "UP"
    return ("SOL regime", ok, what)


def round_trip_cost_pct(size_usd: float, mkt: dict, exec_cfg: dict) -> float:
    """Estimated % lost to fees and slippage buying `size_usd` and selling straight back."""
    liq, chg = mkt.get("liq") or 0.0, mkt.get("price_chg_1h") or 0.0
    slip = X.slippage_pct(size_usd, liq, chg, "BUY", exec_cfg) + X.slippage_pct(size_usd, liq, chg, "SELL", exec_cfg)
    return round(slip + 2 * float(exec_cfg.get("fee_pct", 0.3)), 2)


def cost_capped_size(size_usd: float, mkt: dict, exec_cfg: dict, cap_pct: float) -> tuple[float | None, float]:
    """Largest size ≤ size_usd whose estimated round trip is ≤ cap_pct (shrinks in 20% steps),
    or None if even the minimum order costs more. Returns (size, estimated cost %)."""
    min_order = float(exec_cfg.get("min_order_usd", 25.0))
    s = size_usd
    while True:
        c = round_trip_cost_pct(s, mkt, exec_cfg)
        if c <= cap_pct:
            return round(s, 2), c
        if s <= min_order:
            return None, c
        s = max(min_order, s * 0.8)


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
