"""Central configuration. All secrets/env-driven values live here; nothing
secrets-bearing is ever sent to the frontend (§22)."""
import os

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _load_dotenv(path: str) -> None:
    """Tiny .env loader (no extra dependency). Real environment variables win."""
    try:
        with open(path, encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                k, v = line.split("=", 1)
                v = v.strip().strip('"').strip("'")
                if v:                      # blank value (e.g. DATABASE_URL=) = use the default
                    os.environ.setdefault(k.strip(), v)
    except FileNotFoundError:
        pass


_load_dotenv(os.path.join(BASE_DIR, ".env"))

_db = os.getenv("DATABASE_URL", "").strip()
if _db.startswith("postgres://"):                 # some hosts still hand out the old scheme
    _db = "postgresql://" + _db[len("postgres://"):]
if not _db:
    if os.getenv("RAILWAY_ENVIRONMENT") or os.getenv("RAILWAY_PROJECT_ID"):
        # Fail loudly: silently using a SQLite file inside the container would
        # lose all data on every redeploy.
        raise RuntimeError(
            "DATABASE_URL is empty on Railway. In the API service > Variables, set it "
            "with 'Add Reference' to the Postgres service's DATABASE_URL.")
    _db = f"sqlite:///{os.path.join(BASE_DIR, 'radar.db')}"
DATABASE_URL = _db

# Ingestion
INGEST_INTERVAL_SECONDS = int(os.getenv("INGEST_INTERVAL_SECONDS", "60"))
SNAPSHOT_BUCKET_SECONDS = 300            # §33 event/time-series grid
BACKFILL_HOURS = 48
MAX_TRACKED_TOKENS = int(os.getenv("MAX_TRACKED_TOKENS", "40"))

# Universe filter: this is a MEME-COIN radar, so drop stablecoins, wrapped majors
# and anything already too big / too illiquid to be an early-stage meme coin.
MAX_MCAP_USD = float(os.getenv("MAX_MCAP_USD", "100000000"))      # ceiling (default $100M)
MIN_LIQUIDITY_USD = float(os.getenv("MIN_LIQUIDITY_USD", "3000"))  # floor (default $3k)

# Providers
DEXSCREENER_ENABLED = os.getenv("DEXSCREENER_ENABLED", "1") == "1"
DEXSCREENER_BASE = "https://api.dexscreener.com"
MOCK_SOCIAL_ENABLED = os.getenv("MOCK_SOCIAL_ENABLED", "1") == "1"

# Solana-only research scope (HARD CONSTRAINT for the MVP). Set SOLANA_ONLY=0 to
# re-enable Ethereum/Base/BNB discovery.
SOLANA_ONLY = os.getenv("SOLANA_ONLY", "1") == "1"

# Helius (real on-chain risk checks). Disabled automatically when no key is set.
# Quota guard: at most HELIUS_MAX_CHECKS_PER_TICK tokens per ingest tick, and a
# token is re-checked at most every HELIUS_RECHECK_HOURS hours. Each check is
# 2 RPC calls.
HELIUS_API_KEY = os.getenv("HELIUS_API_KEY", "").strip()
HELIUS_RPC_URL = os.getenv(
    "HELIUS_RPC_URL", "https://mainnet.helius-rpc.com/?api-key={key}")
HELIUS_MAX_CHECKS_PER_TICK = int(os.getenv("HELIUS_MAX_CHECKS_PER_TICK", "3"))
HELIUS_RECHECK_HOURS = float(os.getenv("HELIUS_RECHECK_HOURS", "6"))
# Real holder counts use the DAS getTokenAccounts method (1000 accounts per page).
# Count at most this many pages per token (5 => "5,000+" for very large tokens).
HELIUS_HOLDER_MAX_PAGES = int(os.getenv("HELIUS_HOLDER_MAX_PAGES", "5"))

# Security
DEMO_MODE = os.getenv("DEMO_MODE", "1") == "1"
JWT_SECRET = os.getenv("JWT_SECRET", "dev-only-insecure-secret-change-me")
JWT_TTL_HOURS = 24
RATE_LIMIT_PER_MIN = int(os.getenv("RATE_LIMIT_PER_MIN", "240"))

# Engine thresholds (centralised so they can be tuned/backtested — §34)
EMERGING_MIN_TREND = 42
# A 1h-vs-previous-1h comparison needs 2h of recorded data. Real tokens are no
# longer backfilled with simulated history, so "emerging" waits for this much.
MIN_HISTORY_HOURS = float(os.getenv("MIN_HISTORY_HOURS", "2"))
EMERGING_MIN_FOMO = 38
EMERGING_MIN_SOCIAL_G = 70.0    # % 1h
EMERGING_MIN_VOLUME_G = 60.0    # % 1h
ANOMALY_Z = 3.0
ALERT_COOLDOWN_SECONDS = 1800

# Telegram delivery (server-side only; never sent to the frontend). Set both in
# Railway → Variables. The bot must be an admin of the channel / member of the chat.
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "").strip()
TELEGRAM_MAX_PER_TICK = int(os.getenv("TELEGRAM_MAX_PER_TICK", "8"))   # flood guard
