"""/tokens/{a}/holders must never serve simulated whale trades as real data.
WalletActivity is only written by the simulator; older versions wrote it for real
tokens too, and the endpoint returned those rows labelled source=helius, mock=false."""
import pytest
from fastapi.testclient import TestClient

from app import security
from app.database import SessionLocal
from app.main import app, ensure_schema_and_demo_user
from app.models import Token, WalletActivity

ensure_schema_and_demo_user()
client = TestClient(app)          # no `with`: the live ingestion loop never starts


@pytest.fixture(autouse=True)
def _fresh_rate_limits():
    security._hits.clear()
    yield


@pytest.fixture(scope="module")
def tokens():
    with SessionLocal() as db:
        real = Token(chain="solana", address="RealWhaleTestMint1111111111111111pump", symbol="RWT")
        mock = Token(chain="solana", address="MockWhaleTestMint", symbol="MWT")
        db.add_all([real, mock]); db.flush()
        for t in (real, mock):     # a legacy simulated row on each
            db.add(WalletActivity(token_id=t.id, ts=1_700_000_000, wallet=t.address[:6] + "wh123",
                                  side="sell", amount_usd=100_000.0, kind="whale"))
        db.commit()
        ids = {"real": (real.id, real.address), "mock": (mock.id, mock.address)}
    yield ids
    with SessionLocal() as db:
        tids = [i for i, _ in ids.values()]
        db.query(WalletActivity).filter(WalletActivity.token_id.in_(tids)).delete(synchronize_session=False)
        db.query(Token).filter(Token.id.in_(tids)).delete(synchronize_session=False)
        db.commit()


def test_real_token_gets_no_simulated_whales(tokens):
    d = client.get(f"/api/tokens/{tokens['real'][1]}/holders").json()
    assert d["whales"] == [] and d["mock"] is False and d["source"] == "helius"


def test_demo_token_still_shows_its_simulated_whales(tokens):
    d = client.get(f"/api/tokens/{tokens['mock'][1]}/holders").json()
    assert len(d["whales"]) == 1 and d["mock"] is True and d["source"] == "mock:chain-sim"
