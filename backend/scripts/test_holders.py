"""Real holder-count tests. No network: Helius RPC is mocked."""
import asyncio, datetime as dt, json, os, sys, tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
fd, dbpath = tempfile.mkstemp(suffix=".db"); os.close(fd)
os.environ["DATABASE_URL"] = f"sqlite:///{dbpath}"
os.environ["HELIUS_API_KEY"] = "SECRETKEY123"
os.environ["SOLANA_ONLY"] = "1"

import httpx  # noqa: E402
from sqlalchemy import select  # noqa: E402
from app.database import Base, engine, SessionLocal  # noqa: E402
from app.models import Token, TokenRiskCheck  # noqa: E402
from app.providers import helius as H  # noqa: E402
from app.services import ingestion as ING  # noqa: E402
from app.services.holders import real_holder_stats  # noqa: E402
from app.routers import data_api as API  # noqa: E402

Base.metadata.create_all(engine)
PASS, FAIL = [], []
def check(name, cond, extra=""):
    (PASS if cond else FAIL).append(name); print(("  ✅" if cond else "  ❌"), name, extra)

def transport(pages, das_error=False):
    """pages: list of lists of (owner, amount) per DAS page."""
    def handler(req: httpx.Request):
        body = json.loads(req.content); m = body["method"]
        if m == "getAccountInfo":
            return httpx.Response(200, json={"result": {"value": {"owner": "Tokenkeg", "data": {"parsed": {
                "type": "mint", "info": {"mintAuthority": None, "freezeAuthority": None,
                                         "supply": "100000000", "decimals": 6}}}}}})
        if m == "getTokenLargestAccounts":
            return httpx.Response(200, json={"result": {"value": [{"uiAmountString": "10"}]}})
        if m == "getTokenAccounts":
            if das_error:
                return httpx.Response(200, json={"error": {"message": "method not available"}})
            pg = body["params"]["page"]
            accts = pages[pg - 1] if pg <= len(pages) else []
            return httpx.Response(200, json={"result": {"token_accounts": [
                {"owner": o, "amount": a} for o, a in accts]}})
        return httpx.Response(404, json={})
    return httpx.MockTransport(handler)

def page(start, n, amount=5):
    return [(f"W{i}", amount) for i in range(start, start + n)]

async def run():
    # 1. distinct owners, zero balances ignored, duplicates merged, short page => not capped
    pg = page(0, 700) + [("W0", 3), ("Z1", 0)]            # W0 twice, Z1 empty
    r = await H.HeliusProvider(api_key="SECRETKEY123", transport=transport([pg])).check_token("MintA")
    check("distinct non-zero owners counted", r.ok and r.holders_count == 700, str(r.holders_count))
    check("short page => not capped", r.holders_capped is False)

    # 2. pagination across pages
    r = await H.HeliusProvider(api_key="SECRETKEY123",
                               transport=transport([page(0, 1000), page(1000, 250)])).check_token("MintA")
    check("pagination adds up (1250)", r.holders_count == 1250 and not r.holders_capped, str(r.holders_count))

    # 3. page cap => capped flag
    import app.providers.helius as HM
    HM.HELIUS_HOLDER_MAX_PAGES = 2
    r = await H.HeliusProvider(api_key="SECRETKEY123", transport=transport(
        [page(0, 1000), page(1000, 1000), page(2000, 1000)])).check_token("MintA")
    check("cap reached => at least 2000 + capped", r.holders_count == 2000 and r.holders_capped is True,
          f"{r.holders_count} {r.holders_capped}")
    HM.HELIUS_HOLDER_MAX_PAGES = 5

    # 4. DAS failure never breaks the risk check
    r = await H.HeliusProvider(api_key="SECRETKEY123", transport=transport([], das_error=True)).check_token("MintA")
    check("DAS error: risk check still ok, holders unknown", r.ok and r.holders_count is None)
    check("api key never leaks", "SECRETKEY123" not in (r.error or ""))

    # 5. stats + growth from stored real checks
    now = dt.datetime.utcnow()
    with SessionLocal() as db:
        t = Token(address="RealTok" + "1" * 30, chain="solana", symbol="RT", name="RealTok"); db.add(t); db.flush()
        u = Token(address="NewTok" + "2" * 30, chain="solana", symbol="NT", name="NewTok"); db.add(u); db.flush()
        for hrs, cnt in [(20, 1000), (10, 1100), (0, 1200)]:
            db.add(TokenRiskCheck(token_id=t.id, ok=True, checked_at=now - dt.timedelta(hours=hrs),
                                  holders_count=cnt, holders_capped=False))
        db.add(TokenRiskCheck(token_id=u.id, ok=True, checked_at=now, holders_count=50))
        db.add(TokenRiskCheck(token_id=u.id, ok=False, checked_at=now, error="x"))
        db.commit()
        st = real_holder_stats(db)
        check("latest real count used", st[t.id]["count"] == 1200)
        check("24h growth from our own history (+20%)", st[t.id]["growth_pct"] == 20.0, str(st[t.id]["growth_pct"]))
        check("single check => growth unknown, never faked", st[u.id]["growth_pct"] is None)
        tid, uid = t.id, u.id

    # 6. onchain_checks stores holder counts and rechecks old rows without them
    with SessionLocal() as db:
        w = Token(address="OldTok" + "3" * 30, chain="solana", symbol="OT", name="OldTok"); db.add(w); db.flush()
        db.add(TokenRiskCheck(token_id=w.id, ok=True, checked_at=now - dt.timedelta(hours=2), holders_count=None))
        db.commit(); wid = w.id
    orig = H.HeliusProvider
    class P(orig):
        def __init__(self, *a, **k): super().__init__(api_key="SECRETKEY123", transport=transport([page(0, 321)]))
    ING.helius_mod.HeliusProvider = P
    await ING.onchain_checks()
    ING.helius_mod.HeliusProvider = orig
    with SessionLocal() as db:
        last = db.execute(select(TokenRiskCheck).where(TokenRiskCheck.token_id == wid)
                          .order_by(TokenRiskCheck.id.desc()).limit(1)).scalar_one()
        check("old check without holders is re-run and stores the count", last.holders_count == 321, str(last.holders_count))

    # 7. endpoints serve real data
    with SessionLocal() as db:
        tok = db.execute(select(Token).where(Token.id == tid)).scalar_one()
        h = API.token_holders(tok.address, db)
        check("/holders current is real (helius)", h["current"]["holders"] == 1200 and h["current"]["source"] == "helius"
              and h["holders_mock"] is False)
        hist = API.token_history(tok.address, metric="holders", tf="24h", db=db)
        check("holder chart points are real checks", len(hist["points"]) == 3 and hist["mock"] is False,
              str(len(hist["points"])))
        check("holders listed as real in data_quality",
              "holders" in API.DATA_QUALITY["real"] and "holders" not in API.DATA_QUALITY["simulated"])

asyncio.run(run())
print(f"\n{len(PASS)} passed, {len(FAIL)} failed")
sys.exit(1 if FAIL else 0)
