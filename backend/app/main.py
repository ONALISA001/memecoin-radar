"""FastAPI application entry point.

- Serves the REST API on :8000 (bind 0.0.0.0 — the Next.js dev server proxies
  /api/* here server-side; the browser never reaches this process directly).
- Runs the ingestion scheduler from FastAPI lifespan (production: lift tick()
  into a Celery beat worker — see docs/ARCHITECTURE.md).
"""
from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from starlette.middleware.base import BaseHTTPMiddleware

from .config import INGEST_INTERVAL_SECONDS
from .database import Base, SessionLocal, engine, migrate_postgres
from .routers import data_api, paper_api, user_api
from .security import hash_password
from .services import ingestion

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("main")


def _ensure_column(table: str, column: str, ddl: str):
    """Idempotent column add for live SQLite DBs (dev migrations)."""
    from sqlalchemy import text
    if engine.dialect.name != "sqlite":      # PRAGMA is SQLite-only; Postgres tables are created complete
        return
    with engine.begin() as conn:
        cols = {r[1] for r in conn.execute(text(f"PRAGMA table_info({table})"))}
        if column not in cols:
            conn.execute(text(f"ALTER TABLE {table} ADD COLUMN {ddl}"))
            log.info("migrated: %s.%s added", table, column)


def ensure_schema_and_demo_user():
    from .models import User
    Base.metadata.create_all(engine)
    migrate_postgres()
    try:
        _ensure_column("trend_scores", "kind", "kind VARCHAR(8) DEFAULT 'live'")
        # v3 intelligence events (additive, idempotent)
        for col, ddl in [
            ("category", "category VARCHAR(16)"), ("importance", "importance VARCHAR(8)"),
            ("importance_score", "importance_score INTEGER DEFAULT 0"),
            ("confidence", "confidence INTEGER DEFAULT 0"),
            ("signal_text", "signal_text VARCHAR(300)"), ("why_json", "why_json JSON"),
            ("concern", "concern VARCHAR(300)"), ("chain_json", "chain_json JSON"),
            ("checklist_json", "checklist_json JSON"),
        ]:
            _ensure_column("signal_events", col, ddl)
        _ensure_column("tokens", "new_token_evented", "new_token_evented BOOLEAN DEFAULT 0")
        _ensure_column("token_risk_checks", "holders_count", "holders_count INTEGER")
        _ensure_column("token_risk_checks", "holders_capped", "holders_capped BOOLEAN DEFAULT 0")
    except Exception as exc:
        log.warning("column migration failed (non-fatal): %s", exc)
    with SessionLocal() as db:
        from sqlalchemy import select
        exists = db.execute(select(User).where(User.email == "demo@memecoin.radar")).scalar_one_or_none()
        if not exists:
            db.add(User(email="demo@memecoin.radar",
                        password_hash=hash_password("radar-demo-2026"),
                        api_token="demo"))
            db.commit()
            log.info("demo user created")


async def scheduler_loop():
    # first tick happens immediately so fresh data appears right after boot
    from .services.backfill_scores import run_retro_backfill
    ticks = 0
    while True:
        try:
            stats = await ingestion.tick()
            log.info("tick: %s", stats)
            ticks += 1
            if ticks == 2:
                # bootstrap the calibration window once, off the hot path
                try:
                    res = await asyncio.to_thread(run_retro_backfill)
                    log.info("retro backfill: %s", res)
                except Exception:
                    log.exception("retro backfill failed")
        except Exception:
            log.exception("tick crashed")
        await asyncio.sleep(INGEST_INTERVAL_SECONDS)


@asynccontextmanager
async def lifespan(app: FastAPI):
    ensure_schema_and_demo_user()
    task = asyncio.create_task(scheduler_loop())
    yield
    task.cancel()


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    """§22 secure headers on every response."""
    async def dispatch(self, request, call_next):
        resp = await call_next(request)
        resp.headers["X-Content-Type-Options"] = "nosniff"
        resp.headers["Referrer-Policy"] = "no-referrer"
        resp.headers["X-Frame-Options"] = "DENY"
        resp.headers["Cache-Control"] = "no-store"
        return resp


app = FastAPI(title="MemeCoin Radar API", version="0.1.0", lifespan=lifespan,
              docs_url="/docs" if True else None)

# CORS: read-only analytics API, no cookies/credentialed requests — widened so a
# static frontend hosted anywhere (Netlify Drop, Vercel) can call it cross-origin.
# No secrets ever leave through responses (§22); a browser app only sends a JWT
# it already holds.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)
app.add_middleware(SecurityHeadersMiddleware)

app.include_router(data_api.router, prefix="/api", tags=["data"])
app.include_router(user_api.router, prefix="/api", tags=["user"])
app.include_router(paper_api.router, prefix="/api", tags=["paper"])


@app.get("/")
def root():
    return {"ok": True, "service": "memecoin-radar-api", "docs": "/docs"}
