---
name: liquidity-microstructure
description: AMM pool liquidity, price impact, slippage and exit-ability analysis for memecoins, plus funding/OI/liquidation reads for majors with perps. Use when asking "can this size get in and out", "how thin is this pool", or how the paper execution model's costs compare to reality. Adapted from Vibe-Trading's market-microstructure, execution-model, perp-funding-basis and liquidation-heatmap skills.
---

# Liquidity & microstructure

## 1. AMM price impact (constant product x·y=k)

For a pool with quote-side reserve `R` (USD on one side ≈ liquidity/2), buying `Δ` USD:

```
shortfall      ≈ Δ / (R + Δ)        # avg fill vs spot (the marginal price moves ≈ 2Δ/R)
cost_per_side  ≈ shortfall + floor  # floor = exit.round_trip_loss_pct / 2
round_trip     ≈ shortfall_in + shortfall_out + 2·floor
```

`floor` is the real fee + spread, measured by the Jupiter quote at
`/onchain-risk` → `exit.round_trip_loss_pct` (or `contract_facts.exit_round_trip_loss_pct`).
Use it instead of guessing a fee, and don't add the pool fee on top.

Rules of thumb: size ≤0.5% of pool liquidity ⇒ ~1% shortfall; 2% ⇒ ~4%; 5% ⇒ ~10%.

**Stressed exit:** state the scenario. A −50% price move in a constant-product pool
cuts USD liquidity to ~71% (√0.5); "−50% liquidity" means a ~−75% price move or an LP
pull. Report both if in doubt.

Compare with the app's model in `backend/app/paper/execution.py` (`slippage_pct`: base
slippage + half-spread + linear size/liquidity impact + volatility term, capped; thin-book
penalty below a liquidity floor; fees both sides; defaults in `config.py`) and say where
it is too optimistic or too pessimistic for this pool.

## 2. Liquidity quality

| Metric | Healthy | Warning |
|---|---|---|
| Liquidity / market cap | >10% | <3% (mcap is paper) |
| 24h volume / liquidity | 0.5–5× | >20× (wash / churn) or <0.1× (dead) |
| Liquidity trend | stable/rising, consistent with √(price change) | falling faster than price ⇒ LP pulling |
| Venue (`dex`) | graduated AMM | bonding curve only |
| Holder overhang | largest wallet's tokens ≪ pool's token reserve | one wallet holds more tokens than the pool ⇒ a dump makes exit impossible |

`liq_chg_24h` is sometimes 0.0 (unpopulated) and `/history` has no liquidity series;
infer the trend from earlier snapshots (e.g. verdict evidence) and the price change.
| Buy/sell count balance | mixed | one-sided spikes |

Liquidity deterioration is one of the app's exit triggers — check `risk/events`.

## 3. Order-flow toxicity (simplified)

- Large sells clustered right after large buys ⇒ snipers flipping into retail.
- Buy count high but average buy tiny + sells large ⇒ retail buying whales' exits.
- Volume concentrated in a few wallets ⇒ wash/churn.

## 4. Majors & perps (via family MCP `perps_markets`, `perp_detail`)

- Funding >0.1%/8h sustained: longs crowded, local-top risk. Negative funding: shorts crowded.
- OI up + price up: leveraged chase. OI down + price down: deleveraging, possible flush bottom.
- Big OI with thin books ⇒ liquidation cascades move SOL and drag memecoin beta with it.

## Output

Pool liquidity, mcap/liquidity, impact table for $100 / $500 / $2,000 (in and out,
normal and −50% stressed), toxicity notes, verdict: **tradeable / small-size only / untradeable**.
