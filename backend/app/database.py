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


# Columns added after their table first shipped: {table: [(name, postgres type, sqlite type)]}.
_ADDED_COLUMNS = {
    "verdict_outcomes": [("ret_12h", "DOUBLE PRECISION", "FLOAT"), ("judge_h", "INTEGER", "INTEGER"),
                         ("ret_judge", "DOUBLE PRECISION", "FLOAT"), ("max_up_judge", "DOUBLE PRECISION", "FLOAT"),
                         ("max_dd_judge", "DOUBLE PRECISION", "FLOAT"),
                         ("liq_change_judge", "DOUBLE PRECISION", "FLOAT")],
    "token_risk_checks": [("top1_wallet_pct", "DOUBLE PRECISION", "FLOAT"),
                          ("top10_wallet_pct", "DOUBLE PRECISION", "FLOAT"),
                          ("excluded_pct", "DOUBLE PRECISION", "FLOAT"),
                          ("token_extensions", "VARCHAR(200)", "VARCHAR(200)"),
                          ("transfer_fee_bps", "INTEGER", "INTEGER"),
                          ("transfer_fee_mutable", "BOOLEAN", "BOOLEAN"),
                          ("exit_sol_in", "DOUBLE PRECISION", "FLOAT"),
                          ("exit_buy_route", "BOOLEAN", "BOOLEAN"),
                          ("exit_sell_route", "BOOLEAN", "BOOLEAN"),
                          ("exit_round_trip_loss_pct", "DOUBLE PRECISION", "FLOAT"),
                          ("exit_route", "VARCHAR(120)", "VARCHAR(120)"),
                          ("exit_error", "VARCHAR(200)", "VARCHAR(200)")],
    "demo_accounts": [("halt_until", "INTEGER", "INTEGER"),
                      ("halt_reason", "VARCHAR(16)", "VARCHAR(16)"),
                      ("strategy_key", "VARCHAR(32)", "VARCHAR(32)")],
    "tokens": [("discovery_source", "VARCHAR(16)", "VARCHAR(16)"),
               ("launch_alert", "VARCHAR(8)", "VARCHAR(8)")],
}


def migrate_postgres() -> None:
    """create_all() never alters tables that already exist. Fix columns that were
    created too narrow in earlier versions and add newer columns (idempotent).
    On SQLite only the added columns are handled."""
    from sqlalchemy import inspect, text
    if engine.dialect.name == "sqlite":
        insp = inspect(engine)
        with engine.begin() as conn:
            for table, cols in _ADDED_COLUMNS.items():
                if not insp.has_table(table):
                    continue
                have = {c["name"] for c in insp.get_columns(table)}
                for name, _, sqlite_type in cols:
                    if name not in have:
                        conn.execute(text(f"ALTER TABLE {table} ADD COLUMN {name} {sqlite_type}"))
        return
    if engine.dialect.name != "postgresql":
        return
    with engine.begin() as conn:
        # epoch-milliseconds (~1.7e12) overflows a 32-bit INTEGER on Postgres
        conn.execute(text("ALTER TABLE IF EXISTS tokens ALTER COLUMN token_created_at TYPE BIGINT"))
        # real holder counts (added after token_risk_checks first shipped)
        conn.execute(text("ALTER TABLE IF EXISTS token_risk_checks ADD COLUMN IF NOT EXISTS holders_count INTEGER"))
        conn.execute(text("ALTER TABLE IF EXISTS token_risk_checks ADD COLUMN IF NOT EXISTS holders_capped BOOLEAN DEFAULT FALSE"))
        for table, cols in _ADDED_COLUMNS.items():
            for name, pg_type, _ in cols:
                conn.execute(text(f"ALTER TABLE IF EXISTS {table} ADD COLUMN IF NOT EXISTS {name} {pg_type}"))


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
