"""Launch forensics tests (bundles, snipers, funders). No network: Helius RPC is mocked."""
import asyncio, datetime as dt, json, os, sys, tempfile, time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
fd, dbpath = tempfile.mkstemp(suffix=".db"); os.close(fd)
os.environ["DATABASE_URL"] = f"sqlite:///{dbpath}"
os.environ["HELIUS_API_KEY"] = "SECRETKEY123"

import httpx  # noqa: E402
from app.database import Base, engine, SessionLocal  # noqa: E402
from app.models import Token, TokenRiskCheck, TokenLaunchForensics  # noqa: E402
from app.providers import helius as H  # noqa: E402
from app.providers import launch_forensics as LF  # noqa: E402
from app.services import launch_forensics as SVC  # noqa: E402
from app.services.contract_facts import latest_contract_facts  # noqa: E402
from app.services.launch_alerts import launch_verdict, format_launch  # noqa: E402

Base.metadata.create_all(engine)
PASS, FAIL = [], []
def check(name, cond, extra=""):
    (PASS if cond else FAIL).append(name); print(("  ✅" if cond else "  ❌"), name, extra)

# Real on-curve wallet addresses (well-known mainnet wallets / programs' upgrade keys are
# not needed: we just need valid ed25519 points). Generated deterministically below.
def wallet(i: int) -> str:
    import hashlib
    n = i
    while True:
        b = hashlib.sha256(f"w{n}".encode()).digest()
        a = H._B58[0]
        num = int.from_bytes(b, "big"); s = ""
        while num:
            num, r = divmod(num, 58); s = H._B58[r] + s
        s = "1" * (len(b) - len(b.lstrip(b"\0"))) + s
        if H.is_on_curve(s):
            return s
        n += 1000

MINT, CURVE = "MintXYZ", "Curve1111111111111111111111111111111111111"     # CURVE is not a valid point
CREATOR, B1, B2, S1, E1, FUNDER = (wallet(i) for i in range(1, 7))
check("test wallets are on-curve, curve PDA is not", all(H.is_on_curve(w) for w in (CREATOR, B1, S1))
      and not H.is_on_curve(CURVE))
SUPPLY = 1_000_000_000.0

def tx(slot, payer, deltas, t=1_700_000_000, err=None, sig=None, instr=None):
    pre, post = [], []
    for i, (owner, d) in enumerate(deltas.items()):
        pre.append({"accountIndex": i, "mint": MINT, "owner": owner, "uiTokenAmount": {"uiAmountString": "0"}})
        post.append({"accountIndex": i, "mint": MINT, "owner": owner, "uiTokenAmount": {"uiAmountString": str(d)}})
    return {"slot": slot, "blockTime": t, "transaction": {"signatures": [sig or f"s{slot}{payer[:4]}"],
            "message": {"accountKeys": [{"pubkey": payer, "signer": True}], "instructions": instr or []}},
            "meta": {"err": err, "preTokenBalances": pre, "postTokenBalances": post, "innerInstructions": []}}

LAUNCH = [
    tx(100, CREATOR, {CURVE: 900_000_000, CREATOR: 30_000_000}),          # create + dev buy 3%
    tx(100, B1, {B1: 60_000_000}), tx(100, B2, {B2: 50_000_000}),         # bundled: 11%
    tx(102, S1, {S1: 40_000_000}),                                        # sniper 4% (slot +2)
    tx(150, E1, {E1: 10_000_000}),                                        # ordinary early buyer
    tx(101, E1, {E1: 999}, err={"InstructionError": 1}),                  # failed: ignored
]

def unit():
    lf = LF.analyse_launch(LAUNCH, MINT, SUPPLY)
    check("creator = fee payer of creation tx", lf.status == "ok" and lf.creator == CREATOR)
    check("dev buy 3%", lf.dev_buy_pct == 3.0, str(lf.dev_buy_pct))
    check("bundle: 2 wallets, 11% in launch slot", lf.bundle_wallets == 2 and lf.bundle_pct == 11.0)
    check("snipers within 3 slots include bundle (3 wallets, 15%)", lf.sniper_wallets == 3 and lf.sniper_pct == 15.0)
    check("bonding curve (PDA) never counted as a buyer", all(w["wallet"] != CURVE for w in lf.wallets))
    check("failed tx ignored", next(w for w in lf.wallets if w["wallet"] == E1)["pct"] == 1.0)
    check("roles labelled", {w["wallet"]: w["role"] for w in lf.wallets}[S1] == "sniper")
    check("no data => no_data", LF.analyse_launch([], MINT, SUPPLY).status == "no_data")

    funding_tx = {"slot": 5, "blockTime": 1_700_000_000 - 3600, "transaction": {"message": {
        "accountKeys": [{"pubkey": CREATOR}], "instructions": [
            {"parsed": {"type": "transfer", "info": {"source": CREATOR, "destination": B1, "lamports": 1}}}]}},
        "meta": {"innerInstructions": []}}
    check("first funder from system transfer", LF.first_funder(funding_tx, B1) == CREATOR)
    LF.apply_funding(lf, {B1: {"funder": CREATOR, "first_time": 1_700_000_000 - 3600, "reached": True},
                          B2: {"funder": FUNDER, "first_time": 1_700_000_000 - 7200, "reached": True},
                          S1: {"funder": FUNDER, "first_time": None, "reached": False}})
    check("creator-funded buyer detected", lf.funded_by_creator == 1)
    check("shared funder group of 2", lf.shared_funder_wallets == 2)
    check("fresh wallets counted (B1, B2 < 24h old)", lf.fresh_early_wallets == 2 and lf.funder_checked == 3)
    ids = {f["id"]: f["severity"] for f in LF.forensic_flags(lf)}
    check("flags: creator funding high, bundle medium (11%)",
          ids.get("creator_funded_buyers") == "high" and ids.get("bundled_launch") == "medium", str(ids))
    return lf

def rpc_mock(launch_txs, gtfa=False, too_many=False):
    calls = []
    sig_by = {t["transaction"]["signatures"][0]: t for t in launch_txs}
    def handler(req):
        body = json.loads(req.content); m, p = body["method"], body["params"]
        calls.append(m)
        if m == "getTransactionsForAddress":
            if not gtfa:
                return httpx.Response(200, json={"error": {"code": -32601, "message": "requires Developer plan"}})
            return httpx.Response(200, json={"result": {"data": launch_txs if p[0] == MINT else []}})
        if m == "getSignaturesForAddress":
            if p[0] != MINT:
                return httpx.Response(200, json={"result": []})
            if too_many:
                return httpx.Response(200, json={"result": [{"signature": f"x{i}", "err": None} for i in range(1000)]})
            # real RPC order: newest first, also within a slot
            newest_first = [t for _, t in sorted(enumerate(launch_txs), key=lambda it: (-it[1]["slot"], -it[0]))]
            return httpx.Response(200, json={"result": [{"signature": t["transaction"]["signatures"][0],
                                                         "err": t["meta"]["err"]} for t in newest_first]})
        if m == "getTransaction":
            return httpx.Response(200, json={"result": sig_by.get(p[0])})
        if m == "getTokenAccountsByOwner":
            amt = "25000000" if p[0] == CREATOR else "40000000" if p[0] == S1 else "0"
            return httpx.Response(200, json={"result": {"value": [{"account": {"data": {"parsed": {"info": {
                "tokenAmount": {"uiAmountString": amt}}}}}}]}})
        return httpx.Response(200, json={"result": None})
    return httpx.MockTransport(handler), calls

async def provider_tests():
    LF._GTFA_UNAVAILABLE = False
    t, calls = rpc_mock(LAUNCH)
    p = LF.LaunchForensicsProvider(api_key="SECRETKEY123", transport=t, pace=0)
    lf = await p.analyse(MINT, SUPPLY)
    check("free plan: GTFA refused, falls back to standard RPC", lf.status == "ok" and lf.method == "rpc"
          and calls[0] == "getTransactionsForAddress" and "getSignaturesForAddress" in calls)
    check("GTFA not retried after refusal", LF._GTFA_UNAVAILABLE and calls.count("getTransactionsForAddress") == 1)
    check("same result via fallback", lf.bundle_pct == 11.0 and lf.creator == CREATOR)
    check("current holdings: dev 2.5%, snipers 4%", lf.dev_holds_pct == 2.5 and lf.snipers_hold_pct == 4.0,
          f"{lf.dev_holds_pct} {lf.snipers_hold_pct}")
    check("rpc call budget sane", p.calls < 40, str(p.calls))

    LF._GTFA_UNAVAILABLE = False
    t, calls = rpc_mock(LAUNCH, gtfa=True)
    lf = await LF.LaunchForensicsProvider(api_key="K", transport=t, pace=0).analyse(MINT, SUPPLY)
    check("Developer plan: one GTFA call for the launch", lf.method == "gtfa" and "getSignaturesForAddress" not in calls)

    LF._GTFA_UNAVAILABLE = True
    t, _ = rpc_mock(LAUNCH, too_many=True)
    lf = await LF.LaunchForensicsProvider(api_key="K", transport=t, pace=0).analyse(MINT, SUPPLY)
    check("history too long => too_active (not guessed)", lf.status == "too_active")

    def boom(req): return httpx.Response(401, json={})
    lf = await LF.LaunchForensicsProvider(api_key="SECRETKEY123", transport=httpx.MockTransport(boom), pace=0).analyse(MINT, SUPPLY)
    check("HTTP error => error status, key scrubbed", lf.status == "error" and "SECRETKEY123" not in lf.error)

async def service_tests():
    now_ms = time.time() * 1000
    with SessionLocal() as db:
        fresh = Token(chain="solana", address="FreshMint", symbol="FR", discovery_source="new_pool",
                      token_created_at=now_ms - 3.6e6)
        old = Token(chain="solana", address="OldMint", symbol="OLD", token_created_at=now_ms - 200 * 3.6e6)
        rug = Token(chain="solana", address="RugMint", symbol="RUG", discovery_source="new_pool",
                    token_created_at=now_ms - 1e6)
        db.add_all([fresh, old, rug]); db.flush()
        for t, auth in ((fresh, None), (old, None), (rug, "MintAuth")):
            db.add(TokenRiskCheck(token_id=t.id, ok=True, supply=SUPPLY, mint_authority=auth,
                                  top1_wallet_pct=2, top10_wallet_pct=12, holders_count=100))
        db.commit()
        cands = SVC.candidates(db)
        check("only the fresh, non-failed token is a candidate", [c[1] for c in cands] == ["FreshMint"], str(cands))
        fid = fresh.id
        f = latest_contract_facts(db, [fresh])[fid]
        check("launch alert WAITS for forensics", launch_verdict(f) == (None, []))

    class FakeProv:
        calls = 0
        async def analyse(self, mint, supply):
            lf = LF.analyse_launch(LAUNCH, MINT, supply)
            lf.funded_by_creator = 1
            return lf
    res = await SVC.run_launch_forensics(SessionLocal, FakeProv())
    check("service analyses and stores", res["ok"] == 1)
    with SessionLocal() as db:
        check("analysed once: not a candidate again", SVC.candidates(db) == [])
        tok = db.get(Token, fid)
        f = latest_contract_facts(db, [tok])[fid]
        v, why = launch_verdict(f)
        check("creator-funded buyers block the launch alert", v == "fail" and any("creator funded" in w for w in why), str(why))
        row = db.query(TokenLaunchForensics).one()
        row.funded_by_creator = 0; db.commit()
        f = latest_contract_facts(db, [tok])[fid]
        v, _ = launch_verdict(f)
        check("clean-enough launch passes (bundle 11% < 15%)", v == "pass")
        msg = format_launch(tok, f, {"liquidity": 10000, "mcap": 50000})
        check("alert shows launch wallet line", "Launch wallets: bundle 11%" in msg and "deployer history" not in msg)
        row.status = "too_active"; db.commit()
        f = latest_contract_facts(db, [tok])[fid]
        check("forensics impossible => alert not blocked", launch_verdict(f)[0] == "pass")
        row.status = "ok"; row.funded_by_creator = 2; db.commit()

        from app.engine import risk as R
        class Ctx:
            social_proxy, telegram, anomalies = True, {}, {}
            d = {"liq_now": 80_000, "liq_chg_3h": 0, "liq_3h_ago": 80_000, "vol_liq_ratio": 1,
                 "price_chg_1h": 5, "buy_share_1h": 0.55, "whale_net_24h": 0, "trades_per_trader": 2,
                 "vol24_liq_live": 2, "volume_24h": 100_000, "uniques_1h": 50, "mentions_1h": 0,
                 "lowq_24h": 0, "top10_now": 0, "deployer_pct_now": 0}
        f = latest_contract_facts(db, [tok])[fid]
        out = R.calculate_risk(Ctx(), f)
        comp = next(c for c in out["components"] if c["label"] == "Launch & deployer")
        check("risk engine scores launch forensics", comp["points"] >= 8 and comp["source"] == "helius:launch-forensics",
              str(comp))
        check("creator-funding surfaces as a risk flag", any("creator funded" in x for x in out["flags"]))

lf = unit()
asyncio.run(provider_tests())
asyncio.run(service_tests())
print(f"\n{len(PASS)} passed, {len(FAIL)} failed")
os.remove(dbpath)
sys.exit(1 if FAIL else 0)
