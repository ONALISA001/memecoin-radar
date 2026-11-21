"""Central configuration. All secrets/env-driven values live here; nothing
secrets-bearing is ever sent to the frontend (§22)."""
import os
import re

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

ON_RENDER = bool(os.getenv("RENDER") or os.getenv("RENDER_SERVICE_ID"))
ON_RAILWAY = bool(os.getenv("RAILWAY_ENVIRONMENT") or os.getenv("RAILWAY_PROJECT_ID"))


class DatabaseUrlError(RuntimeError):
    """A DATABASE_URL problem, explained without ever showing the password."""


_QUOTES = "\"'`"


def mask_db_url(url: str) -> str:
    """The URL with its password replaced by *** (safe for logs and error messages)."""
    return re.sub(r"(://[^:/@\s]+:)[^@\s]*@", r"\1***@", url or "")


def normalize_db_url(url: str) -> str:
    """Accept the URLs hosts hand out (and common copy-paste slips) and make them work
    with SQLAlchemy + psycopg2.

    * A whole pasted line such as  DIRECT_URL="postgresql://…"  or  export DATABASE_URL='…'
      → just the URL; surrounding quotes and spaces are dropped.
    * postgres://  → postgresql://   (old scheme still given by some hosts)
    * A password left inside the placeholder's brackets, [secret] → secret.
    * Supabase requires TLS: add sslmode=require when the URL doesn't say otherwise.
    """
    url = (url or "").strip().strip(_QUOTES).strip()
    m = re.match(r"^(?:export\s+)?[A-Za-z_][A-Za-z0-9_]*\s*=\s*(.+)$", url)
    if m and "://" not in url.split("=", 1)[0]:
        url = m.group(1).strip().strip(_QUOTES).strip()
    if url.startswith("postgres://"):
        url = "postgresql://" + url[len("postgres://"):]
    bracketed = re.match(r"^(\w[\w+.-]*://[^:/@\s]+:)\[([^\]@\s]+)\](@.+)$", url)
    if bracketed and bracketed.group(2).upper() not in ("YOUR-PASSWORD", "YOUR_PASSWORD", "PASSWORD"):
        url = bracketed.group(1) + bracketed.group(2) + bracketed.group(3)
    if "supabase.co" in url or "supabase.com" in url:
        if "sslmode=" not in url:
            url += ("&" if "?" in url else "?") + "sslmode=require"
    return url


def check_db_url(url: str) -> None:
    """Fail with a clear, password-free message instead of a parser traceback that would
    print the whole connection string (password included) into the host's logs."""
    if not url or url.startswith("sqlite"):
        return
    safe = mask_db_url(url)
    if re.search(r"YOUR[-_]PASSWORD", url, re.IGNORECASE):
        raise DatabaseUrlError(
            "DATABASE_URL still contains the [YOUR-PASSWORD] placeholder: replace it (brackets "
            f"included) with your database password. Value given: {safe}")
    if not url.startswith(("postgresql://", "postgresql+")):
        raise DatabaseUrlError(
            "DATABASE_URL must start with postgresql:// — paste only the connection string, "
            f"without a name or quotes in front. Value given starts with: {safe[:24]!r}")
    try:
        from sqlalchemy.engine import make_url
        u = make_url(url)
    except Exception:  # noqa: BLE001  the parser's own message would contain the password
        raise DatabaseUrlError(
            "DATABASE_URL could not be read. If your password contains @ : / ? # % or spaces, "
            f"reset it to letters and digits only (or URL-encode it). Value given: {safe}") from None
    if not u.host:
        raise DatabaseUrlError(f"DATABASE_URL has no host name. Value given: {safe}")


_db = normalize_db_url(os.getenv("DATABASE_URL", ""))
if not _db:
    if ON_RAILWAY or ON_RENDER:
        # Fail loudly: silently using a SQLite file inside the container would
        # lose all data on every redeploy.
        raise DatabaseUrlError(
            "DATABASE_URL is empty. Set it in the host's environment variables to your Postgres "
            "connection string (Supabase: Connect > Connection String > 'Session pooler').")
    _db = f"sqlite:///{os.path.join(BASE_DIR, 'radar.db')}"
check_db_url(_db)
DATABASE_URL = _db
DATABASE_URL_SAFE = mask_db_url(_db)      # for logs
# Connection pool (Postgres). Supabase's free tier allows a limited number of connections,
# shared with its own services, so keep this small; one API process needs only a few.
DB_POOL_SIZE = int(os.getenv("DB_POOL_SIZE", "5"))
DB_MAX_OVERFLOW = int(os.getenv("DB_MAX_OVERFLOW", "5"))
# Free database plans are small (Supabase: 500 MB). Score snapshots older than this many hours
# keep only their summary (what charts and backtests read); the latest keeps everything.
SNAPSHOT_SLIM_AFTER_H = float(os.getenv("SNAPSHOT_SLIM_AFTER_H", "2"))
# Free web services sleep without inbound traffic (Render: 15 min), which would stop the
# ingestion loop. The app pings its own public URL every KEEP_AWAKE_MINUTES. Render sets
# RENDER_EXTERNAL_URL automatically; KEEP_AWAKE_URL overrides it; KEEP_AWAKE=0 turns it off.
KEEP_AWAKE = os.getenv("KEEP_AWAKE", "1") == "1"
KEEP_AWAKE_URL = (os.getenv("KEEP_AWAKE_URL") or os.getenv("RENDER_EXTERNAL_URL") or "").strip().rstrip("/")
KEEP_AWAKE_MINUTES = float(os.getenv("KEEP_AWAKE_MINUTES", "10"))

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

# Launch radar: a live websocket of pump.fun coin creations and graduations, so new coins
# show up seconds after they are created (not minutes later once they reach a DEX).
# Market numbers come from DexScreener (batches of 30), candles from GeckoTerminal.
LAUNCH_STREAM_ENABLED = os.getenv("LAUNCH_STREAM_ENABLED", "1") == "1"
LAUNCH_STREAM_URL = os.getenv("LAUNCH_STREAM_URL", "wss://pumpportal.fun/api/data")
LAUNCH_BOOK_MAX = int(os.getenv("LAUNCH_BOOK_MAX", "3000"))          # recent launches kept in memory
LAUNCH_ENRICH_SECONDS = float(os.getenv("LAUNCH_ENRICH_SECONDS", "20"))
LAUNCH_ENRICH_BATCHES = int(os.getenv("LAUNCH_ENRICH_BATCHES", "3"))  # x30 coins per refresh
LAUNCH_TRADE_SUBS = int(os.getenv("LAUNCH_TRADE_SUBS", "60"))        # coins with every trade streamed
LAUNCH_HOT_SLOTS = int(os.getenv("LAUNCH_HOT_SLOTS", "10"))          # hot launches fed to full scoring
# Coins still on the bonding curve have no DEX pool, so their liquidity is counted as 2x the real SOL
# in the curve (what a balanced pool with that SOL holds). A full curve is only ~85 SOL (~$12K at
# $70 SOL), so curve coins get this smaller floor for tracking and the Early tab. Passing Priority
# coins still need $15K, which in practice means graduating first.
LAUNCH_CURVE_MIN_LIQ_USD = float(os.getenv("LAUNCH_CURVE_MIN_LIQ_USD", "3000"))

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
# Strategy supervisor: reviews the Strategy Lab and PROPOSES changes (owner approves each one)
SUPERVISOR_ENABLED = os.getenv("SUPERVISOR_ENABLED", "1") == "1"
SUPERVISOR_INTERVAL_H = float(os.getenv("SUPERVISOR_INTERVAL_H", "6"))
SCORECARD_TELEGRAM = os.getenv("SCORECARD_TELEGRAM", "1") == "1"       # daily summary
SCORECARD_HOUR_UTC = int(os.getenv("SCORECARD_HOUR_UTC", "6"))         # 06 UTC = 09:00 Nairobi
# Creator track record (earlier launches by a coin's creator; model from GMGN dev-score)
CREATOR_SCORE_ENABLED = os.getenv("CREATOR_SCORE_ENABLED", "1") == "1"
CREATOR_PER_TICK = int(os.getenv("CREATOR_PER_TICK", "1"))
CREATOR_DAILY_MAX = int(os.getenv("CREATOR_DAILY_MAX", "40"))         # creators scored per 24 h
CREATOR_TTL_H = float(os.getenv("CREATOR_TTL_H", "24"))               # re-score a creator after
CREATOR_TX_SCAN = int(os.getenv("CREATOR_TX_SCAN", "300"))            # Developer-plan fast path
CREATOR_RPC_TX = int(os.getenv("CREATOR_RPC_TX", "100"))              # free-plan path: 1 call per tx
CREATOR_USE_GTFA = os.getenv("CREATOR_USE_GTFA", "1") == "1"
CREATOR_PEAK_LOOKUPS = int(os.getenv("CREATOR_PEAK_LOOKUPS", "4"))    # GeckoTerminal calls per creator
# Fresh-launch alerts: allow Token-2022 tokens whose extensions were read and are all harmless
LAUNCH_ALLOW_CLEAN_TOKEN_2022 = os.getenv("LAUNCH_ALLOW_CLEAN_TOKEN_2022", "1") == "1"

# Security
# DEMO_MODE=1: anyone gets a READ-ONLY visitor token (browse the dashboards).
# Changing anything (alerts, watchlist, paper trader, admin jobs) needs an owner
# token, which /api/auth/login issues only for the right OWNER_KEY.
DEMO_MODE = os.getenv("DEMO_MODE", "1") == "1"
# Everyday actions (save paper settings, watchlist, alerts, close/resume trades, start the lab) are
# open to every visitor. Set OPEN_BASIC_ACTIONS=0 to make them owner-only again.
OPEN_BASIC_ACTIONS = os.getenv("OPEN_BASIC_ACTIONS", "1") == "1"
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
JWT_TTL_HOURS = max(1, int(os.getenv("JWT_TTL_HOURS", "24")))   # login lifetime; raise (e.g. 168) to log in weekly
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
