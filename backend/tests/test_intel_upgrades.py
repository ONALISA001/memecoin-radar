"""Priority coins, distribution watch, Strategy Lab review and the new presets."""
from app.paper import config as PC
from app.paper import lab_review as LR
from app.services import market_views as MV


def card(addr, **kw):
    base = {"address": addr, "symbol": addr.upper(), "name": addr, "risk": 20, "liquidity": 80_000,
            "sellp": 30, "sellp_band": "neutral", "momentum": 70, "momentum_band": "strong",
            "whale_class": "neutral", "ath_status": None, "volume_1h": 10_000, "price": 1.0}
    base.update(kw)
    return base


# ---------------------------------------------------------------- priority coins
def test_priority_coin_passes_every_gate_and_is_ranked():
    cards = [card("good"), card("better", momentum=90, momentum_band="extreme"),
             card("risky", risk=75), card("dumping", sellp=70, sellp_band="heavy_distribution"),
             card("illiquid", liquidity=2_000), card("avoided"), card("sleepy", momentum=40, momentum_band="weakening")]
    out = MV.priority_coins(cards, {"avoided": "AVOID", "better": "STRONG_WATCH"}, {}, limit=8)
    addrs = [r["address"] for r in out["items"]]
    assert addrs == ["better", "good"]                      # ranked by score, verdict bonus counts
    assert out["passed"] == 2 and out["checked"] == len(cards)
    near = {r["address"] for r in out["near_misses"]}
    assert near == {"illiquid", "sleepy"}                   # one soft gate failed
    assert all(k["passed"] for k in out["items"][0]["checks"])


def test_constructive_events_can_stand_in_for_momentum():
    c = card("quiet", momentum=45, momentum_band="weakening")
    assert not MV.priority_coins([c], {}, {}, 5)["items"]
    flow = {"quiet": {"positive": 3, "danger": 0, "positive_importance": 180}}
    assert MV.priority_coins([c], {}, flow, 5)["items"][0]["address"] == "quiet"


def test_priority_score_bounds():
    assert 0 <= MV.priority_score(card("x", momentum=100, sellp=0, risk=0), "STRONG_WATCH",
                                  {"positive_importance": 1e6}) <= 100
    assert MV.priority_score({}, None, None) >= 0


# ---------------------------------------------------------------- distribution watch
def test_distribution_watch_filters_and_sorts():
    cards = [card("a", sellp=60, sellp_band="distribution", momentum_band="weakening"),
             card("b", sellp=80, sellp_band="heavy_distribution", momentum_band="low", liquidity=50_000),
             card("c", sellp=55, sellp_band="distribution", momentum_band="strong", liquidity=1_000),
             card("d")]                                     # healthy: never listed
    change = {"a": -12.0, "b": -3.0, "c": -40.0}
    out = MV.distribution_watch(cards, change)
    assert [r["address"] for r in out["items"]] == ["b", "a"]   # c fails min liquidity
    assert out["items"][1]["window_change"] == -12.0
    out = MV.distribution_watch(cards, change, sort="price_drop")
    assert [r["address"] for r in out["items"]] == ["a", "b"]
    out = MV.distribution_watch(cards, change, momentum={"low"})
    assert [r["address"] for r in out["items"]] == ["b"]
    out = MV.distribution_watch(cards, change, min_liquidity=0, momentum={"strong"})
    assert [r["address"] for r in out["items"]] == ["c"]


def test_windows_include_2h_to_24h():
    assert {"1h", "2h", "4h", "6h", "12h", "24h"} <= set(MV.WINDOWS_S)
    assert MV.WINDOWS_S["2h"] == 7200 and MV.WINDOWS_S["4h"] == 14400


# ---------------------------------------------------------------- strategy lab
def test_two_new_lab_accounts_with_distinct_rules():
    assert {"ai_verdict", "liquid_scalper"} <= set(PC.PRESETS)
    ai = PC.merged_settings(PC.PRESETS["ai_verdict"]["settings"])
    assert ai["verdict_filter"] == 1 and ai["block_bad_verdicts"] and ai["verdict_exit"] and ai["verdict_sizing"]
    sc = PC.merged_settings(PC.PRESETS["liquid_scalper"]["settings"])
    assert sc["min_liquidity_usd"] >= 150_000 and sc["take_profit_pct"] == 10 and sc["max_hold_hours"] == 3
    # every preset key is a real setting (merged_settings silently drops unknown keys)
    for key, p in PC.PRESETS.items():
        unknown = set(p["settings"]) - set(PC.DEFAULT_SETTINGS)
        assert not unknown, (key, unknown)


def row(key, trades, ret, pf, dd, edge=None, win=50.0, exits=None):
    return {"key": key, "name": key, "running": True, "trades": trades, "return_pct": ret,
            "profit_factor": pf, "max_drawdown_pct": dd, "edge": edge, "win_rate_pct": win,
            "exits": exits or {}}


def test_lab_review_ranks_and_compares_ai_with_baseline():
    lab = {"presets": [row("baseline", 40, -4.0, 0.7, 12.0, "negative", 38.0),
                       row("ai_verdict", 35, 6.0, 1.6, 5.0, "positive", 55.0,
                           {"TRAILING_STOP": 8, "TAKE_PROFIT": 4, "STOP_LOSS": 6}),
                       row("liquid_scalper", 3, 1.0, 2.0, 1.0),
                       {"key": "pullback", "name": "pullback", "running": False}],
           "main": row("main", 12, 0.5, 1.05, 4.0, "unclear")}
    rev = LR.review(lab)
    assert rev["available"]
    st = {s["key"]: s for s in rev["strategies"]}
    assert "pullback" not in st                              # not running => not reviewed
    assert st["ai_verdict"]["status"] == "proven_edge" and st["ai_verdict"]["rank"] == 1
    assert st["baseline"]["status"] == "losing"
    assert st["liquid_scalper"]["status"] == "too_early"
    assert rev["head_to_head"]["return_diff_pp"] == 10.0
    assert rev["head_to_head"]["read"] == "the verdict filter is adding value so far"
    assert "proven edge" in rev["verdict"]


def test_lab_review_without_lab():
    assert LR.review({"presets": [], "main": None})["available"] is False
    early = LR.review({"presets": [row("baseline", 2, 1.0, None, 0.5)], "main": None})
    assert early["available"] and "Too early" in early["verdict"]
