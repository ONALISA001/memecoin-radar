"""Engine/session setup. SQLite for dev by default; set DATABASE_URL to a
PostgreSQL DSN for production (schema is portable — no SQLite-specific SQL).
WAL mode keeps reads non-blocking while the ingestion loop writes."""
from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker, declarative_base

from .config import DATABASE_URL

connect_args = {"check_same_thread": False} if DATABASE_URL.startswith("sqlite") else {}
engine = create_engine(DATABASE_URL, connect_args=connect_args, pool_pre_ping=True)

if DATABASE_URL.startswith("sqlite"):
    @event.listens_for(engine, "connect")
    def _sqlite_pragmas(dbapi_conn, _):
        cur = dbapi_conn.cursor()
        cur.execute("PRAGMA journal_mode=WAL")
        cur.execute("PRAGMA synchronous=NORMAL")
        cur.execute("PRAGMA foreign_keys=ON")
        cur.close()

SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
Base = declarative_base()


def migrate_postgres() -> None:
    """create_all() never alters tables that already exist. Fix columns that were
    created too narrow in earlier versions (idempotent; no-op on SQLite)."""
    if engine.dialect.name != "postgresql":
        return
    from sqlalchemy import text
    with engine.begin() as conn:
        # epoch-milliseconds (~1.7e12) overflows a 32-bit INTEGER on Postgres
        conn.execute(text("ALTER TABLE IF EXISTS tokens ALTER COLUMN token_created_at TYPE BIGINT"))
        # real holder counts (added after token_risk_checks first shipped)
        conn.execute(text("ALTER TABLE IF EXISTS token_risk_checks ADD COLUMN IF NOT EXISTS holders_count INTEGER"))
        conn.execute(text("ALTER TABLE IF EXISTS token_risk_checks ADD COLUMN IF NOT EXISTS holders_capped BOOLEAN DEFAULT FALSE"))


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
