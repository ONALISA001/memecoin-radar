"""Launch radar: live creations, DexScreener enrichment, launch flags, hot picks, candles, API."""
import asyncio
import json

import httpx
import pytest
from fastapi.testclient import TestClient

from app import security
from app.main import app
from app.services import launch_radar as LR

client = TestClient(app)          # no `with`: the stream loops never start
NOW = 1_800_000_000.0


@pytest.fixture(autouse=True)
def _fresh():
    LR.reset()
    security._hits.clear()
    yield
    LR.reset()


def create(mint, creator="Dev1", ts=NOW, initial=10_493_887.0, **kw):
    m = {"signature": "sig", "mint": mint, "traderPublicKey": creator, "txType": "create",
         "initialBuy": initial, "solAmount": 0.296, "bondingCurveKey": f"Curve{mint}",
         "vTokensInBondingCurve": 1_062_506_112.5, "vSolInBondingCurve": 30.29, "marketCapSol": 28.51,
         "name": f"Coin {mint}", "symbol": mint.upper()[:6], "uri": "https://ipfs.io/ipfs/x", "pool": "pump"}
    m.update(kw)
    return LR.handle_message(m, ts)


def pair(mint, dex="pumpfun", buys=300, sells=100, mcap=60_000, liq=0, chg_h1=40.0, price_usd=0.00006):
    return {"chainId": "solana", "dexId": dex, "pairAddress": f"Pair{mint}{dex}",
            "baseToken": {"address": mint, "symbol": mint.upper(), "name": mint},
            "quoteToken": {"symbol": "SOL"}, "priceUsd": str(price_usd), "priceNative": str(price_usd / 150),
            "txns": {w: {"buys": buys, "sells": sells} for w in ("m5", "h1", "h6", "h24")},
            "volume": {"m5": 2_000, "h1": 30_000, "h6": 50_000, "h24": 50_000},
            "priceChange": {"m5": 3.0, "h1": chg_h1, "h6": chg_h1, "h24": chg_h1},
            "liquidity": {"usd": liq}, "marketCap": mcap, "info": {"imageUrl": f"https://img/{mint}.png"}}


def test_curve_progress_maths():
    assert LR.progress_from_v_tokens(LR.V_TOKENS_START) == 0
    assert LR.progress_from_v_tokens(LR.V_TOKENS_START - LR.TOKENS_FOR_SALE) == 100
    # market cap at launch (~28.5 SOL) and at graduation (~411 SOL) map to the ends of the curve
    assert LR.progress_from_mcap_sol(28.51) == pytest.approx(1.3, abs=0.2)
    assert LR.progress_from_mcap_sol(411) == pytest.approx(100, abs=1)


def test_creation_and_trades_are_tracked_live():
    assert create("aaa") == "create"
    l = LR.BOOK["aaa"]
    assert l.creator == "Dev1" and l.bonding_key == "Curveaaa" and LR.progress(l) == pytest.approx(1.3, abs=0.1)
    assert LR.handle_message({"txType": "buy", "mint": "aaa", "traderPublicKey": "W1", "solAmount": 1.5,
                              "vTokensInBondingCurve": 900_000_000, "marketCapSol": 40}, NOW + 5) == "buy"
    assert LR.handle_message({"txType": "sell", "mint": "aaa", "traderPublicKey": "Dev1", "solAmount": 0.2},
                             NOW + 9) == "sell"
    assert (l.live_buys, l.live_sells, l.dev_sold_sol, len(l.traders)) == (1, 1, 0.2, 2)
    assert LR.progress(l) > 20
    assert any(f["id"] == "dev_sold" and f["severe"] for f in LR.flags(l, NOW + 10))
    # junk and trades for unknown coins are ignored
    assert LR.handle_message({"txType": "buy", "mint": "nope"}, NOW) is None
    assert LR.handle_message({"message": "Successfully subscribed"}, NOW) is None
    assert LR.handle_message({"txType": "migrate", "mint": "aaa"}, NOW + 60) == "migrate"
    assert l.graduated and LR.progress(l) == 100


def test_book_is_bounded(monkeypatch):
    monkeypatch.setattr(LR, "LAUNCH_BOOK_MAX", 5)
    for i in range(12):
        create(f"m{i}", creator=f"D{i}", ts=NOW + i)
    assert list(LR.BOOK) == [f"m{i}" for i in range(7, 12)]


def test_dexscreener_enrichment_and_graduation():
    create("bbb")
    LR.apply_dex_pairs([pair("bbb"), pair("bbb", dex="pumpswap", liq=25_000, mcap=70_000), pair("unknown")], NOW + 120)
    l = LR.BOOK["bbb"]
    assert l.graduated and l.dex_id == "pumpswap" and l.dex["liq_usd"] == 25_000   # DEX pair beats the curve
    assert l.image_url == "https://img/bbb.png" and LR.STATUS["sol_usd"] == pytest.approx(150)
    r = LR.row(l, NOW + 120)
    assert (r["buys_h1"], r["sells_h1"], r["chg_h1"], r["mcap_usd"], r["progress"]) == (300, 100, 40.0, 70_000, 100.0)
    assert "unknown" not in LR.BOOK


def test_launch_flags():
    for i in range(3):
        create(f"s{i}", creator="Serial", ts=NOW + i)
    assert any(f["id"] == "serial" and f["severe"] for f in LR.flags(LR.BOOK["s2"], NOW + 5))
    create("big", creator="Whale", initial=250_000_000)
    assert any(f["id"] == "dev_big" and f["severe"] for f in LR.flags(LR.BOOK["big"], NOW))
    create("dmp", creator="Other")
    LR.apply_dex_pairs([pair("dmp", mcap=100_000)], NOW + 100)
    LR.apply_dex_pairs([pair("dmp", mcap=30_000)], NOW + 200)
    assert any(f["id"] == "dumped" for f in LR.flags(LR.BOOK["dmp"], NOW + 200))


def test_hot_needs_activity_buyers_and_clean_flags():
    create("good", creator="A")
    create("quiet", creator="B")
    create("sold", creator="C")
    create("old", creator="D", ts=NOW - 8 * 3600)
    LR.apply_dex_pairs([pair("good"), pair("quiet", buys=10, sells=5), pair("old")], NOW + 300)
    LR.apply_dex_pairs([pair("sold")], NOW + 300)
    LR.handle_message({"txType": "sell", "mint": "sold", "traderPublicKey": "C", "solAmount": 3}, NOW + 301)
    t = NOW + 310
    assert LR.hot_score(LR.BOOK["good"], t) > 0
    assert all(LR.hot_score(LR.BOOK[m], t) is None for m in ("quiet", "sold", "old"))
    hot = LR.hot_raw_tokens(10, t)
    assert [r.address for r in hot] == ["good"] and hot[0].extra["source"] == "new_pool"


def test_listing_tabs():
    create("n1", creator="A", ts=NOW)
    create("n2", creator="B", ts=NOW + 10)
    create("g1", creator="C", ts=NOW + 20)
    LR.apply_dex_pairs([pair("n1", mcap=200_000), pair("n2", mcap=40_000), pair("g1", dex="pumpswap")], NOW + 100)
    t = NOW + 120
    assert [r["mint"] for r in LR.listing("new", 10, t)["items"]] == ["g1", "n2", "n1"]
    assert [r["mint"] for r in LR.listing("bonding", 10, t)["items"]] == ["n1", "n2"]     # closest to graduating
    assert [r["mint"] for r in LR.listing("graduated", 10, t)["items"]] == ["g1"]
    out = LR.listing("new", 10, t, min_mcap=50_000)
    assert {r["mint"] for r in out["items"]} == {"n1", "g1"} and out["counts"]["launched_1h"] == 3


def test_enrich_selection_waits_a_minute_and_rotates():
    create("fresh", creator="A", ts=NOW)
    create("older", creator="B", ts=NOW - 300)
    assert LR.select_for_enrich(NOW + 10) == ["older"]
    assert set(LR.select_for_enrich(NOW + 90)) == {"fresh", "older"}
    LR.BOOK["older"].dex_ts = NOW + 85
    assert LR.select_for_enrich(NOW + 90) == ["fresh"]          # refreshed 5 s ago: waits its turn


def test_enrich_once_uses_dexscreener_batches():
    for i in range(35):
        create(f"e{i:02d}", creator=f"D{i}", ts=NOW - 600)
    calls = []

    def handler(req: httpx.Request):
        calls.append(req.url.path)
        mints = req.url.path.rsplit("/", 1)[-1].split(",")
        return httpx.Response(200, json=[pair(m) for m in mints[:2]])

    import time as _t
    real = _t.time
    try:
        LR.time.time = lambda: NOW          # module uses time.time()
        n = asyncio.run(LR.enrich_once(httpx.MockTransport(handler)))
    finally:
        LR.time.time = real
    assert len(calls) == 2 and n == 4


def test_candles_from_geckoterminal_and_cache():
    create("ccc")
    hits = []

    def handler(req: httpx.Request):
        hits.append(str(req.url))
        return httpx.Response(200, json={"data": {"attributes": {"ohlcv_list": [
            [NOW + 300, 2, 3, 1, 2.5, 100], [NOW, 1, 2, 0.5, 2, 50]]}}})

    out = asyncio.run(LR.candles("ccc", "5m", httpx.MockTransport(handler)))
    assert [c["t"] for c in out["candles"]] == [NOW, NOW + 300] and out["candles"][1]["c"] == 2.5
    assert "/pools/Curveccc/ohlcv/minute" in hits[0] and "aggregate=5" in hits[0]
    asyncio.run(LR.candles("ccc", "5m", httpx.MockTransport(handler)))
    assert len(hits) == 1                                       # cached
    assert asyncio.run(LR.candles("zzz", "5m"))["candles"] == []
    with pytest.raises(ValueError):
        asyncio.run(LR.candles("ccc", "3m"))


def test_launches_api():
    import time
    create("apiCoin1111111111111111111111111111", ts=time.time() - 120)
    r = client.get("/api/launches?tab=new")
    assert r.status_code == 200, r.text
    d = r.json()
    assert d["items"][0]["mint"].startswith("apiCoin") and "status" in d and "counts" in d
    assert client.get("/api/launches?tab=bogus").status_code == 422
    assert client.get("/api/launches/not-a-mint!/candles").status_code == 400
    assert client.get("/api/launches/apiCoin1111111111111111111111111111/candles?tf=2m").status_code == 422


def test_stream_message_shapes_are_json_safe():
    create("jjj")
    json.dumps(LR.listing("new", 5, NOW + 5))                   # sets never leak into the API


# ------------------------------------------------------------------ curve liquidity
def test_curve_liquidity_is_twice_the_real_sol():
    LR.STATUS["sol_usd"] = 70.0
    create("cv0", creator="A", vTokensInBondingCurve=LR.V_TOKENS_START)
    create("cv1", creator="B", vTokensInBondingCurve=LR.V_TOKENS_START - LR.TOKENS_FOR_SALE)
    assert LR.curve_real_sol(LR.BOOK["cv0"]) == pytest.approx(0, abs=0.01)
    assert LR.curve_real_sol(LR.BOOK["cv1"]) == pytest.approx(85, abs=0.5)          # a full curve
    assert LR.curve_liquidity_usd(LR.BOOK["cv1"]) == pytest.approx(2 * 85 * 70, rel=0.01)
    LR.handle_message({"txType": "migrate", "mint": "cv1"}, NOW)
    assert LR.curve_liquidity_usd(LR.BOOK["cv1"]) is None and not LR.is_on_curve("cv1")


def test_curve_ticks_only_for_coins_still_on_the_curve():
    create("on", creator="A", vTokensInBondingCurve=700_000_000)
    create("grad", creator="B")
    create("nodata", creator="C", vTokensInBondingCurve=700_000_000)
    LR.apply_dex_pairs([pair("on"), pair("grad", dex="pumpswap", liq=30_000)], NOW + 100)
    ticks = LR.curve_ticks(["on", "grad", "nodata", "unknown"])
    assert list(ticks) == ["on"]
    t = ticks["on"]
    assert t.liquidity_usd == LR.curve_liquidity_usd(LR.BOOK["on"]) > 3000
    assert (t.txns_h1_buys, t.txns_h1_sells, t.pc_h1) == (300, 100, 40.0)
    assert LR.row(LR.BOOK["on"], NOW + 100)["liq_usd"] == t.liquidity_usd     # shown on the radar too


def test_curve_coins_get_the_smaller_tracking_floor():
    from app.providers.base import MarketTick, RawToken
    from app.services import ingestion as ING
    create("crv", creator="A", vTokensInBondingCurve=700_000_000)
    LR.apply_dex_pairs([pair("crv")], NOW + 100)
    mt = MarketTick(liquidity_usd=4_000, txns_h1_buys=30, txns_h1_sells=10)
    assert ING._passes_new_pool_floor(mt, on_curve=True) and not ING._passes_new_pool_floor(mt)
    cands = [RawToken(address="crv", chain="solana", name="", symbol=""),
             RawToken(address="dexcoin", chain="solana", name="", symbol="")]
    kept = ING.select_new_pool_tokens(cands, {"crv": mt, "dexcoin": mt})
    assert [r.address for r in kept] == ["crv"]


def test_curve_coin_can_be_an_early_candidate_below_8k():
    from app.services import market_views as MV
    create("crvE", creator="A", vTokensInBondingCurve=700_000_000)
    LR.apply_dex_pairs([pair("crvE")], NOW + 100)
    base = {"symbol": "X", "name": "x", "risk": 30, "liquidity": 4_000, "volume_24h": 40_000, "sellp": 30,
            "sellp_band": "neutral", "momentum": 45, "momentum_band": "weakening", "whale_class": "neutral",
            "price": 1.0, "price_change_h1": 8.0, "buy_share": 0.62, "vol_accel_h1": 50, "holder_growth": 4,
            "organic": 70, "liq_band": "healthy", "age_hours": 1}
    cards = [{**base, "address": "crvE"}, {**base, "address": "dexE"}]
    out = MV.priority_coins(cards, {}, {}, 10, hist={"crvE": [30, 36, 41, 45], "dexE": [30, 36, 41, 45]})
    assert [r["address"] for r in out["early"]] == ["crvE"]          # same coin on a DEX needs $8K
    assert out["items"] == [] or all(r["address"] != "crvE" for r in out["items"])   # Passing still needs $15K
