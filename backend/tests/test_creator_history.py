"""Creator (dev) track record: creation detection, classification, labels, wiring."""
import asyncio
import json
import time

import httpx

from app.engine.verdict import clamp, rules_verdict
from app.providers import creator_history as ch
from app.providers.creator_history import (CreatorHistoryProvider, classify, creator_flags,
                                           find_creations, summarise)
from app.services.launch_alerts import launch_verdict

DEV = "DevWa11et1111111111111111111111111111111111"
OTHER = "Someone1111111111111111111111111111111111111"
NOW = 1_800_000_000


def create_tx(mint, payer=DEV, t=NOW - 3 * 86400, inner=True, program="spl-token", err=None):
    ins = {"program": program, "parsed": {"type": "initializeMint2", "info": {"mint": mint, "decimals": 6}}}
    msg = {"accountKeys": [{"pubkey": payer}], "instructions": [] if inner else [ins]}
    meta = {"err": err, "innerInstructions": [{"index": 0, "instructions": [ins]}] if inner else []}
    return {"slot": 1, "blockTime": t, "transaction": {"message": msg}, "meta": meta}


def swap_tx(payer=DEV):
    return {"slot": 2, "blockTime": NOW, "meta": {"err": None},
            "transaction": {"message": {"accountKeys": [payer], "instructions": [
                {"program": "spl-token", "parsed": {"type": "transfer", "info": {}}}]}}}


def pair(mint, dex="pumpfun", liq=0.0, mcap=0.0, sym="X"):
    return {"dexId": dex, "liquidity": {"usd": liq}, "marketCap": mcap,
            "baseToken": {"address": mint, "symbol": sym}}


def launch(mint, state, graduated=False, age_d=3, mcap=0):
    return {"mint": mint, "time": NOW - age_d * 86400, "state": state, "graduated": graduated,
            "mcap_usd": mcap, "symbol": mint}


# ---------------------------------------------------------------- detection
def test_finds_creations_inner_and_outer_token2022_only_by_creator():
    txs = [create_tx("A"), create_tx("B", inner=False, program="spl-token-2022"),
           create_tx("C", payer=OTHER), create_tx("D", err={"x": 1}), swap_tx(), create_tx("A")]
    mints = [c["mint"] for c in find_creations(txs, DEV)]
    assert sorted(mints) == ["A", "B"]       # not someone else's, not failed, no duplicates


# ---------------------------------------------------------------- classification
def test_classify_states():
    old = {"mint": "M", "time": NOW - 3 * 86400}
    assert classify(old, [], NOW)["state"] == "dead"                       # no market at all
    assert classify(old, [pair("M", liq=300)], NOW)["state"] == "dead"
    assert classify(old, [pair("M", liq=4000, mcap=9000)], NOW)["state"] == "faded"
    alive = classify(old, [pair("M", "pumpswap", liq=40_000, mcap=300_000, sym="WIN")], NOW)
    assert alive["state"] == "alive" and alive["graduated"] and alive["symbol"] == "WIN"
    young = classify({"mint": "M", "time": NOW - 3600}, [], NOW)
    assert young["state"] == "young"                                     # too early to call dead


# ---------------------------------------------------------------- labels
def test_serial_rugger_and_current_token_excluded():
    ls = [launch("NOW", "young", age_d=0.01)] + [launch(f"D{i}", "dead") for i in range(5)]
    s = summarise(ls, exclude_mint="NOW", now=NOW)
    assert s["prior_launches"] == 5 and s["dead"] == 5 and s["label"] == "SERIAL_RUGGER"
    assert any(f["id"] == "serial_rugger" and f["severity"] == "high" for f in creator_flags(s))


def test_launch_spam_in_24h_is_serial_even_if_all_young():
    ls = [launch(f"Y{i}", "young", age_d=0.1) for i in range(6)]
    s = summarise(ls, exclude_mint="Y0", now=NOW)
    assert s["label"] == "SERIAL_RUGGER" and s["matured"] == 0
    assert creator_flags(s)[0]["id"] == "launch_spam"


def test_proven_first_launch_risky_unproven():
    proven = [launch("G", "alive", graduated=True, mcap=2e6), launch("D", "dead")]
    s = summarise(proven, now=NOW)
    assert s["label"] == "PROVEN" and s["best"]["mcap_usd"] == 2e6
    assert summarise([launch("ME", "young", age_d=0.01)], exclude_mint="ME", now=NOW)["label"] == "FIRST_LAUNCH"
    assert summarise([], now=NOW, truncated=True)["label"] == "UNPROVEN"   # busy wallet: unknown, not "first"
    assert summarise([launch("A", "dead"), launch("B", "dead"), launch("C", "alive")], now=NOW)["label"] == "RISKY"


# ---------------------------------------------------------------- verdict + alert wiring
CLEAN = {
    "risk_engine": {"hard_fails": [], "band": "LOW", "score": 12, "flags": []},
    "contract": {"verified": True, "mint_authority_active": False, "freeze_authority_active": False},
    "holders": {"top10_wallet_pct": 18.0},
    "exit": {"sell_route": True, "buy_route": True, "round_trip_loss_pct": 2.0},
    "market": {"liquidity_usd": 150_000, "organic_score": 70, "buy_share_1h": 0.6,
               "price_change_1h_pct": 8.0, "holder_growth_pct": 4.0},
    "launch": {"status": "ok", "early_buyers_funded_by_creator": 0, "bundle_pct": 2,
               "sniper_pct": 5, "largest_shared_funder_group": 1},
}


def with_creator(**cr):
    return {**CLEAN, "creator": cr}


def test_serial_rugger_caps_verdict_to_avoid_even_if_llm_loves_it():
    ev = with_creator(status="ok", label="SERIAL_RUGGER", prior_dead=9)
    assert clamp("STRONG_WATCH", ev)[0] == "AVOID"
    assert rules_verdict(ev)["rating"] in ("AVOID", "NEUTRAL", "RUG_RISK")
    assert clamp("STRONG_WATCH", with_creator(status="ok", label="RISKY", prior_dead=3))[0] == "NEUTRAL"


def test_unchecked_creator_caps_at_watch_proven_does_not():
    assert clamp("STRONG_WATCH", with_creator(status=None))[0] == "WATCH"
    ev = with_creator(status="ok", label="PROVEN", prior_graduated=2, prior_alive=1)
    assert clamp("STRONG_WATCH", ev)[0] == "STRONG_WATCH"
    assert any("graduated" in p for p in rules_verdict(ev)["positives"])


def _alert_facts(creator):
    return {"source": "helius", "top1_wallet_pct": 3.0, "top10_wallet_pct": 15.0,
            "mint_authority": False, "freeze_authority": False, "token_2022": False,
            "exit_buy_route": True, "exit_sell_route": True, "exit_round_trip_loss_pct": 2.0,
            "launch": {"status": "ok", "funded_by_creator": 0, "bundle_pct": 1, "sniper_pct": 2,
                       "shared_funder_wallets": 0},
            "creator": creator}


def test_launch_alert_blocked_for_serial_launcher_not_for_unchecked():
    bad = {"status": "ok", "label": "SERIAL_RUGGER", "dead": 7, "matured": 8, "launches_24h": 0}
    v, why = launch_verdict(_alert_facts(bad))
    assert v == "fail" and "creator" in why[0]
    assert launch_verdict(_alert_facts(None))[0] == "pass"


# ---------------------------------------------------------------- provider end to end (no network)
def test_provider_gtfa_then_dexscreener(monkeypatch):
    monkeypatch.setattr(ch, "_GTFA_UNAVAILABLE", False)
    monkeypatch.setattr(ch, "CREATOR_USE_GTFA", True)
    t_old = int(time.time()) - 5 * 86400
    txs = [create_tx("DeadMint", t=t_old), create_tx("GoodMint", t=t_old), swap_tx()]
    seen = {}

    def handler(req: httpx.Request):
        if req.method == "POST":
            body = json.loads(req.content)
            seen["method"] = body["method"]
            assert body["params"][0] == DEV
            return httpx.Response(200, json={"result": {"data": txs, "paginationToken": None}})
        assert "/tokens/v1/solana/" in req.url.path
        return httpx.Response(200, json=[pair("GoodMint", "raydium", 60_000, 900_000, "GOOD"),
                                         pair("DeadMint", "pumpfun", 50, 2_000, "DEAD")])

    prov = CreatorHistoryProvider(api_key="k", transport=httpx.MockTransport(handler), pace=0)
    h = asyncio.run(prov.analyse(DEV))
    assert h.status == "ok" and h.method == "gtfa" and seen["method"] == "getTransactionsForAddress"
    states = {l["mint"]: (l["state"], l["graduated"]) for l in h.launches}
    assert states == {"GoodMint": ("alive", True), "DeadMint": ("dead", False)}


def test_provider_market_outage_is_error_not_dead(monkeypatch):
    monkeypatch.setattr(ch, "_GTFA_UNAVAILABLE", False)

    def handler(req: httpx.Request):
        if req.method == "POST":
            return httpx.Response(200, json={"result": {"data": [create_tx("M")], "paginationToken": None}})
        return httpx.Response(503)

    prov = CreatorHistoryProvider(api_key="k", transport=httpx.MockTransport(handler), pace=0)
    h = asyncio.run(prov.analyse(DEV))
    assert h.status == "error" and h.launches == []     # unknown, never "everything died"
