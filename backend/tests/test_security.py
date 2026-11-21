"""Access rules: visitors are read-only, only the owner key can change data."""
import pytest
from fastapi.testclient import TestClient

from app import security
from app.main import app, ensure_schema_and_demo_user

ensure_schema_and_demo_user()
client = TestClient(app)          # no `with`: lifespan (live ingestion loop) never starts

OWNER_KEY = "test-owner-key-123"


@pytest.fixture(autouse=True)
def _fresh_rate_limits():
    security._hits.clear()
    security._login_hits.clear()
    yield


def _hdr(token):
    return {"Authorization": f"Bearer {token}"}


def visitor_token():
    r = client.post("/api/auth/demo")
    assert r.status_code == 200
    return r.json()["token"]


def owner_token():
    r = client.post("/api/auth/login", json={"key": OWNER_KEY})
    assert r.status_code == 200, r.text
    assert r.json()["role"] == "owner"
    return r.json()["token"]


# Endpoints that change data or run jobs, split by who may call them.
# Owner-only: destructive, costly or outward-facing. Visitors must get 403.
OWNER_ROUTES = [
    ("delete", "/api/alerts/1", None),
    ("post", "/api/alerts/telegram/test", None),
    ("post", "/api/admin/backtest/run", None),
    ("post", "/api/paper/lab/reset", None),
    ("post", "/api/paper/lab/apply/x", None),
    ("post", "/api/paper/reset", None),
    ("post", "/api/paper/backtest", {}),
    ("post", "/api/paper/backtest/compare", {"hours": 24}),
    ("post", "/api/paper/supervisor/review", None),
    ("post", "/api/paper/supervisor/proposals/1/approve", None),
]
# Everyday actions open to every visitor session (OPEN_BASIC_ACTIONS=1, the default).
OPEN_ROUTES = [
    ("post", "/api/watchlist", {"address": "So11111111111111111111111111111111111111112"}),
    ("delete", "/api/watchlist/So11111111111111111111111111111111111111112", None),
    ("post", "/api/alerts", {"name": "x", "condition": {"metric": "trend_score", "op": "gt", "value": 1}}),
    ("patch", "/api/alerts/1", {"enabled": False}),
    ("post", "/api/alerts/events/read-all", None),
    ("post", "/api/paper/settings", {}),
    ("post", "/api/paper/lab/start", None),
    ("post", "/api/paper/resume", None),
    ("post", "/api/paper/trades/1/close", None),
]
WRITE_ROUTES = OWNER_ROUTES + OPEN_ROUTES


def _write_routes():
    for route in app.routes:
        methods = getattr(route, "methods", set()) - {"GET", "HEAD", "OPTIONS"}
        if methods and not route.path.startswith("/api/auth/"):
            yield route, methods


def test_every_write_route_has_an_explicit_guard():
    """Any new POST/PATCH/PUT/DELETE endpoint fails this test until it states who may call it."""
    unguarded = [f"{sorted(m)} {r.path}" for r, m in _write_routes()
                 if not any(d.dependency in (security.require_owner, security.require_user)
                            for d in r.dependencies)]
    assert not unguarded, f"write routes without require_owner/require_user: {unguarded}"


def test_open_routes_are_exactly_the_listed_ones():
    """Opening a route to visitors must be a deliberate, reviewed change to OPEN_ROUTES."""
    open_paths = sorted(f"{sorted(m)[0]} {r.path}" for r, m in _write_routes()
                        if any(d.dependency is security.require_user for d in r.dependencies))
    assert len(open_paths) == len(OPEN_ROUTES), open_paths


def test_write_route_list_is_complete():
    """Keeps the two lists (used for the checks below) in step with the app."""
    assert len(list(_write_routes())) == len(WRITE_ROUTES)


def test_no_token_is_rejected():
    assert client.post("/api/paper/reset").status_code == 401
    assert client.post("/api/paper/settings", json={}).status_code == 401     # open != anonymous


@pytest.mark.parametrize("method,path,body", OWNER_ROUTES)
def test_visitor_cannot_do_owner_actions(method, path, body):
    kw = {"headers": _hdr(visitor_token())}
    if body is not None:
        kw["json"] = body
    r = getattr(client, method)(path, **kw)
    assert r.status_code == 403, f"{method.upper()} {path} -> {r.status_code}"


@pytest.mark.parametrize("method,path,body", OPEN_ROUTES)
def test_visitor_can_do_basic_actions(method, path, body):
    kw = {"headers": _hdr(visitor_token())}
    if body is not None:
        kw["json"] = body
    r = getattr(client, method)(path, **kw)
    # allowed through the guard (it may still be 404 for an id that doesn't exist)
    assert r.status_code not in (401, 403), f"{method.upper()} {path} -> {r.status_code}: {r.text[:120]}"


def test_basic_actions_can_be_locked_again(monkeypatch):
    from app import config
    monkeypatch.setattr(config, "OPEN_BASIC_ACTIONS", False)
    r = client.post("/api/paper/settings", json={}, headers=_hdr(visitor_token()))
    assert r.status_code == 403


def test_visitor_can_read():
    r = client.get("/api/watchlist", headers=_hdr(visitor_token()))
    assert r.status_code == 200


def test_owner_can_write():
    r = client.post("/api/alerts/events/read-all", headers=_hdr(owner_token()))
    assert r.status_code == 200, r.text


def test_wrong_key_rejected():
    r = client.post("/api/auth/login", json={"key": "nope"})
    assert r.status_code == 401
    assert "token" not in r.json()


def test_login_is_rate_limited():
    codes = [client.post("/api/auth/login", json={"key": f"guess{i}"}).status_code for i in range(8)]
    assert 429 in codes
    # once limited, even the right key is refused until the window passes
    assert client.post("/api/auth/login", json={"key": OWNER_KEY}).status_code == 429


def test_forged_owner_token_rejected():
    import jwt, time
    forged = jwt.encode({"sub": "1", "role": "owner", "exp": int(time.time()) + 600},
                        "dev-only-insecure-secret-change-me", algorithm="HS256")
    assert client.post("/api/paper/reset", headers=_hdr(forged)).status_code == 401


def test_no_owner_key_means_no_owner_login(monkeypatch):
    from app.routers import user_api
    monkeypatch.setattr(user_api, "OWNER_KEY", "")
    monkeypatch.setattr(security, "OWNER_KEY", "")
    assert client.post("/api/auth/login", json={"key": ""}).status_code == 422
    assert client.post("/api/auth/login", json={"key": "anything"}).status_code == 503


def test_cors_closed_by_default():
    r = client.options("/api/tokens", headers={"Origin": "https://evil.example",
                                               "Access-Control-Request-Method": "GET"})
    assert r.headers.get("access-control-allow-origin") is None
