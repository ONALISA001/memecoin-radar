"""New intelligence endpoints: priority coins, distribution watch, verdict windows,
calibration windows, the Strategy Lab review and the richer watchlist."""
import time

import pytest
from fastapi.testclient import TestClient

from app import security
from app.database import SessionLocal
from app.engine.backtest import HORIZONS, calibration_report
from app.main import app, ensure_schema_and_demo_user
from app.models import (PriceSnapshot, SignalEvent, SignalOutcome, Token, TokenVerdict, TrendScore,
                        Watchlist)

ensure_schema_and_demo_user()
client = TestClient(app)          # no `with`: the live ingestion loop never starts
NOW = int(time.time())


@pytest.fixture(autouse=True)
def _fresh_rate_limits():
    security._hits.clear()
    yield


def _summary(**kw):
    s = {"price": 1.0, "liquidity": 60_000, "volume_1h": 20_000, "price_change_h1": 5.0, "price_change_h24": 12.0,
         "momentum": 72, "momentum_band": "strong", "sellp": 30, "sellp_band": "neutral", "whale_class": "neutral",
         "liq_health": 75, "liq_band": "healthy"}
    s.update(kw)
    return s


@pytest.fixture(scope="module")
def tokens():
    with SessionLocal() as db:
        good = Token(chain="solana", address="IntelGoodMint", symbol="IGOOD", name="Intel good")
        dump = Token(chain="solana", address="IntelDumpMint", symbol="IDUMP", name="Intel dump")
        db.add_all([good, dump]); db.flush()
        db.add(TrendScore(token_id=good.id, ts=NOW - 30, trend=60, fomo=40, risk=20, risk_class="LOW", kind="live",
                          components_json={"summary": _summary()}))
        db.add(TrendScore(token_id=dump.id, ts=NOW - 30, trend=30, fomo=20, risk=40, risk_class="MODERATE", kind="live",
                          components_json={"summary": _summary(price=0.5, sellp=78, sellp_band="heavy_distribution",
                                                               momentum=30, momentum_band="low",
                                                               whale_class="distributing")}))
        # 2h-ago price for the window change: 1.0 -> 0.5 = -50%
        db.add(PriceSnapshot(token_id=dump.id, ts=NOW - 7200, price_usd=1.0))
        db.add(SignalEvent(token_id=good.id, ts=NOW - 600, kind="momentum_acceleration", title="up",
                           severity="positive", category="momentum", importance="HIGH", importance_score=70))
        db.add(SignalEvent(token_id=dump.id, ts=NOW - 600, kind="distribution_warning", title="down",
                           severity="danger", category="distribution", importance="HIGH", importance_score=70))
        db.commit()
        ids = [good.id, dump.id]
    yield {"good": "IntelGoodMint", "dump": "IntelDumpMint"}
    # leave the shared test DB as we found it (other modules wipe `tokens` and need no FK refs)
    with SessionLocal() as db:
        for m in (Watchlist, SignalEvent, PriceSnapshot, TrendScore, TokenVerdict):
            db.query(m).filter(m.token_id.in_(ids)).delete(synchronize_session=False)
        db.query(SignalOutcome).filter(SignalOutcome.token_id == 1).delete(synchronize_session=False)
        db.query(Token).filter(Token.id.in_(ids)).delete(synchronize_session=False)
        db.commit()


def test_priority_endpoint(tokens):
    r = client.get("/api/signals/priority?window=2h")
    assert r.status_code == 200, r.text
    d = r.json()
    addrs = [i["address"] for i in d["items"]]
    assert tokens["good"] in addrs and tokens["dump"] not in addrs
    assert d["window"] == "2h" and len(d["gates"]) == 5
    assert client.get("/api/signals/priority?window=3h").status_code == 422


def test_distribution_endpoint_with_window_and_filters(tokens):
    d = client.get("/api/market/distribution?window=2h&min_liquidity=0").json()
    row = next(i for i in d["items"] if i["address"] == tokens["dump"])
    assert row["window_change"] == pytest.approx(-50.0)
    assert all(i["address"] != tokens["good"] for i in d["items"])
    assert not any(i["address"] == tokens["dump"] for i in
                   client.get("/api/market/distribution?momentum=strong,extreme&min_liquidity=0").json()["items"])
    assert any(i["address"] == tokens["dump"] for i in
               client.get("/api/market/distribution?whale=distributing&min_liquidity=0").json()["items"])
    assert client.get("/api/market/distribution?sort=bogus").status_code == 422


def test_feed_tone_filter(tokens):
    up = client.get("/api/signals/feed?tone=constructive&since_secs=7200").json()["items"]
    down = client.get("/api/signals/feed?tone=risk&since_secs=7200").json()["items"]
    assert up and all(e["severity"] == "positive" for e in up)
    assert down and all(e["severity"] in ("danger", "warning") for e in down)


def test_verdict_windows():
    assert client.get("/api/verdicts?hours=2").json()["hours"] == 2
    sc = client.get("/api/verdicts/scorecard?hours=4").json()
    assert sc["hours"] == 4 and "overall" in sc
    assert client.get("/api/verdicts?hours=0").status_code == 422


def test_calibration_window_summary_and_rules():
    with SessionLocal() as db:
        for i, (ret, dd, out) in enumerate([(20, -5, "continued"), (-60, -70, "rug"), (2, -3, "neutral")]):
            db.add(SignalOutcome(token_id=1, ts=NOW - 3600 * 3 - i, horizon_secs=7200, trend=75, fomo=50, risk=30,
                                 emerging=False, kind="live", fwd_ret_pct=ret, max_dd_pct=dd, outcome=out))
        db.add(SignalOutcome(token_id=1, ts=NOW - 86400 * 10, horizon_secs=7200, trend=75, risk=30,
                             kind="live", fwd_ret_pct=50, max_dd_pct=-1, outcome="continued"))
        db.commit()
        assert 7200 in HORIZONS
        recent = calibration_report(db, days=1, now=NOW)
        s = recent["horizons"]["7200"]["summary"]
        assert s["n"] == 3 and s["continuation_rate"] == pytest.approx(33.3) and s["reversal_rate"] == pytest.approx(33.3)
        assert calibration_report(db, now=NOW)["horizons"]["7200"]["summary"]["n"] == 4   # all history
        rules = {r["rule"]: r for r in recent["signal_rules"] if r["horizon_secs"] == 7200}
        # a combined *signal* rule ("&") is scored on continuation, not on adverse outcomes
        both = rules["trend ≥ 70 & risk < 50"]
        assert both["kind"] == "signal" and both["hit_rate_pct"] == pytest.approx(33.3)
    assert client.get("/api/calibration/overview?days=1").status_code == 200


def test_lab_review_and_watchlist_endpoints(tokens):
    tok = client.post("/api/auth/login", json={"key": "test-owner-key-123"}).json()["token"]
    h = {"Authorization": f"Bearer {tok}"}
    rv = client.get("/api/paper/lab/review?ai=false", headers=h)
    assert rv.status_code == 200, rv.text
    assert "available" in rv.json()
    assert client.post("/api/paper/lab/start", headers=h).status_code == 200
    rv = client.get("/api/paper/lab/review?ai=false", headers=h).json()
    assert rv["available"] and {"ai_verdict", "liquid_scalper"} <= {s["key"] for s in rv["strategies"]}
    assert client.post("/api/watchlist", json={"address": tokens["good"]}, headers=h).status_code in (200, 201)
    items = client.get("/api/watchlist", headers=h).json()["items"]
    w = next(i for i in items if i["address"] == tokens["good"])
    assert w["intel"]["momentum_band"] == "strong" and w["events_24h"]["up"] >= 1
    assert w["priority"]["passed"] is True and w["priority"]["score"] > 0
