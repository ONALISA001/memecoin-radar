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
MAX_TRACKED_TOKENS = int(os.getenv("MAX_TRACKED_TOKENS", "60"))

# Universe filter: this is a MEME-COIN radar, so drop stablecoins, wrapped majors
# and anything already too big / too illiquid to be an early-stage meme coin.
MAX_MCAP_USD = float(os.getenv("MAX_MCAP_USD", "100000000"))      # ceiling (default $100M)
MIN_LIQUIDITY_USD = float(os.getenv("MIN_LIQUIDITY_USD", "3000"))  # floor (default $3k)

# Providers
DEXSCREENER_ENABLED = os.getenv("DEXSCREENER_ENABLED", "1") == "1"
# When DexScreener is unreachable: 0 (default) = keep the last REAL data, mark it stale and
# pause scoring + paper trading until it's back. 1 = old behaviour: a simulated demo universe.
DEMO_FALLBACK = os.getenv("DEMO_FALLBACK", "0") == "1"
DEXSCREENER_BASE = "https://api.dexscreener.com"

# Real crypto news from publisher RSS feeds (free, no key). Replaces the simulated
# news provider: drives the FOMO news component, narrative news activity and the
# "news" confirmation check. Set NEWS_RSS_ENABLED=0 to fall back to mock news.
NEWS_RSS_ENABLED = os.getenv("NEWS_RSS_ENABLED", "1") == "1"
# There is no free real social (X/Twitter) source. With this OFF (default), real coins
# never use simulated social data: "attention" = unique buyers (GeckoTerminal) and the
# bot/wash measure = trades per unique trader. Influencers/engagement have no source → 0.
USE_SIMULATED_SOCIAL = os.getenv("USE_SIMULATED_SOCIAL", "0") == "1"
# REAL social: public Telegram call channels you choose (comma-separated usernames,
# e.g. "channelone,@channeltwo"). Read via the free t.me/s preview page; empty = off.
TELEGRAM_CALL_CHANNELS = os.getenv("TELEGRAM_CALL_CHANNELS", "")
TELEGRAM_REFRESH_MINUTES = float(os.getenv("TELEGRAM_REFRESH_MINUTES", "5"))
TELEGRAM_DISCOVERY_SLOTS = int(os.getenv("TELEGRAM_DISCOVERY_SLOTS", "10"))   # called coins added to tracking
NEWS_REFRESH_MINUTES = float(os.getenv("NEWS_REFRESH_MINUTES", "10"))

# Early discovery: freshly created Solana pools from GeckoTerminal (free, keyless).
# Up to NEW_POOL_SLOTS of MAX_TRACKED_TOKENS are reserved for them; a fresh token
# stays tracked for NEW_POOL_MAX_AGE_H after its pool was created. The floor keeps
# out the hundreds of dead launches per hour.
NEW_POOLS_ENABLED = os.getenv("NEW_POOLS_ENABLED", "1") == "1"
GECKOTERMINAL_BASE = "https://api.geckoterminal.com/api/v2"
NEW_POOL_PAGES = int(os.getenv("NEW_POOL_PAGES", "2"))              # 20 pools/page, 1 call each
NEW_POOL_SLOTS = int(os.getenv("NEW_POOL_SLOTS", "20"))
NEW_POOL_MAX_AGE_H = float(os.getenv("NEW_POOL_MAX_AGE_H", "6"))
NEW_POOL_MIN_LIQUIDITY_USD = float(os.getenv("NEW_POOL_MIN_LIQUIDITY_USD", "8000"))
NEW_POOL_MIN_TXNS_H1 = int(os.getenv("NEW_POOL_MIN_TXNS_H1", "30"))

# Launch alert: one Telegram message when a fresh-pool token's FIRST complete
# Helius check comes back clean (mint+freeze off, standard SPL, wallets spread).
LAUNCH_ALERTS_ENABLED = os.getenv("LAUNCH_ALERTS_ENABLED", "1") == "1"
LAUNCH_MAX_TOP10_WALLET_PCT = float(os.getenv("LAUNCH_MAX_TOP10_WALLET_PCT", "30"))
LAUNCH_MAX_TOP1_WALLET_PCT = float(os.getenv("LAUNCH_MAX_TOP1_WALLET_PCT", "10"))
MOCK_SOCIAL_ENABLED = os.getenv("MOCK_SOCIAL_ENABLED", "1") == "1"

# Solana-only research scope (HARD CONSTRAINT for the MVP). Set SOLANA_ONLY=0 to
# re-enable Ethereum/Base/BNB discovery.
SOLANA_ONLY = os.getenv("SOLANA_ONLY", "1") == "1"

# Helius (real on-chain risk checks). Disabled automatically when no key is set.
# Quota guard: at most HELIUS_MAX_CHECKS_PER_TICK tokens per ingest tick, and a
# token is re-checked at most every HELIUS_RECHECK_HOURS hours. Each check is 3
# RPC calls (mint info, largest accounts, their owners) plus holder-count pages.
HELIUS_API_KEY = os.getenv("HELIUS_API_KEY", "").strip()
HELIUS_RPC_URL = os.getenv(
    "HELIUS_RPC_URL", "https://mainnet.helius-rpc.com/?api-key={key}")
HELIUS_MAX_CHECKS_PER_TICK = int(os.getenv("HELIUS_MAX_CHECKS_PER_TICK", "3"))
HELIUS_RECHECK_HOURS = float(os.getenv("HELIUS_RECHECK_HOURS", "6"))
# Real holder counts use the DAS getTokenAccounts method (1000 accounts per page).
# Count at most this many pages per token (5 => "5,000+" for very large tokens).
HELIUS_HOLDER_MAX_PAGES = int(os.getenv("HELIUS_HOLDER_MAX_PAGES", "5"))

# Exit analyst (Jupiter round-trip quote: buy EXIT_CHECK_SOL of the token, quote selling
# it straight back). Two quote calls per checked token, no wallet, no transaction.
# Without JUPITER_API_KEY the keyless lite endpoint is used. Set JUPITER_QUOTE_URL=""
# to switch the check off; point it elsewhere if Jupiter moves the endpoint.
JUPITER_API_KEY = os.getenv("JUPITER_API_KEY", "").strip()
JUPITER_QUOTE_URL = os.getenv(
    "JUPITER_QUOTE_URL",
    "https://api.jup.ag/swap/v1/quote" if JUPITER_API_KEY else "https://lite-api.jup.ag/swap/v1/quote").strip()
EXIT_CHECK_SOL = float(os.getenv("EXIT_CHECK_SOL", "0.1"))
# Launch forensics (bundles, snipers, shared funders, creator-funded buyers). Runs ONCE per
# token (launch facts never change), only after its Helius check, and only for tokens that
# did not already fail a hard safety check. Free plan: ~50-80 credits per token.
LAUNCH_FORENSICS_ENABLED = os.getenv("LAUNCH_FORENSICS_ENABLED", "1") == "1"
LAUNCH_FORENSICS_PER_TICK = int(os.getenv("LAUNCH_FORENSICS_PER_TICK", "2"))
LAUNCH_FORENSICS_MAX_AGE_H = float(os.getenv("LAUNCH_FORENSICS_MAX_AGE_H", "72"))  # older coins skipped
LAUNCH_EARLY_TX = int(os.getenv("LAUNCH_EARLY_TX", "40"))            # oldest transactions read
LAUNCH_EARLY_BUYERS = int(os.getenv("LAUNCH_EARLY_BUYERS", "20"))
LAUNCH_SNIPER_SLOTS = int(os.getenv("LAUNCH_SNIPER_SLOTS", "3"))      # ~1.2 s after creation
LAUNCH_FUNDER_CHECKS = int(os.getenv("LAUNCH_FUNDER_CHECKS", "5"))    # biggest early buyers traced
LAUNCH_MAX_SIG_PAGES = int(os.getenv("LAUNCH_MAX_SIG_PAGES", "15"))   # 1000 signatures per page
LAUNCH_USE_GTFA = os.getenv("LAUNCH_USE_GTFA", "1") == "1"           # try the Developer-plan fast path
# Fresh-launch alert thresholds from launch forensics
LAUNCH_MAX_BUNDLE_PCT = float(os.getenv("LAUNCH_MAX_BUNDLE_PCT", "15"))
LAUNCH_MAX_SNIPER_PCT = float(os.getenv("LAUNCH_MAX_SNIPER_PCT", "25"))
# AI verdict (bull / bear / judge). Without LLM_API_KEY a rule-based verdict is used.
# LLM_PROVIDER=anthropic (default) or openai (any OpenAI-compatible endpoint via LLM_BASE_URL).
LLM_PROVIDER = os.getenv("LLM_PROVIDER", "anthropic").strip().lower()
LLM_API_KEY = os.getenv("LLM_API_KEY", "").strip()
LLM_BASE_URL = os.getenv("LLM_BASE_URL", "").strip()
_default_model = "claude-haiku-5-5" if LLM_PROVIDER == "anthropic" else ""
LLM_QUICK_MODEL = os.getenv("LLM_QUICK_MODEL", _default_model).strip()   # bull + bear
LLM_JUDGE_MODEL = os.getenv("LLM_JUDGE_MODEL", _default_model).strip()   # judge
VERDICT_ENABLED = os.getenv("VERDICT_ENABLED", "1") == "1"
VERDICT_LLM_PER_TICK = int(os.getenv("VERDICT_LLM_PER_TICK", "1"))       # 3 LLM calls each
VERDICT_RULES_PER_TICK = int(os.getenv("VERDICT_RULES_PER_TICK", "10"))   # free (no LLM)
VERDICT_DAILY_LLM_MAX = int(os.getenv("VERDICT_DAILY_LLM_MAX", "40"))     # AI verdicts per 24h
VERDICT_REFRESH_HOURS = float(os.getenv("VERDICT_REFRESH_HOURS", "6"))
VERDICT_MIN_GAP_MIN = float(os.getenv("VERDICT_MIN_GAP_MIN", "30"))       # re-judge on change, not sooner
VERDICT_TELEGRAM = os.getenv("VERDICT_TELEGRAM", "1") == "1"   # follow-up for launch-alerted coins
# Outcome tracker: score every verdict at 1h / 6h / 24h and keep a scorecard
OUTCOMES_ENABLED = os.getenv("OUTCOMES_ENABLED", "1") == "1"
OUTCOME_JUDGE_HOURS = int(os.getenv("OUTCOME_JUDGE_HOURS", "12"))     # calls judged this long after
VERDICT_TRACK_RECORD = os.getenv("VERDICT_TRACK_RECORD", "1") == "1"   # show the judge its record
SCORECARD_TELEGRAM = os.getenv("SCORECARD_TELEGRAM", "1") == "1"       # daily summary
SCORECARD_HOUR_UTC = int(os.getenv("SCORECARD_HOUR_UTC", "6"))         # 06 UTC = 09:00 Nairobi
# Fresh-launch alerts: allow Token-2022 tokens whose extensions were read and are all harmless
LAUNCH_ALLOW_CLEAN_TOKEN_2022 = os.getenv("LAUNCH_ALLOW_CLEAN_TOKEN_2022", "1") == "1"

# Security
# DEMO_MODE=1: anyone gets a READ-ONLY visitor token (browse the dashboards).
# Changing anything (alerts, watchlist, paper trader, admin jobs) needs an owner
# token, which /api/auth/login issues only for the right OWNER_KEY.
DEMO_MODE = os.getenv("DEMO_MODE", "1") == "1"
OWNER_KEY = os.getenv("OWNER_KEY", "").strip()
_INSECURE_JWT_DEFAULT = "dev-only-insecure-secret-change-me"
_jwt = os.getenv("JWT_SECRET", "").strip()
JWT_SECRET_IS_EPHEMERAL = not _jwt or _jwt == _INSECURE_JWT_DEFAULT
if JWT_SECRET_IS_EPHEMERAL:
    # Never sign tokens with a publicly known secret. A random per-process secret
    # is safe; the only cost is that logins reset when the server restarts.
    import secrets as _secrets
    _jwt = _secrets.token_urlsafe(48)
JWT_SECRET = _jwt
LOGIN_ATTEMPTS_PER_MIN = int(os.getenv("LOGIN_ATTEMPTS_PER_MIN", "5"))
# Browsers reach the API through the Netlify /api proxy (same origin), so no
# cross-origin access is needed. List extra origins comma-separated if ever required.
CORS_ORIGINS = [o.strip() for o in os.getenv("CORS_ORIGINS", "").split(",") if o.strip()]
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
# High-severity risk events → Telegram, each with a short AI read (services/risk_alerts.py)
RISK_ALERT_TELEGRAM = os.getenv("RISK_ALERT_TELEGRAM", "1") == "1"
RISK_ALERT_AI = os.getenv("RISK_ALERT_AI", "1") == "1"                       # needs LLM_API_KEY
RISK_ALERT_AI_PER_TICK = int(os.getenv("RISK_ALERT_AI_PER_TICK", "3"))
RISK_ALERT_AI_DAILY_MAX = int(os.getenv("RISK_ALERT_AI_DAILY_MAX", "40"))
RISK_ALERT_MAX_AGE_MIN = float(os.getenv("RISK_ALERT_MAX_AGE_MIN", "30"))   # never send stale events
