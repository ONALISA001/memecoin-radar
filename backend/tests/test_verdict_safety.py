"""AI verdict safety rails: the gate and the caps decide before (and above) any LLM."""
import copy

from app.engine.risk import classify
from app.engine.verdict import RATINGS, clamp, gate, rules_verdict

CLEAN = {
    "risk_engine": {"hard_fails": [], "band": "LOW", "score": 12, "flags": []},
    "contract": {"verified": True, "mint_authority_active": False, "freeze_authority_active": False},
    "holders": {"top10_wallet_pct": 18.0},
    "exit": {"sell_route": True, "buy_route": True, "round_trip_loss_pct": 2.0},
    "market": {"liquidity_usd": 150_000, "organic_score": 70, "buy_share_1h": 0.6,
               "price_change_1h_pct": 8.0, "holder_growth_pct": 4.0},
    "launch": {"status": "ok", "early_buyers_funded_by_creator": 0, "bundle_pct": 2,
               "sniper_pct": 5, "largest_shared_funder_group": 1},
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


def test_unknown_data_is_review_not_safe():
    assert gate(ev(exit__sell_route=None), False)["rating"] == "REVIEW"
    assert gate(ev(holders__top10_wallet_pct=None), False)["rating"] == "REVIEW"
    assert gate(ev(contract__verified=False), False)["rating"] == "REVIEW"
    assert gate(ev(launch__status=None), launch_expected=True)["rating"] == "REVIEW"


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
