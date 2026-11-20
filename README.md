# 🛰️ MemeCoin Radar

**AI-powered meme-coin trend intelligence & risk analysis platform.**
Not a prediction machine. Not financial advice. An analytics terminal that turns chaotic
social / market / on-chain / news data into *explainable* signals so you can do your own research.

---

## Time Machine (replay a trade before you take it)

Ported from the Day Trader skill's `trader-timemachine` and adapted to 24/7 memecoins.
`GET /api/tokens/{address}/timemachine?stop_pct=12&target_pct=35&hours=24` (defaults
come from the paper strategy) and the **Time Machine** panel on every token page:

- **10,000 replays** (`engine/timemachine.py`): a block bootstrap (30-min blocks) of the
  token's own REAL 5-minute price returns over the last 7 days. Only snapshots exactly
  5 min apart count — gaps are never bridged. Odds of target first / stop first /
  neither, green %, average R, exit-return percentiles, a fan chart. Seeded (19) so a
  run is repeatable; chunked so memory stays ~10 MB.
- **What really happened**: the same stop/target/hold rule re-run on real snapshots
  after every past signal of the same kind (all tokens, 14 days, last 200 signals).
  Data gaps are counted, not filled; holds still running are excluded.
- **Verdict**: among outcomes that hit a side, target rate vs break-even `1/(1+R)`
  (coin flip / break-even <5 pts, leans 5–15, strongly ≥15). History needs ≥10
  decided signals before it gives one.
- Honest limits: 5-minute closes miss moves inside a bucket, and a real stop can fill
  worse. Needs ≥3h of unbroken data. Tests: `backend/scripts/test_timemachine.py`.

## What it does (v4 — AI Paper Trading & Strategy Simulation)

A complete virtual-funds trading laboratory wired directly into the v3 intelligence
engine (v4 §1: MARKET DATA → INTEL → SIGNAL → RISK → EXECUTION → LEDGER → ANALYTICS).
`backend/app/paper/` contains one shared strategy core (`config`, `risk`, `execution`,
`engine`, `backtest`) reused by live paper trading, backtests and replay (§1/§20/§21).

- **Demo account (§2/§3)**: $10k virtual cash, lazy-per-user, every setting editable via
  API (capital, position caps, strategy filters, risk limits, execution model) — no code
  changes. Reset with confirmation + CSV/JSON export (§23–24).
- **Signals (§4–5)**: enriched v3 SignalEvents become `PaperSignal` rows with confidence,
  factor metrics and structured ✓/⚠ explanations; nothing fabricated — missing data is
  stored as unavailable (§34).
- **Risk engine (§6/§25)**: 14 gates (confidence, liquidity, volume, mcap, risk score,
  bullish signals, position/exposure caps, daily loss, drawdown, consecutive losses…).
  Every signal ends `approved` or `rejected` with a persisted reason — the user can audit
  *AI wanted* vs *risk allowed* at `/api/paper/signals/stats`.
- **Execution (§7/§8)**: memecoin-aware cost model — base slippage + order-size price
  impact (size/pool) + volatility slip, thin-book penalty, fees both sides. Fill = price
  × (1±slip); never a real transaction (§26).
- **Exits (§10)**: stop-loss, take-profit, trailing stop, AI sell-signal (danger intel
  events), confidence collapse, risk-threshold, liquidity deterioration, max-hold,
  manual — each with exact UTC timestamps and reason (§32).
- **Ledger & timeline (§9/§14)**: `PaperPosition` (full accounting incl. slippage/fees),
  per-trade `PaperTradeEvent` stream, and trade events projected into the Live
  Intelligence Feed (`category=paper`) telling detection→decision→execution→result (§15).
- **Analytics (§16–19/§25)**: `/api/paper/analytics` — win rate, profit factor,
  drawdown, avg/median P&L & hold, fees-vs-slippage split, exit-reason mix, **confidence
  buckets** (does higher confidence win?), **signal effectiveness** (which factors
  correlate with outcomes), calendar P&L; §19 forward returns come from the v3 outcome
  labeling of paper feed events.
- **Backtest & replay (§20–21)**: `POST /api/paper/backtest` runs the same strategy core
  over recorded signal+price history (structurally look-ahead-free; data gaps counted,
  not filled), returns equity curve/top trades and optional replay frames consumed by the
  interactive replay player (play/pause/2x–10x/scrub).
- **Versioning (§22)**: settings changes mint a new immutable `StrategyVersion`; trades
  keep their original config forever.
- **Reliability (§31/§33)**: per-account event cursor + unique (account, source_event)
  make reprocessing impossible; savepoint-guarded writes survive duplicate inserts;
  37-test suite `backend/scripts/test_paper.py` covers signals/risk/execution/positions/
  accounting/reliability — all green.
- **UI**: `/paper` dashboard (equity curve, open positions, live signals, risk audit,
  timeline), `/paper/trades` ledger, `/paper/trades/[id]` trade analysis with signal
  metrics + decision explanation + risk-decision checklist, `/paper/analytics`,
  `/paper/backtest` (+replay), `/paper/settings`. Every surface labeled
  **PAPER TRADING · SIMULATION · VIRTUAL FUNDS**.

Previous summaries below for the intelligence platform the trader builds on.

## What it does (v3 — Real-Time Event Intelligence)

v3 turns the v2 signal feed into a full event-detection engine (`app/engine/intel.py`,
thresholds centralized in `app/engine/intel_config.py`):

- **12 event categories** (v3 §5–§15): new token, momentum acceleration, unusual volume
  (vs the token's own 15-min baseline, with `2x/3x/5x` escalation buckets §43), buy/sell
  pressure (activity-floored §35), whale accumulation/distribution (sustained direction,
  aggregate-inference labeled §23), holder growth/weakening, liquidity +/-, new high/breakout,
  reversal/divergence, distribution warning, elevated risk.
- **Enriched events (§52 acceptance)**: every event carries WHAT (title + signal statement),
  significance (importance LOW/MEDIUM/HIGH/CRITICAL + score), confidence 0–100 (share of
  supporting checks, age-capped §37: 58 <30m / 68 <1h / 80 <3h / 95 <24h), WHY (checklist +
  bullets), the possible contradiction (concern line) and a metrics snapshot; whale events are
  labeled *inferred* (§38).
- **Signal chains (§16)**: ordered-subsequence detection over a 90-minute window —
  `pattern_emerging` (volume→flow→holders→momentum) and `pattern_distribution`
  (new-high→abnormal-volume→distribution-flow→weakening).
- **Cooldown dedupe + escalation (§43)**: same kind+token inside its cooldown only re-fires
  when a strictly higher magnitude bucket is crossed.
- **Market Pulse (§22)**: `/signals/pulse` — aggregate event flow, importance mix,
  bullish-vs-risk balance, chain count, most active tokens over 15m/1h/4h/24h.
- **Feed filters & sorts (§23–24)**: `/signals/feed` supports category multiselect,
  min-importance tier, chain, token-age band, momentum band, min-confidence, search and
  `smart` (importance × 6h recency decay) / importance / confidence / recent ordering.
- **Watchlist intelligence (§30)**: `/signals/watchlist` groups events per watched token with
  a headline state; UI section on the Watchlist page deep-links into token timelines
  (`/token/<addr>?event=<id>` highlight mode).
- **Outcome labeling (§49) + quality dashboard (§50)**: every event is back-labeled with
  forward returns at +15m/+1h/+4h/+24h (`SignalEventOutcome`); `/signals/quality` reports
  coverage, avg confidence per category, dedupe health (~0% dup target), labeling backlog and
  avg forward return per category — surfaced on the Backtesting page.
- **Surfaces**: `/feed` page (Market Pulse + filters + rich event cards with
  why/checklist/concern expansion and View/Watch/Alert/Compare actions), compact live-intel
  panel on the Dashboard, enriched timeline on token pages, alert/compare prefill via query
  params, landing-page "Real-Time Memecoin Intelligence" section with demonstration cards
  explicitly labeled as examples (v2 §45–46 pattern).

Previous v2 summary below for the detection math the events are built on.

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

Access: the UI logs visitors in automatically via `POST /auth/demo` with a
**read-only** token (only when `DEMO_MODE=1`). Write endpoints need an owner token
from `POST /auth/login` with the server's `OWNER_KEY` ("Owner login" in the sidebar).

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
