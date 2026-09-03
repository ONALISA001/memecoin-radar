# 🛰️ MemeCoin Radar

**AI-powered meme-coin trend intelligence & risk analysis platform.**
Not a prediction machine. Not financial advice. An analytics terminal that turns chaotic
social / market / on-chain / news data into *explainable* signals so you can do your own research.

---

## What it does (v2 — Memecoin Market Intelligence)

**Engines (all explainable, all from stored 5-min snapshots):** multi-factor
Momentum 0–100 (§4–5 bands + "why this score" factors), Sell-Pressure 0–100
(accumulation→heavy distribution, §6), Liquidity Health (§10), ATH/price-position
status (§7), Volume Acceleration 5m–4h + price↑vol↓ divergence (§12), whale
accumulation/distribution — explicitly inferred (§8), momentum reversals &
divergence detection (§18), and signal confluence composites (§13); plus the v1
Trend/FOMO/Risk scores, organic-vs-manufactured forensics, regimes and CUSUM
change-points, and forward-outcome calibration/backtesting (§31).

**Product surfaces:** Dashboard (overview + trending + live intelligence feed +
distribution watch), New Coins discovery (11 sort orders, §3), multi-filter
Screener (§14), token pages with full market-intelligence panel + signal-history
timeline (§15), head-to-head Compare (§29), Narratives, Backtesting (measured
hit-rates), Watchlist, Alerts with momentum/sell-pressure/ATH/reversal rules
(§20). New API: `/tokens/new`, `/screener`, `/tokens/compare`, `/signals/feed`.

---

## Original architecture notes

| Question | Feature |
|---|---|
| What is gaining attention? | 🔥 Trending board + **Trend Score (0–100)** with full component breakdown |
| What is accelerating *before* it's widely known? | 👀 Emerging board + **FOMO Index** (DORMANT → EXTREME FOMO) |
| What narratives are forming? | 🧠 Narrative engine (seeded taxonomies + automatic keyword clustering) |
| Is momentum confirmed by multiple independent sources? | ✅ Signal-confirmation panel (Social / Market / On-chain / News) + conflict detection |
| What should I be worried about? | 🚨 **Risk Score (0–100, higher = riskier)** + risk-event feed |

The system is built around **events and time-series data** (§33): every metric is stored as
5-minute bucketed snapshots, which powers acceleration math, anomaly z-scores, score history
and (later) backtesting — never just “current values”.

## Stack (Phase 1 substitutions documented in `docs/ARCHITECTURE.md`)

```
┌──────────────┐   /api/* (rewrite)   ┌───────────────────────────────────┐
│ Frontend     │ ───────────────────▶ │ Backend  (FastAPI, :8000)          │
│ Next.js 15   │                      │  ├─ providers/   dexscreener(REAL) │
│ React 19     │                      │  │               mock social/on-chain│
│ Tailwind     │                      │  │               news (isolated)    │
│ Recharts     │                      │  ├─ engine/      trend·fomo·risk·  │
└──────────────┘                      │  │               narratives·anomaly │
                                      │  ├─ services/    ingestion loop    │
                                      │  └─ SQLAlchemy → SQLite (dev) /    │
                                      │     Postgres via DATABASE_URL      │
                                      └───────────────────────────────────┘
```

## Quick start

```bash
# 1) Backend (port 8000)
cd backend
python3 -m venv .venv && . .venv/bin/activate   # optional
pip install -r requirements.txt
python seed.py            # creates DB, demo user, 48h of backfilled time-series
uvicorn app.main:app --host 0.0.0.0 --port 8000

# 2) Frontend (port 3000) — new terminal
cd frontend
npm install
npm run dev               # open http://localhost:3000

# production mode (what the live preview runs):
npm run build && npm run start
```

**Deploy publicly (access from anywhere):** see **[DEPLOYMENT.md](DEPLOYMENT.md)** —
Vercel frontend + Railway backend + Postgres, ~15 min, no server admin.

**Docker (reference topology):** `docker-compose.yml` + per-service `Dockerfile`s
ship the independent frontend / backend / Postgres deployment from §26 (Redis
provisioned-commented for the Phase-5/6 Celery + cache move). In compose, point the
frontend rewrite at `http://backend:8000` instead of `127.0.0.1` (one line in
`frontend/next.config.mjs`, read from an env var if you like).

The browser only ever talks to the Next server (`/api/*` is proxied to the backend
server-side), so no API keys or DB credentials are exposed to the client.

Demo login (seeded): handled automatically by the UI via `POST /auth/demo`
(only available when `DEMO_MODE=1`).

## Repo layout

```
memecoin-radar/
├── docs/ARCHITECTURE.md     # §39 analysis: architecture, schema, API contracts,
│                            #   scoring formulas, external-API & rate-limit audit,
│                            #   mock strategy, phased MVP plan
├── backend/
│   ├── app/
│   │   ├── main.py          # FastAPI app, middleware (security headers, rate limit)
│   │   ├── config.py        # env-driven settings
│   │   ├── database.py      # engine/session (SQLite dev, Postgres-ready)
│   │   ├── models.py        # 20 tables per §27 + indexes
│   │   ├── schemas.py       # Pydantic contracts (§28)
│   │   ├── security.py      # auth, rate limiting, validation helpers
│   │   ├── providers/       # ← MODULAR DATA-SOURCE ADAPTERS (§3)
│   │   │   ├── base.py          # provider interface + registry + health
│   │   │   ├── dexscreener.py   # REAL market data (free, no key)
│   │   │   └── mock_providers.py# MOCK social/on-chain/news (clearly isolated §32)
│   │   ├── engine/          # ← ANALYTICS ENGINE (§5–§11, §20, §34–§36)
│   │   │   ├── timeseries.py    # bucketed deltas, acceleration, EMA, z-scores
│   │   │   ├── scoring.py       # calculate_* sub-scores + trend score (explainable)
│   │   │   ├── fomo.py          # FOMO index + classification (§7)
│   │   │   ├── risk.py          # risk engine + risk events (§8)
│   │   │   ├── narratives.py    # narrative discovery + scoring (§9)
│   │   │   ├── ai_analysis.py   # deterministic explainable composer (§17–18)
│   │   │   └── alerts.py        # condition evaluator + channels (§16)
│   │   └── routers/         # REST API (§28)
│   └── seed.py              # schema + demo user + 48h backfill (§33)
└── frontend/                # Next.js App Router, dark terminal UI (§24)
```

## Honest disclaimers baked into the product (§31)

Scores are algorithmic estimates over data that may be incomplete, delayed, or mocked
(mock sources are labeled in the UI data-confidence panel). High Trend Score ≠ price will
rise; Extreme FOMO often precedes violent reversals; memecoins can go to zero.
Historical relationships ≠ future results.
