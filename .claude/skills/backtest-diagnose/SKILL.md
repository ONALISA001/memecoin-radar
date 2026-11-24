---
name: backtest-diagnose
description: Diagnose a failed or underperforming MemeCoin Radar paper backtest / strategy-lab result — classify the root cause (runtime error, logic bug, data gap, overfitting, cost model) and propose or apply a minimal fix. Use when a backtest errors, shows zero trades, looks too good, or lags live paper trading. Adapted from Vibe-Trading's backtest-diagnose skill.
---

# Backtest diagnosis

Backtest code: `backend/app/paper/backtest.py` (shared strategy core with live paper:
`config.py`, `risk.py`, `execution.py`, `engine.py`). Endpoints: `POST /api/paper/backtest`,
`POST /api/paper/backtest/compare`, `GET /api/paper/backtest/jobs/{id}`, `/api/paper/lab`.
Tests: `backend/scripts/test_paper.py`, `test_strategy_lab.py`, `backend/tests/test_backtest_jobs.py`.

## Workflow

1. Reproduce: get the request body, job result and error/log.
2. Read the relevant core module(s) — don't guess.
3. Classify with the taxonomy below.
4. Fix one thing, re-run the backtest *and* the paper test suites.
5. At most 3 repair iterations; then report what's left.

## Taxonomy

**Runtime errors** — KeyError on a signal/metric field (schema drift between
`PaperSignal` and backtest input), empty history window, None in price data.

**Logic bugs (runs but wrong)**

| Symptom | Likely cause |
|---|---|
| Zero trades | a risk gate always rejects (check rejection reasons in `/paper/signals/stats`), filters too strict, or no recorded signals in the window |
| Trades only late in window | history not loaded for early period |
| Capital mostly idle | sparse signals or position caps too tight |
| Positions open at end | max-hold/forced close not applied at window end |
| Results far better than live paper | look-ahead (using future price/outcome fields), costs disabled, gaps bridged |

**Data issues** — gaps must be counted, not filled; a "great" result built on bridged
gaps is invalid. Rate-limit / provider-down errors are not code bugs — don't patch code for them.

**Overfitting** — many parameter tweaks on one short window; check out-of-sample and
`compare` across windows; a strategy whose edge vanishes when the window shifts is noise.

## Hard gates before trusting a result

- trade_count ≥ 30 (otherwise "insufficient sample")
- no NaN in equity curve; costs (fees + slippage) are non-zero
- live paper vs backtest on overlapping period roughly agree
- result survives a ±20% change in key thresholds

## Output

Root cause, evidence (file:line, numbers), fix applied or proposed, and ≥2 concrete
action items ("change X from A to B in file:line").
