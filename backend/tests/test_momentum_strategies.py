"""Momentum-confluence strategies (A–F): the shared rules, live trading, and the backtester."""
import datetime as dt
import time

import pytest

from app.database import SessionLocal
from app.main import ensure_schema_and_demo_user
from app.models import (DemoAccount, PaperPosition, PaperSignal, PaperTradeEvent, PriceSnapshot, SignalEvent,
                        Token, TokenVerdict, TrendScore, User)
from app.paper import backtest as BT
from app.paper import config as PC
from app.paper import confluence as CF
from app.paper import engine as PE

ensure_schema_and_demo_user()


def snap(**kw):
    s = {"momentum": 72.0, "momentum_hist": [66.0, 70.0, 72.0], "volume_g_1h": 55.0, "regime": "BULLISH",
         "sellp": 30.0, "price_chg_1h": 8.0, "breadth": 0.6}
    s.update(kw)
    return s


def S(key=None, **kw):
    return PC.merged_settings({**(PC.PRESETS[key]["settings"] if key else {}), **kw})


# ---------------------------------------------------------------- shared rules
def test_rules_are_off_by_default():
    assert CF.entry_checks(PC.merged_settings({}), snap(momentum=10, regime="DISTRIBUTION")) == []
    assert CF.size_multiplier(PC.merged_settings({}), snap())[0] == 1.0
    assert CF.exit_reason(PC.merged_settings({}), snap(momentum=5, regime="DISTRIBUTION"), 0, 99999, 1, 0.5) is None


def test_momentum_entry_rules():
    a = S("mom_a")
    assert all(ok for _, ok, _ in CF.entry_checks(a, snap()))
    names = lambda s, sn: {n for n, ok, _ in CF.entry_checks(s, sn) if not ok}  # noqa: E731
    assert "momentum strong" in names(a, snap(momentum=60, momentum_hist=[58, 60]))
    assert "momentum confirmed" in names(a, snap(momentum_hist=[50, 72]))          # spike, not sustained
    assert "momentum rising" in names(a, snap(momentum=70, momentum_hist=[72, 70]))
    assert "volume accelerating" in names(S("mom_b"), snap(volume_g_1h=10))
    assert "bullish regime" in names(S("mom_c"), snap(regime="NEUTRAL"))
    assert "not distributing" in names(a, snap(regime="DISTRIBUTION"))
    d = S("mom_d")
    assert "low sell pressure" in names(d, snap(sellp=55))
    assert "market breadth" in names(d, snap(breadth=0.3))
    assert not names(d, snap())


def test_confluence_sizing():
    f = S("mom_f")
    assert CF.size_multiplier(f, snap()) == (1.0, 4)
    assert CF.size_multiplier(f, snap(sellp=60)) == (0.6, 3)
    assert CF.size_multiplier(f, snap(sellp=60, regime="NEUTRAL"))[0] is None   # 2 factors => skip


def test_momentum_exits():
    a = S("mom_a")
    assert CF.exit_reason(a, snap(momentum=45), 0, 600, 1.0, 1.1) == "MOMENTUM_EXIT"
    assert CF.exit_reason(a, snap(regime="DISTRIBUTION"), 0, 600, 1.0, 1.1) == "REGIME_EXIT"
    assert CF.exit_reason(a, snap(), 0, 3 * 3600, 1.0, 1.01) == "TIME_STOP"     # +1% after 3h
    assert CF.exit_reason(a, snap(), 0, 3 * 3600, 1.0, 1.10) is None            # working: keep
    assert CF.breadth({1: {"price_chg_24h": 5}, 2: {"price_chg_24h": -3}, 3: {}}) == 0.5


def test_family_presets_are_valid():
    assert set(PC.MOMENTUM_FAMILY) <= set(PC.PRESETS)
    for k in PC.MOMENTUM_FAMILY:
        assert not set(PC.PRESETS[k]["settings"]) - set(PC.DEFAULT_SETTINGS), k
    assert S("mom_a")["take_profit_pct"] == 0 and S("mom_a")["breakeven_at_pct"] == 15


# ---------------------------------------------------------------- live trading
def _mk(**kw):
    m = {"price": 1.0, "liq": 100_000, "mcap": 1_000_000, "volume_24h": 400_000, "price_chg_1h": 8.0,
         "momentum": 72.0, "sellp": 30.0, "liq_chg_3h": 1.0, "risk": 30.0, "regime": "BULLISH",
         "vol_liq_ratio": 4.0, "buy_share_pct": 60.0, "price_chg_15m": 1.0, "price_chg_24h": 10.0,
         "volume_g_1h": 55.0}
    m.update(kw)
    return m


def _event(db, tid, ts):
    e = SignalEvent(token_id=tid, ts=ts, kind="momentum_acceleration", key="", title="mom", detail="m",
                    severity="positive", category="momentum", importance="HIGH", importance_score=80,
                    confidence=85, signal_text="m", why_json=[],
                    metrics_json={"momentum": 72, "liquidity": 100_000, "volume_24h": 400_000, "mcap": 1_000_000},
                    checklist_json=[{"label": "a", "passed": True, "value": "x"},
                                    {"label": "b", "passed": True, "value": "y"}])
    db.add(e)
    db.flush()
    return e


def _tick(db, acct, now, market):
    PE._now_ts, PE._breadth = now, CF.breadth(market)
    stats = {"signals": 0, "approved": 0, "rejected": 0, "bought": 0, "sold": 0, "accounts": 1}
    PE._run_account(db, acct, now, market, stats)
    db.flush()
    return stats


def test_live_momentum_account_entry_sizing_exit_and_cooldown():
    now = int(time.time())
    with SessionLocal() as db:
        usr = User(email="mom-live@t.dev", password_hash="x", api_token="mom-live")
        tok = Token(address="MomLiveMint", chain="solana", symbol="MOML")
        db.add_all([usr, tok]); db.flush()
        for i, m in enumerate((66.0, 70.0)):          # two earlier readings, both ≥65
            db.add(TrendScore(token_id=tok.id, ts=now - 600 + 300 * i, kind="live", trend=50, risk=30,
                              components_json={"summary": {"momentum": m}}))
        edge = db.query(SignalEvent.id).order_by(SignalEvent.id.desc()).limit(1).scalar() or 0
        acct = DemoAccount(user_id=usr.id, label="Lab · B", strategy_key="mom_b", starting_cash=10_000,
                           cash=10_000, peak_equity=10_000, settings_json=dict(PC.PRESETS["mom_b"]["settings"]),
                           last_event_id=edge)
        db.add(acct); db.flush()
        try:
            # weak volume => rejected with the rule named
            _event(db, tok.id, now)
            st = _tick(db, acct, now, {tok.id: _mk(volume_g_1h=10.0)})
            sig = db.query(PaperSignal).filter_by(account_id=acct.id).one()
            assert st["rejected"] == 1 and "volume accelerating" in sig.reject_reason
            # strong volume => bought, tagged with its confluence factors, no fixed target
            _event(db, tok.id, now + 60)
            st = _tick(db, acct, now + 60, {tok.id: _mk()})
            pos = db.query(PaperPosition).filter_by(account_id=acct.id).one()
            assert st["bought"] == 1 and pos.target_price is None
            assert pos.metrics_json["confluence_factors"] == 4
            # momentum fades below 50 => momentum exit
            _tick(db, acct, now + 900, {tok.id: _mk(momentum=45.0, price=1.05)})
            assert pos.status == "closed" and pos.exit_reason == "MOMENTUM_EXIT"
            # re-entry inside the 4h cooldown is refused
            _event(db, tok.id, now + 1200)
            _tick(db, acct, now + 1200, {tok.id: _mk()})
            last = db.query(PaperSignal).filter_by(account_id=acct.id).order_by(PaperSignal.id.desc()).first()
            assert last.status == "rejected" and "re-entry cooldown" in last.reject_reason
        finally:
            db.rollback()


# ---------------------------------------------------------------- backtester
T0 = 1_700_000_000        # a fixed past window, isolated from other tests' data


@pytest.fixture(scope="module")
def history():
    """S: strong, rising momentum, bullish, volume up, price climbing then momentum fades at +2h.
       W: weak momentum, neutral regime, flat price. One momentum event each at T0."""
    with SessionLocal() as db:
        s = Token(address="BtStrongMint", chain="solana", symbol="BTS")
        w = Token(address="BtWeakMint", chain="solana", symbol="BTW")
        db.add_all([s, w]); db.flush()
        for i in range(-12, 12 * 6 + 1):              # T0-1h … T0+6h, 5-min buckets
            ts = T0 + 300 * i
            strong = i <= 24                           # until T0+2h
            db.add(TrendScore(token_id=s.id, ts=ts, kind="live", trend=60, risk=30, components_json={"summary": {
                "momentum": 66 + 0.1 * (i + 12) if strong else 40.0, "sellp": 30.0,
                "confluence": "emerging_momentum" if strong else None, "volume_g_1h": 55.0,
                "price_change_h1": 8.0, "price_change_h24": 10.0, "buy_share": 0.6,
                "liquidity": 100_000, "volume_24h": 400_000}}))
            db.add(TrendScore(token_id=w.id, ts=ts, kind="live", trend=30, risk=30, components_json={"summary": {
                "momentum": 45.0, "sellp": 30.0, "confluence": None, "volume_g_1h": 5.0,
                "price_change_h1": 0.0, "price_change_h24": 5.0, "buy_share": 0.5,
                "liquidity": 100_000, "volume_24h": 400_000}}))
            db.add(PriceSnapshot(token_id=s.id, ts=ts, price_usd=1.0 * (1.01 ** max(0, min(i, 24)))))
            db.add(PriceSnapshot(token_id=w.id, ts=ts, price_usd=1.0 - 0.0001 * max(0, i)))
        for t in (s, w):
            db.add(SignalEvent(token_id=t.id, ts=T0, kind="momentum_acceleration", key="", title="m", detail="m",
                               severity="positive", category="momentum", importance="HIGH", importance_score=80,
                               confidence=85, signal_text="m", why_json=[],
                               metrics_json={"momentum": 70, "liquidity": 100_000, "volume_24h": 400_000,
                                             "mcap": 1_000_000},
                               checklist_json=[{"label": "a", "passed": True, "value": "x"},
                                               {"label": "b", "passed": True, "value": "y"}]))
        db.add(TokenVerdict(token_id=s.id, rating="WATCH", confidence="medium", source="rules", reason="r",
                            created_at=dt.datetime.fromtimestamp(T0 - 600, dt.timezone.utc).replace(tzinfo=None)))
        db.commit()
        ids = {"S": s.id, "W": w.id}
    yield ids
    with SessionLocal() as db:
        for m in (SignalEvent, PriceSnapshot, TrendScore, TokenVerdict):
            db.query(m).filter(m.token_id.in_(ids.values())).delete(synchronize_session=False)
        db.query(Token).filter(Token.id.in_(ids.values())).delete(synchronize_session=False)
        db.commit()


def _bt(db, key):
    return BT.run(db, T0 - 600, T0 + 6 * 3600, 10_000, PC.PRESETS[key]["settings"])


def test_backtest_applies_each_strategy_rule(history):
    with SessionLocal() as db:
        traded = {k: {t["token_id"] for t in _bt(db, k)["trades"]} for k in PC.MOMENTUM_FAMILY}
    S_, W_ = history["S"], history["W"]
    assert traded["baseline"] == {S_, W_}
    for k in ("mom_a", "mom_b", "mom_c", "mom_d", "mom_e", "mom_f"):
        assert traded[k] == {S_}, (k, traded[k])


def test_backtest_let_it_run_and_momentum_exit(history):
    """take_profit 0 used to put the target AT the entry price and close every trade at once."""
    with SessionLocal() as db:
        t = _bt(db, "mom_a")["trades"][0]
    assert t["exit_reason"] == "MOMENTUM_EXIT"
    assert t["hold_secs"] >= 2 * 3600 and t["pnl"] > 0


def test_compare_family(history):
    with SessionLocal() as db:
        out = BT.compare_family(db, T0 - 600, T0 + 6 * 3600, 10_000)
    rows = {r["key"]: r for r in out["strategies"]}
    assert set(rows) == set(PC.MOMENTUM_FAMILY)
    assert rows["mom_a"]["trades"] == 1 and rows["baseline"]["trades"] == 2
    assert rows["mom_a"]["vs_baseline_pp"] is not None and rows["baseline"]["vs_baseline_pp"] is None
    assert out["leader"] is None                 # nobody has 10 trades: no winner declared
