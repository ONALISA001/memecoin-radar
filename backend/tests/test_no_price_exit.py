"""Positions whose coin stops reporting prices get closed at the last known price."""
import time

from app.database import SessionLocal
from app.main import ensure_schema_and_demo_user
from app.models import DemoAccount, PaperPosition, PriceSnapshot, Token, User
from app.paper import config as PC
from app.paper import engine as PE

ensure_schema_and_demo_user()


def _setup(db, tag, settings=None):
    usr = User(email=f"{tag}@t.dev", password_hash="x", api_token=tag)
    tok = Token(address=f"{tag}Mint", chain="solana", symbol=tag[:6].upper())
    db.add_all([usr, tok]); db.flush()
    acct = DemoAccount(user_id=usr.id, label="AI Paper Trader", starting_cash=10_000, cash=9_700,
                       peak_equity=10_000, settings_json=settings or {})
    db.add(acct); db.flush()
    return acct, tok


def _pos(db, acct, tok, entry_ts):
    p = PaperPosition(account_id=acct.id, token_id=tok.id, status="open", entry_ts=entry_ts, entry_price=1.0,
                      qty=297.0, size_usd=300.0, cost_usd=299.1, entry_fee=0.9, peak_price=1.2, trough_price=0.7,
                      stop_price=0.88, metrics_json={}, max_hold_secs=None, ai_confidence=70.0, risk_score=30.0)
    db.add(p); db.flush()
    return p


def _manage(db, acct, p, now):
    st = {"signals": 0, "approved": 0, "rejected": 0, "bought": 0, "sold": 0, "accounts": 1}
    closed = PE._manage_exit(db, acct, PC.merged_settings(acct.settings_json), p, now, {}, {}, st)
    db.flush()
    return closed


def test_default_is_six_hours():
    assert PC.DEFAULT_SETTINGS["no_price_exit_h"] == 6.0


def test_closes_at_last_snapshot_after_hours_without_price():
    now = int(time.time())
    with SessionLocal() as db:
        acct, tok = _setup(db, "noprice-snap")
        p = _pos(db, acct, tok, now - 100 * 3600)                      # stuck for days, no max hold
        db.add(PriceSnapshot(token_id=tok.id, ts=now - 30 * 3600, price_usd=0.5)); db.flush()
        try:
            cash0 = acct.cash
            assert _manage(db, acct, p, now)
            assert p.status == "closed" and p.exit_reason == "NO_PRICE_EXIT"
            # last known price with thin-book slippage: exit below $0.50, above nothing
            assert 0.4 < p.exit_price < 0.5 and p.realized_pnl < 0
            assert acct.cash > cash0                                    # the stuck money comes back
        finally:
            db.rollback()


def test_short_data_gap_and_young_position_are_left_alone():
    now = int(time.time())
    with SessionLocal() as db:
        acct, tok = _setup(db, "noprice-gap")
        old = _pos(db, acct, tok, now - 48 * 3600)
        db.add(PriceSnapshot(token_id=tok.id, ts=now - 1800, price_usd=0.9)); db.flush()   # priced 30 min ago
        try:
            assert not _manage(db, acct, old, now) and old.status == "open"
            acct2, tok2 = _setup(db, "noprice-young")
            young = _pos(db, acct2, tok2, now - 2 * 3600)                                   # opened 2h ago
            assert not _manage(db, acct2, young, now) and young.status == "open"
        finally:
            db.rollback()


def test_falls_back_to_lowest_seen_price_and_can_be_switched_off():
    now = int(time.time())
    with SessionLocal() as db:
        acct, tok = _setup(db, "noprice-pruned")                       # snapshots pruned: none left
        p = _pos(db, acct, tok, now - 100 * 3600)
        try:
            assert _manage(db, acct, p, now) and p.exit_price < 0.7    # from trough $0.70, minus slippage
            acct2, tok2 = _setup(db, "noprice-off", {"no_price_exit_h": 0})
            p2 = _pos(db, acct2, tok2, now - 100 * 3600)
            assert not _manage(db, acct2, p2, now) and p2.status == "open"
        finally:
            db.rollback()
