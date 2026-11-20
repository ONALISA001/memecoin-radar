"""Paper trader x AI verdict rules: entry filter, block, sizing, exit, tagging, analytics."""
import datetime as dt, os, sys, tempfile, time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
fd, dbpath = tempfile.mkstemp(suffix=".db"); os.close(fd)
os.environ["DATABASE_URL"] = f"sqlite:///{dbpath}"

from app.database import Base, engine, SessionLocal  # noqa: E402
from app.models import PaperPosition, PaperSignal, SignalEvent, Token, TokenVerdict, User  # noqa: E402
from app.paper import engine as PE, filters as F  # noqa: E402
from app.security import hash_password  # noqa: E402

Base.metadata.create_all(engine)
db = SessionLocal()
NOW = int(time.time())
PASS, FAIL = [], []
def check(name, cond, extra=""):
    (PASS if cond else FAIL).append(name); print(("  ✅" if cond else "  ❌"), name, extra)

SYMS = ["WATCHY", "AVOIDY", "NONE", "STRONG", "STALE"]
toks = {s: Token(address=f"pv{s}", chain="solana", symbol=s, name=s, token_created_at=(NOW - 7200) * 1000)
        for s in SYMS}
db.add_all(toks.values()); db.flush()
utc = lambda secs_ago: dt.datetime.utcfromtimestamp(NOW - secs_ago)  # noqa: E731
for sym, rating, conf, ago in (("WATCHY", "WATCH", "medium", 1800), ("AVOIDY", "AVOID", "high", 1800),
                               ("STRONG", "STRONG_WATCH", "high", 600), ("STALE", "WATCH", "high", 10 * 3600)):
    db.add(TokenVerdict(token_id=toks[sym].id, rating=rating, confidence=conf, source="llm", created_at=utc(ago)))
db.add(TokenVerdict(token_id=toks["NONE"].id, rating="REVIEW", confidence="low", source="gate", created_at=utc(60)))
MKT = {t.id: {"price": 0.0001, "liq": 80000, "mcap": 900000, "volume_24h": 300000, "price_chg_1h": 3.0,
              "momentum": 74.0, "sellp": 25.0, "liq_chg_3h": 2.0, "risk": 35.0, "regime": "BULLISH"}
       for t in toks.values()}

BASE = {"min_confidence": 60, "max_position_usd": 400.0, "take_profit_pct": 50.0, "stop_loss_pct": 10.0,
        "max_open_positions": 10, "max_exposure_pct": 90.0, "min_cash_reserve_pct": 5.0,
        "max_loss_per_trade_pct": 50.0}
SCEN = {"off": {}, "watch+": {"verdict_filter": 1}, "strong": {"verdict_filter": 2},
        "block": {"block_bad_verdicts": 1}, "sizing": {"verdict_sizing": 1}, "exit": {"verdict_exit": 1}}
accts = {}
for i, (name, extra) in enumerate(SCEN.items()):
    u = User(email=f"pv{i}@t.dev", password_hash=hash_password("x"), api_token=f"pv{i}")
    db.add(u); db.flush()
    a = PE.get_or_create_account(db, u.id)
    a.settings_json = {**BASE, **extra}
    PE.apply_settings(db, a, a.settings_json)
    accts[name] = a
db.commit()
for t in toks.values():
    db.add(SignalEvent(token_id=t.id, ts=NOW, kind="momentum_acceleration", key="", title=f"momentum — ${t.symbol}",
                       detail="x", severity="positive", category="momentum", importance="HIGH", importance_score=80,
                       confidence=82, signal_text="x", why_json=["m"], concern=None, chain_json=None,
                       metrics_json={"momentum": 74, "sell_pressure": 25, "liq_health": 70, "liquidity": 80000,
                                     "volume_24h": 300000, "mcap": 900000, "buy_share": 61},
                       checklist_json=[{"label": "a", "passed": True, "value": "x"},
                                       {"label": "b", "passed": True, "value": "y"}]))
db.flush()
PE.run_tick(db, NOW, MKT)
db.commit()

def bought(name):
    pos = db.query(PaperPosition).filter_by(account_id=accts[name].id).all()
    return {db.get(Token, p.token_id).symbol: p for p in pos}

def why(name, sym):
    s = db.query(PaperSignal).filter_by(account_id=accts[name].id, token_id=toks[sym].id).first()
    return s.reject_reason if s else None

print("ENTRY RULES")
check("rules off: trades every coin (unchanged behaviour)", set(bought("off")) == set(SYMS), str(sorted(bought("off"))))
w = bought("watch+")
check("Watch+ filter: only fresh Watch / Strong watch", set(w) == {"WATCHY", "STRONG"}, str(sorted(w)))
check("…Avoid rejected with its rating", "Avoid" in (why("watch+", "AVOIDY") or ""), why("watch+", "AVOIDY"))
check("…no verdict (REVIEW only) rejected", "no verdict" in (why("watch+", "NONE") or ""), why("watch+", "NONE"))
check("…stale verdict rejected", "too old" in (why("watch+", "STALE") or ""), why("watch+", "STALE"))
check("Strong-only filter", set(bought("strong")) == {"STRONG"}, str(sorted(bought("strong"))))
b = bought("block")
check("block bad verdicts: Avoid blocked, coins without a verdict still allowed",
      "AVOIDY" not in b and {"WATCHY", "NONE", "STRONG", "STALE"} <= set(b), str(sorted(b)))

print("TAGGING & SIZING")
off = bought("off")
check("every trade tagged with its entry verdict", off["WATCHY"].metrics_json["verdict"]["rating"] == "WATCH"
      and off["STRONG"].metrics_json["verdict"]["confidence"] == "high" and "verdict" not in off["NONE"].metrics_json)
sz = bought("sizing")
base = off["STRONG"].size_usd
check("Strong watch (high) keeps full size", abs(sz["STRONG"].size_usd - base) < 0.01, f"{sz['STRONG'].size_usd} vs {base}")
check("Watch (medium) sized at 60%", abs(sz["WATCHY"].size_usd - round(base * 0.6, 2)) < 0.01, str(sz["WATCHY"].size_usd))
check("no verdict => size unchanged", abs(sz["NONE"].size_usd - base) < 0.01)
check("multiplier recorded on the trade", sz["WATCHY"].metrics_json.get("verdict_size_mult") == 0.6)
check("size never above the risk-rule size", all(p.size_usd <= base + 0.01 for p in sz.values()))

print("EXIT ON DOWNGRADE")
ex_pos = bought("exit")["WATCHY"]
PE.run_tick(db, NOW + 300, MKT); db.commit()
db.refresh(ex_pos)
check("no downgrade => position stays open", ex_pos.status == "open")
db.add(TokenVerdict(token_id=toks["WATCHY"].id, rating="RUG_RISK", confidence="high", source="gate",
                    created_at=dt.datetime.utcfromtimestamp(NOW + 400)))
db.commit()
PE.run_tick(db, NOW + 600, MKT); db.commit()
db.refresh(ex_pos)
check("re-judged Rug risk after entry => VERDICT_EXIT", ex_pos.status == "closed" and ex_pos.exit_reason == "VERDICT_EXIT",
      f"{ex_pos.status} {ex_pos.exit_reason}")
other = bought("off")["WATCHY"]
db.refresh(other)
check("accounts without the exit rule keep holding", other.status == "open")
stale_avoid = bought("exit").get("AVOIDY")
check("an Avoid verdict from BEFORE entry does not force an exit", stale_avoid is not None and stale_avoid.status == "open")

print("ANALYTICS")
from app.routers.paper_api import paper_analytics  # noqa: E402
u = db.get(User, accts["exit"].user_id)
an = paper_analytics(db=db, user=u)
rows = {e["label"]: e for e in an.get("signal_effectiveness", [])}
check("analytics compares trades by entry verdict", rows.get("AI verdict: Watch", {}).get("trades") == 1, str(rows.get("AI verdict: Watch")))

db.close()
print(f"\n{len(PASS)} passed, {len(FAIL)} failed")
os.remove(dbpath)
sys.exit(1 if FAIL else 0)
