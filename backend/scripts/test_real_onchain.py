"""Real on-chain activity: GeckoTerminal unique traders, Helius holder growth rate,
whale flow from top-10 real-wallet share changes. No network."""
import asyncio, datetime as dt, os, sys, tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
fd, dbpath = tempfile.mkstemp(suffix=".db"); os.close(fd)
os.environ["DATABASE_URL"] = f"sqlite:///{dbpath}"
os.environ["HELIUS_API_KEY"] = ""
os.environ["SOLANA_ONLY"] = "1"
os.environ["NEWS_RSS_ENABLED"] = "0"
os.environ["NEW_POOLS_ENABLED"] = "0"

import httpx  # noqa: E402
from sqlalchemy import select  # noqa: E402
from app.database import Base, engine, SessionLocal  # noqa: E402
from app.engine.context import TokenContext  # noqa: E402
from app.models import HolderSnapshot, Token, TokenRiskCheck  # noqa: E402
from app.providers import geckoterminal as GT  # noqa: E402
from app.providers.base import RawToken  # noqa: E402
from app.services import ingestion as ING, onchain_real as OR  # noqa: E402

Base.metadata.create_all(engine)
PASS, FAIL = [], []
def check(name, cond, extra=""):
    (PASS if cond else FAIL).append(name); print(("  ✅" if cond else "  ❌"), name, extra)

def pool(addr, b5, s5, b1, s1):
    return {"id": f"solana_{addr}", "type": "pool", "attributes": {"address": addr, "transactions": {
        "m5": {"buys": b5 + 3, "sells": s5 + 1, "buyers": b5, "sellers": s5},
        "h1": {"buys": b1 * 2, "sells": s1 * 2, "buyers": b1, "sellers": s1},
        "h24": {"buys": 900, "sells": 700, "buyers": 400, "sellers": 300}}}}

act = GT.parse_pool_activity({"data": [pool("PoolA", 12, 5, 140, 60), {"attributes": {}}]})
check("parses unique buyers/sellers per window", act["PoolA"]["buyers_m5"] == 12 and act["PoolA"]["sellers_h1"] == 60
      and act["PoolA"]["txns_h1"] == 400 and len(act) == 1)

calls = []
def transport(fail_second=False):
    def h(req):
        addrs = str(req.url).rsplit("/", 1)[1].split(",")
        calls.append(len(addrs))
        if fail_second and len(calls) == 2:
            return httpx.Response(429)
        return httpx.Response(200, json={"data": [pool(a, 1, 1, 10, 5) for a in addrs]})
    return httpx.MockTransport(h)

async def provider_tests():
    res = await GT.GeckoTerminalProvider(transport=transport()).pool_activity([f"P{i}" for i in range(65)])
    check("batched 30 pools per call", calls == [30, 30, 5] and len(res) == 65, str(calls))
    calls.clear()
    res = await GT.GeckoTerminalProvider(transport=transport(True)).pool_activity([f"P{i}" for i in range(65)])
    check("rate-limited => keeps what it got, no exception", len(res) == 30)
asyncio.run(provider_tests())

# --- whale flow bookkeeping --------------------------------------------------------
B = 1_000_000 * 300
c1 = TokenRiskCheck(top10_wallet_pct=20.0, checked_at=dt.datetime.fromtimestamp(B - 7200, dt.timezone.utc))
c2 = TokenRiskCheck(top10_wallet_pct=30.0, checked_at=dt.datetime.fromtimestamp(B + 120, dt.timezone.utc))
check("whale flow booked in the bucket after the check: +10pt × $2M = +$200k",
      OR.whale_flow_usd([c1, c2], 2e6, B + 300) == 200000.0)
check("…and only there (no double count)", OR.whale_flow_usd([c1, c2], 2e6, B) == 0.0
      and OR.whale_flow_usd([c1, c2], 2e6, B + 600) == 0.0)
check("no wallet data => unknown, not zero", OR.whale_flow_usd([], 2e6, B) is None)

# --- holder growth as a rate ---------------------------------------------------------
class Ctx: pass
ctx = Ctx(); ctx.d = {}; ctx.sources = {}
OR.apply_real_holder_growth(ctx, {"count": 1100, "growth_pct": 10.0, "span_h": 10.0})
check("Helius counts → hourly rate (10% over 10h = 1%/h), not a step spike",
      ctx.d["holders_g_1h"] == 1.0 and ctx.d["holders_g_15m"] == 0.25 and ctx.d["holders_now"] == 1100)
OR.apply_real_holder_growth(ctx, None)
check("no holder data => growth 0 (unknown)", ctx.d["holders_g_1h"] == 0.0 and ctx.sources["onchain"] == "helius+geckoterminal")

# --- full tick: real values land in HolderSnapshot -------------------------------------
A = "OnchainAAA" + "1" * 28
PAIR = "PairAAA" + "9" * 30
def tk():
    return dict(price_usd=0.001, mcap=2e6, fdv=2e6, liquidity_usd=90000, volume_h24=500000, volume_h6=200000,
                volume_h1=40000, volume_m5=3000, pc_m5=1.0, pc_h1=12.0, pc_h6=30.0, pc_h24=80.0,
                txns_h1_buys=210, txns_h1_sells=140, txns_h24_buys=3100, txns_h24_sells=2600,
                txns_m5_buys=18, txns_m5_sells=12, pair_liquidity_usd=90000)
async def fake_discover():
    return [RawToken(address=A, chain="solana", name="Onchain", symbol="ONC", pair_address=PAIR)], {A: tk()}, False
ING.discover_tokens = fake_discover
async def fake_activity(self, pools):
    return {PAIR: {"buyers_m5": 9, "sellers_m5": 4, "buyers_h1": 120, "sellers_h1": 50,
                   "buyers_h24": 400, "sellers_h24": 300, "txns_h1": 380}} if PAIR in pools else {}
GT.GeckoTerminalProvider.pool_activity = fake_activity

async def run():
    await ING.tick()
    with SessionLocal() as db:
        t = db.execute(select(Token).where(Token.address == A)).scalar_one()
        h = db.execute(select(HolderSnapshot).where(HolderSnapshot.token_id == t.id)).scalars().all()
        check("one real on-chain row per bucket (no simulated backfill)", len(h) == 1, str(len(h)))
        r = h[0]
        check("unique buyers / traders are real", r.new_wallets == 9 and r.active_wallets == 13)
        check("holders / top-10 / deployer unknown until Helius checks => NULL, not invented",
              r.holders is None and r.top10_pct is None and r.deployer_pct is None and r.whale_net_usd is None)
        ctx = TokenContext.build(db, t, r.ts)
        check("engine skips NULLs instead of reading zeros", ctx.holders == {} and ctx.new_wallets == {r.ts: 9})
        tid = t.id
        now = dt.datetime.now(dt.timezone.utc).replace(tzinfo=None)
        db.add(TokenRiskCheck(token_id=tid, ok=True, holders_count=1000, top10_wallet_pct=20.0, top1_wallet_pct=3.0,
                              checked_at=now - dt.timedelta(hours=8)))
        db.add(TokenRiskCheck(token_id=tid, ok=True, holders_count=1080, top10_wallet_pct=25.0, top1_wallet_pct=3.0,
                              checked_at=now))
        db.commit()
    ING.bucket_of = (lambda real: (lambda x: real(x) + 300))(ING.bucket_of)
    await ING.tick()
    with SessionLocal() as db:
        rows = db.execute(select(HolderSnapshot).where(HolderSnapshot.token_id == tid)
                          .order_by(HolderSnapshot.ts)).scalars().all()
        r = rows[-1]
        check("holders + top-10 real-wallet share from Helius", r.holders == 1080 and r.top10_pct == 25.0)
        check("whale accumulation booked: +5pt × $2M = +$100k", r.whale_net_usd == 100000.0, str(r.whale_net_usd))
asyncio.run(run())

print(f"\n{len(PASS)} passed, {len(FAIL)} failed")
sys.exit(1 if FAIL else 0)
