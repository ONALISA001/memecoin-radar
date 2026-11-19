"""Real contract facts in the risk score + no simulated backfill for real tokens.
No network: discovery is mocked, Helius checks are inserted directly."""
import asyncio, datetime as dt, os, subprocess, sys, tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
fd, dbpath = tempfile.mkstemp(suffix=".db"); os.close(fd)
os.environ["DATABASE_URL"] = f"sqlite:///{dbpath}"
os.environ["HELIUS_API_KEY"] = ""
os.environ["SOLANA_ONLY"] = "1"

from sqlalchemy import func, select  # noqa: E402
from app.database import Base, engine, SessionLocal  # noqa: E402
from app.engine import risk as R, signals as S, timeseries as tsx  # noqa: E402
from app.engine.context import TokenContext  # noqa: E402
from app.models import PriceSnapshot, SocialSnapshot, Token, TokenRiskCheck, TrendScore  # noqa: E402
from app.providers.base import RawToken  # noqa: E402
from app.services import ingestion as ING  # noqa: E402
from app.services.contract_facts import contract_facts_for, latest_contract_facts  # noqa: E402
from app.routers import data_api as API  # noqa: E402

Base.metadata.create_all(engine)
PASS, FAIL = [], []
def check(name, cond, extra=""):
    (PASS if cond else FAIL).append(name); print(("  ✅" if cond else "  ❌"), name, extra)

# --- timeseries: no baseline => no fake spike ---------------------------
now = 1_000_000 * 300
only_recent = {now - 300: 50.0, now - 600: 40.0}
check("no previous window => growth 0 (not +2000%)", tsx.window_growth(only_recent, now, 3600)[2] == 0.0)
with_base = {now - 300: 50.0, now - 3900: 25.0}
check("real baseline still measured", tsx.window_growth(with_base, now, 3600)[2] == 100.0)
check("zero-valued baseline buckets still count as baseline",
      tsx.window_growth({now - 300: 5.0, now - 3900: 0.0}, now, 3600)[2] > 0)
check("level growth: history too short => 0", tsx.level_growth({now - 300: 2.0}, now, 3600)[2] == 0.0)
check("history_hours", abs(tsx.history_hours({now - 7200: 1.0, now: 1.0}, now) - 2.0) < 1e-9)

# --- full tick with mocked discovery ------------------------------------
def tk(price):
    return dict(price_usd=price, mcap=2e6, fdv=2e6, liquidity_usd=90000, volume_h24=500000,
                volume_h6=200000, volume_h1=40000, volume_m5=3000, pc_m5=1.0, pc_h1=12.0,
                pc_h6=30.0, pc_h24=80.0, txns_h1_buys=210, txns_h1_sells=140,
                txns_h24_buys=3100, txns_h24_sells=2600, txns_m5_buys=18, txns_m5_sells=12,
                pair_liquidity_usd=90000)

A, B = "RealAAAA" + "1" * 30, "RealBBBB" + "2" * 30
async def fake_discover():
    return ([RawToken(address=A, chain="solana", name="Alpha", symbol="ALPHA"),
             RawToken(address=B, chain="solana", name="Beta", symbol="BETA")],
            {A: tk(0.001), B: tk(0.002)}, False)
ING.discover_tokens = fake_discover


def sc_ts_any(db):
    return db.execute(select(TrendScore.ts).limit(1)).scalar()


def risk_comp(db, tok, label):
    sc = db.execute(select(TrendScore).where(TrendScore.token_id == tok.id)
                    .order_by(TrendScore.id.desc()).limit(1)).scalar_one()
    comps = (sc.components_json or {}).get("risk") or []
    return next((c for c in comps if c["label"] == label), None), sc


async def run():
    await ING.tick()
    with SessionLocal() as db:
        a = db.execute(select(Token).where(Token.address == A)).scalar_one()
        b = db.execute(select(Token).where(Token.address == B)).scalar_one()
        n_price = db.execute(select(func.count()).select_from(PriceSnapshot)
                             .where(PriceSnapshot.token_id == a.id)).scalar()
        n_soc = db.execute(select(func.count()).select_from(SocialSnapshot)
                           .where(SocialSnapshot.token_id == a.id)).scalar()
        check("real token: NO simulated 48h price backfill (only the live bucket)", n_price == 1, str(n_price))
        check("real token: no simulated social backfill", n_soc <= 1, str(n_soc))

        c, sc = risk_comp(db, a, "Contract permissions")
        check("unchecked token => contract 'unknown' + unverified penalty",
              c and "unknown" in c["value"] and c["points"] == R.CONTRACT_UNVERIFIED_POINTS
              and c["source"] == "unverified", str(c))
        d, _ = risk_comp(db, a, "Deployer history")
        check("deployer history unavailable (not simulated)", d and d["value"] == "unavailable" and d["points"] == 0)
        ctx = TokenContext.build(db, a, sc.ts)
        check("new real token is not 'emerging' without recorded history",
              S.is_emerging(ctx, 99, 60, 1.0, 5) is False)
        from app.engine.market_intel import compute_market_intel
        mi = compute_market_intel(ctx)
        check("first price of a new token is NOT a 'new high' breakout", not mi["ath"].get("new_ath"))

        # Helius says: A has mint ON + freeze ON; B is clean
        db.add(TokenRiskCheck(token_id=a.id, ok=True, mint_authority="Auth1", freeze_authority="Auth2",
                              top1_pct=5, top10_pct=20, token_2022=False))
        db.add(TokenRiskCheck(token_id=b.id, ok=True, mint_authority=None, freeze_authority=None,
                              top1_pct=5, top10_pct=20, token_2022=False))
        # an older failed check must not override; a newer failed one must not hide the last good one
        db.add(TokenRiskCheck(token_id=b.id, ok=False, error="timeout",
                              checked_at=dt.datetime.utcnow() + dt.timedelta(minutes=1)))
        db.commit()
        fa, fb = contract_facts_for(db, a), contract_facts_for(db, b)
        check("facts come from Helius", fa["source"] == "helius" and fa["verified"] is True)
        check("mint/freeze ON read from the real check", fa["mint_authority"] and fa["freeze_authority"])
        check("latest failed check doesn't hide last good one", fb["source"] == "helius"
              and fb["mint_authority"] is False)

    ING.bucket_of = (lambda real: (lambda t: real(t) + 300))(ING.bucket_of)
    await ING.tick()
    with SessionLocal() as db:
        a = db.execute(select(Token).where(Token.address == A)).scalar_one()
        b = db.execute(select(Token).where(Token.address == B)).scalar_one()
        ca, _ = risk_comp(db, a, "Contract permissions")
        cb, _ = risk_comp(db, b, "Contract permissions")
        check("risk score uses REAL mint+freeze (15/15)", ca["points"] == 15 and ca["source"] == "helius",
              str(ca))
        check("clean token scores 0 contract risk", cb["points"] == 0 and "off" in cb["value"], str(cb))
        detail = API.token_detail(A, db)
        check("token page shows real facts", detail["contract_facts"]["source"] == "helius")
        from app.providers.mock_providers import Sim
        maddr = "MockXYZ" + "9" * 30
        st = Sim.fresh(maddr, price_now=0.01, mcap_now=1e6, liq_now=5e4, vol_h1=1e4, vol_h24=1e5,
                       pc_h1=1.0, pc_h6=2.0, pc_h24=3.0, ref_bucket=sc_ts_any(db)).to_state()
        mock = Token(address=maddr, chain="solana", symbol="MK", state_json=st); db.add(mock)
        bare = Token(address="MockBARE" + "8" * 30, chain="solana", symbol="MB"); db.add(bare); db.flush()
        facts = latest_contract_facts(db, [mock, bare])
        check("demo Mock tokens keep (labeled) simulated facts", facts[mock.id]["source"].startswith("mock"))
        check("Mock token without sim state => unverified, no crash", facts[bare.id]["source"] == "unverified")
        db.rollback()

    # purge script: dry run reports, --apply deletes pre-discovery rows only
    with SessionLocal() as db:
        a = db.execute(select(Token).where(Token.address == A)).scalar_one()
        first = int(a.first_seen_at.replace(tzinfo=dt.timezone.utc).timestamp()) // 300 * 300
        db.add(PriceSnapshot(token_id=a.id, ts=first - 3600, price_usd=9.9))   # legacy fake row
        db.commit()
    script = os.path.join(os.path.dirname(os.path.abspath(__file__)), "purge_sim_backfill.py")
    env = dict(os.environ)
    out = subprocess.run([sys.executable, script], capture_output=True, text=True, env=env).stdout
    check("purge dry run reports the legacy row", "price_snapshots" in out and " 1" in out, out.strip()[-120:])
    subprocess.run([sys.executable, script, "--apply"], capture_output=True, text=True, env=env)
    with SessionLocal() as db:
        left = db.execute(select(func.count()).select_from(PriceSnapshot)
                          .where(PriceSnapshot.token_id == a.id)).scalar()
        check("purge removed only the pre-discovery row", left == 2, str(left))

asyncio.run(run())
print(f"\n{len(PASS)} passed, {len(FAIL)} failed")
sys.exit(1 if FAIL else 0)
