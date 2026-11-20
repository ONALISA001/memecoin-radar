"""Trade post-mortems: cause classification, provisional -> final reviews, roll-up + suggestions."""
import os, sys, tempfile, time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
fd, dbpath = tempfile.mkstemp(suffix=".db"); os.close(fd)
os.environ["DATABASE_URL"] = f"sqlite:///{dbpath}"

from app.database import Base, engine, SessionLocal  # noqa: E402
from app.models import (LiquiditySnapshot, PaperPosition, PaperTradeReview, PriceSnapshot,  # noqa: E402
                        SignalEvent, Token, User)
from app.paper import engine as PE, postmortem as PM  # noqa: E402
from app.routers import paper_api as API  # noqa: E402
from app.security import hash_password  # noqa: E402

Base.metadata.create_all(engine)
db = SessionLocal()
PASS, FAIL = [], []
def check(name, cond, extra=""):
    (PASS if cond else FAIL).append(name); print(("  ✅" if cond else "  ❌"), name, extra)

# --- classify(): one case per cause ------------------------------------------------
def F(**kw):
    f = {"pnl_pct": -12.0, "exit_reason": "STOP_LOSS", "mfe_pct": 2.0, "mae_pct": -13.0, "post_max_pct": None,
         "post_min_pct": None, "post_hours": 24.0, "target_pct": 35.0, "reached_target_after": False,
         "rug_during": False, "rug_after": False, "entry_chg_1h": 10.0, "had_fixed_target": True}
    f.update(kw); return f
cases = [
    ("RUG", F(rug_during=True, mae_pct=-85.0)),
    ("STOP_THEN_RECOVERED", F(reached_target_after=True, post_max_pct=40.0)),
    ("EARLY_EXIT", F(exit_reason="RISK_EXIT", pnl_pct=-6.0, post_max_pct=18.0)),
    ("GAVE_BACK", F(mfe_pct=22.0)),
    ("CHASED", F(entry_chg_1h=75.0, mfe_pct=1.0)),
    ("SAVED_BY_EXIT", F(exit_reason="SELL_SIGNAL", pnl_pct=-7.0, post_min_pct=-40.0, post_max_pct=-5.0)),
    ("SAVED_BY_EXIT", F(pnl_pct=-12.0, post_min_pct=-20.0, rug_after=True)),
    ("TIMED_OUT", F(exit_reason="MAX_HOLD", pnl_pct=-9.0, post_max_pct=2.0)),
    ("COSTS", F(exit_reason="MANUAL", pnl_pct=-3.4, mfe_pct=1.0)),
    ("FAILED_SETUP", F()),
    ("LEFT_ON_TABLE", F(pnl_pct=33.0, exit_reason="TAKE_PROFIT", post_max_pct=120.0)),
    ("CLEAN_WIN", F(pnl_pct=33.0, exit_reason="TAKE_PROFIT", post_max_pct=40.0)),
    ("CLEAN_WIN", F(pnl_pct=50.0, exit_reason="TRAILING_STOP", post_max_pct=200.0, had_fixed_target=False)),
]
for want, f in cases:
    got, lesson = PM.classify(f)
    check(f"classify → {want}", got == want and len(lesson) > 10, f"got {got}: {lesson}")
check("a stop-out that kept falling is not blamed on the stop",
      PM.classify(F(post_max_pct=-10.0, post_min_pct=-60.0))[0] == "SAVED_BY_EXIT")
check("rug during the trade wins over every other cause",
      PM.classify(F(rug_during=True, mae_pct=-90, reached_target_after=True, mfe_pct=30))[0] == "RUG")

# --- end-to-end through the paper engine -------------------------------------------
NOW = int(time.time()) // 300 * 300 - 3 * 86400
usr = User(email="pm@t.dev", password_hash=hash_password("x"), api_token="pmt")
db.add(usr); db.flush()
acct = PE.get_or_create_account(db, usr.id); db.commit()

def mk(price, **kw):
    m = {"price": price, "liq": 90000, "mcap": 900000, "volume_24h": 400000, "price_chg_1h": 12.0,
         "momentum": 74.0, "sellp": 25.0, "liq_chg_3h": 2.0, "risk": 35.0, "regime": "BULLISH",
         "vol_liq_ratio": 4.4, "buy_share_pct": 60.0, "price_chg_15m": 1.0, "price_chg_24h": 20.0}
    m.update(kw); return m

def event(tok, ts):
    e = SignalEvent(token_id=tok.id, ts=ts, kind="momentum_acceleration", key="", title="mom", detail="m",
                    severity="positive", category="momentum", importance="HIGH", importance_score=80,
                    confidence=85, signal_text="m", why_json=[], metrics_json={"momentum": 74, "liquidity": 90000,
                    "volume_24h": 400000, "mcap": 900000},
                    checklist_json=[{"label": "a", "passed": True, "value": "x"},
                                    {"label": "b", "passed": True, "value": "y"}])
    db.add(e); db.flush(); return e

def snaps(tok, path, t0, liq=90000.0):
    for i, px in enumerate(path):
        db.add(PriceSnapshot(token_id=tok.id, ts=t0 + 300 * i, price_usd=px))
        db.add(LiquiditySnapshot(token_id=tok.id, ts=t0 + 300 * i, liquidity_usd=liq))

def trade(i, after_path, t0, chg_1h=12.0):
    """Buy at 0.001, stop out at 0.00085 (-15%), then the after-exit path."""
    # clear the loss-streak / daily-loss brakes: this suite is about reviews, not brakes
    acct.consec_losses, acct.day_realized, acct.halt_until, acct.halt_reason = 0, 0.0, None, None
    tok = Token(address=f"pm{i}", chain="solana", symbol=f"PM{i}", token_created_at=(t0 - 86400) * 1000)
    db.add(tok); db.flush()
    event(tok, t0)
    PE.run_tick(db, t0, {tok.id: mk(0.001, price_chg_1h=chg_1h)}); db.commit()
    snaps(tok, [0.001, 0.00098, 0.00092], t0)
    PE.run_tick(db, t0 + 900, {tok.id: mk(0.00085)}); db.commit()
    snaps(tok, after_path, t0 + 1200)
    db.commit()
    return db.query(PaperPosition).filter_by(token_id=tok.id).one()

p1 = trade(1, [0.0009, 0.0011, 0.0014, 0.0015], NOW)
check("trade opened and stopped out", p1.status == "closed" and p1.exit_reason == "STOP_LOSS", p1.exit_reason)
check("entry facts recorded on the position",
      (p1.metrics_json or {}).get("entry_market", {}).get("price_chg_1h") == 12.0
      and p1.metrics_json.get("signal_kind") == "momentum_acceleration")
check("worst price while open tracked", p1.trough_price is not None and p1.trough_price <= 0.00085)
r1 = db.query(PaperTradeReview).filter_by(position_id=p1.id).one_or_none()
check("provisional review written at close", r1 is not None and r1.status == "provisional", getattr(r1, "status", None))
check("provisional review uses in-trade data only", r1 is not None and r1.post_max_pct is None)

# the after-exit window passes: final review sees the recovery
later = NOW + 26 * 3600
n = PM.review_due(db, later); db.commit()
r1 = db.query(PaperTradeReview).filter_by(position_id=p1.id).one()
check("review finalized after the window", n >= 1 and r1.status == "final")
check("stopped out, then recovered past the target", r1.cause == "STOP_THEN_RECOVERED", f"{r1.cause}: {r1.lesson}")
check("after-exit best is vs entry and real", r1.post_max_pct is not None and r1.post_max_pct > 35, str(r1.post_max_pct))
check("final reviews are never rewritten",
      PM.review_due(db, later + 3600) == 0 and PM.review_position(db, p1, later + 3600).reviewed_ts == r1.reviewed_ts)

# a stop-out that keeps falling (and rugs) => the stop saved money
p2 = trade(2, [0.0007, 0.0004, 0.0001], NOW + 3600)
PM.review_due(db, later + 3600); db.commit()
r2 = db.query(PaperTradeReview).filter_by(position_id=p2.id).one()
check("rug after exit counts as the exit working", r2.cause == "SAVED_BY_EXIT" and r2.rugged, f"{r2.cause}")

# trades closed before post-mortems existed get back-filled
old = trade(3, [0.0009, 0.00088], NOW + 7200)
db.query(PaperTradeReview).filter_by(position_id=old.id).delete(); db.commit()
PM.review_due(db, later + 7200); db.commit()
check("old trades without a review are back-filled",
      db.query(PaperTradeReview).filter_by(position_id=old.id).count() == 1)

# --- roll-up: five more shake-outs => a stop-loss suggestion ---------------------------
for i in range(4, 9):
    trade(i, [0.0009, 0.0012, 0.0016], NOW + 3 * 3600 + i * 600)
PM.review_due(db, later + 6 * 3600); db.commit()
s = PM.summary(db, acct.id, PE.settings_for(acct), 30, now=later + 6 * 3600)
top = s["loss_causes"][0] if s["loss_causes"] else {}
check("summary counts every reviewed trade", s["reviewed"] == 8 and s["losses"] == 8, f"{s['reviewed']}/{s['losses']}")
check("biggest loss cause is the shake-out", top.get("cause") == "STOP_THEN_RECOVERED" and top.get("trades") == 6,
      str(top))
sug = {g["cause"]: g for g in s["suggestions"]}
st = sug.get("STOP_THEN_RECOVERED", {})
check("suggests a wider stop, with evidence and a trade-off",
      st.get("setting") == "stop_loss_pct" and st.get("suggested", 0) > st.get("current", 99)
      and "stop-outs" in st.get("why", "") and st.get("tradeoff"), str(st))
check("no suggestion from a cause seen fewer than MIN_CAUSE_N times", "SAVED_BY_EXIT" not in sug)
check("small samples are flagged", s["enough_data"] is False)
check("suggestions never change settings by themselves",
      PE.settings_for(acct)["stop_loss_pct"] == st.get("current"))
check("per-signal-kind breakdown present",
      s["by_signal_kind"] and s["by_signal_kind"][0]["kind"] == "momentum_acceleration")

# --- API --------------------------------------------------------------------------
out = API.paper_postmortems(account="main", days=30, db=db, user=usr)
check("API returns the main account roll-up", out["reviewed"] == 8 and out["account"]["key"] == "main")
try:
    API.paper_postmortems(account="pullback", days=30, db=db, user=usr); ok = False
except Exception as e:
    ok = getattr(e, "status_code", None) == 404
check("API 404s for a lab account that is not running", ok)
det = API.paper_trade_detail(position_id=p1.id, db=db, user=usr)
check("trade detail carries the review", (det.get("review") or {}).get("cause") == "STOP_THEN_RECOVERED")

# --- reset clears reviews with the positions ----------------------------------------
PE.reset_account(db, acct); db.commit()
check("reset removes reviews too", db.query(PaperTradeReview).filter_by(account_id=acct.id).count() == 0)

print(f"\n{len(PASS)} passed, {len(FAIL)} failed")
db.close()
sys.exit(1 if FAIL else 0)
