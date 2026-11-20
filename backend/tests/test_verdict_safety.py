"""AI verdict safety rails: the gate and the caps decide before (and above) any LLM."""
import copy

from app.engine.risk import classify
from app.engine.verdict import RATINGS, clamp, gate, missing_checks, rules_verdict

CLEAN = {
    "risk_engine": {"hard_fails": [], "band": "LOW", "score": 12, "flags": []},
    "contract": {"verified": True, "mint_authority_active": False, "freeze_authority_active": False},
    "holders": {"top10_wallet_pct": 18.0},
    "exit": {"sell_route": True, "buy_route": True, "round_trip_loss_pct": 2.0},
    "market": {"liquidity_usd": 150_000, "organic_score": 70, "buy_share_1h": 0.6,
               "price_change_1h_pct": 8.0, "holder_growth_pct": 4.0},
    "launch": {"status": "ok", "early_buyers_funded_by_creator": 0, "bundle_pct": 2,
               "sniper_pct": 5, "largest_shared_funder_group": 1},
    "creator": {"status": "ok", "band": "clean", "score": 82, "summary": "6 earlier launches"},
}


def ev(**changes):
    e = copy.deepcopy(CLEAN)
    for path, val in changes.items():
        sect, key = path.split("__")
        e[sect][key] = val
    return e


def test_clean_token_passes_gate_and_can_reach_top_rating():
    assert gate(CLEAN, launch_expected=True) is None
    assert clamp("STRONG_WATCH", CLEAN)[0] == "STRONG_WATCH"
    assert rules_verdict(CLEAN)["rating"] == "STRONG_WATCH"


def test_hard_fail_is_rug_risk_without_llm():
    out = gate(ev(risk_engine__hard_fails=["freeze authority active"]), launch_expected=False)
    assert out["rating"] == "RUG_RISK" and out["source"] == "gate"


def test_missing_market_data_is_review():
    assert gate(ev(market__liquidity_usd=None), False)["rating"] == "REVIEW"
    assert gate(ev(risk_engine__band=None), False)["rating"] == "REVIEW"


def test_unknown_onchain_data_gives_provisional_verdict_never_top_rating():
    """Missing on-chain checks no longer park a coin in REVIEW: it gets a verdict, but the
    unknown is treated as risk — capped at WATCH, low confidence, labelled provisional."""
    cases = [ev(exit__sell_route=None), ev(holders__top10_wallet_pct=None),
             ev(contract__verified=False), ev(launch__status=None)]
    for e in cases:
        assert gate(e, launch_expected=True) is None
        assert clamp("STRONG_WATCH", e)[0] == "WATCH"
        missing = missing_checks(e, launch_expected=True)
        assert missing
        out = rules_verdict(e, missing)
        assert out["rating"] == "WATCH" and out["confidence"] == "low" and out["provisional"]
        assert "Provisional" in out["reason"]


def test_rules_verdict_is_decisive_on_weak_flow():
    """Distribution + fading momentum + thin liquidity => Avoid, not a default Neutral."""
    weak = ev(market__sell_pressure="distribution", market__momentum="weakening",
              market__liquidity_health="thin", market__price_change_1h_pct=-25.0,
              market__organic_score=30, market__buy_share_1h=0.3, market__holder_growth_pct=-2.0)
    weak["risk_engine"]["score"] = 45
    assert rules_verdict(weak)["rating"] == "AVOID"
    assert clamp("STRONG_WATCH", ev(market__sell_pressure="heavy_distribution"))[0] == "NEUTRAL"


def test_llm_cannot_rate_above_the_caps():
    cases = [
        (ev(risk_engine__band="HIGH"), "AVOID"),
        (ev(risk_engine__band="ELEVATED"), "NEUTRAL"),
        (ev(launch__early_buyers_funded_by_creator=2), "AVOID"),
        (ev(launch__sniper_pct=40), "NEUTRAL"),
        (ev(exit__round_trip_loss_pct=25), "NEUTRAL"),
        (ev(exit__buy_route=False), "NEUTRAL"),
        (ev(market__liquidity_usd=4_000), "NEUTRAL"),
        (ev(launch__status="error"), "WATCH"),
        (ev(creator__band="stay_away"), "AVOID"),
        (ev(creator__band="avoid"), "NEUTRAL"),
        (ev(creator__band="mixed"), "WATCH"),
        (ev(creator__band="new"), "WATCH"),
        (ev(creator__band=None), "WATCH"),
        (ev(creator__band="mixed", creator__dump_tier="often"), "NEUTRAL"),
        (ev(creator__band="mixed", creator__dump_tier="systematic"), "AVOID"),
    ]
    for e, ceiling in cases:
        got, why = clamp("STRONG_WATCH", e)
        assert got == ceiling, (e, got)
        assert why
        # a rating already below the cap is left alone
        assert clamp("RUG_RISK", e)[0] == "RUG_RISK"


def test_rules_verdict_never_exceeds_caps():
    for band in ["LOW", "MODERATE", "ELEVATED", "HIGH"]:
        r = rules_verdict(ev(risk_engine__band=band))["rating"]
        assert RATINGS.index(r) <= RATINGS.index(clamp("STRONG_WATCH", ev(risk_engine__band=band))[0])


def test_risk_bands():
    assert classify(0) == "LOW" and classify(29.9) == "LOW"
    assert classify(30) == "MODERATE" and classify(50) == "ELEVATED" and classify(70) == "HIGH"


def test_old_evidence_without_creator_section_is_capped_not_crashing():
    e = copy.deepcopy(CLEAN)
    del e["creator"]
    assert clamp("STRONG_WATCH", e)[0] == "WATCH"


def test_dumping_creator_shows_in_rules_reason():
    out = rules_verdict(ev(creator__band="stay_away", creator__score=20, creator__summary="dumped 5 of 6"))
    assert out["rating"] in ("AVOID", "NEUTRAL") and "dumped 5 of 6" in out["reason"]
