"""Early discovery (GeckoTerminal fresh pools) + launch-safety alerts. No network."""
import asyncio, datetime as dt, os, sys, tempfile, time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
fd, dbpath = tempfile.mkstemp(suffix=".db"); os.close(fd)
os.environ["DATABASE_URL"] = f"sqlite:///{dbpath}"
os.environ["HELIUS_API_KEY"] = "SECRETKEY123"
os.environ["LAUNCH_FORENSICS_ENABLED"] = "0"   # gated separately in test_launch_forensics.py
os.environ["SOLANA_ONLY"] = "1"
os.environ["MAX_TRACKED_TOKENS"] = "6"
os.environ["NEW_POOL_SLOTS"] = "2"

import httpx  # noqa: E402
from sqlalchemy import inspect, select, text  # noqa: E402
from app.database import Base, engine, SessionLocal, migrate_postgres  # noqa: E402

PASS, FAIL = [], []
def check(name, cond, extra=""):
    (PASS if cond else FAIL).append(name); print(("  ✅" if cond else "  ❌"), name, extra)

# --- migration: an OLD tokens table gets the new columns ----------------------
with engine.begin() as c:
    c.execute(text("""CREATE TABLE tokens (id INTEGER PRIMARY KEY, address VARCHAR(128) UNIQUE NOT NULL,
        chain VARCHAR(32), name VARCHAR(160), symbol VARCHAR(32), image_url VARCHAR(512), description TEXT,
        deployer VARCHAR(128), pair_address VARCHAR(128), dex VARCHAR(64), url VARCHAR(512),
        archetype VARCHAR(32), state_json JSON, first_seen_at DATETIME DEFAULT CURRENT_TIMESTAMP,
        token_created_at BIGINT, new_token_evented BOOLEAN)"""))
migrate_postgres()
cols = {c["name"] for c in inspect(engine).get_columns("tokens")}
check("sqlite migration adds discovery_source + launch_alert", {"discovery_source", "launch_alert"} <= cols)

from app.engine import telegram  # noqa: E402
from app.models import SystemLog, Token, TokenRiskCheck  # noqa: E402
from app.providers import geckoterminal as GT, helius as H  # noqa: E402
from app.providers.base import MarketTick, RawToken  # noqa: E402
from app.providers.dexscreener import DexscreenerProvider  # noqa: E402
from app.routers import data_api as API  # noqa: E402
from app.services import ingestion as ING  # noqa: E402
Base.metadata.create_all(engine)

NOW_MS = int(time.time() * 1000)
iso = lambda mins_ago: (dt.datetime.now(dt.timezone.utc) - dt.timedelta(minutes=mins_ago)).isoformat()  # noqa: E731

def pool(base, mins_ago=20, liq=25000, h1=(80, 40), sym="NEW", quote="So11111111111111111111111111111111111111112",
         dex="pump-fun", addr=None):
    return {"id": f"solana_{addr or base + 'pool'}", "type": "pool",
            "attributes": {"address": addr or base + "pool", "name": f"{sym} / SOL",
                           "pool_created_at": iso(mins_ago), "base_token_price_usd": "0.00012",
                           "fdv_usd": "120000", "market_cap_usd": None, "reserve_in_usd": str(liq),
                           "price_change_percentage": {"m5": "3.1", "h1": "45", "h6": "45", "h24": "45"},
                           "transactions": {"m5": {"buys": 9, "sells": 4}, "h1": {"buys": h1[0], "sells": h1[1]},
                                            "h24": {"buys": h1[0], "sells": h1[1]}},
                           "volume_usd": {"m5": "1200", "h1": "30000", "h6": "30000", "h24": "30000"}},
            "relationships": {"base_token": {"data": {"id": f"solana_{base}", "type": "token"}},
                              "quote_token": {"data": {"id": f"solana_{quote}", "type": "token"}},
                              "dex": {"data": {"id": dex, "type": "dex"}}}}

def inc(base, sym, name="Token"):
    return {"id": f"solana_{base}", "type": "token",
            "attributes": {"address": base, "symbol": sym, "name": name, "image_url": "missing.png"}}

F1, F2, F3, F4 = ("Fresh1" + "1" * 32, "Fresh2" + "2" * 32, "Fresh3" + "3" * 32, "Fresh4" + "4" * 32)
USDC = "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v"
SOL = "So11111111111111111111111111111111111111112"

# --- parsing / quality floor -----------------------------------------------
page = {"data": [pool(F1, sym="ONE"), pool(F2, sym="TWO", h1=(200, 100)),
                 pool("Old" + "9" * 35, mins_ago=60 * 7), pool("Thin" + "8" * 34, liq=900),
                 pool("Quiet" + "7" * 33, h1=(5, 3)), pool(USDC, sym="USDC"),
                 pool(SOL, quote=F3, sym="SOL")],
        "included": [inc(F1, "ONE", "One Coin"), inc(F2, "TWO")]}
parsed = GT.parse_new_pools(page, NOW_MS)
addrs = [r.address for r, _ in parsed]
check("fresh, liquid, active pools pass the floor", addrs == [F1, F2], str(addrs))
r1, m1 = parsed[0]
check("symbol/name from included token", r1.symbol == "ONE" and r1.name == "One Coin")
check("placeholder image dropped", r1.image_url == "")
check("dex + creation time captured", r1.dex == "pump-fun" and abs(r1.token_created_at - (NOW_MS - 20 * 60000)) < 5000)
check("real market tick from the pool", m1.liquidity_usd == 25000 and m1.txns_h1_buys == 80 and m1.fdv == 120000
      and m1.mcap == 120000 and m1.pc_h1 == 45.0)
check("source tagged new_pool", r1.extra["source"] == "new_pool")

def gt_transport(pages, fail_page=None):
    def handler(req):
        p = int(req.url.params.get("page", "1"))
        if p == fail_page:
            return httpx.Response(429, json={})
        return httpx.Response(200, json=pages[p - 1] if p <= len(pages) else {"data": []})
    return httpx.MockTransport(handler)

async def provider_tests():
    deeper = {"data": [pool(F1, liq=90000, addr="Deep" + "5" * 30, sym="ONE")], "included": [inc(F1, "ONE")]}
    res = await GT.GeckoTerminalProvider(transport=gt_transport([page, deeper])).new_pools(NOW_MS)
    check("same token in two pools => deepest pool kept", len([r for r, _ in res if r.address == F1]) == 1
          and next(m for r, m in res if r.address == F1).liquidity_usd == 90000)
    res = await GT.GeckoTerminalProvider(transport=gt_transport([page], fail_page=1)).new_pools(NOW_MS)
    check("rate-limited => empty list, no exception", res == [])
asyncio.run(provider_tests())

# --- discovery merge (providers mocked) --------------------------------------
P = [f"Promo{i}" + str(i) * 33 for i in range(1, 7)]
def mt(liq=50000, b=100, s=60):
    return MarketTick(price_usd=0.001, mcap=1e6, fdv=1e6, liquidity_usd=liq, pair_liquidity_usd=liq,
                      volume_h1=20000, volume_h24=90000, txns_h1_buys=b, txns_h1_sells=s,
                      txns_h24_buys=b * 5, txns_h24_sells=s * 5, txns_m5_buys=5, txns_m5_sells=3)
state = {"gt": [], "dex": {}}
async def fake_promo(self):
    return [RawToken(address=a, chain="solana", name=a[:6], symbol=a[:6], extra={"source": "boost"}) for a in P]
async def fake_market(self, raws):
    return {r.address: state["dex"][r.address] for r in raws if r.address in state["dex"]}
async def fake_new_pools(self, now_ms=None):
    return state["gt"]
DexscreenerProvider.discover = fake_promo
DexscreenerProvider.fetch_market = fake_market
GT.GeckoTerminalProvider.new_pools = fake_new_pools

def raw(a, mins_ago=20, sym=None):
    return RawToken(address=a, chain="solana", name=sym or a[:6], symbol=sym or a[:6], dex="pump-fun",
                    token_created_at=NOW_MS - mins_ago * 60000, extra={"source": "new_pool"})

class FakeHelius:
    plan = {}
    calls = []
    async def check_token(self, mint):
        FakeHelius.calls.append(mint)
        return FakeHelius.plan.get(mint) or H.OnchainRisk(ok=False, error="unplanned")
ING.helius_mod.HeliusProvider = FakeHelius

sent = []
def fake_send(text, **kw):
    sent.append(text)
    return {"ok": not text.startswith("FAIL"), "error": None}
telegram.send_text = fake_send
telegram.is_configured = lambda: True

def clean(**kw):
    base = dict(ok=True, supply=1e9, decimals=6, top1_pct=40, top10_pct=60, holders_sampled=20,
                holders_count=300, top1_wallet_pct=4.0, top10_wallet_pct=18.0, excluded_pct=40.0)
    base.update(kw); return H.OnchainRisk(**base)

async def run():
    # tick 1: F1 (on DexScreener), F2 (GeckoTerminal tick only), F4 dead on DexScreener
    state["gt"] = [(raw(F1, sym="ONE"), mt()), (raw(F2, sym="TWO"), mt(b=300, s=100)), (raw(F4, sym="FOUR"), mt())]
    state["dex"] = {a: mt() for a in P} | {F1: mt(b=150, s=90), F4: mt(b=3, s=2)}
    raws, ticks, fb = await ING.discover_tokens()
    srcs = {r.address: r.extra.get("source") for r in raws}
    fresh = [a for a, s in srcs.items() if s == "new_pool"]
    check("fresh pools get reserved slots, most-traded first", fresh == [F2, F1], str(fresh))
    check("dead launch (3 buys/h on DexScreener) dropped", F4 not in srcs)
    check("token not on DexScreener yet uses GeckoTerminal market data", ticks[F2]["txns_h1_buys"] == 300)
    check("total universe capped at MAX_TRACKED_TOKENS", len(raws) == 6 and not fb, str(len(raws)))
    check("promoted coins fill the remaining slots", sum(1 for s in srcs.values() if s == "boost") == 4)

    # Helius plan: F1 clean, F2 mint ON; also a stale-but-checked promo token competes
    FakeHelius.plan = {F1: clean(), F2: clean(mint_authority="Auth"), P[0]: clean()}
    ING.HELIUS_MAX_CHECKS_PER_TICK = 2
    await ING.tick()          # upserts tokens; runs onchain_checks (2 per tick)
    with SessionLocal() as db:
        toks = {t.address: t for t in db.execute(select(Token)).scalars().all()}
        check("discovery_source stored", toks[F1].discovery_source == "new_pool"
              and toks[P[1]].discovery_source == "boost")
    first_two = FakeHelius.calls[:2]
    check("never-checked tokens are checked first", len(first_two) == 2, str(first_two))

    ING.bucket_of = (lambda real: (lambda t: real(t) + 300))(ING.bucket_of)
    await ING.tick()          # second tick: launch decisions for F1/F2
    with SessionLocal() as db:
        f1 = db.execute(select(Token).where(Token.address == F1)).scalar_one()
        f2 = db.execute(select(Token).where(Token.address == F2)).scalar_one()
        p1 = db.execute(select(Token).where(Token.address == P[1])).scalar_one()
        check("clean fresh launch => alert sent", f1.launch_alert == "sent", str(f1.launch_alert))
        check("mint-authority launch => skipped, never alerted", f2.launch_alert == "skipped", str(f2.launch_alert))
        check("promoted coins never get launch alerts", p1.launch_alert is None)
        logs = [l.message for l in db.execute(select(SystemLog).where(SystemLog.source == "launch")).scalars()]
        check("skip reason logged", any("mint authority active" in m for m in logs), str(logs))
    msgs = [m for m in sent if "FRESH LAUNCH" in m]
    check("exactly one Telegram launch alert", len(msgs) == 1, str(len(msgs)))
    m = msgs[0] if msgs else ""
    check("alert names token, checks and the not-a-buy-signal warning",
          "$ONE" in m and "Mint authority renounced" in m and "Top-10 wallets 18%" in m
          and "Not a buy signal" in m and F1 in m, m[:300])

    # tick 3: feed empty — F1 stays tracked (sticky); no duplicate alert
    state["gt"] = []
    FakeHelius.calls.clear()
    ING.bucket_of = (lambda real: (lambda t: real(t) + 300))(ING.bucket_of)
    await ING.tick()
    raws, _, _ = await ING.discover_tokens()
    check("fresh token stays tracked after leaving the feed", F1 in [r.address for r in raws])
    check("F2 (not on DexScreener, gone from feed) drops out quietly", F2 not in [r.address for r in raws])
    check("no duplicate alert", len([m for m in sent if "FRESH LAUNCH" in m]) == 1)

    # delivery failure is recorded, not retried
    from app.services import launch_alerts as LA
    with SessionLocal() as db:
        f3 = Token(address=F3, chain="solana", symbol="FAIL", discovery_source="new_pool",
                   token_created_at=NOW_MS - 600000); db.add(f3); db.commit(); tid = f3.id
    LA.send_launch_alerts([(tid, "FAIL test")])
    with SessionLocal() as db:
        check("failed delivery marked failed", db.get(Token, tid).launch_alert == "failed")

    # queue order: a never-checked token beats a 30h-stale re-check
    with SessionLocal() as db:
        recent = dt.datetime.now(dt.timezone.utc).replace(tzinfo=None)
        for t in db.execute(select(Token)).scalars().all():      # everything else is fresh
            db.add(TokenRiskCheck(token_id=t.id, ok=True, holders_count=1, top10_wallet_pct=1.0,
                                  top1_wallet_pct=1.0, checked_at=recent))
        X, Y = "NeverChecked" + "x" * 26, "StaleChecked" + "y" * 26
        db.add(Token(address=X, chain="solana", symbol="X"))
        ty = Token(address=Y, chain="solana", symbol="Y"); db.add(ty); db.flush()
        db.add(TokenRiskCheck(token_id=ty.id, ok=True, holders_count=1, top10_wallet_pct=1.0,
                              top1_wallet_pct=1.0, checked_at=recent - dt.timedelta(hours=30)))
        db.commit()
    FakeHelius.calls.clear()
    ING.HELIUS_MAX_CHECKS_PER_TICK = 1
    await ING.onchain_checks()
    check("never-checked token beats a 30h-stale re-check", FakeHelius.calls == [X], str(FakeHelius.calls))

    # flood guard: 3 clean launches, cap 2 => 2 claimed now, 1 left pending
    from app import config as C
    with SessionLocal() as db:
        ids = []
        for i in range(3):
            t = Token(address=f"Flood{i}" + str(i) * 33, chain="solana", symbol=f"FL{i}",
                      discovery_source="new_pool", token_created_at=NOW_MS - 600000)
            db.add(t); db.flush(); ids.append(t.id)
            db.add(TokenRiskCheck(token_id=t.id, ok=True, top1_wallet_pct=2.0, top10_wallet_pct=10.0))
            from app.models import TrendScore
            db.add(TrendScore(token_id=t.id, ts=1, trend=1, fomo=1, risk=1, components_json={"summary": {}}))
        db.commit()
        old_cap, C.TELEGRAM_MAX_PER_TICK = C.TELEGRAM_MAX_PER_TICK, 2
        res = LA.evaluate_launches(db)
        C.TELEGRAM_MAX_PER_TICK = old_cap
        states = [db.get(Token, i).launch_alert for i in ids]
        check("flood guard: 2 sent now, 1 waits for next tick", len(res["to_send"]) == 2
              and states.count(None) == 1, str(states))

    # API
    with SessionLocal() as db:
        res = API.tokens_new(sort="newest", limit=40, max_age_h=None, source="new_pool", db=db)
        items = res["items"]
        check("New Coins API filters fresh pools", items and all(i["discovery_source"] == "new_pool" for i in items),
              str([i["symbol"] for i in items]))
        check("card carries launch_alert", any(i["launch_alert"] == "sent" for i in items))
        promo = API.tokens_new(sort="newest", limit=40, max_age_h=None, source="promoted", db=db)["items"]
        check("promoted filter", promo and all(i["discovery_source"] == "boost" for i in promo))

asyncio.run(run())
print(f"\n{len(PASS)} passed, {len(FAIL)} failed")
sys.exit(1 if FAIL else 0)
