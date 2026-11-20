"""Strategy Lab: preset accounts, entry filters, let-winners-run exits, pullback entries."""
import os, sys, tempfile, time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
fd, dbpath = tempfile.mkstemp(suffix=".db"); os.close(fd)
os.environ["DATABASE_URL"] = f"sqlite:///{dbpath}"

from app.database import Base, engine, SessionLocal  # noqa: E402
from app.models import (DemoAccount, PaperPosition, PaperSignal, SignalEvent, Token,  # noqa: E402
                        TokenRiskCheck, User)
from app.paper import config as PC, engine as PE, filters as F, lab as LAB  # noqa: E402
from app.routers import paper_api as API  # noqa: E402
from app.security import hash_password  # noqa: E402

Base.metadata.create_all(engine)
db = SessionLocal()
PASS, FAIL = [], []
def check(name, cond, extra=""):
    (PASS if cond else FAIL).append(name); print(("  ✅" if cond else "  ❌"), name, extra)

NOW = int(time.time())
usr = User(email="lab@t.dev", password_hash=hash_password("x"), api_token="lt")
young = Token(address="labYOUNG", chain="solana", symbol="YNG", token_created_at=(NOW - 1200) * 1000)
old = Token(address="labOLD", chain="solana", symbol="OLD", token_created_at=(NOW - 3 * 3600) * 1000,
            discovery_source="new_pool", launch_alert="sent")
trend = Token(address="labTREND", chain="solana", symbol="TRD", token_created_at=(NOW - 10 * 3600) * 1000)
db.add_all([usr, young, old, trend]); db.flush()

def mk(price=0.001, **kw):
    m = {"price": price, "liq": 90000, "mcap": 900000, "volume_24h": 400000, "price_chg_1h": 12.0,
         "momentum": 74.0, "sellp": 25.0, "liq_chg_3h": 2.0, "risk": 35.0, "regime": "BULLISH",
         "vol_liq_ratio": 4.4, "buy_share_pct": 60.0, "price_chg_15m": 1.0, "price_chg_24h": 20.0}
    m.update(kw); return m
MKT = {young.id: mk(), old.id: mk(), trend.id: mk(price_chg_24h=5.0)}

def event(tok, ts):
    e = SignalEvent(token_id=tok.id, ts=ts, kind="momentum_acceleration", key="", title="mom", detail="m",
                    severity="positive", category="momentum", importance="HIGH", importance_score=80,
                    confidence=85, signal_text="m", why_json=[], metrics_json={"momentum": 74, "liquidity": 90000,
                    "volume_24h": 400000, "mcap": 900000},
                    checklist_json=[{"label": "a", "passed": True, "value": "x"},
                                    {"label": "b", "passed": True, "value": "y"}])
    db.add(e); db.flush(); return e

# --- filters off by default --------------------------------------------------
check("all strategy rules off by default", F.strategy_checks(db, PC.merged_settings({}), young, mk(), NOW) == [])
vm = PC.merged_settings(PC.PRESETS["verified_momentum"]["settings"])
res = {n: ok for n, ok, _ in F.strategy_checks(db, vm, young, mk(price_chg_1h=90, vol_liq_ratio=30, buy_share_pct=45), NOW)}
check("young coin fails age rule", res["coin age"] is False)
check("unverified contract fails", res["verified contract"] is False and res["wallet concentration"] is False)
check("wash-volume / chasing / weak buying caught", not res["volume vs liquidity"] and not res["not chasing"]
      and not res["buyers in control"])

# --- lab accounts --------------------------------------------------------------
main = PE.get_or_create_account(db, usr.id); db.commit()
accts = LAB.start_lab(db, usr.id); db.commit()
check("one account per preset created", sorted(accts) == sorted(PC.PRESETS))
check("start is idempotent", len(LAB.start_lab(db, usr.id)) == len(PC.PRESETS) and
      db.query(DemoAccount).filter(DemoAccount.strategy_key.isnot(None)).count() == len(PC.PRESETS))
check("main account stays the main account", PE.get_or_create_account(db, usr.id).id == main.id)
check("presets get their own strategy versions", len({a.strategy_version_id for a in accts.values()}) == len(PC.PRESETS))

# --- first tick: momentum events on a young and a 3h-old fresh-pool coin -------------
event(young, NOW); event(old, NOW)
PE.run_tick(db, NOW, MKT); db.commit()
def positions(key):
    acct = main if key == "main" else accts[key]
    return {p.token_id for p in db.query(PaperPosition).filter_by(account_id=acct.id).all()}
check("baseline buys both (current rules)", positions("baseline") == {young.id, old.id})
check("main account unchanged: buys both", positions("main") == {young.id, old.id})
check("Verified Momentum: no buys without verified contracts", positions("verified_momentum") == set())
sig = db.query(PaperSignal).filter_by(account_id=accts["verified_momentum"].id, token_id=old.id).one()
check("rejection explains the rule", sig.status == "rejected" and "verified contract" in sig.reject_reason,
      sig.reject_reason)
check("Trend Pullback ignores momentum events", positions("pullback") == set())
paper_feed = db.query(SignalEvent).filter(SignalEvent.category == "paper").count()
check("lab trades stay out of the public feed (only main's 2 buys posted)", paper_feed == 2, str(paper_feed))

# --- verify the 3h coin; Verified Momentum + Launch Survivor buy it -------------------
db.add(TokenRiskCheck(token_id=old.id, ok=True, mint_authority=None, freeze_authority=None, token_2022=False,
                      top1_wallet_pct=4.0, top10_wallet_pct=20.0))
db.add(TokenRiskCheck(token_id=trend.id, ok=True, mint_authority=None, freeze_authority=None, token_2022=False,
                      top1_wallet_pct=3.0, top10_wallet_pct=18.0))
db.commit()
event(old, NOW + 300)
PE.run_tick(db, NOW + 300, MKT); db.commit()
check("Verified Momentum buys the verified 3h-old coin", positions("verified_momentum") == {old.id})
check("Launch Survivor buys the fresh pool that passed launch checks", positions("launch_survivor") == {old.id})
pos = db.query(PaperPosition).filter_by(account_id=accts["verified_momentum"].id).one()
check("no fixed take-profit (let winners run)", pos.target_price is None)

# --- exits: +25% → break-even stop; +35% arms trail; trail exit locks profit ----------
up = lambda f: {**MKT, old.id: mk(price=0.001 * f)}  # noqa: E731
PE.run_tick(db, NOW + 600, up(1.25)); db.commit(); db.refresh(pos)
check("break-even stop after +20%", pos.status == "open" and pos.stop_price >= pos.entry_price, f"{pos.stop_price/pos.entry_price:.3f}")
PE.run_tick(db, NOW + 900, up(1.28)); db.commit(); db.refresh(pos)
check("trailing stop not armed below +30%", pos.status == "open")
PE.run_tick(db, NOW + 1200, up(1.60)); db.commit(); db.refresh(pos)
check("runner kept past the old +35% target", pos.status == "open")
PE.run_tick(db, NOW + 1500, up(1.25)); db.commit(); db.refresh(pos)
check("trailing stop exit locks the gain", pos.status == "closed" and pos.exit_reason == "TRAILING_STOP"
      and pos.realized_pnl > 0, f"{pos.exit_reason} {pos.realized_pnl}")
bl = db.query(PaperPosition).filter_by(account_id=accts["baseline"].id, token_id=old.id).one()
check("baseline took its fixed +35% target instead", bl.exit_reason == "TAKE_PROFIT")

# --- pullback entries -------------------------------------------------------------
pb = PC.merged_settings(PC.PRESETS["pullback"]["settings"])
ok, _ = F.pullback_candidate(pb, mk(price_chg_24h=60, price_chg_1h=-10, price_chg_15m=1.5))
bad, _ = F.pullback_candidate(pb, mk(price_chg_24h=60, price_chg_1h=-10, price_chg_15m=-2))
check("pullback needs uptrend + dip + bounce", ok and not bad)
dip = {trend.id: mk(price_chg_24h=70, price_chg_1h=-12, price_chg_15m=2.0)}
PE.run_tick(db, NOW + 1800, dip); db.commit()
check("Trend Pullback buys the dip in an uptrend", positions("pullback") == {trend.id})
PE.run_tick(db, NOW + 2100, dip); db.commit()
n = db.query(PaperSignal).filter_by(account_id=accts["pullback"].id, kind="pullback_bounce").count()
check("one pullback entry per coin per cooldown", n == 1, str(n))
check("pullback signals don't collide on the dedupe key", db.query(PaperSignal).filter(
    PaperSignal.source_event_id.is_(None)).count() == n)

# --- API ----------------------------------------------------------------------------
cmp_ = API.paper_lab(db=db, user=usr)
names = [r["key"] for r in cmp_["presets"]]
check("compare lists every preset + main", names == list(PC.PRESETS) and cmp_["main"]["key"] == "main")
vmr = next(r for r in cmp_["presets"] if r["key"] == "verified_momentum")
check("stats computed", vmr["trades"] == 1 and vmr["win_rate_pct"] == 100.0 and vmr["exits"] == {"TRAILING_STOP": 1}
      and vmr["enough_trades"] is False)
API.paper_lab_apply("launch_survivor", db=db, user=usr)
eff = PE.settings_for(PE.get_or_create_account(db, usr.id))
check("apply preset to main", eff["fresh_pool_only"] is True and eff["stop_loss_pct"] == 20.0)
API.paper_settings_update({"stop_loss_pct": 18.0}, db=db, user=usr)
eff = PE.settings_for(PE.get_or_create_account(db, usr.id))
check("settings form save keeps preset rules", eff["fresh_pool_only"] is True and eff["stop_loss_pct"] == 18.0)
API.paper_lab_reset(db=db, user=usr)
check("lab reset clears lab positions only", db.query(PaperPosition).filter(
    PaperPosition.account_id.in_([a.id for a in accts.values()])).count() == 0
      and db.query(PaperPosition).filter_by(account_id=main.id).count() == 2)

# --- confidence ranges -------------------------------------------------------------------
check("no range under 10 trades", LAB.mean_ci([5.0] * 9) is None)
lo, hi = LAB.mean_ci([4.0, 6.0, 5.0, 3.0, 7.0, 5.5, 4.5, 6.5, 5.0, 5.0, 4.8, 5.2])
check("consistent winners => range above 0", 0 < lo < 5.13 < hi, f"{lo} {hi}")
lo, hi = LAB.mean_ci([30.0, -12.0, -12.0, 25.0, -12.0, -12.0, 40.0, -12.0, -12.0, -12.0])
check("a few big wins among many losses => range spans 0 (luck can't be ruled out)", lo < 0 < hi, f"{lo} {hi}")

# --- units the engine really receives from an ingestion tick -------------------------
import asyncio  # noqa: E402
from app.providers.base import RawToken  # noqa: E402
from app.services import ingestion as ING  # noqa: E402
A = "UnitsAAAA" + "1" * 29
async def fake_discover():
    return ([RawToken(address=A, chain="solana", name="Units", symbol="UNT")],
            {A: dict(price_usd=0.001, mcap=2e6, fdv=2e6, liquidity_usd=50000, volume_h24=600000,
                     volume_h6=200000, volume_h1=40000, volume_m5=3000, pc_m5=1.0, pc_h1=-9.0, pc_h6=30.0,
                     pc_h24=75.0, txns_h1_buys=300, txns_h1_sells=100, txns_h24_buys=3100,
                     txns_h24_sells=2600, txns_m5_buys=18, txns_m5_sells=12, pair_liquidity_usd=50000)}, False)
ING.discover_tokens = fake_discover
seen = {}
real_run = ING.paper_engine.run_tick
def spy(db_, now_, market):
    seen.update(market); return real_run(db_, now_, market)
ING.paper_engine.run_tick = spy
os.environ["HELIUS_API_KEY"] = ""
asyncio.run(ING.tick())
m = next(iter(v for v in seen.values()), {})
with SessionLocal() as s2:
    tid = s2.query(Token).filter_by(address=A).one().id
m = seen.get(tid, {})
check("buy share arrives as a percent (75, not 0.75)", abs((m.get("buy_share_pct") or 0) - 75.0) < 1e-6, str(m.get("buy_share_pct")))
check("volume/liquidity uses 24h volume (600k / 50k = 12x)", abs((m.get("vol_liq_ratio") or 0) - 12.0) < 0.01, str(m.get("vol_liq_ratio")))
check("24h/1h change come from live DexScreener, not short history", m.get("price_chg_24h") == 75.0 and m.get("price_chg_1h") == -9.0,
      f"{m.get('price_chg_24h')} {m.get('price_chg_1h')}")

db.close()
print(f"\n{len(PASS)} passed, {len(FAIL)} failed")
sys.exit(1 if FAIL else 0)
