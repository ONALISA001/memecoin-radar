"""Creator track record: finding earlier launches in raw transactions, and the GMGN
dev-score model on top. Transactions mimic Helius `jsonParsed` getTransaction output."""
import asyncio
import json

import httpx
import pytest

from app.engine import dev_score as D
from app.engine.dev_score import CoinRecord
from app.providers import creator_history as CH

DEV = "Dev1111111111111111111111111111111111111111"
CURVE = "Curve11111111111111111111111111111111111111"
NOW = 1_800_000_000.0


def tx(sig, ts, payer, *, mint_init=None, sol=0.0, tokens=(), inner=True, err=None):
    """tokens: [(mint, owner, pre, post)]; sol: payer's SOL change."""
    keys = [{"pubkey": payer, "signer": True}, {"pubkey": CURVE}]
    ix = []
    if mint_init:
        ix = [{"program": "spl-token", "parsed": {"type": "initializeMint2",
                                                  "info": {"mint": mint_init, "decimals": 6}}}]
    pre_tb, post_tb = [], []
    for i, (m, owner, pre, post) in enumerate(tokens):
        pre_tb.append({"accountIndex": 10 + i, "mint": m, "owner": owner,
                       "uiTokenAmount": {"uiAmountString": str(pre)}})
        post_tb.append({"accountIndex": 10 + i, "mint": m, "owner": owner,
                        "uiTokenAmount": {"uiAmountString": str(post)}})
    return {"blockTime": ts, "slot": int(ts),
            "transaction": {"signatures": [sig], "message": {
                "accountKeys": keys, "instructions": [] if inner else ix}},
            "meta": {"err": err, "preBalances": [10_000_000_000, 0],
                     "postBalances": [int(10_000_000_000 + sol * 1e9), 0],
                     "preTokenBalances": pre_tb, "postTokenBalances": post_tb,
                     "innerInstructions": [{"index": 0, "instructions": ix}] if inner else []}}


def dump_launch(mint, t0, buy=1.0, sell=3.0, sell_after=10):
    """Launch + dev buy in the same tx, then a big sell `sell_after` seconds later."""
    return [tx(f"c-{mint}", t0, DEV, mint_init=mint, sol=-buy, tokens=[(mint, DEV, 0, 1000)]),
            tx(f"s-{mint}", t0 + sell_after, DEV, sol=+sell, tokens=[(mint, DEV, 1000, 0)])]


# ------------------------------------------------------------------ parsing
def test_finds_launch_in_inner_and_top_level_instructions():
    a = tx("a", 100, DEV, mint_init="MintA", inner=True)
    b = tx("b", 200, DEV, mint_init="MintB", inner=False)
    found = CH.find_launches([a, b], DEV)
    assert set(found) == {"MintA", "MintB"}
    assert found["MintA"]["launch_ts"] == 100


def test_ignores_mints_someone_else_paid_for_and_failed_txs():
    other = tx("o", 100, "Someone1111111111111111111111111111111111", mint_init="MintX")
    failed = tx("f", 100, DEV, mint_init="MintY", err={"InstructionError": [0, "x"]})
    assert CH.find_launches([other, failed], DEV) == {}


def test_buy_sell_timing_and_amounts():
    txs = dump_launch("MintA", 1000, buy=1.0, sell=3.0, sell_after=12)
    recs = CH.build_records(txs, DEV, CH.find_launches(txs, DEV))
    r = recs["MintA"]
    assert r.sol_in == pytest.approx(1.0) and r.sol_out == pytest.approx(3.0)
    assert r.first_buy_s == 0 and r.first_sell_s == 12


def test_supply_moved_out_without_sol_is_flagged_not_counted_as_sale():
    txs = [tx("c", 1000, DEV, mint_init="M", sol=-1, tokens=[("M", DEV, 0, 1000)]),
           tx("t", 1100, DEV, sol=-0.000005, tokens=[("M", DEV, 1000, 0)])]
    r = CH.build_records(txs, DEV, CH.find_launches(txs, DEV))["M"]
    assert r.moved_out and r.sol_out == 0 and r.first_sell_s is None


def test_market_status_from_dexscreener_pairs():
    recs = {"A": CoinRecord("A", 0), "B": CoinRecord("B", 0), "C": CoinRecord("C", 0)}
    pairs = [{"baseToken": {"address": "A", "symbol": "AAA"}, "dexId": "pumpswap",
              "liquidity": {"usd": 50_000}, "marketCap": 2_000_000, "pairAddress": "PA"},
             {"baseToken": {"address": "A"}, "dexId": "pumpfun", "liquidity": {"usd": 0}},
             {"baseToken": {"address": "B"}, "dexId": "pumpfun", "liquidity": {"usd": 900}, "marketCap": 5000}]
    best = CH.apply_market(recs, pairs)
    assert recs["A"].graduated and recs["A"].alive and recs["A"].symbol == "AAA"
    assert recs["B"].graduated is False and not recs["B"].alive
    assert recs["C"].graduated is False and recs["C"].liquidity_usd is None
    assert set(best) == {"A", "B"}


def test_peak_from_candles():
    payload = {"data": {"attributes": {"ohlcv_list": [[1, 0.001, 0.004, 0.001, 0.002, 9],
                                                      [2, 0.002, 0.003, 0.001, 0.001, 9]]}}}
    assert CH.peak_from_ohlcv(payload, 1e9) == pytest.approx(4e6)
    assert CH.peak_from_ohlcv({}, 1e9) is None


# ------------------------------------------------------------------ scoring
def clean_coin(i, **kw):
    base = dict(mint=f"C{i}", launch_ts=NOW - 86400 * (60 - i), sol_in=1.0, sol_out=1.2,
                first_buy_s=0, first_sell_s=3 * 86400, graduated=True, liquidity_usd=20_000,
                mcap_usd=300_000, has_market=True)
    base.update(kw)
    return CoinRecord(**base)


def dumper_coin(i, **kw):
    base = dict(mint=f"D{i}", launch_ts=NOW - 86400 * (60 - i), sol_in=1.0, sol_out=3.5,
                first_buy_s=0, first_sell_s=8, graduated=False, liquidity_usd=0, mcap_usd=4000,
                has_market=True)
    base.update(kw)
    return CoinRecord(**base)


def test_no_earlier_launches_is_new_not_clean():
    s = D.score([], history_complete=True, now=NOW)
    assert s["band"] == "new" and s["total"] is None


def test_serial_dumper_is_stay_away_even_with_a_big_hit():
    coins = [dumper_coin(i) for i in range(6)]
    coins.append(dumper_coin(9, peak_mcap_usd=80_000_000, mcap_usd=40_000_000, graduated=True,
                             liquidity_usd=900_000))
    s = D.score(coins, career_start_ts=NOW - 86400 * 90, now=NOW)
    assert s["dump_tier"] == "systematic"
    assert s["total"] <= D.CAP_SYS_TOT and s["band"] in ("avoid", "stay_away")
    assert s["power"] > 60          # the big hit is still recognised on the POWER axis


def test_clean_proven_creator_scores_clean():
    coins = [clean_coin(i) for i in range(6)] + [clean_coin(7, peak_mcap_usd=12_000_000, mcap_usd=6_000_000)]
    s = D.score(coins, career_start_ts=NOW - 86400 * 90, history_complete=True, now=NOW)
    assert s["dump_tier"] == "none" and s["dumps"] == 0
    assert s["band"] == "clean", s


def test_thin_record_cannot_score_clean():
    s = D.score([clean_coin(1)], career_start_ts=NOW - 86400 * 2, history_complete=True, now=NOW)
    assert s["dump_tier"] == "unproven"
    assert s["total"] <= D.CAP_OFTEN_TOT and s["band"] != "clean"


def test_shrink_only_pulls_down():
    s = D.score([clean_coin(1, sol_out=0.0, first_sell_s=None)], now=NOW)
    assert s["terms"]["shrink"] >= 0


def test_dump_definition_needs_both_multiple_and_speed():
    late = D.score([dumper_coin(1, first_sell_s=3600)] * 1, now=NOW)
    small = D.score([dumper_coin(1, sol_out=1.2)], now=NOW)
    fast_big = D.score([dumper_coin(1)], now=NOW)
    assert late["dumps"] == 0 and small["dumps"] == 0 and fast_big["dumps"] == 1


def test_coins_stuck_on_curve_cost_conduct():
    few = D.score([clean_coin(i, graduated=False, liquidity_usd=0) for i in range(10)], now=NOW)
    many = D.score([clean_coin(i, graduated=False, liquidity_usd=0) for i in range(120)], now=NOW)
    assert many["terms"]["factory_pen"] > few["terms"]["factory_pen"] == 0
    assert many["conduct"] < few["conduct"]


def test_summary_mentions_dumps_and_launches():
    s = D.score([dumper_coin(i) for i in range(3)], now=NOW)
    assert "3 earlier launches" in s["summary"] and "dumped at open in 3 of 3" in s["summary"]


# ------------------------------------------------------------------ provider end-to-end (mocked HTTP)
def test_provider_history_with_mocked_helius_and_dexscreener(monkeypatch):
    monkeypatch.setattr(CH, "CREATOR_USE_GTFA", False)
    monkeypatch.setattr(CH, "CREATOR_RPC_TX", 50)
    txs = dump_launch("OldCoin", NOW - 5000) + dump_launch("CurrentCoin", NOW - 100)
    by_sig = {t["transaction"]["signatures"][0]: t for t in txs}

    def handler(req: httpx.Request):
        if "helius" in req.url.host:
            body = json.loads(req.content)
            if body["method"] == "getSignaturesForAddress":
                return httpx.Response(200, json={"result": [{"signature": s} for s in by_sig]})
            return httpx.Response(200, json={"result": by_sig[body["params"][0]]})
        if "dexscreener" in req.url.host:
            return httpx.Response(200, json=[{"baseToken": {"address": "OldCoin", "symbol": "OLD"},
                                              "dexId": "pumpfun", "liquidity": {"usd": 0}, "marketCap": 3000}])
        return httpx.Response(404)

    prov = CH.CreatorHistoryProvider(api_key="k", transport=httpx.MockTransport(handler), pace=0)
    h = asyncio.run(prov.history(DEV, exclude={"CurrentCoin"}))
    assert h.status == "ok" and h.method == "rpc" and h.history_complete
    assert [r.mint for r in h.records] == ["OldCoin"]           # the coin being judged is excluded
    assert h.records[0].first_sell_s == 10 and h.records[0].graduated is False
    s = D.score(h.records, career_start_ts=h.career_start_ts, history_complete=True, now=NOW)
    assert s["dumps"] == 1


def test_provider_error_never_leaks_api_key():
    def boom(req):
        raise httpx.ConnectError("failed for https://x/?api-key=SECRETKEY")
    prov = CH.CreatorHistoryProvider(api_key="SECRETKEY", transport=httpx.MockTransport(boom), pace=0)
    h = asyncio.run(prov.history(DEV))
    assert h.status == "error" and "SECRETKEY" not in h.error
