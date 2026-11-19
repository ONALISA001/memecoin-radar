"""DexScreener outage: no demo universe; last real data kept; scoring + paper paused;
automatic recovery. No network."""
import asyncio, os, sys, tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
fd, dbpath = tempfile.mkstemp(suffix=".db"); os.close(fd)
os.environ["DATABASE_URL"] = f"sqlite:///{dbpath}"
os.environ["HELIUS_API_KEY"] = ""
os.environ["SOLANA_ONLY"] = "1"
os.environ["NEWS_RSS_ENABLED"] = "0"
os.environ["NEW_POOLS_ENABLED"] = "0"

from sqlalchemy import func, select  # noqa: E402
from app.database import Base, engine, SessionLocal  # noqa: E402
from app.models import DataSource, PriceSnapshot, Token, TrendScore  # noqa: E402
from app.providers import geckoterminal as GT  # noqa: E402
from app.providers.base import MarketTick, RawToken  # noqa: E402
from app.providers.dexscreener import DexscreenerProvider  # noqa: E402
from app.routers import data_api as API  # noqa: E402
from app.services import ingestion as ING  # noqa: E402

Base.metadata.create_all(engine)
PASS, FAIL = [], []
def check(name, cond, extra=""):
    (PASS if cond else FAIL).append(name); print(("  ✅" if cond else "  ❌"), name, extra)

A = "OutageAAA" + "1" * 29
state = {"down": False}
async def promo(self):
    if state["down"]:
        raise ConnectionError("api.dexscreener.com unreachable")
    return [RawToken(address=A, chain="solana", name="Real", symbol="REAL", pair_address="P" * 40)]
async def market(self, raws):
    return {r.address: MarketTick(price_usd=0.001, mcap=1e6, fdv=1e6, liquidity_usd=80000, pair_liquidity_usd=80000,
                                  volume_h1=9000, volume_h24=90000, txns_h1_buys=50, txns_h1_sells=30,
                                  txns_h24_buys=500, txns_h24_sells=400) for r in raws}
async def activity(self, pools):
    return {}
DexscreenerProvider.discover = promo
DexscreenerProvider.fetch_market = market
GT.GeckoTerminalProvider.pool_activity = activity
paper_calls = []
real_paper = ING.paper_engine.run_tick
ING.paper_engine.run_tick = lambda db, now, m: (paper_calls.append(now), real_paper(db, now, m))[1]

def counts(db):
    return (db.execute(select(func.count()).select_from(TrendScore)).scalar(),
            db.execute(select(func.count()).select_from(PriceSnapshot)).scalar())

async def run():
    await ING.tick()
    with SessionLocal() as db:
        before = counts(db)
        ok_at = db.execute(select(DataSource.last_ok_at).where(DataSource.name == "dexscreener")).scalar()
    check("real tick scores and trades normally", before[0] == 1 and len(paper_calls) == 1)

    state["down"] = True
    ING.bucket_of = (lambda real: (lambda x: real(x) + 300))(ING.bucket_of)
    res = await ING.tick()
    with SessionLocal() as db:
        check("outage reported, not hidden", res.get("outage") is True and "unreachable" in (res.get("error") or ""))
        check("no new scores or snapshots invented", counts(db) == before, f"{counts(db)} vs {before}")
        check("no demo (Mock*) universe created",
              db.execute(select(func.count()).select_from(Token).where(Token.address.like("Mock%"))).scalar() == 0)
        check("paper trading paused", len(paper_calls) == 1)
        src = db.execute(select(DataSource).where(DataSource.name == "dexscreener")).scalar_one()
        check("source marked down with since + reason", src.status == "down" and src.meta_json["since"]
              and src.meta_json["mode"] == "paused")
        check("last_ok_at keeps the last time it worked", src.last_ok_at == ok_at)
        items = API.trending(db=db, chain=None, min_liquidity=0, max_risk=100, min_trend=0, min_fomo=0,
                             min_mcap=0, max_mcap=1e15, narrative=None, sort="trend", limit=40)
        items = items.get("items", items) if isinstance(items, dict) else items
        check("dashboards keep showing the last real data", any(i["address"] == A for i in items))

    state["down"] = False
    ING.bucket_of = (lambda real: (lambda x: real(x) + 300))(ING.bucket_of)
    res = await ING.tick()
    with SessionLocal() as db:
        src = db.execute(select(DataSource).where(DataSource.name == "dexscreener")).scalar_one()
        check("recovers automatically", not res.get("outage") and src.status == "ok"
              and counts(db)[0] == before[0] + 1 and len(paper_calls) == 2)
        check("outage state cleared", ING.OUTAGE["since"] is None)

    # opt-in demo mode still works
    state["down"] = True
    ING.DEMO_FALLBACK = True
    raws, ticks, fb = await ING.discover_tokens()
    check("DEMO_FALLBACK=1 restores the labelled demo universe", fb and raws and raws[0].address.startswith("Mock"))
asyncio.run(run())

print(f"\n{len(PASS)} passed, {len(FAIL)} failed")
sys.exit(1 if FAIL else 0)
