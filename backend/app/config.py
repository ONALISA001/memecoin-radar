"""Central configuration. All secrets/env-driven values live here; nothing
secrets-bearing is ever sent to the frontend (§22)."""
import os

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

DATABASE_URL = os.getenv("DATABASE_URL", f"sqlite:///{os.path.join(BASE_DIR, 'radar.db')}")

# Ingestion
INGEST_INTERVAL_SECONDS = int(os.getenv("INGEST_INTERVAL_SECONDS", "60"))
SNAPSHOT_BUCKET_SECONDS = 300            # §33 event/time-series grid
BACKFILL_HOURS = 48
MAX_TRACKED_TOKENS = int(os.getenv("MAX_TRACKED_TOKENS", "40"))

# Providers
DEXSCREENER_ENABLED = os.getenv("DEXSCREENER_ENABLED", "1") == "1"
DEXSCREENER_BASE = "https://api.dexscreener.com"
MOCK_SOCIAL_ENABLED = os.getenv("MOCK_SOCIAL_ENABLED", "1") == "1"

# Security
DEMO_MODE = os.getenv("DEMO_MODE", "1") == "1"
JWT_SECRET = os.getenv("JWT_SECRET", "dev-only-insecure-secret-change-me")
JWT_TTL_HOURS = 24
RATE_LIMIT_PER_MIN = int(os.getenv("RATE_LIMIT_PER_MIN", "240"))

# Engine thresholds (centralised so they can be tuned/backtested — §34)
EMERGING_MIN_TREND = 42
EMERGING_MIN_FOMO = 38
EMERGING_MIN_SOCIAL_G = 70.0    # % 1h
EMERGING_MIN_VOLUME_G = 60.0    # % 1h
ANOMALY_Z = 3.0
ALERT_COOLDOWN_SECONDS = 1800
