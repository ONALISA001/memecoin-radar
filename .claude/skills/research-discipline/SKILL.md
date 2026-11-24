---
name: research-discipline
description: 60-second bias self-check to run at the START of any memecoin research task (token deep-dive, screener pass, narrative study, strategy review). Covers survivorship, shill, narrative, confirmation, recency and liquidity-illusion bias. Adapted from Vibe-Trading's research-discipline skill.
---

# Memecoin research bias self-check

Run this before the first data pull. These biases systematically warp AI research on
memecoins; correcting for them is cheaper than un-writing a wrong conclusion.

| Bias | How it shows up | Correction |
|---|---|---|
| **Survivorship** | You study only the coins still trending; the 95% that rugged or bled out vanish from the sample. | Pull `risk/events`, `distribution_watch` and dead launches too. Base rates come from *all* launches, not winners. |
| **Shill / source bias** | Telegram calls, boosted DexScreener listings and paid KOL posts dominate the social data. | Treat boosts and promo channels as marketing, not demand. Weight on-chain buys and holder growth over mentions. |
| **Narrative bias** | "AI agent coin", "cat coin" label pulls you into analysing the meme instead of the order flow. | Ignore the label; look at liquidity, holder structure, creator history, buy/sell pressure. |
| **Confirmation** | Once a coin looks good, you only fetch bullish evidence. | Munger inversion: for every bull point, fetch the bear case (holders, creator, forensics, onchain-risk). Cite ≥1 disconfirming data point per conclusion. |
| **Recency** | A 5-minute pump dominates the read; or you reuse a figure from an hour ago. | Check every timestamp. On memecoins >1 h is stale. Compare 5m vs 1h vs 24h windows. |
| **Liquidity illusion** | A big market cap on a thin pool looks like a big coin. | Always read market cap *against* pool liquidity and price impact for a realistic size. |

## How to apply

1. Write the thesis in one sentence.
2. For each row, ask: "am I about to fall into this?"
3. Broaden the data plan: losers too, bear case, on-chain over social, fresh timestamps.
4. Before concluding, re-check: disconfirming evidence cited? any number stale? any figure assumed rather than fetched?

Pairs with `radar-data` (where the numbers come from).
