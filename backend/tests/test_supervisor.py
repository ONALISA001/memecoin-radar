"""Priority-coin entries (strategy G) and the strategy supervisor (propose → owner approves)."""
import time
import uuid

import pytest
from fastapi.testclient import TestClient

from app import security
from app.database import SessionLocal
from app.main import app, ensure_schema_and_demo_user
from app.models import (DemoAccount, PaperPosition, PaperSignal, SignalEvent, StrategyProposal, Token,
                        TrendScore, User)
from app.paper import config as PC
from app.paper import confluence as CF
from app.paper import engine as PE
from app.paper import lab as LAB
from app.paper import postmortem as PM
from app.paper import supervisor as SUP

ensure_schema_and_demo_user()
client = TestClient(app)


@pytest.fixture(autouse=True)
def _fresh():
    security._hits.clear()
    security._login_hits.clear()
    yield


def _user(db, tag):
    uid = uuid.uuid4().hex
    u = User(email=f"sup-{tag}-{uid}@t.dev", password_hash="x", api_token=f"sup-{tag}-{uid}")
    db.add(u)
    db.flush()
    return u


def _acct(db, u, key, settings=None, label=None):
    a = DemoAccount(user_id=u.id, label=label or f"Lab · {key}", strategy_key=key, starting_cash=10_000,
                    cash=10_000, peak_equity=10_000,
                    settings_json=dict(settings if settings is not None else PC.PRESETS.get(key, {}).get("settings", {})))
    db.add(a)
    db.flush()
    return a


def _trades(db, acct, pnls, token_id=None):
    """Closed trades with these P&L values ($ on a $100 position, so pnl_pct = pnl)."""
    if token_id is None:
        t = Token(address=f"SupTok{uuid.uuid4().hex}", chain="solana", symbol="SUP")
        db.add(t)
        db.flush()
        token_id = t.id
    for i, pnl in enumerate(pnls):
        db.add(PaperPosition(account_id=acct.id, token_id=token_id, status="closed", entry_ts=1000 + i,
                             exit_ts=2000 + i, entry_price=1, qty=100, size_usd=100, cost_usd=100,
                             realized_pnl=pnl, pnl_pct=pnl, exit_reason="STOP_LOSS" if pnl < 0 else "TRAILING_STOP"))
    acct.cash = 10_000 + sum(pnls)
    db.flush()


# ---------------------------------------------------------------- strategy G
def test_priority_preset_is_valid():
    g = PC.merged_settings(PC.PRESETS["priority"]["settings"])
    assert g["priority_entries"] and g["allowed_kinds"] == ["__none__"] and g["min_momentum"] == 60
    assert not set(PC.PRESETS["priority"]["settings"]) - set(PC.DEFAULT_SETTINGS)


def test_priority_coin_entries_buy_only_listed_coins_that_pass_own_rules():
    now = int(time.time())
    with SessionLocal() as db:
        try:
            u = _user(db, "g")
            good = Token(address="PrioGoodMint", chain="solana", symbol="PGOOD")
            flat = Token(address="PrioFlatMint", chain="solana", symbol="PFLAT")
            db.add_all([good, flat]); db.flush()
            summ = {"price": 1.0, "liquidity": 80_000, "volume_1h": 20_000, "momentum": 85, "momentum_band": "extreme",
                    "sellp": 30, "sellp_band": "neutral", "price_change_h1": 5, "price_change_h24": 10,
                    "buy_share": 0.7, "vol_accel_h1": 60, "holder_growth": 5}
            for t, prev in ((good, 80.0), (flat, 90.0)):
                db.add(TrendScore(token_id=t.id, ts=now - 300, kind="live", trend=60, risk=20,
                                  components_json={"summary": {**summ, "momentum": prev}}))
                db.add(TrendScore(token_id=t.id, ts=now, kind="live", trend=60, risk=20, risk_class="LOW",
                                  components_json={"summary": summ}))
            db.flush()
            g = _acct(db, u, "priority")
            mk = lambda: {"price": 1.0, "liq": 80_000, "mcap": 900_000, "volume_24h": 400_000, "price_chg_1h": 5.0,  # noqa: E731
                          "momentum": 85.0, "sellp": 30.0, "liq_chg_3h": 0.0, "risk": 20.0, "regime": "BULLISH",
                          "vol_liq_ratio": 5.0, "buy_share_pct": 60.0, "price_chg_24h": 10.0, "volume_g_1h": 50.0}
            market = {good.id: mk(), flat.id: mk()}
            PE._now_ts, PE._breadth = now, CF.breadth(market)
            PE._PRIO_CACHE.clear()
            # both score ≥55; FLAT's momentum fell (90 → 85): not rising, so its own rules refuse it
            PE._priority_entries(db, g, PE.settings_for(g), now, market,
                                 {"signals": 0, "approved": 0, "rejected": 0, "bought": 0, "sold": 0})
            sigs = {s.token_id: s for s in db.query(PaperSignal).filter_by(account_id=g.id).all()}
            assert sigs[good.id].kind == "priority_coin" and sigs[good.id].status == "approved"
            assert sigs[flat.id].status == "rejected" and "momentum rising" in sigs[flat.id].reject_reason
            pos = db.query(PaperPosition).filter_by(account_id=g.id).one()
            assert pos.token_id == good.id and pos.metrics_json["priority_score"] > 0
            # the per-coin signal cooldown stops a second signal on the next tick
            PE._priority_entries(db, g, PE.settings_for(g), now + 300, market,
                                 {"signals": 0, "approved": 0, "rejected": 0, "bought": 0, "sold": 0})
            assert db.query(PaperSignal).filter_by(account_id=g.id).count() == 2
        finally:
            db.rollback()
            PE._PRIO_CACHE.clear()


# ---------------------------------------------------------------- supervisor
def _no_pm(monkeypatch, suggestions_by_account=None):
    sugg = suggestions_by_account or {}
    monkeypatch.setattr(PM, "summary", lambda db, account_id, settings, **k: {"suggestions": sugg.get(account_id, [])})


def test_retire_losing_strategy_only_after_enough_trades_and_on_approval(monkeypatch):
    _no_pm(monkeypatch)
    with SessionLocal() as db:
        try:
            u = _user(db, "ret")
            _acct(db, u, "baseline")
            loser = _acct(db, u, "mom_a")
            early = _acct(db, u, "mom_b")
            base = db.query(DemoAccount).filter_by(user_id=u.id, strategy_key="baseline").one()
            _trades(db, loser, [-3.0] * 22 + [2.0] * 10)      # 32 trades, clearly losing
            _trades(db, early, [-3.0] * 8)                     # losing but only 8 trades
            _trades(db, base, [-3.0] * 40)                     # the yardstick is never retired
            res = SUP.review(db, u.id, {}, backtest=False)
            kinds = {(p["kind"], p["account_id"]) for p in res["created"]}
            assert kinds == {("retire", loser.id)}
            pid = res["created"][0]["id"]
            assert loser.active                                # nothing happens before approval
            SUP.decide(db, u.id, pid, approve=True)
            assert loser.active is False
            assert "mom_a" not in LAB.start_lab(db, u.id)      # a retired preset is not recreated
            assert next(r for r in LAB.compare(db, u.id, {})["presets"] if r["key"] == "mom_a")["retired"]
            with pytest.raises(ValueError):
                SUP.decide(db, u.id, pid, approve=True)        # already decided
        finally:
            db.rollback()


def test_challenger_learned_from_losses_then_promote(monkeypatch):
    with SessionLocal() as db:
        try:
            u = _user(db, "ch")
            best = _acct(db, u, "mom_b")
            _trades(db, best, [6.0] * 15 + [-3.0] * 10)        # 25 trades, profitable
            sugg = {"cause": "CHASED", "setting": "max_chase_pct_1h", "current": 60.0, "suggested": 35,
                    "why": "6 losses were bought after a big 1h run.", "tradeoff": "Skips some breakouts."}
            _no_pm(monkeypatch, {best.id: [sugg]})
            res = SUP.review(db, u.id, {}, backtest=False)
            ch = [p for p in res["created"] if p["kind"] == "challenger"]
            assert len(ch) == 1 and ch[0]["change"] == {"max_chase_pct_1h": 35}
            assert "Learned from its losses" in ch[0]["reason"]
            # rejecting keeps it from coming back on the next review
            SUP.decide(db, u.id, ch[0]["id"], approve=False)
            assert not [p for p in SUP.review(db, u.id, {}, backtest=False)["created"] if p["kind"] == "challenger"]
            # a fresh proposal (different fix) approved => a separate challenger account
            sugg2 = {**sugg, "setting": "breakeven_at_pct", "current": 15.0, "suggested": 10}
            _no_pm(monkeypatch, {best.id: [sugg2]})
            pid = next(p for p in SUP.review(db, u.id, {}, backtest=False)["created"] if p["kind"] == "challenger")["id"]
            SUP.decide(db, u.id, pid, approve=True)
            new = db.query(DemoAccount).filter_by(user_id=u.id, strategy_key=f"ch{pid}").one()
            assert new.active and PE.settings_for(new)["breakeven_at_pct"] == 10
            assert PE.settings_for(new)["min_volume_accel_pct"] == 40     # everything else copied from B
            rows = {r["key"]: r for r in LAB.compare(db, u.id, {})["presets"]}
            assert rows[f"ch{pid}"]["family"] == "challenger"
            # the challenger beats its parent over 30+ trades => promote proposal
            _no_pm(monkeypatch)
            _trades(db, new, [8.0] * 25 + [-3.0] * 7)
            promo = [p for p in SUP.review(db, u.id, {}, backtest=False)["created"] if p["kind"] == "promote"]
            assert len(promo) == 1 and promo[0]["account_id"] == new.id
            SUP.decide(db, u.id, promo[0]["id"], approve=True)
            assert best.active is False and new.active and "Promoted" in new.label
        finally:
            db.rollback()


def test_fix_main_applies_setting_only_when_approved(monkeypatch):
    with SessionLocal() as db:
        try:
            u = _user(db, "fix")
            main = PE.get_or_create_account(db, u.id)
            sugg = {"cause": "STOP_THEN_RECOVERED", "setting": "stop_loss_pct", "current": 12.0, "suggested": 16,
                    "why": "7 of 10 stop-outs later recovered.", "tradeoff": "Bigger losses on real failures."}
            _no_pm(monkeypatch, {main.id: [sugg]})
            p = next(x for x in SUP.review(db, u.id, {}, backtest=False)["created"] if x["kind"] == "fix_main")
            assert PE.settings_for(main)["stop_loss_pct"] == 12
            SUP.decide(db, u.id, p["id"], approve=True)
            assert PE.settings_for(main)["stop_loss_pct"] == 16
        finally:
            db.rollback()


def test_pending_proposals_expire():
    with SessionLocal() as db:
        try:
            u = _user(db, "exp")
            db.add(StrategyProposal(user_id=u.id, created_ts=int(time.time()) - 49 * 3600, kind="retire",
                                    fingerprint="x", title="t", reason="r", status="pending"))
            db.flush()
            assert SUP.expire(db, u.id, int(time.time())) == 1
        finally:
            db.rollback()


def test_supervisor_api_is_owner_only_for_changes():
    vis = client.post("/api/auth/demo").json()["token"]
    h = {"Authorization": f"Bearer {vis}"}
    assert client.get("/api/paper/supervisor", headers=h).status_code == 200
    assert client.post("/api/paper/supervisor/review", headers=h).status_code == 403
    assert client.post("/api/paper/supervisor/proposals/1/approve", headers=h).status_code == 403
    own = client.post("/api/auth/login", json={"key": "test-owner-key-123"}).json()["token"]
    ho = {"Authorization": f"Bearer {own}"}
    r = client.post("/api/paper/supervisor/review", headers=ho)
    assert r.status_code == 200 and "pending" in r.json()
    assert client.post("/api/paper/supervisor/proposals/999999/approve", headers=ho).status_code == 404
    assert client.post("/api/paper/supervisor/proposals/1/explode", headers=ho).status_code in (403, 404)
