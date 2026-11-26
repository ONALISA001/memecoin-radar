"""Real RSS news: parsing, sentiment, narrative/symbol tagging, storage, tick wiring. No network."""
import asyncio, os, sys, tempfile, time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
fd, dbpath = tempfile.mkstemp(suffix=".db"); os.close(fd)
os.environ["DATABASE_URL"] = f"sqlite:///{dbpath}"
os.environ["HELIUS_API_KEY"] = ""
os.environ["SOLANA_ONLY"] = "1"
os.environ["NEWS_RSS_ENABLED"] = "1"

import httpx  # noqa: E402
from sqlalchemy import select  # noqa: E402
from app.database import Base, engine, SessionLocal  # noqa: E402
from app.models import DataSource, NewsArticle, Token  # noqa: E402
from app.providers import news_rss as N  # noqa: E402
from app.providers.base import RawToken  # noqa: E402
from app.routers import data_api as API  # noqa: E402
from app.services import ingestion as ING, news as NS  # noqa: E402
from app.engine.context import TokenContext  # noqa: E402

Base.metadata.create_all(engine)
PASS, FAIL = [], []
def check(name, cond, extra=""):
    (PASS if cond else FAIL).append(name); print(("  ✅" if cond else "  ❌"), name, extra)

RSS = """<?xml version="1.0"?><rss><channel><title>Feed</title>
<item><title><![CDATA[Solana memecoin $BONK surges as AI agents rally]]></title>
<link>https://example.com/a</link><pubDate>Thu, 19 Nov 2026 10:00:00 GMT</pubDate>
<description><![CDATA[<p>Traders &amp; whales pile into <b>meme coins</b>.</p>]]></description></item>
<item><title>Exchange hacked, prices crash &#8212; bank says</title><link>https://example.com/b</link>
<pubDate>Thu, 19 Nov 2026 09:00:00 GMT</pubDate></item>
<item><title>No link here</title></item>
<item><title>Sneaky</title><link>javascript:alert(1)</link></item>
</channel></rss>"""
ATOM = """<feed><entry><title>Trump-themed coin MAGA jumps after election news</title>
<link rel="alternate" href="https://atom.example/x?a=1&amp;b=2"/><updated>2026-11-19T08:30:00Z</updated>
<summary>Politifi tokens move.</summary></entry></feed>"""

arts = N.parse_feed(RSS, "Pub")
check("RSS: valid items parsed, bad/linkless skipped", [a.url for a in arts] == ["https://example.com/a", "https://example.com/b"])
check("CDATA + entities decoded, tags stripped", arts[0].title.startswith("Solana memecoin $BONK") and "&" in arts[0].text
      and "<b>" not in arts[0].text and "—" in arts[1].title)
check("RFC-822 pubDate parsed", arts[0].ts == 1795082400, str(arts[0].ts))
atom = N.parse_feed(ATOM, "AtomPub")
check("Atom entry with href link + ISO time", len(atom) == 1 and atom[0].url == "https://atom.example/x?a=1&b=2"
      and atom[0].ts == 1795077000, f"{atom and atom[0].url} {atom and atom[0].ts}")

check("bullish headline scores +1", N.sentiment_score("Bitcoin surges to all-time high") == 1.0)
check("bearish headline scores -1", arts[1].sentiment == -1.0)
check("whole words only: 'bank'/'again' don't count", N.sentiment_score("bank says again") == 0.0)
check("mixed => between", N.sentiment_score("surge then crash then crash") < 0)

# --- tagging ------------------------------------------------------------------
check("AI agents + memecoin narratives found", [n for n, _ in NS.match_narratives(arts[0].text)][:2]
      == ["Internet Culture", "AI Agents"] or {"Internet Culture", "AI Agents"} <= {n for n, _ in NS.match_narratives(arts[0].text)},
      str(NS.match_narratives(arts[0].text)))
check("generic words like 'cat'/'ai' alone don't tag news", NS.match_narratives("The cat and AI market update") == [])
check("politics from Atom", NS.match_narratives(atom[0].text)[0][0] == "Politics & Figures")
syms = ["BONK", "WIF", "THE", "MOODENG", "MAGA"]
check("$CASHTAG matched", "BONK" in NS.match_symbols(arts[0].text, syms))
check("short symbol needs a cashtag (WIF not in 'WIFE')", NS.match_symbols("WIFE and WIF news", syms) == [])
check("stop-listed word not matched without a cashtag", NS.match_symbols("THE BIG STORY", ["THE"]) == [])
check("long distinctive symbol matched as word", NS.match_symbols("MOODENG hits new users", syms) == ["MOODENG"])

# --- fetch: one publisher down doesn't empty the feed ----------------------------
def transport():
    def h(req):
        if "down" in str(req.url):
            return httpx.Response(500)
        if "atom" in str(req.url):
            return httpx.Response(200, text=ATOM)
        return httpx.Response(200, text=RSS)
    return httpx.MockTransport(h)

async def fetch_tests():
    srcs = [("A", "https://a.example/rss"), ("Dup", "https://dup.example/rss"),
            ("Down", "https://down.example/rss"), ("Atom", "https://atom.example/feed")]
    got, status = await N.fetch_news(srcs, transport=transport())
    check("failed publisher reported, others kept", status["Down"].startswith("error") and status["A"] == 2
          and status["Atom"] == 1, str(status))
    check("articles collected from live publishers", len(got) == 5, str(len(got)))
    return got
fetched = asyncio.run(fetch_tests())

# --- storage --------------------------------------------------------------------
now = 1795086000
with SessionLocal() as db:
    db.add(Token(address="Bonk" + "1" * 34, chain="solana", symbol="BONK"))
    db.add(NewsArticle(ts=now - 100, source="CoinPulse", title="sim", url="https://mocknews.example/x-0",
                       narrative="Animals"))
    db.add(NewsArticle(ts=now - 9 * 86400, source="Old", title="old", url="https://old.example"))
    db.commit()
    r1 = NS.store_articles(db, fetched, now); db.commit()
    r2 = NS.store_articles(db, fetched, now); db.commit()
    rows = db.execute(select(NewsArticle).order_by(NewsArticle.id)).scalars().all()
    check("URL dedupe across sources and runs", r1["added"] == 3 and r2["added"] == 0, f"{r1} {r2}")
    check("simulated mock news removed", r1["removed_mock"] == 1 and not any("mocknews" in x.url for x in rows))
    check("old rows pruned", r1["pruned"] == 1)
    a = next(x for x in rows if x.url == "https://example.com/a")
    check("stored with narrative, symbol, sentiment", a.narrative in ("Internet Culture", "AI Agents")
          and a.entities_json == ["BONK"] and a.sentiment == 1.0, f"{a.narrative} {a.entities_json} {a.sentiment}")
    check("only headline stored (no article body column)", not hasattr(NewsArticle, "description"))
    fut = NS.store_articles(db, [N.Article("P", "Future dated", "https://f.example", now + 99999, 0.0, "x")], now)
    db.commit()
    check("future-dated item gets fetch time", db.execute(select(NewsArticle.ts).where(
        NewsArticle.url == "https://f.example")).scalar() == now)

    # /news keeps the last 7 days of the REAL clock; the fixture is dated 2026-11-19, so pin
    # the clock to the test's `now` (else this check starts failing a week after that date)
    real_time = API.time
    API.time = type("FrozenTime", (), {"time": staticmethod(lambda: now)})
    try:
        res = API.news_feed(limit=10, narrative=None, symbol="bonk", db=db)
    finally:
        API.time = real_time
    check("/news?symbol= filters by mention", res["source"] == "rss" and [i["url"] for i in res["items"]]
          == ["https://example.com/a"], str(res["items"])[:200])

# --- tick wiring ----------------------------------------------------------------
def tk(price):
    return dict(price_usd=price, mcap=2e6, fdv=2e6, liquidity_usd=90000, volume_h24=500000, volume_h6=200000,
                volume_h1=40000, volume_m5=3000, pc_m5=1.0, pc_h1=12.0, pc_h6=30.0, pc_h24=80.0,
                txns_h1_buys=210, txns_h1_sells=140, txns_h24_buys=3100, txns_h24_sells=2600,
                txns_m5_buys=18, txns_m5_sells=12, pair_liquidity_usd=90000)
A = "MemeAAAA" + "1" * 30
async def fake_discover():
    return [RawToken(address=A, chain="solana", name="Alpha", symbol="ALPHA")], {A: tk(0.001)}, False
ING.discover_tokens = fake_discover
calls = {"n": 0}
async def fake_fetch():
    calls["n"] += 1
    return [N.Article("Pub", "Doge rally lifts memecoins", f"https://t.example/{calls['n']}",
                      int(time.time()) - 60, 1.0, "Doge rally lifts memecoins")], {"Pub": 1, "Down": "error: X"}
ING.news_rss.fetch_news = fake_fetch

async def tick_tests():
    NS.SCHEDULE.last = 0
    await ING.tick()
    ING.bucket_of = (lambda real: (lambda t: real(t) + 300))(ING.bucket_of)
    await ING.tick()          # within NEWS_REFRESH_MINUTES: no second fetch
    with SessionLocal() as db:
        check("news fetched once per refresh window", calls["n"] == 1, str(calls["n"]))
        names = {s.name: s for s in db.execute(select(DataSource)).scalars().all()}
        check("news-rss source recorded ok, news-sim gone", names.get("news-rss") and names["news-rss"].status == "ok"
              and "news-sim" not in names, str(list(names)))
        check("no simulated articles generated", not db.execute(select(NewsArticle).where(
            NewsArticle.url.like("https://mocknews.example/%"))).first())
        t = db.execute(select(Token).where(Token.address == A)).scalar_one()
        ctx = TokenContext.build(db, t, int(time.time()))
        check("engine labels news source as real", ctx.sources["news"] == "rss")
asyncio.run(tick_tests())

print(f"\n{len(PASS)} passed, {len(FAIL)} failed")
sys.exit(1 if FAIL else 0)
