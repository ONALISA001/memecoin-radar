"""Universe filter + real-trade-count tests. No network: discovery is mocked."""
import asyncio, os, sys, tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
fd, dbpath = tempfile.mkstemp(suffix=".db"); os.close(fd)
os.environ["DATABASE_URL"] = f"sqlite:///{dbpath}"
os.environ["HELIUS_API_KEY"] = ""
os.environ["SOLANA_ONLY"] = "1"

from sqlalchemy import select  # noqa: E402
from app.database import Base, engine, SessionLocal  # noqa: E402
from app.models import Token, TrendScore  # noqa: E402
from app.providers import dexscreener as D  # noqa: E402
from app.providers.base import RawToken  # noqa: E402
from app.services import ingestion as ING  # noqa: E402
from app.routers import data_api as API  # noqa: E402

Base.metadata.create_all(engine)
PASS, FAIL = [], []
def check(name, cond, extra=""):
    (PASS if cond else FAIL).append(name); print(("  ✅" if cond else "  ❌"), name, extra)

# --- pure filter -------------------------------------------------------
check("USDC symbol excluded", D.exclusion_reason("USDC", "x", 1e6, 1e6) == "stablecoin/major")
check("USDC mint excluded", D.exclusion_reason("?", "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v", 1e6, 1e6))
check("mega-cap excluded", D.exclusion_reason("FOO", "x", 6.8e10, 1e6) == "market cap above ceiling")
check("dust liquidity excluded", D.exclusion_reason("FOO", "x", 1e5, 500) == "liquidity below floor")
check("normal meme coin kept", D.exclusion_reason("PEPE2", "x", 2e6, 80000) is None)

# --- full tick with mocked discovery ------------------------------------
def tk(price, **kw):
    base = dict(price_usd=price, mcap=2e6, fdv=2e6, liquidity_usd=90000, volume_h24=500000,
                volume_h6=200000, volume_h1=40000, volume_m5=3000, pc_m5=1.0, pc_h1=12.0,
                pc_h6=30.0, pc_h24=80.0, txns_h1_buys=210, txns_h1_sells=140,
                txns_h24_buys=3100, txns_h24_sells=2600, txns_m5_buys=18, txns_m5_sells=12,
                pair_liquidity_usd=90000)
    base.update(kw); return base

A, B = "MemeAAAA" + "1" * 30, "MemeBBBB" + "2" * 30
state = {"mode": 1}
async def fake_discover():
    raws = [RawToken(address=A, chain="solana", name="Alpha", symbol="ALPHA"),
            RawToken(address=B, chain="solana", name="Beta", symbol="BETA")]
    ticks = {A: tk(0.001), B: tk(0.002)}
    if state["mode"] == 2:               # B drops out of discovery
        raws, ticks = raws[:1], {A: ticks[A]}
    return raws, ticks, False
ING.discover_tokens = fake_discover

async def run():
    await ING.tick()
    with SessionLocal() as db:
        rows = API.latest_score_rows(db)
        cards = {t.symbol: API.card(t, sc) for sc, t in rows}
        check("both tokens tracked", set(cards) == {"ALPHA", "BETA"}, str(set(cards)))
        a = cards["ALPHA"]
        check("txns_1h is the REAL DexScreener count", a["txns_1h"] == 350, str(a["txns_1h"]))
        check("buys/sells 1h are real", a["buys_1h"] == 210 and a["sells_1h"] == 140)
        check("txns_24h is real", a["txns_24h"] == 5700, str(a["txns_24h"]))
        check("buy_share real", abs(a["buy_share"] - 0.6) < 1e-9)
        check("volume_24h is the real value", a["volume_24h"] == 500000)
        check("card carries data_quality labels",
              "holders" in a["data_quality"]["simulated"] and "txns_1h" in a["data_quality"]["real"])
    state["mode"] = 2
    # next tick is in a later bucket, so the dropped token must stop refreshing
    real_bucket = ING.bucket_of
    ING.bucket_of = lambda t: real_bucket(t) + 1200
    await ING.tick()
    ING.bucket_of = real_bucket
    with SessionLocal() as db:
        cards = {t.symbol: sc for sc, t in API.latest_score_rows(db)}
        check("dropped token disappears from trending", set(cards) == {"ALPHA"}, str(set(cards)))
        from app.models import VolumeSnapshot
        n_b = db.execute(select(VolumeSnapshot).join(Token, Token.id == VolumeSnapshot.token_id)
                         .where(Token.symbol == "BETA", VolumeSnapshot.ts > ING.bucket_of(0) + 0)).all()
        newest_b = max(v[0].ts for v in n_b)
        newest_a = max(v[0].ts for v in db.execute(select(VolumeSnapshot).join(
            Token, Token.id == VolumeSnapshot.token_id).where(Token.symbol == "ALPHA")).all())
        check("no fabricated live bucket for the dropped token", newest_b < newest_a)

asyncio.run(run())
print(f"\n{len(PASS)} passed, {len(FAIL)} failed")
sys.exit(1 if FAIL else 0)
