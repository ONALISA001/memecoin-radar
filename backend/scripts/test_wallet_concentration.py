"""Real-wallet holder concentration (pools/PDAs/burn excluded). No network."""
import asyncio, json, os, sys, tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
fd, dbpath = tempfile.mkstemp(suffix=".db"); os.close(fd)
os.environ["DATABASE_URL"] = f"sqlite:///{dbpath}"
os.environ["HELIUS_API_KEY"] = "SECRETKEY123"
os.environ["SOLANA_ONLY"] = "1"

import httpx  # noqa: E402
from cryptography.hazmat.primitives import serialization  # noqa: E402
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey  # noqa: E402
from sqlalchemy import inspect, select, text  # noqa: E402
from app.database import Base, engine, SessionLocal, migrate_postgres  # noqa: E402

PASS, FAIL = [], []
def check(name, cond, extra=""):
    (PASS if cond else FAIL).append(name); print(("  ✅" if cond else "  ❌"), name, extra)

# --- migration: an OLD token_risk_checks table gets the new columns ---------
with engine.begin() as c:
    c.execute(text("""CREATE TABLE token_risk_checks (id INTEGER PRIMARY KEY, token_id INTEGER NOT NULL,
        checked_at DATETIME DEFAULT CURRENT_TIMESTAMP, ok BOOLEAN, error VARCHAR(200), mint_authority VARCHAR(64),
        freeze_authority VARCHAR(64), supply FLOAT, decimals INTEGER, top1_pct FLOAT, top10_pct FLOAT,
        holders_sampled INTEGER, token_2022 BOOLEAN, holders_count INTEGER, holders_capped BOOLEAN)"""))
migrate_postgres()
cols = {c["name"] for c in inspect(engine).get_columns("token_risk_checks")}
check("sqlite migration adds wallet columns", {"top1_wallet_pct", "top10_wallet_pct", "excluded_pct"} <= cols)
migrate_postgres()
check("migration is idempotent", True)

from app.engine import risk as R  # noqa: E402
from app.engine.context import TokenContext  # noqa: E402
from app.models import Token, TokenRiskCheck  # noqa: E402
from app.providers import helius as H  # noqa: E402
from app.services import ingestion as ING  # noqa: E402
from app.services.contract_facts import contract_facts_for  # noqa: E402
Base.metadata.create_all(engine)     # creates the other tables; keeps the migrated one

# --- curve test ------------------------------------------------------------
def b58enc(b):
    n = int.from_bytes(b, "big"); s = ""
    while n:
        n, r = divmod(n, 58); s = H._B58[r] + s
    return "1" * (len(b) - len(b.lstrip(b"\0"))) + s

def wallet():
    return b58enc(Ed25519PrivateKey.generate().public_key()
                  .public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw))

POOL = "5Q544fKrFoe6tsEbD7S8EmxGTJYAKtTVhAW5Q5pge4j1"      # Raydium AMM authority (a PDA)
check("real wallets are on-curve", all(H.is_on_curve(wallet()) for _ in range(100)))
check("pool authority PDA is off-curve", H.is_on_curve(POOL) is False)
check("garbage address => not a wallet", H.is_on_curve("not-base58-0OIl") is False)

# --- concentration math ----------------------------------------------------
W = [wallet() for _ in range(12)]
holdings = [(POOL, 600.0), (W[0], 50.0), (W[0], 30.0), ("1nc1nerator11111111111111111111111111111111", 100.0)] + \
           [(W[i], 10.0) for i in range(1, 12)]
t1, t10, ex = H.wallet_concentration(holdings, 1000.0)
check("pool + burn excluded", ex == 70.0, str(ex))
check("same owner's accounts merged (largest wallet 8%)", t1 == 8.0, str(t1))
check("top-10 wallets = 80 + 9×10 = 17%", t10 == 17.0, str(t10))

# --- provider: getMultipleAccounts owner lookup -----------------------------
ACCTS = [(f"Acct{i}", amt, owner) for i, (owner, amt) in enumerate(holdings)]
def transport(owner_error=False):
    def handler(req):
        body = json.loads(req.content); m = body["method"]
        if m == "getAccountInfo":
            return httpx.Response(200, json={"result": {"value": {"owner": "Tokenkeg", "data": {"parsed": {
                "type": "mint", "info": {"mintAuthority": None, "freezeAuthority": None,
                                         "supply": "1000000000", "decimals": 6}}}}}})
        if m == "getTokenLargestAccounts":
            return httpx.Response(200, json={"result": {"value": [
                {"address": a, "uiAmountString": str(amt)} for a, amt, _ in ACCTS]}})
        if m == "getMultipleAccounts":
            if owner_error:
                return httpx.Response(200, json={"error": {"message": "boom"}})
            by = {a: o for a, _, o in ACCTS}
            return httpx.Response(200, json={"result": {"value": [
                {"data": {"parsed": {"info": {"owner": by[a]}}}} for a in body["params"][0]]}})
        if m == "getTokenAccounts":
            return httpx.Response(200, json={"result": {"token_accounts": [{"owner": "X", "amount": 1}]}})
        return httpx.Response(404, json={})
    return httpx.MockTransport(handler)

async def run():
    r = await H.HeliusProvider(api_key="SECRETKEY123", transport=transport()).check_token("MintA")
    check("check ok with wallet figures", r.ok and r.top1_wallet_pct == 8.0 and r.top10_wallet_pct == 17.0
          and r.excluded_pct == 70.0, f"{r.top1_wallet_pct} {r.top10_wallet_pct} {r.excluded_pct}")
    check("raw account top1 still kept (pool = 60%)", r.top1_pct == 60.0, str(r.top1_pct))
    check("no false 'pool vault' alarm when wallets are spread",
          not any(f["id"].startswith("top1") for f in H.risk_flags(r)), str(H.risk_flags(r)))
    r2 = await H.HeliusProvider(api_key="SECRETKEY123", transport=transport(True)).check_token("MintA")
    check("owner lookup failure: check still ok, wallet figures unknown",
          r2.ok and r2.top10_wallet_pct is None and r2.mint_authority is None)

    # --- ingestion stores it; incomplete old checks are retried ------------
    with SessionLocal() as db:
        t = Token(address="RealTok" + "1" * 30, chain="solana", symbol="RT"); db.add(t); db.flush()
        tid = t.id
        db.add(TokenRiskCheck(token_id=tid, ok=True, holders_count=10,
                              checked_at=__import__("datetime").datetime.now(__import__("datetime").timezone.utc).replace(tzinfo=None)
                              - __import__("datetime").timedelta(hours=2)))   # old-style check
        db.commit()
    real_cls = H.HeliusProvider
    ING.helius_mod.HeliusProvider = lambda: real_cls(api_key="SECRETKEY123", transport=transport())
    await ING.onchain_checks()
    ING.helius_mod.HeliusProvider = real_cls
    with SessionLocal() as db:
        last = db.execute(select(TokenRiskCheck).where(TokenRiskCheck.token_id == tid)
                          .order_by(TokenRiskCheck.id.desc()).limit(1)).scalar_one()
        check("old check without wallet data re-checked after 1h", last.top10_wallet_pct == 17.0,
              str(last.top10_wallet_pct))
        f = contract_facts_for(db, db.get(Token, tid))
        check("facts carry real wallet concentration", f["top10_wallet_pct"] == 17.0 and f["top1_wallet_pct"] == 8.0)

        # --- risk engine -------------------------------------------------
        ctx = TokenContext.build(db, db.get(Token, tid), 1_000_000 * 300)
        comp = lambda rp: next(c for c in rp["components"] if c["label"] == "Holder concentration")  # noqa: E731
        c = comp(R.calculate_risk(ctx, f))
        check("risk uses real wallet figures (t10 17% → 3, top1 8% → 4)", c["points"] == 7 and c["source"] == "helius",
              str(c))
        heavy = dict(f, top10_wallet_pct=65.0, top1_wallet_pct=22.0)
        rp = R.calculate_risk(ctx, heavy)
        check("concentrated token maxes concentration (25)", comp(rp)["points"] == 25)
        check("concentration flag raised", any("top-10 wallets" in x for x in rp["flags"]))
        cu = comp(R.calculate_risk(ctx, dict(f, top10_wallet_pct=None, top1_wallet_pct=None)))
        check("no wallet data => unverified penalty", cu["points"] == R.CONCENTRATION_UNVERIFIED_POINTS
              and cu["source"] == "unverified" and cu["value"] == "unknown")
        cm = comp(R.calculate_risk(ctx, {"source": "mock:chain-sim"}))
        check("demo tokens keep labeled simulated concentration", cm["source"] == "mock:chain-sim")

asyncio.run(run())
print(f"\n{len(PASS)} passed, {len(FAIL)} failed")
sys.exit(1 if FAIL else 0)
