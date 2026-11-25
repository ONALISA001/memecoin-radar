"""SOL regime filter, cost-aware sizing, creator-record filter, and their Strategy Lab presets."""
import asyncio
import time

import httpx
import pytest

from app.database import SessionLocal
from app.main import ensure_schema_and_demo_user
from app.models import (CreatorProfile, DemoAccount, PaperPosition, PaperSignal, SignalEvent, Token,
                        TokenLaunchForensics, User)
from app.paper import config as PC
from app.paper import confluence as CF
from app.paper import engine as PE
from app.paper import filters as F
from app.providers import sol_market as SOL

ensure_schema_and_demo_user()
EXEC = PC.DEFAULT_SETTINGS["execution"]


@pytest.fixture(autouse=True)
def clean_sol():
    SOL.STATE.clear(); SOL.STATE.update({"ts": 0, "regime": None})
    yield
    SOL.STATE.clear(); SOL.STATE.update({"ts": 0, "regime": None})


def set_sol(regime, chg=-5.0):
    SOL.STATE.clear()
    SOL.STATE.update({"ts": int(time.time()), "price": 150.0, "avg_24h": 155.0, "chg_24h": chg,
                      "funding_1h_pct": 0.001, "regime": regime})


# ---------------------------------------------------------------- presets
def test_presets_valid_and_rules_off_by_default():
    for k in ("verdict_sol", "verdict_cost", "proven_creator"):
        assert not set(PC.PRESETS[k]["settings"]) - set(PC.DEFAULT_SETTINGS), k
    d = PC.merged_settings({})
    assert d["sol_regime_filter"] == 0 and d["max_round_trip_cost_pct"] == 0 and d["creator_filter"] == 0
    # the experiments differ from AI Verdict Follower by exactly one rule
    base = PC.PRESETS["ai_verdict"]["settings"]
    assert set(PC.PRESETS["verdict_cost"]["settings"].items()) - set(base.items()) == {("max_round_trip_cost_pct", 5.0)}


# ---------------------------------------------------------------- SOL regime
def test_classify_and_candles():
    assert SOL.classify(150, 145, 2.0) == "UP"
    assert SOL.classify(140, 150, -5.0) == "DOWN"
    assert SOL.classify(140, 150, -1.0) == "FLAT"
    assert SOL.from_candles([100.0] * 10, None, 0) is None                      # not enough history
    r = SOL.from_candles([100.0] * 6 + [100 - i for i in range(24)], -0.0001, 0)
    assert r["regime"] == "DOWN" and r["chg_24h"] < -3 and r["funding_1h_pct"] == -0.01


def test_refresh_from_hyperliquid_and_outage_keeps_last_reading():
    closes = [{"c": str(100 + i)} for i in range(30)]                           # steady climb

    def ok(req):
        body = req.read().decode()
        if "candleSnapshot" in body:
            return httpx.Response(200, json=closes)
        return httpx.Response(200, json=[{"universe": [{"name": "BTC"}, {"name": "SOL"}]},
                                         [{"funding": "0.00001"}, {"funding": "0.00002"}]])
    r = asyncio.run(SOL.refresh(force=True, transport=httpx.MockTransport(ok)))
    assert r["regime"] == "UP" and r["price"] == 129 and r["funding_1h_pct"] == 0.002
    r2 = asyncio.run(SOL.refresh(force=True, transport=httpx.MockTransport(lambda q: httpx.Response(503))))
    assert r2 == r                                                               # outage: last reading kept
    SOL.STATE["ts"] = int(time.time()) - SOL.STALE_S - 1
    assert SOL.current() is None                                                 # stale counts as unknown


def test_sol_check_modes():
    assert F.sol_check(1, None)[1] is True                                       # no data: not applied
    set_sol("DOWN")
    assert not F.sol_check(1, SOL.current())[1] and not F.sol_check(2, SOL.current())[1]
    set_sol("FLAT", -1)
    assert F.sol_check(1, SOL.current())[1] and not F.sol_check(2, SOL.current())[1]
    set_sol("UP", 2)
    assert F.sol_check(2, SOL.current())[1]


# ---------------------------------------------------------------- cost
def test_round_trip_estimate_and_capped_size():
    deep = {"liq": 500_000, "price_chg_1h": 0}
    thin = {"liq": 8_000, "price_chg_1h": 40}
    assert 3.6 <= F.round_trip_cost_pct(100, deep, EXEC) < 4.0                  # the fixed ~3.6% floor
    size, est = F.cost_capped_size(500, deep, EXEC, 5.0)
    assert size == 500 and est <= 5.0
    mid = {"liq": 40_000, "price_chg_1h": 0}                                     # $500 here costs ~5.5%
    size, est = F.cost_capped_size(500, mid, EXEC, 5.0)
    assert 25 <= size < 500 and est <= 5.0
    size, est = F.cost_capped_size(500, thin, EXEC, 5.0)
    assert size is None and est > 5.0                                            # even $25 too costly


# ---------------------------------------------------------------- creator
def test_creator_check_modes():
    assert F.creator_check(1, 3, None)[1] and not F.creator_check(2, 3, None)[1]
    bad = {"band": "stay_away", "launches": 9, "total": 12}
    clean = {"band": "clean", "launches": 4, "total": 88}
    young = {"band": "clean", "launches": 1, "total": 90}
    assert not F.creator_check(1, 3, bad)[1] and F.creator_check(1, 3, clean)[1]
    assert F.creator_check(2, 3, clean)[1] and not F.creator_check(2, 3, young)[1]
    assert not F.creator_check(2, 3, {"band": "mixed", "launches": 6, "total": 60})[1]


# ---------------------------------------------------------------- live account
def _mk(**kw):
    m = {"price": 1.0, "liq": 100_000, "mcap": 1_000_000, "volume_24h": 400_000, "price_chg_1h": 8.0,
         "momentum": 72.0, "sellp": 30.0, "liq_chg_3h": 1.0, "risk": 30.0, "regime": "BULLISH",
         "vol_liq_ratio": 4.0, "buy_share_pct": 60.0, "price_chg_15m": 1.0, "price_chg_24h": 10.0}
    m.update(kw)
    return m


def _signal(db, tid, ts):
    db.add(SignalEvent(token_id=tid, ts=ts, kind="momentum_acceleration", key="", title="mom", detail="m",
                       severity="positive", category="momentum", importance="HIGH", importance_score=80,
                       confidence=85, signal_text="m", why_json=[], metrics_json={"momentum": 72},
                       checklist_json=[{"label": "a", "passed": True, "value": "x"},
                                       {"label": "b", "passed": True, "value": "y"}]))
    db.flush()


def _tick(db, acct, now, market):
    PE._now_ts, PE._breadth = now, CF.breadth(market)
    stats = {"signals": 0, "approved": 0, "rejected": 0, "bought": 0, "sold": 0, "accounts": 1}
    PE._run_account(db, acct, now, market, stats)
    db.flush()


def test_live_account_applies_all_three_rules():
    now = int(time.time())
    with SessionLocal() as db:
        usr = User(email="rcc-live@t.dev", password_hash="x", api_token="rcc-live")
        tok = Token(address="RccLiveMint", chain="solana", symbol="RCC")
        db.add_all([usr, tok]); db.flush()
        edge = db.query(SignalEvent.id).order_by(SignalEvent.id.desc()).limit(1).scalar() or 0
        acct = DemoAccount(user_id=usr.id, label="Lab · test", strategy_key="proven_creator",
                           starting_cash=10_000, cash=10_000, peak_equity=10_000, last_event_id=edge,
                           settings_json={"creator_filter": 2, "creator_min_launches": 3,
                                          "sol_regime_filter": 1, "max_round_trip_cost_pct": 5.0})
        db.add(acct); db.flush()
        last = lambda: db.query(PaperSignal).filter_by(account_id=acct.id).order_by(PaperSignal.id.desc()).first()  # noqa: E731
        try:
            # 1) creator unknown => rejected by the creator rule
            _signal(db, tok.id, now); _tick(db, acct, now, {tok.id: _mk()})
            assert last().status == "rejected" and "creator" in last().reject_reason
            # clean creator with 4 launches
            db.add_all([TokenLaunchForensics(token_id=tok.id, status="ok", creator="CreatorWalletX"),
                        CreatorProfile(creator="CreatorWalletX", status="ok", band="clean", launches=4, total=88)])
            db.flush()
            # 2) SOL downtrend => rejected by the SOL rule
            set_sol("DOWN")
            _signal(db, tok.id, now + 60); _tick(db, acct, now + 60, {tok.id: _mk()})
            assert last().status == "rejected" and "SOL" in last().reject_reason
            # 3) SOL up, but a thin fast pool: too costly even at the minimum order
            set_sol("UP", 2)
            _signal(db, tok.id, now + 120); _tick(db, acct, now + 120, {tok.id: _mk(liq=16_000, price_chg_1h=60)})
            assert last().status == "rejected" and "round-trip cost" in last().reject_reason
            # 4) all good => bought, size within the cost cap, SOL regime tagged on the trade
            _signal(db, tok.id, now + 180); _tick(db, acct, now + 180, {tok.id: _mk(liq=40_000)})
            assert last().status == "approved", last().reject_reason
            pos = db.query(PaperPosition).filter_by(account_id=acct.id).one()
            assert pos.metrics_json["sol"]["regime"] == "UP"
            assert pos.metrics_json["est_round_trip_pct"] <= 5.0
            assert F.round_trip_cost_pct(pos.size_usd, _mk(liq=40_000), EXEC) <= 5.0
        finally:
            db.rollback()
