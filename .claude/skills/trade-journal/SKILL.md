---
name: trade-journal
description: Analyze the paper-trading ledger (or a user-supplied CSV export) for a trading profile and behavioral biases — disposition effect, overtrading, chasing, revenge trading, anchoring, rule drift — and tie each to concrete settings changes. Use when reviewing paper performance, postmortems, or "why am I losing". Adapted from Vibe-Trading's trade-journal and behavioral-finance skills.
---

# Trade journal & behavioral review

Data: `paper_trades` / `GET /api/paper/trades`, `/paper/analytics`, `/paper/postmortems`,
`/paper/export` (CSV/JSON), or a CSV the user provides. Need ≥20 closed trades for
any bias claim; below that, describe, don't diagnose.

## Profile

Trades, win rate, profit factor, avg/median win & loss, avg hold (winners vs losers),
exit-reason mix, P&L by hour-of-day and by confidence bucket, fees+slippage share of gross P&L.

## Bias diagnostics

| Bias | Test | Flag if |
|---|---|---|
| **Disposition effect** | hold time of losers vs winners | losers held >1.5× longer, or winners cut at small gains while losers hit max-hold |
| **Overtrading** | trades/day vs edge; costs vs gross P&L | costs >40% of gross profit, or P&L falls as frequency rises |
| **Chasing** | entry vs prior 1h move | most entries after >50% 1h run-ups and those underperform |
| **Revenge trading** | entries shortly after a loss | trades within 30 min of a loss underperform the rest |
| **Anchoring** | stops/targets vs volatility | same fixed % stop on coins with very different volatility |
| **Rule drift** | settings changes (`StrategyVersion`) vs results | frequent tweaks after drawdowns, each with <20 trades |
| **Confidence miscalibration** | win rate by confidence bucket | higher confidence doesn't win more |

For the automated paper engine, "bias" means a **rule** that encodes the bias (e.g.
trailing stop too tight = cutting winners). Point at the setting.

## Output

```
## Paper trading profile — <date range>, N trades
<profile table>
## Bias diagnostics
- <bias>: <metric> → <evidence> → <suggested setting change>
## Top 3 changes (each testable with a backtest)
```

Every suggestion must be testable via `backtest-diagnose` / `POST /api/paper/backtest/compare`.
