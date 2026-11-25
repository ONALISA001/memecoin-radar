---
name: risk-analysis
description: Risk measurement for memecoin positions and the paper portfolio — rug/tail risk, VaR/CVaR from real 5-minute returns, drawdown, Monte Carlo via the Time Machine, position sizing and stop placement. Use when sizing a paper trade, stress-testing a strategy, or reviewing the paper account's risk gates. Adapted from Vibe-Trading's risk-analysis skill.
---

# Risk analysis for memecoins

Memecoin returns are not normal: fat right tail (rare 10×), fatter left tail (rug to ~0).
Any risk number must include the **rug scenario** separately from price volatility.

## 1. Two risk buckets

1. **Event risk (rug / abandon / freeze)** — from `onchain-analysis` score, creator
   history, forensics, `risk/events`. Express as a rough probability band
   (low <5%, medium 5–20%, high >20%) with reasons; loss in that branch ≈ −90 to −100%.
2. **Price risk** — from real returns (`/tokens/{a}/history`, `price_history`).

## 2. Price-risk metrics (from real data only)

```python
import numpy as np
r = np.diff(np.log(prices))            # 5-min log returns, contiguous only
var95  = -np.percentile(r, 5)
cvar95 = -r[r <= -var95].mean()
dd     = (prices / np.maximum.accumulate(prices) - 1).min()
```

Scale by √n only as a rough guide — memecoin returns cluster and trend.
Use `/tokens/{a}/history?tf=7d` (the default 24h window may hold only a few points).
Compute returns only between points exactly 5 min apart; never bridge gaps. Report the
sample window, its age, and the share of exactly-zero returns — a high share (>15%)
means repeated snapshots and understated volatility.

## 3. Monte Carlo — use the Time Machine

`GET /tokens/{a}/timemachine?stop_pct=&target_pct=&hours=` returns:
- `simulation` (10k block-bootstrap replays of this coin's own returns):
  `target_first_pct`, `stop_first_pct`, `neither_pct`, `avg_r`, `exit_return_pct`, `verdict`;
  null with a `reason` when there isn't enough data — report that, don't guess.
- `history` (past signals re-run): `target_first_pct`, `avg_r`, `counted`, `data_gap`, `scope`, `verdict`.
- top level: `plan`, `entry_price` (latest price), `pool_returns`, `pool_hours`, `kind`.

Caveats: the return pool can stitch together separate segments, so check
`pool_hours` and the history dates. If `kind` is null, `history` covers *all* tokens'
signals, not this coin's — and a high `data_gap` count means survivorship. When the two
verdicts disagree, prefer the token-specific `simulation` unless its sample is stale or
tiny; say why. Break-even target rate = `1/(1+R)` with `R = target/stop`.

## 4. Sizing

Settings fields (`/paper/settings`, or `DEFAULT_SETTINGS` in `backend/app/paper/config.py`):
`starting_capital`, `max_position_usd`, `max_position_pct`, `max_loss_per_trade_pct`,
`max_exposure_pct`, `min_cash_reserve_pct`, `stop_loss_pct`, `take_profit_pct`,
`max_hold_hours`, `sell_pressure_exit`. `/paper/*` routes need a bearer token; without
one (401), use the config defaults and say equity/exposure were not checked.

```
max_loss          = equity × max_loss_per_trade_pct
size_by_stop      = max_loss / (stop_pct + expected exit slippage)
size_by_liquidity = pool_liquidity × 0.5%          (see liquidity-microstructure)
size_by_rug       = max_loss / 1.0                 # rug branch loses ~100%
size_by_caps      = min(max_position_usd, equity × max_position_pct)
size = min(all of the above)   → name the binding one
```

Expected value must include the rug branch and the "neither" outcomes. Easiest is the
Time Machine's `avg_r` (which already includes neither-exits):
`EV ≈ (1−p_rug)·avg_r·stop_pct − p_rug·1.0 − round_trip_cost`.

Also check the strategy's own exit gates against current data (e.g. `sellp` already
above `sell_pressure_exit` means the engine would exit at once — a reject).

## 5. Portfolio-level (paper account)

- Correlation: memecoins move together with SOL and the memecoin beta; 5 positions ≠ 5 independent bets.
- Check daily loss, drawdown, consecutive-loss and exposure gates (`/paper/settings`, `/paper/signals/stats`) — are they binding? too loose?

## Output

Event-risk band with reasons, VaR/CVaR/max DD (with sample window), Time Machine
result, sizing table with the binding constraint, EV with rug branch, and a clear
**approve / reduce / reject** for the *paper* trade.
