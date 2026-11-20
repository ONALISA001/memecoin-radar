"""Creator track record wired into launch alerts and the risk engine (same line as the verdict caps)."""
from app.engine import risk as R
from app.engine.dev_score import creator_concerns
from app.engine.verdict import clamp
from app.services.launch_alerts import _creator_line, launch_verdict

CLEAN_CR = {"status": "ok", "band": "clean", "total": 82, "dump_tier": "none", "launches": 7,
            "dumps": 0, "traded": 5, "summary": "7 earlier launches; 2 graduated, 1 still alive."}
DUMPER = {**CLEAN_CR, "band": "mixed", "total": 58, "dump_tier": "often", "dumps": 3, "traded": 6,
          "summary": "7 earlier launches; dumped at open in 3 of 6 he traded."}
STAY_AWAY = {**CLEAN_CR, "band": "stay_away", "total": 21, "dump_tier": "systematic", "dumps": 5, "traded": 5}


def facts(creator):
    return {"source": "helius", "top1_wallet_pct": 3.0, "top10_wallet_pct": 15.0,
            "mint_authority": False, "freeze_authority": False, "token_2022": False,
            "exit_buy_route": True, "exit_sell_route": True, "exit_round_trip_loss_pct": 2.0,
            "launch": {"status": "ok", "funded_by_creator": 0, "bundle_pct": 1, "sniper_pct": 2,
                       "shared_funder_wallets": 0},
            "creator": creator}


# ---------------------------------------------------------------- the shared rule
def test_concerns_match_verdict_caps():
    """Disqualifying here <=> the verdict caps the coin at Neutral or worse."""
    base = {"risk_engine": {"hard_fails": [], "band": "LOW", "score": 10, "flags": []},
            "exit": {"round_trip_loss_pct": 1, "buy_route": True}, "market": {"liquidity_usd": 1e6},
            "launch": {"status": "ok", "early_buyers_funded_by_creator": 0, "bundle_pct": 0,
                       "sniper_pct": 0, "largest_shared_funder_group": 0}}
    for cr in (CLEAN_CR, DUMPER, STAY_AWAY, {**CLEAN_CR, "band": "avoid", "dump_tier": "rare"},
               {**CLEAN_CR, "band": "mixed", "dump_tier": "rare"}, {**CLEAN_CR, "band": "new", "dump_tier": None}):
        top = clamp("STRONG_WATCH", {**base, "creator": cr})[0]
        assert bool(creator_concerns(cr)) == (top in ("NEUTRAL", "AVOID")), (cr["band"], cr["dump_tier"], top)


def test_concerns_say_what_he_did():
    assert creator_concerns(DUMPER) == ["creator dumped at open in 3 of 6 earlier launches"]
    assert creator_concerns(None) == [] and creator_concerns(CLEAN_CR) == []


# ---------------------------------------------------------------- launch alerts
def test_dumper_blocks_launch_alert_even_with_mixed_band():
    v, why = launch_verdict(facts(DUMPER))
    assert v == "fail" and "dumped at open" in why[0]
    assert launch_verdict(facts(STAY_AWAY))[0] == "fail"


def test_clean_and_unscored_creators_do_not_block():
    assert launch_verdict(facts(CLEAN_CR)) == ("pass", [])
    assert launch_verdict(facts(None)) == ("pass", [])      # not scored yet: alert goes, marked


def test_dev_line_in_alert():
    assert _creator_line(facts(None)).startswith("⚠️ Dev track record NOT checked")
    assert _creator_line(facts(CLEAN_CR)).startswith("✓ Dev: Clean record 82/100 · 7 earlier launches")
    assert "no earlier launches" in _creator_line(facts({**CLEAN_CR, "band": "new"}))


# ---------------------------------------------------------------- risk engine
class Ctx:          # minimal TokenContext stand-in
    social_proxy, telegram, anomalies = True, {}, {}
    d = {"liq_now": 80_000, "liq_chg_3h": 0, "liq_3h_ago": 80_000, "vol_liq_ratio": 1,
         "price_chg_1h": 5, "buy_share_1h": 0.55, "whale_net_24h": 0, "trades_per_trader": 2,
         "vol24_liq_live": 2, "volume_24h": 100_000, "uniques_1h": 50, "mentions_1h": 0,
         "lowq_24h": 0, "top10_now": 0, "deployer_pct_now": 0}


def _risk(cr):
    f = facts(cr)
    if cr:
        f.update(deployer_prev_launches=cr["launches"], deployer_suspicious=bool(creator_concerns(cr)))
    r = R.calculate_risk(Ctx(), f)
    dep = next(c for c in r["components"] if c["label"] == "Launch & deployer")
    return r, dep["points"]


def test_risk_uses_real_creator_messages_and_spares_clean_devs():
    r, pts = _risk(DUMPER)
    assert pts >= 10 and any("dumped at open in 3 of 6" in f for f in r["flags"])
    assert not any("short-lived" in f for f in r["flags"])            # no canned wording for real data
    _, clean_pts = _risk(CLEAN_CR)
    assert clean_pts == 0                                             # 7 launches, clean: no penalty
    _, none_pts = _risk(None)
    assert none_pts == 0


def test_systematic_dumper_capped_at_avoid_even_when_band_is_mixed():
    base = {"risk_engine": {"hard_fails": [], "band": "LOW", "score": 10, "flags": []},
            "exit": {"round_trip_loss_pct": 1, "buy_route": True}, "market": {"liquidity_usd": 1e6},
            "launch": {"status": "ok", "early_buyers_funded_by_creator": 0, "bundle_pct": 0,
                       "sniper_pct": 0, "largest_shared_funder_group": 0}}
    cr = {**CLEAN_CR, "band": "mixed", "dump_tier": "systematic"}
    assert clamp("STRONG_WATCH", {**base, "creator": cr})[0] == "AVOID"
    assert launch_verdict(facts(cr))[0] == "fail"
