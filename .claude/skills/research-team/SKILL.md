---
name: research-team
description: Run a multi-agent research desk on a memecoin (or the market) — on-chain, sentiment and liquidity analysts in parallel, then the risk manager, then the alpha synthesizer. Use for "full research on <coin>", "should the paper desk take <coin>", "risk committee on the paper account", or "social alpha scan". Claude Code port of Vibe-Trading's swarm presets (crypto_research_lab, crypto_trading_desk, social_alpha_team, risk_committee).
---

# Research team (multi-agent workflow)

Vibe-Trading runs YAML "swarm" DAGs; here the same teams run as Claude Code
subagents from `.claude/agents/`. Inputs: `target` (symbol/address, or "market") and
`horizon` (default: next 24 h — memecoin horizons are hours, not months).

First resolve the target to an address (`search_tokens` / `/api/search?q=`) so every
agent analyses the same coin.

## Presets

### `coin` — full research (default)
1. **In parallel** (one message, three Agent calls):
   `onchain-analyst`, `sentiment-analyst`, `liquidity-analyst` — each gets target, address, horizon.
2. `risk-manager` — paste all three reports into its prompt.
3. `alpha-synthesizer` — paste all four reports.
4. Present the synthesizer's report; keep the analyst reports available on request.

### `desk` — paper-trade decision
Same as `coin`, but the risk manager must output a concrete paper plan
(size, stop, target, max hold) or a reject. Executing it in the app is the user's
call — never POST a trade without their explicit go-ahead.

### `social` — social alpha scan (market-wide)
`sentiment-analyst` on the market (narratives, intel feed, new/hot launches) → pick
the top 3 coins where attention is organic and accelerating → `onchain-analyst` on
each in parallel → `alpha-synthesizer`.

### `risk-committee` — paper account review
In parallel: `risk-manager` (gates, exposure, drawdown, correlation) and
`journal-coach` (behavioral review) → `alpha-synthesizer` acts as head of risk and
signs off with the top changes, each testable via backtest compare.

### `backtest` — a backtest looks wrong
`backtest-doctor` alone.

## Rules

- Pass reports verbatim between stages; don't summarise away the numbers. Strip only
  their test/debug notes, and forward any data-quality warnings (stale or simulated
  fields) to every later stage.
- If an analyst returns mostly "unavailable", say the research is thin rather than
  letting the synthesizer fill gaps.
- Final output always ends: "Research only — not financial advice. Paper trading is simulated."
