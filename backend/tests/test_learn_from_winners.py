"""Learn-from-winners gate (paper/winners.py) and the 'winners' Strategy Lab preset."""
import time

from app.database import SessionLocal
from app.main import ensure_schema_and_demo_user
from app.models import DemoAccount, PaperPosition, PaperSignal, SignalEvent, Token, User
from app.paper import config as PC
from app.paper import confluence as CF
from app.paper import engine as PE
from app.paper import winners as W

ensure_schema_and_demo_user()

S = PC.merged_settings(PC.PRESETS["winners"]["settings"])
WIN = W.features("momentum_acceleration", "watch", "BULLISH", 72, 8, 100_000, 60, 30, 85)
LOSE = W.features("unusual_volume", "none", "DISTRIBUTION", 40, 70, 20_000, 45, 65, 50)


def test_preset_is_valid_and_rules_off_by_default():
    assert not set(PC.PRESETS["winners"]["settings"]) - set(PC.DEFAULT_SETTINGS)
    assert S["learn_from_winners"] and not PC.merged_settings({})["learn_from_winners"]


def test_buckets():
    assert WIN["momentum"] == "65-80" and WIN["chg_1h"] == "0-10%" and WIN["liquidity"] == "$75-250K"
    assert W.features(None, None, None, None, None, None, None, None, None)["momentum"] is None


def test_judge_needs_history_and_similar_trades():
    assert not W.judge(S, WIN, [(WIN, 10.0)] * 5)["ok"]                      # < 30 trades of history
    hist = [(LOSE, -8.0)] * 40
    j = W.judge(S, WIN, hist)
    assert not j["ok"] and j["similar"] == 0                                   # nothing looks like it


def test_judge_buys_setups_that_won_and_skips_ones_that_lost():
    hist = [(WIN, 9.0)] * 12 + [(WIN, -6.0)] * 3 + [(LOSE, -8.0)] * 30
    j = W.judge(S, WIN, hist)
    assert j["ok"] and j["similar"] == 15 and j["win_rate"] == 80.0 and j["size_mult"] == 1.0
    j = W.judge(S, LOSE, hist)
    assert not j["ok"] and j["win_rate"] == 0
    # winners that don't cover costs on average are not enough
    thin = [(WIN, 0.5)] * 12 + [(WIN, -6.0)] * 3 + [(LOSE, -8.0)] * 30
    assert not W.judge(S, WIN, thin)["ok"]


def _pos(acct, tok, now, pnl, kind="momentum_acceleration", mom=72, regime="BULLISH", closed=True):
    return PaperPosition(
        account_id=acct.id, token_id=tok.id, status="closed" if closed else "open",
        entry_ts=now - 7200, exit_ts=(now - 3600) if closed else None, size_usd=100, pnl_pct=pnl,
        realized_pnl=pnl, ai_confidence=85, market_regime=regime,
        metrics_json={"signal_kind": kind, "momentum": mom, "verdict": {"rating": "watch"},
                      "entry_market": {"price_chg_1h": 8, "liq": 100_000, "buy_share_pct": 60, "sellp": 30}})


def test_live_account_learns_from_other_accounts():
    now = int(time.time())
    with SessionLocal() as db:
        usr = User(email="winners-live@t.dev", password_hash="x", api_token="winners-live")
        tok = Token(address="WinnersMint", chain="solana", symbol="WINR")
        db.add_all([usr, tok]); db.flush()
        other = DemoAccount(user_id=usr.id, label="Lab · other", strategy_key="baseline", starting_cash=10_000,
                            cash=10_000, peak_equity=10_000, settings_json={})
        edge = db.query(SignalEvent.id).order_by(SignalEvent.id.desc()).limit(1).scalar() or 0
        acct = DemoAccount(user_id=usr.id, label="Lab · W", strategy_key="winners", starting_cash=10_000,
                           cash=10_000, peak_equity=10_000, settings_json=dict(PC.PRESETS["winners"]["settings"]),
                           last_event_id=edge)
        db.add_all([other, acct]); db.flush()
        try:
            # history from ANOTHER account: momentum setups won, distribution setups lost
            db.add_all([_pos(other, tok, now, 9.0) for _ in range(12)]
                       + [_pos(other, tok, now, -5.0) for _ in range(3)]
                       + [_pos(other, tok, now, -9.0, kind="unusual_volume", mom=40, regime="DISTRIBUTION")
                          for _ in range(20)]
                       + [_pos(other, tok, now, 50.0, closed=False)])      # open trades never count
            db.flush()
            mk = {"price": 1.0, "liq": 100_000, "mcap": 1_000_000, "volume_24h": 400_000, "price_chg_1h": 8.0,
                  "momentum": 72.0, "sellp": 30.0, "liq_chg_3h": 1.0, "risk": 30.0, "regime": "BULLISH",
                  "vol_liq_ratio": 4.0, "buy_share_pct": 60.0, "price_chg_15m": 1.0, "price_chg_24h": 10.0}
            db.add(SignalEvent(token_id=tok.id, ts=now, kind="momentum_acceleration", key="", title="mom",
                               detail="m", severity="positive", category="momentum", importance="HIGH",
                               importance_score=80, confidence=85, signal_text="m", why_json=[],
                               metrics_json={"momentum": 72, "liquidity": 100_000},
                               checklist_json=[{"label": "a", "passed": True, "value": "x"},
                                               {"label": "b", "passed": True, "value": "y"}]))
            db.flush()
            # the contract-safety rules need Helius data this test doesn't seed: isolate the learned gate
            acct.settings_json = {**acct.settings_json, "require_verified_contract": False,
                                  "max_top10_wallet_pct": 0.0, "max_vol_liq_ratio": 0.0,
                                  "pullback_entries": False, "priority_entries": False}
            PE._now_ts, PE._breadth = now, CF.breadth({tok.id: mk})
            stats = {"signals": 0, "approved": 0, "rejected": 0, "bought": 0, "sold": 0, "accounts": 1}
            PE._run_account(db, acct, now, {tok.id: mk}, stats)
            db.flush()
            sig = db.query(PaperSignal).filter_by(account_id=acct.id).one()
            assert sig.status == "approved", sig.reject_reason
            pos = db.query(PaperPosition).filter_by(account_id=acct.id).one()
            assert pos.metrics_json["winners"]["similar"] == 15 and pos.metrics_json["winners_size_mult"] == 1.0
            pats = W.patterns(db, usr.id, now + 1)
            assert pats["trades"] == 35
            assert pats["patterns"][0]["avg_pct"] > 0 > pats["patterns"][-1]["avg_pct"]
        finally:
            db.rollback()
