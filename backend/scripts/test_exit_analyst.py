"""Exit analyst (Jupiter round trip) + Token-2022 extension tests. No network: mocked."""
import asyncio, json, os, sys, tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
fd, dbpath = tempfile.mkstemp(suffix=".db"); os.close(fd)
os.environ["DATABASE_URL"] = f"sqlite:///{dbpath}"
os.environ["HELIUS_API_KEY"] = "SECRETKEY123"
os.environ["JUPITER_API_KEY"] = "JUPKEY456"

import httpx  # noqa: E402
from app.providers import helius as H  # noqa: E402
from app.providers import jupiter as J  # noqa: E402
from app.services.launch_alerts import launch_verdict  # noqa: E402

PASS, FAIL = [], []
def check(name, cond, extra=""):
    (PASS if cond else FAIL).append(name); print(("  ✅" if cond else "  ❌"), name, extra)

MINT = "TokenMint111"
LAMPORTS = int(0.1 * J.LAMPORTS)

def jup_transport(buy="ok", sell="ok", back_ratio=0.97, status=None):
    """buy/sell: 'ok' | 'noroute' | 'error'. back_ratio = SOL back / SOL in."""
    seen = []
    def handler(req: httpx.Request):
        seen.append(req)
        q = dict(req.url.params)
        if status:
            return httpx.Response(status, json={"error": "rate limited JUPKEY456"})
        selling = q["inputMint"] == MINT
        mode = sell if selling else buy
        if mode == "noroute":
            return httpx.Response(400, json={"error": "No routes found", "errorCode": "COULD_NOT_FIND_ANY_ROUTE"})
        if mode == "error":
            return httpx.Response(500, json={})
        out = int(LAMPORTS * back_ratio) if selling else 5_000_000
        return httpx.Response(200, json={"inAmount": q["amount"], "outAmount": str(out),
                                         "routePlan": [{"swapInfo": {"label": "Raydium CPMM"}}]})
    return httpx.MockTransport(handler), seen

async def exit_tests():
    t, seen = jup_transport()
    r = await J.JupiterProvider(transport=t).check_exit(MINT)
    check("clean round trip: both routes, ~3% loss", r.ok and r.buy_route and r.sell_route
          and abs(r.round_trip_loss_pct - 3.0) < 0.01, f"loss={r.round_trip_loss_pct}")
    check("sell quote uses exactly the bought amount", dict(seen[1].url.params)["amount"] == "5000000")
    check("api key sent as header, not in URL", seen[0].headers.get("x-api-key") == "JUPKEY456"
          and "JUPKEY456" not in str(seen[0].url))
    check("route label kept", r.route == "Raydium CPMM")
    check("clean => verdict pass", J.exit_verdict(r.buy_route, r.sell_route, r.round_trip_loss_pct) == "pass")

    t, _ = jup_transport(sell="noroute")
    r = await J.JupiterProvider(transport=t).check_exit(MINT)
    check("buy ok + no sell route => honeypot fail", r.ok and r.buy_route and r.sell_route is False
          and J.exit_verdict(True, False, None) == "fail")

    t, _ = jup_transport(buy="noroute")
    r = await J.JupiterProvider(transport=t).check_exit(MINT)
    check("no route at all => warn, not fail", r.ok and r.buy_route is False
          and J.exit_verdict(False, False, None) == "warn")

    t, _ = jup_transport(back_ratio=0.60)
    r = await J.JupiterProvider(transport=t).check_exit(MINT)
    check("40% round-trip loss => fail", J.exit_verdict(True, True, r.round_trip_loss_pct) == "fail")
    t, _ = jup_transport(back_ratio=0.85)
    r = await J.JupiterProvider(transport=t).check_exit(MINT)
    check("15% round-trip loss => warn", J.exit_verdict(True, True, r.round_trip_loss_pct) == "warn")

    t, _ = jup_transport(status=429)
    r = await J.JupiterProvider(transport=t).check_exit(MINT)
    check("rate limit => unknown (not ok), key scrubbed", not r.ok and r.sell_route is None
          and "JUPKEY456" not in r.error, r.error)
    check("unknown => verdict None (never 'pass')", J.exit_verdict(None, None, None) is None)
    t, _ = jup_transport(sell="error")
    r = await J.JupiterProvider(transport=t).check_exit(MINT)
    check("server error on sell => unknown, not 'no route'", not r.ok and r.sell_route is None)

def ext_tests():
    exts = [{"extension": "metadataPointer", "state": {"authority": "X"}},
            {"extension": "tokenMetadata", "state": {}}]
    check("metadata-only Token-2022 => nothing risky", H.analyze_extensions(exts) == ([], None, False))
    exts = [{"extension": "transferFeeConfig", "state": {
                "transferFeeConfigAuthority": "Auth1",
                "olderTransferFee": {"transferFeeBasisPoints": 100},
                "newerTransferFee": {"transferFeeBasisPoints": 1000}}},
            {"extension": "permanentDelegate", "state": {"delegate": "Del1"}},
            {"extension": "permanentDelegate", "state": {"delegate": None}},
            {"extension": "defaultAccountState", "state": {"accountState": "initialized"}},
            {"extension": "transferHook", "state": {"programId": "Hook1"}}]
    names, fee, mut = H.analyze_extensions(exts)
    check("scheduled higher fee counts (10%)", fee == 1000)
    check("fee authority => mutable", mut is True)
    check("active delegate + hook flagged, unset delegate and initialized state ignored",
          names == ["permanentDelegate", "transferHook"], str(names))
    sev = {f["id"]: f["severity"] for f in H.extension_flags(names, fee, mut)}
    check("10% fee => high", sev.get("ext_transferFee") == "high")
    check("permanent delegate => high", sev.get("ext_permanentDelegate") == "high")
    check("frozen default state detected",
          H.analyze_extensions([{"extension": "defaultAccountState",
                                 "state": {"accountState": "frozen"}}])[0] == ["defaultAccountState"])

async def helius_ext_test():
    def handler(req):
        m = json.loads(req.content)["method"]
        if m == "getAccountInfo":
            return httpx.Response(200, json={"result": {"value": {"owner": H.TOKEN_2022, "data": {"parsed": {
                "type": "mint", "info": {"mintAuthority": None, "freezeAuthority": None, "supply": "100000000",
                                         "decimals": 6, "extensions": [
                                             {"extension": "transferFeeConfig", "state": {
                                                 "newerTransferFee": {"transferFeeBasisPoints": 50}}}]}}}}}})
        return httpx.Response(200, json={"result": {"value": []}})
    r = await H.HeliusProvider(api_key="SECRETKEY123", transport=httpx.MockTransport(handler)).check_token("M")
    check("Helius check reads extensions from the mint", r.ok and r.token_2022 and r.token_extensions == ""
          and r.transfer_fee_bps == 50)
    ids = [f["id"] for f in H.risk_flags(r)]
    check("specific fee flag replaces vague 'check manually'", "ext_transferFee" in ids and "token_2022" not in ids)

def risk_and_launch_tests():
    from app.engine import risk as R
    base = {"source": "helius", "verified": True, "mint_authority": False, "freeze_authority": False,
            "token_2022": False, "top1_wallet_pct": 3.0, "top10_wallet_pct": 15.0,
            "token_extensions": [], "transfer_fee_bps": None,
            "exit_buy_route": True, "exit_sell_route": True, "exit_round_trip_loss_pct": 2.0, "exit_sol_in": 0.1}
    v, why = launch_verdict(base)
    check("clean launch passes", v == "pass", str(why))
    v, why = launch_verdict({**base, "exit_sell_route": False})
    check("no sell route blocks launch alert", v == "fail" and "no sell route" in why)
    v, _ = launch_verdict({**base, "exit_buy_route": None, "exit_sell_route": None,
                           "exit_round_trip_loss_pct": None})
    check("Jupiter unavailable does NOT block launch alert", v == "pass")
    v, _ = launch_verdict({**base, "token_2022": True, "token_extensions": []})
    check("clean Token-2022 allowed", v == "pass")
    v, why = launch_verdict({**base, "token_2022": True, "token_extensions": None})
    check("un-analysed Token-2022 still blocked", v == "fail")
    v, why = launch_verdict({**base, "token_2022": True, "token_extensions": ["permanentDelegate"]})
    check("risky extension blocks with its name", v == "fail" and any("permanentDelegate" in w for w in why))

    class Ctx:          # minimal TokenContext stand-in
        social_proxy, telegram, anomalies = True, {}, {}
        d = {"liq_now": 80_000, "liq_chg_3h": 0, "liq_3h_ago": 80_000, "vol_liq_ratio": 1,
             "price_chg_1h": 5, "buy_share_1h": 0.55, "whale_net_24h": 0, "trades_per_trader": 2,
             "vol24_liq_live": 2, "volume_24h": 100_000, "uniques_1h": 50, "mentions_1h": 0,
             "lowq_24h": 0, "top10_now": 0, "deployer_pct_now": 0}
    clean = R.calculate_risk(Ctx(), base)
    honeypot = R.calculate_risk(Ctx(), {**base, "exit_sell_route": False})
    check("clean token stays low risk", clean["risk"] < 30 and not clean["hard_fails"], str(clean["risk"]))
    check("honeypot floored at HIGH", honeypot["risk"] >= R.HARD_FAIL_FLOOR
          and honeypot["classification"] == "HIGH", str(honeypot["risk"]))
    delegate = R.calculate_risk(Ctx(), {**base, "token_2022": True, "token_extensions": ["permanentDelegate"]})
    check("permanent delegate floored at HIGH", delegate["classification"] == "HIGH")

asyncio.run(exit_tests())
ext_tests()
asyncio.run(helius_ext_test())
risk_and_launch_tests()
print(f"\n{len(PASS)} passed, {len(FAIL)} failed")
os.remove(dbpath)
sys.exit(1 if FAIL else 0)
