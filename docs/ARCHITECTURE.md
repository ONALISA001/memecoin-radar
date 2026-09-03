# MemeCoin Radar — Architecture & Pre-Implementation Analysis (§39)

## 1. Specification analysis (summary)

The spec reduces to five engineering pillars:

1. **Time-series-first ingestion** (§33): every metric stored as bucketed snapshots → enables
   acceleration, anomaly detection, score history, backtesting.
2. **Modular providers** (§3): adapters isolate DexScreener, Pump.fun, X, news, on-chain RPC.
   Providers are *replaceable*; the engine consumes a normalized `TokenMetrics` struct.
3. **Explainable scoring** (§6–§8, §34–§35): pure functions returning points + raw drivers,
   composed into Trend / FOMO / Risk scores. Nothing opaque reaches the UI.
4. **Multi-signal confirmation & contradiction** (§11, §36): independent-source agreement
   raises confidence; conflicting signals are surfaced, never hidden.
5. **Safety & compliance by design** (§22, §31): no wallet trading in MVP, no secrets to the
   client, careful legal language, no guaranteed-outcome claims.

## 2. System architecture

```
              ┌────────────────────────────────────────────────────┐
              │                 FastAPI backend (:8000)             │
              │                                                    │
  DexScreener │  providers/dexscreener.py   (REAL, REST, no key)   │
  (REST) ────▶│  providers/mock_providers.py (MOCK: social, chain, │
       ┌─────▶│                    news) ← clearly isolated §32    │
       │      │        registry: providers.base.PROVIDERS          │
       │      │                     │ normalized TokenMetrics      │
       │      │                     ▼                              │
       │      │  services/ingestion.py  (async loop, 60s tick)     │
       │      │    ├─ upsert tokens/pairs                          │
       │      │    ├─ append 5-min snapshots (price/vol/liq/       │
       │      │    │  holders/social) + posts, whales, news        │
       │      │    ├─ engine.scoring / fomo / risk  → trend_scores │
       │      │    ├─ engine.narratives → narratives, news events  │
       │      │    ├─ anomaly z-scores → risk_events               │
       │      │    └─ engine.alerts → alert_events + channels      │
       │      │                     │                              │
       │      │  database.py — SQLAlchemy → SQLite (dev default)   │
       │      └───────────────────▶ PostgreSQL via DATABASE_URL    │
       │                     │                                     │
       │      │  routers/  REST + bearer auth + rate-limit +       │
       │      │            security headers                        │
       └──────┴─────────────────┬──────────────────────────────────┘
                                │ HTTPS (server-side proxy only)
              ┌─────────────────▼──────────────────────────────────┐
              │ Next.js 15 frontend (:3000) — /api/* rewrites to   │
              │ backend; browser never sees upstream keys/hosts.   │
              └────────────────────────────────────────────────────┘
```

**Production substitutions (deliberate, documented):**

| Spec item | Production target | Phase-1 implementation | Swap path |
|---|---|---|---|
| PostgreSQL | Postgres 16 + indexes | SQLite (WAL) via `DATABASE_URL` | change env var |
| Redis + Celery workers | Celery beat workers | in-process async scheduler | move `services/ingestion.tick()` into a Celery task, keep signature |
| Redis cache | Redis response cache | none (SQLite is fast at this scale) | add cache-aside in routers |
| LLM API | OpenAI/Anthropic via backend abstraction | deterministic `ai_analysis.py` composer that only narrates stored metrics (cannot fabricate by construction) | implement `LLMProvider` interface, keep composer as fallback/guardrail |
| Email/Telegram | providers | channel interface ships with `browser` + `webhook` (Discord-compatible) channels | implement `EmailChannel`, `TelegramChannel` against same interface |

## 3. Database schema (§27) — 19 tables + indexes

`users(id, email*, password_hash, api_token*, created_at)`
`tokens(id, address*, chain, name, symbol, image_url, description, deployer, pair_address, dex, url, first_seen_at, token_created_at, archetype)` — index `(chain,address)`
`token_pairs(id, token_id→tokens, dex, pair_address*, liquidity_usd, updated_at)`
`price_snapshots(id, token_id, ts, price_usd, mcap, fdv)` — idx `(token_id, ts)`
`volume_snapshots(id, token_id, ts, volume, txns, buys, sells)` — idx `(token_id, ts)`
`liquidity_snapshots(id, token_id, ts, liquidity_usd)` — idx `(token_id, ts)`
`holder_snapshots(id, token_id, ts, holders, top10_pct, deployer_pct, new_wallets, active_wallets, whale_net_usd)` — idx `(token_id, ts)`
`social_snapshots(id, token_id, ts, mentions, engagement, unique_accounts, likes, reposts, replies, sentiment, influencer_posts, low_quality_share)` — idx `(token_id, ts)`
`social_posts(id, token_id, ts, author, text, likes, reposts, quality)` — idx `(token_id, ts)`
`news_articles(id, ts, source, title, url, narrative, sentiment, relevance, entities_json)` — idx `(ts)`
`narratives(id, name*, kind, keywords_json, score, mentions_growth, token_count, cross_platform, first_seen_at, updated_at)`
`token_narratives(token_id→tokens, narrative_id→narratives, relevance, PK(token_id,narrative_id))`
`wallet_activity(id, token_id, ts, wallet, side, amount_usd, kind)` — idx `(token_id, ts)`
`risk_events(id, token_id, ts, category, severity, message)` — idx `(ts)`, `(token_id)`
`trend_scores(id, token_id, ts, trend, fomo, fomo_class, risk, risk_class, confidence, components_json)` — idx `(token_id, ts)`, `(ts, trend)`
`alerts(id, user_id→users, name, condition_json, channel_json, enabled, created_at)`
`watchlists(id, user_id→users, token_id→tokens, created_at, UNIQUE(user_id, token_id))`
`alert_events(id, alert_id→alerts, token_id, ts, payload_json, delivered_json)`
`data_sources(id, name*, kind, status, last_ok_at, meta_json)`
`system_logs(id, ts, level, source, message)` — idx `(ts)`

All snapshot tables carry `ts` epoch at 5-minute bucket boundaries (`SNAPSHOT_BUCKET_SECONDS=300`).

## 4. Scoring formulas (§6–§8, §34) — implemented in `engine/`

Trend Score = Σ sub-scores (each returns points + driver metrics):

| Sub-score | Pts | Drivers (windowed, log/saturation mapped) |
|---|---|---|
| `calculate_social_score` | 25 | mention growth 1h (8), 15m velocity (5), engagement growth (4), unique-account growth & diversity (4), influencer activity (2), cross-source presence (2) |
| `calculate_market_score` | 25 | volume acceleration 1h (9), price momentum 1h (5), txn growth (5), buy/sell balance (3), vol/liq band (3) |
| `calculate_onchain_score` | 15 | holder growth 1h (6) + 24h (4), new/active wallets (3), whale direction (2) |
| `calculate_liquidity_score` | 15 | absolute liquidity log-bands (6), liquidity change 3h (4), stability (3), vol/liq health (2) |
| `calculate_narrative_score` | 10 | best narrative score (6) + narrative count (2) + cross-platform confirmation (2) |
| `calculate_market_context_score` | 10 | sector breadth & momentum derived from tracked universe (no oracle needed) |

FOMO Index (§7) = weighted short-window accelerations (mentions 15m 20 · volume 15m 20 · price 15m 10 · txns 15m 10 · new holders 1h 15 · influencer 10 · engagement accel 10 · news/cross-platform 5).
Bands: 0–20 DORMANT · 21–40 EMERGING · 41–60 MOMENTUM · 61–80 STRONG FOMO · 81–100 EXTREME FOMO (+ overheating warning).

Risk Score (§8, higher = riskier): liquidity risk /30 (absolute, 3h drawdown, vol/liq>5) +
concentration /25 (top-10 %, deployer %) + contract /15 (mint/freeze authority where known) +
deployer /10 (history heuristics) + market behavior /10 (pump magnitude, >90% buy skew, whale dumps) +
social manipulation /10 (low unique-per-mention, low-quality share).
Bands: <30 LOW · 30–49 MODERATE · 50–69 ELEVATED · ≥70 HIGH. Missing data → confidence penalty, **not** a better score (§19).

Anomaly detection (§20): per-metric z-score vs trailing 24 h baseline (`|z| ≥ 3` ⇒ ANOMALY badge /
risk event). Simple statistics preferred over ML per spec.

Emerging (§10): trend ≥ 45, FOMO ≥ 40 (but < 81), mention & volume growth ≥ +80 % (1 h),
positive holder growth, strengthening 30-min trend delta, age ≤ 7d.

Signal confirmation (§11): Social ✓ (mention growth ≥ +50 %) · Market ✓ (volume growth ≥ +50 %) ·
On-chain ✓ (holder growth ≥ +5 %) · News ✓ (≥1 relevant article 24 h). Confidence = sources confirmed
+ provider availability (§19). Contradiction rules (§36) emit explicit warnings.

## 5. API contracts (§28) — implemented

`POST /auth/demo` (demo-mode login) · `GET /market/overview` ·
`GET /tokens/trending?chain&min_liquidity&max_risk&narrative&sort&limit` ·
`GET /tokens/emerging` · `GET /tokens/{address}` · `GET /tokens/{address}/history?metric&tf` ·
`GET /tokens/{address}/social` · `GET /tokens/{address}/holders` · `GET /tokens/{address}/risk` ·
`GET /tokens/{address}/analysis` (AI panels) · `GET /tokens/{address}/why` ·
`GET/POST/DELETE /watchlist` · `GET /watchlist/history` ·
`GET /narratives/trending` · `GET /narratives/{id}` ·
`GET/POST/PATCH/DELETE /alerts` · `GET /alerts/events` ·
`GET /search?q=` · `GET /sources` (data-source transparency §30)
Auth: bearer token; write routes protected; demo token issued only when `DEMO_MODE=1`.

## 6. External API inventory & rate-limit audit (§39.7–9)

| Source | Access | Auth | Rate limits (published) | Phase-1 status |
|---|---|---|---|---|
| DexScreener REST | Free | none | 300 req/min/IP (pairs/tokens), 60 req/min boosts | ✅ REAL (`dexscreener.py`, throttled ≤ 1 req/s, cached) |
| Pump.fun | No official public REST; unofficial WebSocket endpoints unstable/undocumented | n/a | unknown | ⚠️ MOCK adapter; interface ready for a websocket adapter |
| X/Twitter API v2 | Paid (Basic $200/mo; recent-search caps) | OAuth2 bearer | plan-based | ⚠️ MOCK (`mock_providers.py`); adapter interface in place |
| Reddit JSON | Free tier | OAuth | ~100 req/min | ⚠️ MOCK (folded into social adapter interface) |
| Crypto news (RSS/NewsAPI) | Free tiers vary | key for NewsAPI | 100 req/day (free) | ⚠️ MOCK narrative-driven articles |
| Solana RPC | Public endpoints rate-limited; providers (Helius/QuickNode) key-based | API key | plan-based | ⚠️ MOCK holder/whale simulation; interface in place |
| CoinGecko | Free demo key | demo key | 30 req/min | 🔲 deferred (DexScreener covers MVP) |

Deliberately excluded: any provider scraping that violates ToS; missing providers become mock
adapters rather than breaking the system (§39 mandate).

## 7. Which features use mock data in Phase 1 — and how it stays isolated (§39.10)

* All non-market metrics (social, holders, whales, deployer, contract authorities, news) come from
  `providers/mock_providers.py`: **deterministic per-token archetype simulations**
  (`emerging / overheated / rug_risk / steady / dormant`) seeded by `sha256(address)`.
  Regime anchors are floored to the bucket grid, so a restart reproduces identical history.
* Every API response carries `source` labels per metric group and a data-confidence grade; the UI
  shows a "MOCK" chip where data is simulated (§30).
* The ingestion engine cannot tell mock from real — swapping providers requires zero engine changes.

## 8. MVP implementation plan (§32 Phase 1) → status

UI ✔ · token discovery (DexScreener boosts + new profiles) ✔ · token DB ✔ · market metrics ✔ ·
Trend/FOMO/Risk scores + explainability ✔ · trending & emerging dashboards ✔ · token detail ✔ ·
watchlist ✔ · alerts (browser + webhook channels) ✔ · narratives (seeded + auto-discovery,
ahead of schedule from Phase 4) ✔ · anomaly detection ✔ · risk events ✔ ·
mock social/on-chain/news ✔. Post-MVP per spec: real X (2), real on-chain (3), LLM narratives (4),
Telegram/email (5), backtesting over stored `trend_scores` (6, groundwork already stored).
