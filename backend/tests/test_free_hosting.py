"""Render + Supabase hosting: database URL handling, snapshot trimming, health, keep-awake."""
import asyncio
import time

from fastapi.testclient import TestClient

from app import config, main
from app.config import normalize_db_url
from app.database import SessionLocal
from app.main import app, ensure_schema_and_demo_user
from app.models import Token, TrendScore
from app.services import ingestion

ensure_schema_and_demo_user()
client = TestClient(app)


def test_database_url_normalisation():
    assert normalize_db_url("postgres://u:p@h:5432/db") == "postgresql://u:p@h:5432/db"
    sup = normalize_db_url("postgresql://postgres.abc:pw@aws-0-eu-central-1.pooler.supabase.com:5432/postgres")
    assert sup.endswith("?sslmode=require")
    assert normalize_db_url("postgresql://x@db.abc.supabase.co:5432/postgres?sslmode=verify-full").endswith(
        "sslmode=verify-full")                                   # an explicit choice is kept
    assert normalize_db_url("postgresql://x@db.abc.supabase.co/postgres?application_name=a").endswith(
        "&sslmode=require")
    assert normalize_db_url("postgresql://u@railway.internal/db") == "postgresql://u@railway.internal/db"
    assert normalize_db_url("  ") == ""


def _full(i):
    return {"summary": {"momentum": 60 + i, "price": 1.0}, "risk_flags": ["x"], "risk_hard_fails": [],
            "trend": {"big": "x" * 5000}, "market_intel": {"big": "y" * 5000}, "sources": {"market": "dex"}}


def test_old_snapshots_are_trimmed_to_their_summary():
    now = int(time.time())
    with SessionLocal() as db:
        try:
            t = Token(address=f"SlimMint{now}", chain="solana", symbol="SLIM")
            db.add(t); db.flush()
            old = TrendScore(token_id=t.id, ts=now - 3 * 3600, kind="live", components_json=_full(1))
            new = TrendScore(token_id=t.id, ts=now - 600, kind="live", components_json=_full(2))
            db.add_all([old, new]); db.flush()
            ingestion._SLIM_WATERMARK.clear()
            n = ingestion.slim_old_scores(db, now, after_h=2)
            assert n >= 1
            db.expire_all()
            o = db.get(TrendScore, old.id).components_json
            assert set(o) == {"summary", "risk_flags", "risk_hard_fails", "slim"}
            assert o["summary"] == {"momentum": 61, "price": 1.0} and o["risk_flags"] == ["x"]
            assert "trend" in db.get(TrendScore, new.id).components_json      # the latest keeps everything
            assert ingestion.slim_old_scores(db, now, after_h=2) == 0           # watermark: once per row
            assert ingestion.slim_old_scores(db, now, after_h=0) == 0           # 0 = off
        finally:
            db.rollback()
            ingestion._SLIM_WATERMARK.clear()


def test_health_endpoint():
    r = client.get("/health")
    assert r.status_code == 200
    d = r.json()
    assert d["ok"] is True and d["db"] == "sqlite" and "last_score_age_s" in d
    assert client.get("/").json()["ok"] is True                              # cheap keep-awake target


def test_keep_awake_is_off_without_a_public_url(monkeypatch):
    monkeypatch.setattr(main, "KEEP_AWAKE_URL", "")
    assert asyncio.run(asyncio.wait_for(main.keep_awake_loop(), 1)) is None
    monkeypatch.setattr(main, "KEEP_AWAKE_URL", "https://example.invalid")
    monkeypatch.setattr(main, "KEEP_AWAKE", False)
    assert asyncio.run(asyncio.wait_for(main.keep_awake_loop(), 1)) is None


def test_render_defaults():
    assert config.SNAPSHOT_SLIM_AFTER_H == 2 and config.DB_POOL_SIZE == 5
