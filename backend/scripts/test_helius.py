"""Helius on-chain risk + Solana-only tests. No network: RPC is mocked."""
import asyncio, datetime as dt, json, os, sys, tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
fd, dbpath = tempfile.mkstemp(suffix=".db"); os.close(fd)
os.environ["DATABASE_URL"] = f"sqlite:///{dbpath}"
os.environ["HELIUS_API_KEY"] = "SECRETKEY123"
os.environ["SOLANA_ONLY"] = "1"

import httpx  # noqa: E402
from sqlalchemy import select  # noqa: E402
from app.database import Base, engine, SessionLocal  # noqa: E402
from app.models import Token, TokenRiskCheck, DataSource  # noqa: E402
from app.providers import helius as H  # noqa: E402
from app.providers import dexscreener as D  # noqa: E402
from app.services import ingestion as ING  # noqa: E402

Base.metadata.create_all(engine)
PASS, FAIL = [], []
def check(name, cond, extra=""):
    (PASS if cond else FAIL).append(name); print(("  ✅" if cond else "  ❌"), name, extra)

def rpc_transport(mint_auth=None, freeze_auth=None, amounts=(10, 5, 5), owner="Tokenkeg", status=200):
    def handler(req: httpx.Request):
        if status != 200:
            return httpx.Response(status, request=req, json={})
        method = json.loads(req.content)["method"]
        if method == "getAccountInfo":
            return httpx.Response(200, json={"result": {"value": {"owner": owner, "data": {"parsed": {
                "type": "mint", "info": {"mintAuthority": mint_auth, "freezeAuthority": freeze_auth,
                                         "supply": "100000000", "decimals": 6}}}}}})
        return httpx.Response(200, json={"result": {"value": [{"uiAmountString": str(a)} for a in amounts]}})
    return httpx.MockTransport(handler)

async def run():
    p = H.HeliusProvider(api_key="SECRETKEY123", transport=rpc_transport())
    r = await p.check_token("MintA")
    check("renounced mint+freeze, spread holders => low", r.ok and H.risk_label(r) == "low", f"top10={r.top10_pct}")
    check("supply scaled by decimals (100 tokens)", abs(r.supply - 100) < 1e-6)
    check("top1/top10 percentages", r.top1_pct == 10.0 and r.top10_pct == 20.0)

    r = await H.HeliusProvider(api_key="K", transport=rpc_transport(mint_auth="Abc", freeze_auth="Def")).check_token("MintB")
    ids = {f["id"] for f in H.risk_flags(r)}
    check("active mint+freeze authority => high", H.risk_label(r) == "high" and {"mint_authority_active", "freeze_authority_active"} <= ids)

    r = await H.HeliusProvider(api_key="K", transport=rpc_transport(amounts=(60, 5))).check_token("MintC")
    check("top1 >= 50% => high concentration flag", "top1_concentration" in {f["id"] for f in H.risk_flags(r)})

    r = await H.HeliusProvider(api_key="K", transport=rpc_transport(owner=H.TOKEN_2022)).check_token("MintD")
    check("Token-2022 with no risky extensions => low", r.token_2022 and r.token_extensions == ""
          and H.risk_label(r) == "low")
    r.token_extensions = None      # a stored check from before extensions were analysed
    check("un-analysed Token-2022 still flagged medium", H.risk_label(r) == "medium")

    r = await H.HeliusProvider(api_key="SECRETKEY123", transport=rpc_transport(status=401)).check_token("MintE")
    check("HTTP error => ok False, key scrubbed", (not r.ok) and "SECRETKEY123" not in r.error, r.error[:60])
    check("failed check has label unknown", H.risk_label(r) == "unknown")

    r = await H.HeliusProvider(api_key="", transport=rpc_transport()).check_token("MintF")
    check("no key => disabled, no network", (not r.ok) and "no HELIUS_API_KEY" in r.error)

    # ---- ingestion quota guard + retention
    calls = []
    class Fake:
        async def check_token(self, addr):
            calls.append(addr); return H.OnchainRisk(ok=True, supply=1, top1_pct=5, top10_pct=10, holders_sampled=10)
    ING.helius_mod.HeliusProvider = Fake
    ING.helius_mod.HELIUS_API_KEY = "x"
    ING.HELIUS_MAX_CHECKS_PER_TICK = 3
    with SessionLocal() as db:
        for i in range(6):
            db.add(Token(address=f"RealMint{i}", chain="solana", symbol=f"T{i}", name="t"))
        db.add(Token(address="MockSKIP", chain="solana", symbol="M", name="m"))
        db.add(Token(address="0xEthToken", chain="ethereum", symbol="E", name="e"))
        db.commit()
    await ING.onchain_checks()
    check("max 3 checks per tick", len(calls) == 3, str(calls))
    check("mock + non-solana tokens never sent to Helius", not any(a.startswith("Mock") or a.startswith("0x") for a in calls))
    await ING.onchain_checks()
    check("second tick checks the remaining unchecked tokens first", len(set(calls)) == 6, f"{len(set(calls))} distinct")
    n = len(calls); await ING.onchain_checks()
    check("fresh checks are not repeated within recheck window", len(calls) == n)
    with SessionLocal() as db:
        src = db.execute(select(DataSource).where(DataSource.name == "helius")).scalar_one()
        check("helius source reported ok", src.status == "ok")
        row = db.execute(select(TokenRiskCheck)).scalars().first()
        row.checked_at = dt.datetime.utcnow() - dt.timedelta(days=8); db.commit()
    await ING.onchain_checks()
    with SessionLocal() as db:
        old = db.execute(select(TokenRiskCheck).where(TokenRiskCheck.checked_at < dt.datetime.utcnow() - dt.timedelta(days=7))).scalars().all()
        check("rows older than 7 days pruned (retention)", len(old) == 0)

asyncio.run(run())
check("DexScreener limited to Solana when SOLANA_ONLY=1", D.CHAINS == ("solana",))
check("fallback universe is Solana-only", all(t.chain == "solana" for t in ING._fallback_universe()) and len(ING._fallback_universe()) > 0)
print(f"\n{len(PASS)} passed, {len(FAIL)} failed")
sys.exit(1 if FAIL else 0)
