---
name: radar-data
description: How to pull real data from MemeCoin Radar (FOMO MCP tools or the /api HTTP endpoints) for any memecoin research, risk or strategy task. Load this before analysing a token, the market, verdicts or paper trades, so every number comes from the app instead of memory.
---

# Getting data out of MemeCoin Radar

Every analysis in this repo must be grounded in the app's own data. Never invent a
holder %, liquidity figure, social count or win rate — if a source returns nothing,
say "unavailable" (the app itself follows the same rule: missing data is stored as
unavailable, never filled).

## Route 1 — FOMO MCP tools (preferred)

If the `fomo` MCP server is connected (see `mcp-server/README.md`), use its read-only tools:

| Need | Tool |
|---|---|
| Market phase, breadth, narratives | `market_overview`, `narratives`, `news` |
| Fresh pump.fun launches | `launch_radar` (new / bonding / graduated / hot), `launch_candles` |
| Ranked coins | `trending_tokens`, `new_coins`, `priority_coins` |
| Selling pressure / whale exits | `distribution_watch`, `intel_feed` (tone=risk) |
| Everything on one coin | `token_report` (scores, risk, holders, on-chain checks, forensics, creator, verdict, AI analysis) |
| Price path | `price_history`, `launch_candles` |
| Verdict accuracy | `verdicts`, `verdict_scorecard` |
| Simulated trading | `paper_trading_status`, `paper_trades`, `strategy_lab` |

`mcp-server/fomo_family_mcp.py` adds market-wide tools (perps markets / funding,
DEX trending, graduated & bonding tokens, boosted tokens, DexScreener token detail).

## Route 2 — HTTP API (fallback)

Base: `$FOMO_API_URL`, default `https://memecoinrada.netlify.app/api`
(local backend: `http://localhost:8000/api`). GET only unless the user asked for a change.

```bash
API=${FOMO_API_URL:-https://memecoinrada.netlify.app/api}
curl -s "$API/tokens/<address>"                 # snapshot
curl -s "$API/tokens/<address>/risk"            # risk breakdown
curl -s "$API/tokens/<address>/holders"         # holder distribution
curl -s "$API/tokens/<address>/onchain-risk"    # mint/freeze authority, LP, etc.
curl -s "$API/tokens/<address>/launch-forensics"
curl -s "$API/tokens/<address>/creator"         # creator track record
curl -s "$API/tokens/<address>/social"
curl -s "$API/tokens/<address>/history"
curl -s "$API/tokens/<address>/verdict"
curl -s "$API/tokens/<address>/why"
curl -s "$API/tokens/<address>/history?tf=7d"   # default tf=24h may hold only a few points
curl -s "$API/tokens/<address>/scores"
curl -s "$API/tokens/<address>/timemachine?stop_pct=12&target_pct=35&hours=24"
curl -s "$API/search?q=<symbol>"
curl -s "$API/signals/feed"  ;  curl -s "$API/risk/events"  ;  curl -s "$API/market/overview"
curl -s "$API/market/distribution?window=4h" ; curl -s "$API/narratives/trending" ; curl -s "$API/news"
curl -s "$API/tokens/new?source=promoted"   ; curl -s "$API/sources"
curl -s "$API/verdicts/scorecard"
```

`/paper/*` routes need a signed-in bearer token (401 otherwise); without one, read
`DEFAULT_SETTINGS` in `backend/app/paper/config.py` and say live account data was not checked.
Perps and DexScreener boosts exist only as family-MCP tools, not HTTP routes.

The free host may take ~30–45 s to wake and sometimes resets connections; retry once
before calling a source down. Full route list: `backend/app/routers/{data_api,user_api,paper_api}.py`.

## Real vs simulated data — check first

- `/tokens/{a}` → `data_quality.real` / `data_quality.simulated`: drop every simulated
  field (often `mentions_1h`, `social_growth`, `organic`, `fomo`, `trend`, `narratives`,
  `whale_class`, `confidence`).
- `/tokens/{a}/holders` → `whales[]` is simulator output even on real tokens, despite
  `mock:false`. Don't use it.
- `/tokens/{a}/risk` events mix real (Helius-tagged) and "(simulated)" messages; filter by text.
- Some fields read exactly 0.0 when unpopulated (`liq_chg_24h`, `price_change_m15/h4`,
  `vol_accel_h1`); treat a lone 0.0 as unknown.

## Rules

- Cite which tool/endpoint every key number came from and its timestamp.
- Data older than ~1 h on a memecoin is stale — say so. On-chain facts (`checked_at`)
  are refreshed rarely and can be days old; report their age separately.
- Paper trading is simulation only. Never place, suggest placing, or script real trades.
- Output is research, not financial advice; say so in any final report.
