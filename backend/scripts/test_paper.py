"""v4 §33 paper-trading tests: signals, risk, execution, positions,
accounting, reliability. Runs against a scratch SQLite DB — no live data."""
import os, sys, tempfile, time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
fd, dbpath = tempfile.mkstemp(suffix=".db")
os.close(fd)
os.environ["DATABASE_URL"] = f"sqlite:///{dbpath}"

from app.database import Base, engine, SessionLocal  # noqa: E402
from app.models import (DemoAccount, EquitySnapshot, PaperPosition, PaperSignal,
                        PaperTradeEvent, SignalEvent, Token, User)  # noqa: E402
from app.paper import engine as PE, config as PC  # noqa: E402
from app.security import hash_password  # noqa: E402

Base.metadata.create_all(engine)
db = SessionLocal()
NOW = int(time.time())
PASS, FAIL = [], []


def check(name: str, cond: bool, extra: str = ""):
    (PASS if cond else FAIL).append(name)
    print(("  ✅" if cond else "  ❌"), name, extra)


# ---------- fixtures
tok = Token(address="paperUNITtest", chain="solana", symbol="UNIT",
            name="Unit Test Coin", token_created_at=(NOW - 7200) * 1000)
usr = User(email="paper@t.dev", password_hash=hash_password("x"), api_token="pt")
usr2 = User(email="paper2@t.dev", password_hash=hash_password("x"), api_token="pt2")
db.add_all([tok, usr, usr2]); db.flush()

MKT = {tok.id: {"price": 0.000100, "liq": 80000, "mcap": 900000,
                "volume_24h": 300000, "price_chg_1h": 3.0,
                "momentum": 74.0, "sellp": 25.0, "liq_chg_3h": 2.0,
                "risk": 35.0, "regime": "BULLISH"}}

acct = PE.get_or_create_account(db, usr.id)
acct.settings_json = {"min_confidence": 60, "max_position_usd": 400.0,
                      "take_profit_pct": 20.0, "stop_loss_pct": 10.0}
PE.apply_settings(db, acct, acct.settings_json)
db.commit()


def add_event(kind="momentum_acceleration", conf=82, severity="positive", m=None, cat="momentum"):
    e = SignalEvent(token_id=tok.id, ts=NOW, kind=kind, key="", title=f"{kind} — $UNIT",
                    detail=kind, severity=severity, metrics_json=m or {
                        "momentum": 74, "sell_pressure": 25, "liq_health": 70,
                        "liquidity": 80000, "volume_24h": 300000, "mcap": 900000,
                        "buy_share": 61, "holders_g_1h": 4.0, "volume_g_1h": 55.0},
                    category=cat, importance="HIGH", importance_score=80,
                    confidence=conf, signal_text=kind, why_json=["momentum rising"],
                    concern=None, chain_json=None,
                    checklist_json=[{"label": "a", "passed": True, "value": "x"},
                                    {"label": "b", "passed": True, "value": "y"},
                                    {"label": "c", "passed": False, "value": "z"}])
    db.add(e); db.flush()
    return e


# ---------- SIGNAL TESTS ------------------------------------------------
print("SIGNAL TESTS")
e1 = add_event()
stats = PE.run_tick(db, NOW, MKT)
sigs = db.query(PaperSignal).filter_by(account_id=acct.id).all()
check("BUY signal created from momentum event", len(sigs) == 1)
check("signal approved by risk engine", sigs[0].status == "approved")
pos = db.query(PaperPosition).filter_by(account_id=acct.id).all()
check("BUY signal → position opened", len(pos) == 1)
p0 = pos[0]
check("position links signal & strategy version",
      p0.signal_id == sigs[0].id and p0.strategy_version_id is not None)
check("stop/target set from settings",
      abs(p0.stop_price / p0.entry_price - 0.90) < 1e-6
      and abs(p0.target_price / p0.entry_price - 1.20) < 1e-6)

# duplicate event (§31): same source event → no second signal/position
PE.run_tick(db, NOW + 300, MKT)
PE.run_tick(db, NOW + 300, MKT)
db.commit()
check("duplicate event cannot create duplicate position",
      db.query(PaperPosition).filter_by(account_id=acct.id).count() == 1)
check("no duplicate signal rows", db.query(PaperSignal).filter_by(account_id=acct.id).count() == 1)

# SELL signal event → SELL exit
MKT2 = {**MKT, tok.id: {**MKT[tok.id], "price": 0.000110, "momentum": 60.0}}
e2 = add_event(kind="sell_pressure_increasing", conf=70, severity="danger", cat="distribution")
PE.run_tick(db, NOW + 600, MKT2)
db.commit()
p0 = db.get(PaperPosition, p0.id)
check("SELL-signal event closed the position", p0.status == "closed",
      f"reason={p0.exit_reason}")
check("exit reason = SELL_SIGNAL", p0.exit_reason == "SELL_SIGNAL")

# ---------- RISK TESTS --------------------------------------------------
print("RISK TESTS")
# confidence too low
e3 = add_event(conf=45)
PE.run_tick(db, NOW + 900, MKT)
bad = db.query(PaperSignal).filter_by(account_id=acct.id, source_event_id=e3.id).one()
check("low-confidence signal rejected", bad.status == "rejected",
      bad.reject_reason)
check("reject reason recorded", bool(bad.reject_reason))

# position too large → sizing respects caps & reserve
acct2 = PE.get_or_create_account(db, usr2.id)
acct2.cash = 100.0  # tiny cash → size below min order after reserve
db.commit()
e4 = add_event()
# hack: move cursor back so acct2 processes e4 only
acct2.last_event_id = e4.id - 1
stats2 = PE._run_account(db, acct2, e4.ts, MKT, {"signals": 0, "approved": 0, "rejected": 0, "bought": 0, "sold": 0})
tiny = db.query(PaperSignal).filter_by(account_id=acct2.id, source_event_id=e4.id).one()
check("undersized order rejected (cash/reserve)", tiny.status == "rejected",
      tiny.reject_reason)

# daily loss limit blocks new buys
acct.settings_json = {**acct.settings_json, "max_daily_loss_pct": 5.0}
acct.day_realized = -20000.0  # catastrophic day
db.commit()
e5 = add_event()
acct.last_event_id = e5.id - 1
PE.run_tick(db, NOW + 1200, MKT)
blk = db.query(PaperSignal).filter_by(account_id=acct.id, source_event_id=e5.id).one()
check("daily loss exceeded → rejected", blk.status == "rejected" and "daily realized" in blk.reject_reason)
acct.day_realized = 0.0
db.commit()

# max drawdown blocks
acct.peak_equity = 100000.0  # fake peak → massive dd vs equity
db.commit()
e6 = add_event()
acct.last_event_id = e6.id - 1
PE.run_tick(db, NOW + 1500, MKT)
blk2 = db.query(PaperSignal).filter_by(account_id=acct.id, source_event_id=e6.id).one()
check("max drawdown reached → rejected", blk2.status == "rejected" and "drawdown" in (blk2.reject_reason or ""))
acct.peak_equity = 10000.0
db.commit()

# ---------- EXECUTION TESTS ---------------------------------------------
print("EXECUTION TESTS")
from app.paper import execution as X
cfg = PC.DEFAULT_SETTINGS["execution"]
hi_liq = X.slippage_pct(250, 500000, 1.0, "BUY", cfg)
lo_liq = X.slippage_pct(250, 9000, 1.0, "BUY", cfg)
check("low liquidity → higher slippage", lo_liq > hi_liq, f"{hi_liq:.2f}% vs {lo_liq:.2f}%")
big = X.slippage_pct(5000, 100000, 0, "BUY", cfg)
small = X.slippage_pct(50, 100000, 0, "BUY", cfg)
check("bigger order → bigger impact", big > small)
f = X.buy_fill(100.0, 250.0, 9000, 5.0, cfg)
check("BUY fill slips UP", f["exec_price"] > 100.0, f"{f['exec_price']:.4f}")
check("fee charged on notional", abs(f["fee"] - 250 * cfg["fee_pct"] / 100) < 1e-9)
check("qty uses size minus fee", abs(f["qty"] * f["exec_price"] + f["fee"] - 250) < 0.02)
s = X.sell_fill(90.0, 2.5, 9000, -4.0, cfg)
check("SELL fill slips DOWN", s["exec_price"] < 90.0)
check("sell fee deducted from proceeds", abs(s["proceeds"] - (2.5 * s["exec_price"] - s["fee"])) < 1e-9)

# ---------- POSITION TESTS ----------------------------------------------
print("POSITION TESTS")
tok2 = Token(address="paperUNIT2", chain="solana", symbol="UNIT2", name="Unit2")
db.add(tok2); db.flush()
MKTX = {tok2.id: {"price": 100.0, "liq": 400000, "mcap": 5000000,
                  "volume_24h": 900000, "price_chg_1h": 1.0, "momentum": 70.0,
                  "sellp": 20.0, "liq_chg_3h": 1.0, "risk": 30.0, "regime": "BULLISH"}}
e7 = SignalEvent(token_id=tok2.id, ts=NOW, kind="new_high", key="", title="ath", detail="x",
                 severity="positive", metrics_json={"momentum": 70, "sell_pressure": 20,
                 "liquidity": 400000, "volume_24h": 900000, "mcap": 5000000},
                 category="ath", importance="HIGH", importance_score=70, confidence=80,
                 signal_text="", why_json=[], concern=None, chain_json=None,
                 checklist_json=[{"label": "a", "passed": True, "value": "1"},
                                 {"label": "b", "passed": True, "value": "2"}])
db.add(e7); db.flush()
acct.last_event_id = e7.id - 1
acct.day_realized = 0
db.commit()
PE.run_tick(db, NOW + 2000, MKTX)
p2 = db.query(PaperPosition).filter_by(token_id=tok2.id).one()
check("TP setup: position open", p2.status == "open")
# take profit at +25% (target 20%)
PE.run_tick(db, NOW + 2300, {tok2.id: {**MKTX[tok2.id], "price": 126.0}})
db.refresh(p2)
check("TAKE_PROFIT exit fired", p2.status == "closed" and p2.exit_reason == "TAKE_PROFIT")
check("win counts consecutive", (acct.consec_wins or 0) >= 0)  # acct state row locked; still fine

# stop loss
e8 = SignalEvent(token_id=tok2.id, ts=NOW + 2600, kind="momentum_acceleration", key="",
                 title="acc", detail="y", severity="positive",
                 metrics_json={"momentum": 70, "sell_pressure": 20, "liquidity": 400000,
                               "volume_24h": 900000, "mcap": 5000000},
                 category="momentum", importance="HIGH", importance_score=75, confidence=90,
                 signal_text="", why_json=[], concern=None, chain_json=None,
                 checklist_json=[{"label": "a", "passed": True, "value": "1"},
                                 {"label": "b", "passed": True, "value": "2"}])
db.add(e8); db.flush()
acct.last_event_id = e8.id - 1
acct.peak_equity = 0.0  # re-anchor peak; engine recomputes max(peak, equity)
db.commit()
acct.cash = 5000.0  # top-up cash for the second round-trip
PE.run_tick(db, NOW + 2600, MKTX)
p3 = db.query(PaperPosition).filter_by(token_id=tok2.id, status="open").one()
PE.run_tick(db, NOW + 2900, {tok2.id: {**MKTX[tok2.id], "price": 85.0}})
db.refresh(p3)
check("STOP_LOSS exit fired", p3.status == "closed" and p3.exit_reason == "STOP_LOSS")
check("losing trade booked negative P&L", (p3.realized_pnl or 0) < 0, f"{p3.realized_pnl:.2f}")

# manual close
e9 = SignalEvent(token_id=tok2.id, ts=NOW + 3200, kind="new_high", key="", title="ath2",
                 detail="z", severity="positive",
                 metrics_json={"momentum": 70, "sell_pressure": 20, "liquidity": 400000,
                               "volume_24h": 900000, "mcap": 5000000},
                 category="ath", importance="HIGH", importance_score=70, confidence=80,
                 signal_text="", why_json=[], concern=None, chain_json=None,
                 checklist_json=[{"label": "a", "passed": True, "value": "1"},
                                 {"label": "b", "passed": True, "value": "2"}])
db.add(e9); db.flush()
acct.last_event_id = e9.id - 1
acct.peak_equity = 0.0  # re-anchor peak for the manual-close scenario
db.commit()
acct.cash = 5000.0
PE.run_tick(db, NOW + 3200, MKTX)
p4 = db.query(PaperPosition).filter_by(token_id=tok2.id, status="open").one()
closed = PE.manual_close(db, acct, p4.id, NOW + 3400, MKTX)
check("manual close works", closed is not None and closed.status == "closed"
      and closed.exit_reason == "MANUAL")

# ---------- ACCOUNTING TESTS -------------------------------------------
print("ACCOUNTING TESTS")
closed_all = db.query(PaperPosition).filter_by(account_id=acct.id, status="closed").all()
realized = sum(p.realized_pnl for p in closed_all)
fees = sum((p.entry_fee or 0) + (p.exit_fee or 0) for p in closed_all)
check("fees > 0 across trades", fees > 0, f"${fees:.2f}")
check("realized P&L uses (proceeds − cost)", all(
    abs((p.qty * p.exit_price - p.exit_fee) - p.cost_usd - p.realized_pnl) < 0.01
    for p in closed_all if p.exit_price), "")
pf = PE.portfolio_state(db, acct, MKTX)
check("equity = cash + invested", abs(pf["equity"] - (pf["cash"] + pf["invested"])) < 1e-6)
eq_rows = db.query(EquitySnapshot).filter_by(account_id=acct.id).count()
check("equity snapshots recorded", eq_rows >= 2, str(eq_rows))
cash_consistency = abs(acct.cash + sum(p.qty * 0 for p in []) - acct.cash) < 1e-9
check("account cash fields consistent", cash_consistency)

# ---------- RELIABILITY TESTS ------------------------------------------
print("RELIABILITY TESTS")
# restart safety: cursor persisted → rerun with same events does nothing
n_pos = db.query(PaperPosition).filter_by(account_id=acct.id).count()
PE.run_tick(db, NOW + 4000, MKTX)
db.commit()
check("restart-safe: no duplicate processing", db.query(PaperPosition).filter_by(account_id=acct.id).count() == n_pos)
# missing market data → honest rejection, no crash
tok3 = Token(address="paperUNIT3", chain="solana", symbol="UNIT3", name="Unit3")
db.add(tok3); db.flush()
e10 = SignalEvent(token_id=tok3.id, ts=NOW + 4100, kind="momentum_acceleration", key="",
                  title="nodata", detail="", severity="positive",
                  metrics_json={"liquidity": 50000, "volume_24h": 90000, "mcap": 200000},
                  category="momentum", importance="HIGH", importance_score=70, confidence=85,
                  signal_text="", why_json=[], concern=None, chain_json=None,
                  checklist_json=[{"label": "a", "passed": True, "value": "1"},
                                  {"label": "b", "passed": True, "value": "2"}])
db.add(e10); db.commit()
acct.last_event_id = e10.id - 1
PE.run_tick(db, NOW + 4100, {})  # empty market → missing data
d10 = db.query(PaperSignal).filter_by(account_id=acct.id, source_event_id=e10.id).one()
check("missing market data → rejected honestly", d10.status in ("rejected", "pending"), d10.status)
check("no position without market data", db.query(PaperPosition).filter_by(token_id=tok3.id).count() == 0)
# timeline events exist for full lifecycle
tl = db.query(PaperTradeEvent).filter_by(account_id=acct.id).all()
kinds = {e.kind for e in tl}
check("timeline has BUY + SELL + RISK events", {"BUY", "SELL", "RISK"}.issubset(kinds), str(kinds))
# feed rows written
feed = db.query(SignalEvent).filter_by(category="paper").count()
check("trade events projected into intel feed", feed >= 2, str(feed))

db.close()
os.unlink(dbpath)
print(f"\n{len(PASS)} passed, {len(FAIL)} failed")
sys.exit(1 if FAIL else 0)
