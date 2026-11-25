"""Ensemble Vote entries, the clean-launch rule, and the Fade the Pump preset."""
import time
from types import SimpleNamespace as NS

from app.database import SessionLocal
from app.main import ensure_schema_and_demo_user
from app.models import DemoAccount, PaperPosition, PaperSignal, SignalEvent, Token, User
from app.paper import config as PC
from app.paper import confluence as CF
from app.paper import engine as PE
from app.paper import filters as F

ensure_schema_and_demo_user()


def S(key=None, **kw):
    return PC.merged_settings({**(PC.PRESETS[key]["settings"] if key else {}), **kw})


def test_presets_valid_and_rules_off_by_default():
    for k in ("ensemble", "clean_launch", "fade_pump"):
        assert not set(PC.PRESETS[k]["settings"]) - set(PC.DEFAULT_SETTINGS), k
    d = PC.merged_settings({})
    assert not d["clean_launch"] and not d["ensemble_entries"]


# ---------------------------------------------------------------- clean launch
def _lf(**kw):
    f = dict(status="ok", bundle_pct=2.0, sniper_pct=4.0, dev_holds_pct=1.0, shared_funder_wallets=1,
             funded_by_creator=0)
    f.update(kw)
    return NS(**f)


def test_clean_launch_check():
    s = S("clean_launch")
    assert F.clean_launch_check(s, _lf())[1]
    assert not F.clean_launch_check(s, None)[1]                                  # unanalysed never passes
    assert not F.clean_launch_check(s, _lf(status="too_active"))[1]
    for kw, word in (({"bundle_pct": 12}, "bundled"), ({"sniper_pct": 30}, "sniped"),
                     ({"dev_holds_pct": 9}, "dev holds"), ({"shared_funder_wallets": 2, "funded_by_creator": 1}, "linked")):
        ok, why = F.clean_launch_check(s, _lf(**kw))[1:]
        assert not ok and word in why, kw


# ---------------------------------------------------------------- fade the pump
def _mk(**kw):
    m = {"price": 1.0, "liq": 100_000, "mcap": 1_000_000, "volume_24h": 400_000, "price_chg_1h": 8.0,
         "momentum": 60.0, "sellp": 30.0, "liq_chg_3h": 1.0, "risk": 30.0, "regime": "NEUTRAL",
         "vol_liq_ratio": 4.0, "buy_share_pct": 60.0, "price_chg_15m": 1.0, "price_chg_24h": 10.0}
    m.update(kw)
    return m


def test_fade_the_pump_entry_shape():
    s = S("fade_pump")
    faded = _mk(price_chg_24h=180, price_chg_1h=-35, price_chg_15m=3)
    assert F.pullback_candidate(s, faded)[0]
    assert not F.pullback_candidate(s, _mk(price_chg_24h=60, price_chg_1h=-35, price_chg_15m=3))[0]   # no big pump
    assert not F.pullback_candidate(s, _mk(price_chg_24h=180, price_chg_1h=-10, price_chg_15m=3))[0]  # dip too small
    assert not F.pullback_candidate(s, _mk(price_chg_24h=180, price_chg_1h=-35, price_chg_15m=0.5))[0]  # no bounce yet
    # the mild Trend Pullback preset would not take this deep a drop
    assert not F.pullback_candidate(S("pullback"), faded)[0]


# ---------------------------------------------------------------- ensemble
def _acct(db, usr, key, settings, edge=0):
    a = DemoAccount(user_id=usr.id, label=f"Lab · {key}", strategy_key=key, starting_cash=10_000, cash=10_000,
                    peak_equity=10_000, settings_json=settings, last_event_id=edge)
    db.add(a); db.flush()
    return a


def _opened(db, acct, tok, ts):
    db.add(PaperPosition(account_id=acct.id, token_id=tok.id, status="open", entry_ts=ts, entry_price=1.0,
                         qty=100, size_usd=100, cost_usd=99.7, peak_price=1.0, metrics_json={}))
    db.flush()


def test_ensemble_votes_and_entries():
    now = int(time.time())
    with SessionLocal() as db:
        usr = User(email="ens-live@t.dev", password_hash="x", api_token="ens-live")
        tok = Token(address="EnsLiveMint", chain="solana", symbol="ENS")
        db.add_all([usr, tok]); db.flush()
        edge = db.query(SignalEvent.id).order_by(SignalEvent.id.desc()).limit(1).scalar() or 0
        a, b, c = (_acct(db, usr, k, {}, edge) for k in ("mom_a", "pullback", "evidence"))
        settings = {**PC.PRESETS["ensemble"]["settings"], "require_verified_contract": False,
                    "max_top10_wallet_pct": 0.0, "max_vol_liq_ratio": 0.0}
        ens = _acct(db, usr, "ensemble", settings, edge)
        mk = {tok.id: _mk()}

        def tick(t):
            PE._now_ts, PE._breadth = t, CF.breadth(mk)
            st = {"signals": 0, "approved": 0, "rejected": 0, "bought": 0, "sold": 0, "accounts": 1}
            PE._ensemble_entries(db, ens, PC.merged_settings(ens.settings_json), t, mk, st)
            db.flush()
            return st
        try:
            # one vote: nothing; a stale vote (older than the window) doesn't count either
            _opened(db, a, tok, now - 60)
            _opened(db, b, tok, now - 3 * 3600)
            assert tick(now)["signals"] == 0
            # its own buys never count as votes
            assert PE.ensemble_votes(db, ens, now, 1800) == {tok.id: ["mom_a"]}
            # two recent votes => ensemble buy at 60% size, voters recorded
            _opened(db, c, tok, now - 120)
            st = tick(now)
            sig = db.query(PaperSignal).filter_by(account_id=ens.id).one()
            assert st["bought"] == 1, sig.reject_reason
            pos = db.query(PaperPosition).filter_by(account_id=ens.id).one()
            assert pos.metrics_json["ensemble_votes"] == 2 and pos.metrics_json["ensemble_size_mult"] == 0.6
            assert pos.metrics_json["ensemble_voters"] == ["evidence", "mom_a"]
            # held => no second signal
            assert tick(now + 60)["signals"] == 0
            assert db.query(PaperSignal).filter_by(account_id=ens.id, kind="ensemble_vote").count() == 1
        finally:
            db.rollback()
