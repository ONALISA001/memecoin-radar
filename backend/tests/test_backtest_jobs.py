"""Backtests run as background jobs (long windows outlast the hosting proxy's timeout)."""
import time

import pytest
from fastapi.testclient import TestClient

from app import security
from app.main import app, ensure_schema_and_demo_user
from app.paper import jobs as JOBS

ensure_schema_and_demo_user()
client = TestClient(app)


@pytest.fixture(autouse=True)
def _fresh():
    security._hits.clear()
    security._login_hits.clear()
    yield


def _owner():
    tok = client.post("/api/auth/login", json={"key": "test-owner-key-123"}).json()["token"]
    return {"Authorization": f"Bearer {tok}"}


def _wait(h, jid, timeout=30):
    t0 = time.time()
    while time.time() - t0 < timeout:
        j = client.get(f"/api/paper/backtest/jobs/{jid}", headers=h).json()
        if j["status"] in ("done", "error"):
            return j
        time.sleep(0.1)
    raise AssertionError("job did not finish")


def test_backtest_and_compare_run_in_background():
    h = _owner()
    r = client.post("/api/paper/backtest", json={"hours": 24, "background": True}, headers=h)
    assert r.status_code == 200 and r.json()["status"] in ("queued", "running", "done")
    j = _wait(h, r.json()["id"])
    assert j["status"] == "done" and "summary" in j["result"] and j["result"]["run_id"]
    r = client.post("/api/paper/backtest/compare", json={"hours": 24, "background": True}, headers=h)
    j = _wait(h, r.json()["id"])
    assert j["status"] == "done" and len(j["result"]["strategies"]) >= 7


def test_synchronous_mode_still_works():
    h = _owner()
    r = client.post("/api/paper/backtest", json={"hours": 24}, headers=h)
    assert r.status_code == 200 and "summary" in r.json()


def test_jobs_are_scoped_and_errors_are_reported():
    h = _owner()
    vis = {"Authorization": f"Bearer {client.post('/api/auth/demo').json()['token']}"}
    r = client.post("/api/paper/backtest", json={"hours": 24, "background": True}, headers=h)
    jid = r.json()["id"]
    _wait(h, jid)
    # jobs are scoped to their user id (read-only visitors share the demo user, like every other page)
    assert JOBS.get(jid, user_id=424242) is None
    assert client.get(f"/api/paper/backtest/jobs/{jid}", headers=vis).status_code == 200
    assert client.get("/api/paper/backtest/jobs/nope", headers=h).status_code == 404
    # a crashing job reports its error instead of hanging
    job = JOBS.submit(999_999, "test", lambda: 1 / 0)
    for _ in range(100):
        j = JOBS.get(job["id"], 999_999)
        if j["status"] == "error":
            break
        time.sleep(0.05)
    assert j["status"] == "error" and "division" in j["error"]


def test_visitors_cannot_start_jobs():
    vis = {"Authorization": f"Bearer {client.post('/api/auth/demo').json()['token']}"}
    assert client.post("/api/paper/backtest", json={"hours": 24, "background": True}, headers=vis).status_code == 403
    assert client.post("/api/paper/backtest/compare", json={"background": True}, headers=vis).status_code == 403
