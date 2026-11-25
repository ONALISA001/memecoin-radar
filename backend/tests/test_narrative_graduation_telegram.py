"""Narrative rotation, Graduation Play and Telegram call rules (paper/themes.py) and their presets."""
import time

from app.database import SessionLocal
from app.main import ensure_schema_and_demo_user
from app.models import (DemoAccount, Narrative, PaperPosition, PaperSignal, SignalEvent, TelegramPost, Token,
                        TokenNarrative, User)
from app.paper import config as PC
from app.paper import confluence as CF
from app.paper import engine as PE
from app.paper import themes as TH
from app.paper import winners as W
from app.services import launch_radar as LR

ensure_schema_and_demo_user()


def _mk(**kw):
    m = {"price": 1.0, "liq": 100_000, "mcap": 1_000_000, "volume_24h": 400_000, "price_chg_1h": 8.0,
         "momentum": 60.0, "sellp": 30.0, "liq_chg_3h": 1.0, "risk": 30.0, "regime": "NEUTRAL",
         "vol_liq_ratio": 4.0, "buy_share_pct": 60.0, "price_chg_15m": 1.0, "price_chg_24h": 10.0,
         "volume_g_1h": 50.0}
    m.update(kw)
    return m


def test_presets_valid_and_rules_off_by_default():
    for k in ("narrative", "graduation", "telegram"):
        assert not set(PC.PRESETS[k]["settings"]) - set(PC.DEFAULT_SETTINGS), k
    d = PC.merged_settings({})
    assert not d["narrative_rotation"] and d["tg_call_filter"] == 0
    assert not d["graduation_entries"] and not d["telegram_entries"]


def test_telegram_check_modes_and_feature():
    now = 10_000
    call = {"channels": 3, "channel_names": ["a", "b", "c"], "posts": 4, "first_ts": now - 600}
    assert not TH.telegram_check(1, call, 2, 30, now)[1] and TH.telegram_check(1, None, 2, 30, now)[1]
    assert TH.telegram_check(2, call, 2, 30, now)[1]
    assert not TH.telegram_check(2, {**call, "channels": 1}, 2, 30, now)[1]
    assert not TH.telegram_check(2, None, 2, 30, now)[1]
    f = W.features("k", None, None, None, None, None, None, None, None, None, 2)
    assert f["telegram"] == "called" and W.features(*[None] * 9, None, 0)["telegram"] == "not called"


def _user(db, tag):
    usr = User(email=f"{tag}@t.dev", password_hash="x", api_token=tag)
    db.add(usr); db.flush()
    return usr


def _acct(db, usr, key, **over):
    edge = db.query(SignalEvent.id).order_by(SignalEvent.id.desc()).limit(1).scalar() or 0
    s = {**PC.PRESETS[key]["settings"], "require_verified_contract": False, "max_top10_wallet_pct": 0.0, **over}
    a = DemoAccount(user_id=usr.id, label=f"Lab · {key}", strategy_key=key, starting_cash=10_000, cash=10_000,
                    peak_equity=10_000, settings_json=s, last_event_id=edge)
    db.add(a); db.flush()
    return a


def _tick(db, acct, now, market):
    TH._CACHE.clear()                       # as run_tick does at the start of every tick
    PE._now_ts, PE._breadth = now, CF.breadth(market)
    st = {"signals": 0, "approved": 0, "rejected": 0, "bought": 0, "sold": 0, "accounts": 1}
    PE._run_account(db, acct, now, market, st)
    db.flush()
    return st


def _event(db, tid, ts):
    db.add(SignalEvent(token_id=tid, ts=ts, kind="momentum_acceleration", key="", title="mom", detail="m",
                       severity="positive", category="momentum", importance="HIGH", importance_score=80,
                       confidence=85, signal_text="m", why_json=[], metrics_json={"momentum": 72},
                       checklist_json=[{"label": "a", "passed": True, "value": "x"},
                                       {"label": "b", "passed": True, "value": "y"}]))
    db.flush()


def test_narrative_flow_ranks_real_market_data_and_gates_entries():
    now = int(time.time())
    with SessionLocal() as db:
        usr = _user(db, "narr-live")
        toks = [Token(address=f"NarrMint{i}", chain="solana", symbol=f"N{i}") for i in range(7)]
        db.add_all(toks); db.flush()
        hot = Narrative(name="test-hot-cats"); cold = Narrative(name="test-cold-dogs")
        db.add_all([hot, cold]); db.flush()
        db.add_all([TokenNarrative(token_id=t.id, narrative_id=hot.id) for t in toks[:3]]
                   + [TokenNarrative(token_id=t.id, narrative_id=cold.id) for t in toks[3:6]])
        db.flush()
        mk = {**{t.id: _mk(volume_g_1h=120, price_chg_1h=9) for t in toks[:3]},
              **{t.id: _mk(volume_g_1h=300, price_chg_1h=-6) for t in toks[3:6]},   # volume up but price falling
              toks[6].id: _mk()}
        try:
            flow = [n["name"] for n in TH.narrative_flow(db, mk, now) if n["name"].startswith("test-")]
            assert flow == ["test-hot-cats", "test-cold-dogs"]                 # rising price ranks first
            hot_names = [n["name"] for n in TH.hot_narratives(db, mk, now, 2, 3)]
            assert "test-hot-cats" in hot_names and "test-cold-dogs" not in hot_names
            acct = _acct(db, usr, "narrative", narrative_top_k=50)            # isolate from real DB narratives
            _event(db, toks[0].id, now); _event(db, toks[3].id, now); _event(db, toks[6].id, now)
            _tick(db, acct, now, mk)
            got = {s.token_id: s for s in db.query(PaperSignal).filter_by(account_id=acct.id)}
            assert got[toks[0].id].status == "approved", got[toks[0].id].reject_reason
            assert "narrative" in got[toks[3].id].reject_reason and "narrative" in got[toks[6].id].reject_reason
        finally:
            db.rollback()


def test_graduation_entries_from_the_launch_book():
    now = int(time.time())
    with SessionLocal() as db:
        usr = _user(db, "grad-live")
        fresh, old = Token(address="GradFreshMint", chain="solana", symbol="GF"), Token(address="GradOldMint", chain="solana", symbol="GO")
        db.add_all([fresh, old]); db.flush()
        LR.reset()
        LR.BOOK["GradFreshMint"] = LR.Launch(mint="GradFreshMint", graduated=True, graduated_ts=now - 600)
        LR.BOOK["GradOldMint"] = LR.Launch(mint="GradOldMint", graduated=True, graduated_ts=now - 3 * 3600)
        LR.BOOK["OnCurveMint"] = LR.Launch(mint="OnCurveMint")
        mk = {fresh.id: _mk(liq=40_000), old.id: _mk(liq=40_000)}
        try:
            assert TH.graduations(db, now, 30) == [(fresh.id, 10.0)]
            acct = _acct(db, usr, "graduation")
            st = _tick(db, acct, now, mk)
            pos = db.query(PaperPosition).filter_by(account_id=acct.id).one()
            assert st["bought"] == 1 and pos.token_id == fresh.id and pos.metrics_json["graduated_min_ago"] == 10.0
            assert _tick(db, acct, now + 60, mk)["signals"] == 0               # held / cooled down
            # buyers not in control => the account's own rule rejects it
            other = _acct(db, usr, "graduation")
            _tick(db, other, now + 120, {fresh.id: _mk(liq=40_000, buy_share_pct=45)})
            assert "buyers in control" in db.query(PaperSignal).filter_by(account_id=other.id).one().reject_reason
        finally:
            LR.reset()
            db.rollback()


def test_telegram_follow_entries_and_call_tagging():
    now = int(time.time())
    with SessionLocal() as db:
        usr = _user(db, "tg-live")
        two, one = Token(address="TgTwoMint", chain="solana", symbol="TT"), Token(address="TgOneMint", chain="solana", symbol="TO")
        db.add_all([two, one]); db.flush()
        db.add_all([TelegramPost(channel="chanA", post_id=f"tgtest/{now}/1", ts=now - 300, mints_json=["TgTwoMint"]),
                    TelegramPost(channel="chanB", post_id=f"tgtest/{now}/2", ts=now - 200, mints_json=["TgTwoMint", "TgOneMint"])])
        db.flush()
        mk = {two.id: _mk(), one.id: _mk()}
        try:
            acct = _acct(db, usr, "telegram")
            st = _tick(db, acct, now, mk)
            pos = db.query(PaperPosition).filter_by(account_id=acct.id).one()       # only the 2-channel call
            assert st["bought"] == 1 and pos.token_id == two.id
            assert pos.metrics_json["telegram"]["channels"] == 2 and pos.metrics_json["tg_channels"] == 2
            # a fade account (mode 1) refuses the freshly called coin
            fade = _acct(db, usr, "ai_verdict", verdict_filter=0, tg_call_filter=1)
            _event(db, two.id, now + 60)
            _tick(db, fade, now + 60, mk)
            assert "Telegram" in db.query(PaperSignal).filter_by(account_id=fade.id).one().reject_reason
        finally:
            db.rollback()
