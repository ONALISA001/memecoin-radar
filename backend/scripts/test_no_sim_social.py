"""Real coins never use simulated social data; attention = unique buyers; risk uses a
real trading-authenticity check; realistic paper costs. No network."""
import asyncio, os, sys, tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
fd, dbpath = tempfile.mkstemp(suffix=".db"); os.close(fd)
os.environ["DATABASE_URL"] = f"sqlite:///{dbpath}"
os.environ["HELIUS_API_KEY"] = ""
os.environ["SOLANA_ONLY"] = "1"
os.environ["NEWS_RSS_ENABLED"] = "0"
os.environ["NEW_POOLS_ENABLED"] = "0"

from sqlalchemy import select  # noqa: E402
from app.database import Base, engine, SessionLocal  # noqa: E402
from app.engine import fomo as FO, risk as R, scoring as SC  # noqa: E402
from app.engine.context import TokenContext  # noqa: E402
from app.models import DataSource, SocialPost, SocialSnapshot, Token, TrendScore  # noqa: E402
from app.paper import config as PC, execution as X  # noqa: E402
from app.providers import geckoterminal as GT  # noqa: E402
from app.providers.base import RawToken  # noqa: E402
from app.routers import data_api as API  # noqa: E402
from app.services import ingestion as ING  # noqa: E402
from app.services.contract_facts import unverified_facts  # noqa: E402

Base.metadata.create_all(engine)
PASS, FAIL = [], []
def check(name, cond, extra=""):
    (PASS if cond else FAIL).append(name); print(("  ✅" if cond else "  ❌"), name, extra)

A, PAIR = "SocialAAA" + "1" * 29, "PairSoc" + "2" * 30
state = {"b": 9, "s": 4, "m5_txns": 30}
def tk():
    return dict(price_usd=0.001, mcap=2e6, fdv=2e6, liquidity_usd=90000, volume_h24=500000, volume_h6=200000,
                volume_h1=40000, volume_m5=3000, pc_m5=1.0, pc_h1=12.0, pc_h6=30.0, pc_h24=80.0,
                txns_h1_buys=210, txns_h1_sells=140, txns_h24_buys=3100, txns_h24_sells=2600,
                txns_m5_buys=state["m5_txns"] // 2, txns_m5_sells=state["m5_txns"] // 2, pair_liquidity_usd=90000)
async def fake_discover():
    return [RawToken(address=A, chain="solana", name="Soc", symbol="SOC", pair_address=PAIR)], {A: tk()}, False
async def fake_activity(self, pools):
    return {PAIR: {"buyers_m5": state["b"], "sellers_m5": state["s"], "buyers_h1": 100, "sellers_h1": 40,
                   "buyers_h24": 400, "sellers_h24": 300, "txns_h1": 350}}
ING.discover_tokens = fake_discover
GT.GeckoTerminalProvider.pool_activity = fake_activity

async def run():
    await ING.tick()
    with SessionLocal() as db:
        t = db.execute(select(Token).where(Token.address == A)).scalar_one()
        tid = t.id
        check("no simulated social snapshots for a real coin",
              db.query(SocialSnapshot).filter_by(token_id=tid).count() == 0)
        check("no simulated social posts for a real coin", db.query(SocialPost).filter_by(token_id=tid).count() == 0)
        names = {s.name: s.status for s in db.query(DataSource).all()}
        check("data source: real attention ok, social sim gone", names.get("attention-real") == "ok"
              and "x-social-sim" not in names, str(names))
        # an old simulated row from before this change must be ignored
        sc = db.execute(select(TrendScore).where(TrendScore.token_id == tid)).scalars().first()
        db.add(SocialSnapshot(token_id=tid, ts=sc.ts, mentions=99999, unique_accounts=5, engagement=99999,
                              influencer_posts=50, low_quality_share=0.9, sentiment=1.0))
        db.commit()
        ctx = TokenContext.build(db, t, sc.ts + 300)    # windows cover completed buckets
        check("engine uses real unique buyers, ignores old simulated rows",
              ctx.social_proxy and ctx.d["mentions_1h"] == 9 and ctx.d["influencers_1h"] == 0
              and ctx.sources["social"] == "real:unique-buyers", f"{ctx.d['mentions_1h']} {ctx.d['influencers_1h']}")
        check("trades per unique trader from real data (30 trades / 13 traders)",
              abs(ctx.d["trades_per_trader"] - 30 / 13) < 1e-6)

        tr = SC.calculate_trend_score(ctx, {}, {"breadth": 0.5, "sector_momentum": 50})
        soc = tr["components"]["social"]
        lbl = {x["label"]: x for x in soc["drivers"]}
        check("trend: attention labelled as unique buyers; no-source items score 0",
              soc.get("title") == "Attention (unique buyers)" and lbl["Influencer activity"]["points"] == 0
              and lbl["Influencer activity"]["value"] == "no real source" and soc["source"] == "real:unique-buyers")
        fo = FO.calculate_fomo(ctx, news_active=True)
        fl = [c["label"] for c in fo["components"]]
        check("FOMO: influencer/engagement replaced by real buyer dominance / trader acceleration",
              "Buyer dominance (unique wallets, 1h)" in fl and "Unique-trader acceleration (15m)" in fl
              and "Influencer activity" not in fl)
        check("FOMO: real news counts without needing (simulated) mentions",
              next(c for c in fo["components"] if c["label"].startswith("News"))["points"] == 5.0)
        rk = R.calculate_risk(ctx, unverified_facts())
        auth = next(c for c in rk["components"] if c["label"] == "Trading authenticity")
        check("risk: real trading-authenticity check replaces simulated social manipulation",
              auth["source"] == "geckoterminal+dexscreener" and "trades per unique trader" in auth["value"])
        check("organic trading => low authenticity risk", auth["points"] <= 2, str(auth))
        res = API.token_social(A, db=db)
        check("token social feed: empty + labelled no source", res["posts"] == [] and res["source"] == "none"
              and res["mock"] is False)

    # bot-like trading: 150 trades from 13 wallets per 5 min, for two buckets
    state["m5_txns"] = 150
    for _ in range(2):
        ING.bucket_of = (lambda real: (lambda x: real(x) + 300))(ING.bucket_of)
        await ING.tick()
    with SessionLocal() as db:
        t = db.get(Token, tid)
        sc = db.execute(select(TrendScore).where(TrendScore.token_id == tid).order_by(TrendScore.ts.desc())).scalars().first()
        ctx = TokenContext.build(db, t, sc.ts + 300)    # windows cover completed buckets
        rk = R.calculate_risk(ctx, unverified_facts())
        auth = next(c for c in rk["components"] if c["label"] == "Trading authenticity")
        ctx.d["vol24_liq_live"] = 500000 / 90000
        rk_live = R.calculate_risk(ctx, unverified_facts())
        check("wash check uses live 24h volume (500k / 90k = 5.6x)", "5.6x liquidity" in
              next(c for c in rk_live["components"] if c["label"] == "Trading authenticity")["value"])
        stored = next(c for c in (sc.components_json or {}).get("risk", []) if c["label"] == "Trading authenticity")
        check("real scoring path uses the live 24h volume", "5.6x liquidity" in stored["value"], stored["value"])
        check("many trades from few wallets => wash/bot flag", auth["points"] >= 6
              and any("wash/bot" in f for f in rk["flags"]), str(auth))
asyncio.run(run())

# realistic costs
ex = PC.merged_settings({})["execution"]
f = X.buy_fill(1.0, 100.0, 1e9, 0.0, ex)
check("paper buy pays ~1.5% slippage+spread plus 0.3% fee per side",
      abs(f["slip_pct"] - 1.5) < 0.01 and abs(f["fee"] - 0.3) < 1e-9, f"{f['slip_pct']} {f['fee']}")

print(f"\n{len(PASS)} passed, {len(FAIL)} failed")
sys.exit(1 if FAIL else 0)
