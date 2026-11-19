"""Paper trader circuit breakers pause and AUTO-RESUME (they used to be permanent)."""
import os, sys, tempfile, time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
fd, dbpath = tempfile.mkstemp(suffix=".db"); os.close(fd)
os.environ["DATABASE_URL"] = f"sqlite:///{dbpath}"

from app.database import Base, engine, SessionLocal  # noqa: E402
from app.models import PaperPosition, PaperSignal, PaperTradeEvent, SignalEvent, Token, User  # noqa: E402
from app.paper import engine as PE  # noqa: E402
from app.routers import paper_api as API  # noqa: E402
from app.security import hash_password  # noqa: E402

Base.metadata.create_all(engine)
db = SessionLocal()
PASS, FAIL = [], []
def check(name, cond, extra=""):
    (PASS if cond else FAIL).append(name); print(("  ✅" if cond else "  ❌"), name, extra)

NOW = (int(time.time()) // 86400) * 86400 + 3600        # 01:00 UTC today
tok = Token(address="resumeTEST", chain="solana", symbol="RSM", token_created_at=(NOW - 7200) * 1000)
usr = User(email="r@t.dev", password_hash=hash_password("x"), api_token="rt")
db.add_all([tok, usr]); db.flush()
MKT = {tok.id: {"price": 0.0001, "liq": 80000, "mcap": 900000, "volume_24h": 300000, "price_chg_1h": 3.0,
                "momentum": 74.0, "sellp": 25.0, "liq_chg_3h": 2.0, "risk": 35.0, "regime": "BULLISH"}}
acct = PE.get_or_create_account(db, usr.id)
db.commit()

def add_event(ts):
    e = SignalEvent(token_id=tok.id, ts=ts, kind="momentum_acceleration", key="", title="m", detail="m",
                    severity="positive", category="momentum", importance="HIGH", importance_score=80,
                    confidence=85, signal_text="m", why_json=[], metrics_json={"liquidity": 80000,
                    "volume_24h": 300000, "mcap": 900000},
                    checklist_json=[{"label": "a", "passed": True, "value": "x"},
                                    {"label": "b", "passed": True, "value": "y"}])
    db.add(e); db.flush(); return e

def events(kind=None):
    q = db.query(PaperTradeEvent).filter_by(account_id=acct.id)
    return [e for e in q.all() if kind is None or kind in (e.title or "")]

# 5 losses in a row → pause
acct.consec_losses = 5
PE.run_tick(db, NOW, MKT); db.commit()
h = PE.halt_status(acct, NOW)
check("5 losses in a row => paused", h["active"] and h["reason"] == "loss_streak")
check("pause lasts 3h by default", acct.halt_until == NOW + 3 * 3600, str(acct.halt_until - NOW))
check("pause announced in the timeline", len(events("TRADING PAUSED")) == 1)

add_event(NOW + 600)
PE.run_tick(db, NOW + 600, MKT); db.commit()
sig = db.query(PaperSignal).filter_by(account_id=acct.id).order_by(PaperSignal.id.desc()).first()
check("buy signal during pause is rejected", sig.status == "rejected" and "cool-off" in sig.reject_reason,
      sig.reject_reason)
check("no position opened while paused", db.query(PaperPosition).filter_by(account_id=acct.id).count() == 0)
check("pause announced only once", len(events("TRADING PAUSED")) == 1)

# after 3h → auto-resume, streak reset, trades again
PE.run_tick(db, NOW + 3 * 3600, MKT); db.commit()
check("auto-resumed after the cool-off", not PE.halt_status(acct, NOW + 3 * 3600)["active"]
      and len(events("TRADING RESUMED")) == 1)
check("loss streak reset to 0", acct.consec_losses == 0)
add_event(NOW + 3 * 3600 + 60)
PE.run_tick(db, NOW + 3 * 3600 + 60, MKT); db.commit()
check("trades again after resume", db.query(PaperPosition).filter_by(account_id=acct.id).count() == 1)
check("stale signal from the pause wasn't replayed", db.query(PaperPosition).filter_by(account_id=acct.id).count() == 1)

# custom cool-off
PE.apply_settings(db, acct, {"loss_streak_cooldown_hours": 1.5}); db.commit()
acct.consec_losses = 5
t = NOW + 4 * 3600
PE.run_tick(db, t, MKT); db.commit()
check("cool-off length follows settings", acct.halt_until == t + 5400)

# manual resume via API
res = API.paper_resume(db=db, user=usr)
check("Resume now ends the pause immediately", res["resumed"] and not res["halt"]["active"]
      and acct.consec_losses == 0 and len(events("TRADING RESUMED (manual)")) == 1)
check("summary exposes halt status", API.paper_summary(db=db, user=usr)["halt"]["active"] is False)

# drawdown → pause until next UTC day, then re-anchored
for p in db.query(PaperPosition).filter_by(account_id=acct.id, status="open").all():
    PE.manual_close(db, acct, p.id, t, MKT)
acct.consec_losses = 0
acct.peak_equity = acct.cash / 0.70                    # equity is 30% below peak
db.commit()
t2 = t + 600
PE.run_tick(db, t2, MKT); db.commit()
h = PE.halt_status(acct, t2)
next_day = t2 - (t2 % 86400) + 86400
check("30% drawdown => paused until next UTC day", h["active"] and h["reason"] == "drawdown"
      and acct.halt_until == next_day, f"{h} {next_day}")
PE.run_tick(db, t2 + 300, MKT); db.commit()
check("drawdown pause holds while still below the limit", PE.halt_status(acct, t2 + 300)["active"])
PE.run_tick(db, next_day + 60, MKT); db.commit()
pf = PE.portfolio_state(db, acct, MKT)
check("next day: resumed with drawdown re-measured from current equity",
      not PE.halt_status(acct, next_day + 60)["active"] and pf["drawdown_pct"] < 1.0, f"{pf['drawdown_pct']:.2f}")

# drawdown pause lifts early if equity recovers above the limit
acct.peak_equity = acct.cash / 0.70; db.commit()
PE.run_tick(db, next_day + 90, MKT); db.commit()
paused = PE.halt_status(acct, next_day + 90)["active"]
acct.peak_equity = acct.cash * 1.05; db.commit()       # recovered: only 5% below peak
PE.run_tick(db, next_day + 100, MKT); db.commit()
check("drawdown pause lifts early when equity recovers", paused and not PE.halt_status(acct, next_day + 100)["active"])

# account reset clears any pause
acct.consec_losses = 5
PE.run_tick(db, next_day + 120, MKT); db.commit()
PE.reset_account(db, acct); db.commit()
check("account reset clears a pause", acct.halt_until is None and acct.halt_reason is None)

db.close()
print(f"\n{len(PASS)} passed, {len(FAIL)} failed")
sys.exit(1 if FAIL else 0)
