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
from .database import Base, SessionLocal, engine
from .routers import data_api, user_api
from .security import hash_password
from .services import ingestion

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("main")


def _ensure_column(table: str, column: str, ddl: str):
    """Idempotent column add for live SQLite DBs (dev migrations)."""
    from sqlalchemy import text
    with engine.begin() as conn:
        cols = {r[1] for r in conn.execute(text(f"PRAGMA table_info({table})"))}
        if column not in cols:
            conn.execute(text(f"ALTER TABLE {table} ADD COLUMN {ddl}"))
            log.info("migrated: %s.%s added", table, column)


def ensure_schema_and_demo_user():
    from .models import User
    Base.metadata.create_all(engine)
    try:
        _ensure_column("trend_scores", "kind", "kind VARCHAR(8) DEFAULT 'live'")
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

# CORS is intentionally narrow: in this deployment the Next server proxies
# same-origin requests; direct browser access to the API is not required.
app.add_middleware(CORSMiddleware, allow_origins=["http://localhost:3000",
                                                  "http://127.0.0.1:3000"],
                   allow_credentials=False, allow_methods=["GET", "POST", "PATCH", "DELETE"],
                   allow_headers=["Authorization", "Content-Type"])
app.add_middleware(SecurityHeadersMiddleware)

app.include_router(data_api.router, prefix="/api", tags=["data"])
app.include_router(user_api.router, prefix="/api", tags=["user"])


@app.get("/")
def root():
    return {"ok": True, "service": "memecoin-radar-api", "docs": "/docs"}
