"""Real Telegram call-channel feed: parsing, contract-address identity, storage,
per-coin stats, coordinated-shill flag, discovery, scoring, API. No network."""
import asyncio, os, sys, tempfile, time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
fd, dbpath = tempfile.mkstemp(suffix=".db"); os.close(fd)
os.environ["DATABASE_URL"] = f"sqlite:///{dbpath}"
os.environ["HELIUS_API_KEY"] = ""
os.environ["SOLANA_ONLY"] = "1"
os.environ["NEWS_RSS_ENABLED"] = "0"
os.environ["NEW_POOLS_ENABLED"] = "0"
os.environ["TELEGRAM_CALL_CHANNELS"] = "@alphacalls, https://t.me/s/gemhunters ,bad name!,degenhub"

import httpx  # noqa: E402
from cryptography.hazmat.primitives import serialization  # noqa: E402
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey  # noqa: E402
from sqlalchemy import select  # noqa: E402
from app.database import Base, engine, SessionLocal  # noqa: E402
from app.models import TelegramPost, Token, TrendScore  # noqa: E402
from app.providers import geckoterminal as GT, helius as H, telegram_public as TP  # noqa: E402
from app.providers.base import MarketTick, RawToken  # noqa: E402
from app.providers.dexscreener import DexscreenerProvider  # noqa: E402
from app.routers import data_api as API  # noqa: E402
from app.services import ingestion as ING, telegram_feed as TF  # noqa: E402

Base.metadata.create_all(engine)
PASS, FAIL = [], []
def check(name, cond, extra=""):
    (PASS if cond else FAIL).append(name); print(("  ✅" if cond else "  ❌"), name, extra)

def b58(b):
    n = int.from_bytes(b, "big"); s = ""
    while n:
        n, r = divmod(n, 58); s = H._B58[r] + s
    return "1" * (len(b) - len(b.lstrip(b"\0"))) + s
def mint():
    return b58(Ed25519PrivateKey.generate().public_key().public_bytes(serialization.Encoding.Raw,
                                                                      serialization.PublicFormat.Raw))

check("channel list cleaned (@, t.me links, invalid dropped)",
      TP.clean_channels(os.environ["TELEGRAM_CALL_CHANNELS"]) == ["alphacalls", "gemhunters", "degenhub"])
M1, M2, M3 = mint(), mint(), mint()
check("contract address found in text", TP.extract_mints(f"New gem {M1} aping now") == [M1])
check("wSOL/USDC and junk ignored", TP.extract_mints("So11111111111111111111111111111111111111112 "
                                                     "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v 0OIl") == [])
check("$TICKER alone is not an identity", TP.extract_mints("$BONK to the moon!!") == [])

def msg(ch, n, text, ts_iso, views="1.2K", href=None):
    link = f'<a href="{href}">chart</a>' if href else ""
    return (f'<div class="tgme_widget_message" data-post="{ch}/{n}">'
            f'<div class="tgme_widget_message_text js-message_text">{text}{link}</div>'
            f'<span class="tgme_widget_message_views">{views}</span>'
            f'<time datetime="{ts_iso}" class="time">x</time></div>')
iso = lambda secs_ago: time.strftime("%Y-%m-%dT%H:%M:%S+00:00", time.gmtime(time.time() - secs_ago))  # noqa: E731
page = "<html>" + msg("alphacalls", 101, f"🚀 CALL &amp; ape {M1}<br>LFG", iso(120)) + \
       msg("alphacalls", 102, "gm", iso(60), views="980") + \
       msg("alphacalls", 103, "chart below", iso(30), href=f"https://pump.fun/coin/{M2}") + "</html>"
posts = TP.parse_channel(page, "alphacalls")
check("posts parsed with id, views, time", [p.post_id for p in posts] == ["alphacalls/101", "alphacalls/102",
      "alphacalls/103"] and posts[0].views == 1200 and posts[1].views == 980 and posts[0].ts > 0)
check("HTML decoded; contract found in text", "CALL & ape" in posts[0].text and posts[0].mints == [M1])
check("contract found only in a pump.fun link", posts[2].mints == [M2])
check("chatter without a contract kept but unlinked", posts[1].mints == [])

def transport():
    def h(req):
        if "degenhub" in str(req.url):
            return httpx.Response(404)
        if "gemhunters" in str(req.url):
            return httpx.Response(200, text="<html>private or empty</html>")
        return httpx.Response(200, text=page)
    return httpx.MockTransport(h)
got, status = asyncio.run(TP.fetch_channels(["alphacalls", "gemhunters", "degenhub"], gap_s=0, transport=transport()))
check("per-channel status; failures don't stop others", status["alphacalls"] == 3 and
      status["degenhub"].startswith("error") and status["gemhunters"].startswith("error") and len(got) == 3, str(status))

# --- storage + stats ------------------------------------------------------------------
now = int(time.time())
P = TP.TgPost
with SessionLocal() as db:
    TF.store_posts(db, got, now); db.commit()
    again = [P(**{**p.__dict__, "views": p.views + 500}) for p in got]
    r = TF.store_posts(db, again, now); db.commit()
    check("dedupe by post id; views updated", r["added"] == 0 and
          db.execute(select(TelegramPost.views).where(TelegramPost.post_id == "alphacalls/101")).scalar() == 1700)
    TF.store_posts(db, [P("gemhunters", "gemhunters/7", now - 200, 300, f"{M1} sending", [M1]),
                        P("degenhub", "degenhub/9", now - 100, 50, f"ape {M1}", [M1]),
                        P("alphacalls", "alphacalls/1", now - 5000, 10, f"old {M3}", [M3])], now); db.commit()
    st = TF.coin_stats(db, [M1, M2, M3], now)
    check("mentions / channels / views per coin", st[M1]["tg_mentions_1h"] == 3 and st[M1]["tg_channels_24h"] == 3
          and st[M1]["tg_views_1h"] == 1700 + 300 + 50, str(st[M1]))
    check("3 channels within 10 min => coordinated push", st[M1]["tg_coordinated"] and
          st[M1]["tg_coord_channels"] == ["alphacalls", "degenhub", "gemhunters"])
    check("single-channel call isn't coordinated", st[M2]["tg_coordinated"] is False)
    check("older call counted in prev hour", st[M3]["tg_mentions_1h"] == 0 and st[M3]["tg_mentions_prev_1h"] == 1)
    calls = TF.recent_calls(db, now)
    check("recent calls ranked by channel count", calls[0] == (M1, 3) and M3 not in [m for m, _ in calls])

# --- tick: discovery + scoring + API -------------------------------------------------------
A = M2                                      # tracked via promotion; called once
async def promo(self):
    return [RawToken(address=A, chain="solana", name="Two", symbol="TWO", pair_address="PairTwo" + "1" * 30)]
async def market(self, raws):
    return {r.address: MarketTick(price_usd=0.001, mcap=1e6, fdv=1e6, liquidity_usd=80000, pair_liquidity_usd=80000,
                                  volume_h1=9000, volume_h24=90000, txns_h1_buys=50, txns_h1_sells=30,
                                  txns_h24_buys=500, txns_h24_sells=400) for r in raws if r.address in (A, M1)}
async def act(self, pools):
    return {}
async def fake_fetch(channels, **kw):
    return [], {c: 0 for c in channels}
DexscreenerProvider.discover = promo
DexscreenerProvider.fetch_market = market
GT.GeckoTerminalProvider.pool_activity = act
TP.fetch_channels = fake_fetch
ING.tg_public.fetch_channels = fake_fetch

async def run():
    await ING.tick()
    with SessionLocal() as db:
        toks = {t.address: t for t in db.execute(select(Token)).scalars().all()}
        check("coin called in watched channels gets discovered", M1 in toks and toks[M1].discovery_source == "telegram")
        check("called coin without market data isn't added", M3 not in toks)
        sc = db.execute(select(TrendScore).where(TrendScore.token_id == toks[M1].id)).scalars().first()
        soc = {x["label"]: x for x in sc.components_json["trend"]["social"]["drivers"]}
        check("trend uses real Telegram calls", soc["Telegram calls (1h)"]["points"] > 0 and
              soc["Telegram channels calling (24h)"]["value"] == "3 channels", str(soc.get("Telegram calls (1h)")))
        risk = {c["label"]: c for c in sc.components_json["risk"]}
        check("coordinated push raises risk with a flag", "coordinated Telegram push" in risk["Trading authenticity"]["value"]
              and any("Coordinated shilling" in f for f in sc.components_json["risk_flags"]), str(risk["Trading authenticity"]))
        res = API.token_social(M1, db=db)
        check("token page shows the real Telegram posts", res["source"] == "telegram" and len(res["posts"]) == 3
              and res["posts"][0]["url"].startswith("https://t.me/") and "not buy signals" in res["note"])
        new = API.tokens_new(sort="newest", limit=40, max_age_h=None, source="telegram", db=db)["items"]
        check("New Coins filter for Telegram-called coins", [i["address"] for i in new] == [M1])
asyncio.run(run())

def boom(*a, **k):
    raise RuntimeError("telegram db exploded")
ING.tg_feed.recent_calls = boom
raws, ticks, fb = asyncio.run(ING.discover_tokens())
check("a Telegram failure never takes down market data", [r.address for r in raws] == [A] and not fb
      and ING.OUTAGE["since"] is None)

print(f"\n{len(PASS)} passed, {len(FAIL)} failed")
sys.exit(1 if FAIL else 0)
