---
name: onchain-analysis
description: On-chain analysis framework for Solana memecoins — holder concentration, dev/creator wallets, bundled launches, mint/freeze authority, LP status, whale accumulation vs distribution, bonding-curve progress. Use when judging whether a token's supply and wallets look organic or rigged. Adapted from Vibe-Trading's onchain-analysis skill (BTC cycle metrics like MVRV/SOPR replaced with memecoin-relevant checks).
---

# On-chain analysis for memecoins

BTC-style cycle metrics (MVRV, SOPR, NVT, HODL waves) don't apply to a two-hour-old
pump.fun coin. What matters is **who holds the supply, who can change it, and whether
they are buying or leaving.** Pull data with `radar-data`: `token_report`, or
`/tokens/{a}/holders`, `/onchain-risk`, `/launch-forensics`, `/creator`.

## 1. Contract & authority (hard gates)

| Check | Healthy | Red flag |
|---|---|---|
| Mint authority | revoked | live → supply can be inflated |
| Freeze authority | revoked | live → your tokens can be frozen |
| LP | burned / locked | unlocked, or LP held by creator |
| Metadata | immutable | mutable |

Any red flag here is a near-veto regardless of everything else.

## 2. Holder structure

- **Top-10 share** (excluding pool & burn addresses): <25% healthy, 25–40% caution, >40% high risk.
- **Single largest non-pool wallet**: >5% is a meaningful dump risk on a thin pool.
- **Holder count trend**: rising holders with flat price = accumulation; falling holders with rising price = thin, fragile pump.
- **Wallet clusters**: many top wallets funded from one source, same-block buys, or identical sizes ⇒ bundle / sniper cluster. Treat the cluster as one holder.

## 3. Creator / dev

- Creator's past launches and their outcomes (`/creator`): serial launchers with mostly rugged or abandoned coins are a base-rate warning.
- Dev wallet still holding? Selling into strength? Dev sells in the first hour are a strong distribution signal.

## 4. Launch forensics

- Bundled buys in the launch block, sniper share, % of supply bought in the first N seconds.
- Bonding-curve progress and graduation: graduated coins have real AMM liquidity; bonding-curve coins can still die at 0 liquidity.

## 5. Flow: accumulation vs distribution

- Net buy/sell pressure over 5m / 1h / 24h, unique buyers vs sellers.
- Whale wallets adding vs trimming (`distribution_watch`, `intel_feed` tone=risk).
- Price up + whales trimming = distribution into retail; price flat + whales adding = accumulation.

## Where the fields are (HTTP API)

| Check | Field |
|---|---|
| Authorities | `/onchain-risk` → `facts.mint_renounced`, `facts.freeze_disabled` (`mint_authority: null` means renounced) |
| Top-10 / largest wallet | `facts.top10_wallet_pct`, `facts.top1_wallet_pct` — **use the `_wallet_` fields** (pools/burn excluded), not `top10_pct` |
| Exit cost | `/onchain-risk` → `exit.round_trip_loss_pct` (real Jupiter quote) |
| LP burned | `/tokens/{a}` → `contract_facts.lp_burned_pct` (often null) |
| Holder count / growth | `/holders` → `current.{holders, growth_pct, span_h, checked_at}` |
| Real whale sells | `/tokens/{a}/risk` → `events[].message` (free text, "Helius"-tagged). Skip events marked "(simulated)" |
| Distribution | `/tokens/{a}` → `sellp`, `sellp_band`; `/market/distribution?window=4h` |

**Do not trust** `/holders` → `whales[]` or `whale_class`: they come from the demo
simulator even on real tokens (wallet IDs like `<addr[:6]>wh123`), despite `mock:false`.
Always check `/tokens/{a}` → `data_quality.simulated` and drop every field listed there.
Note each fact's `checked_at`; on-chain facts are refreshed rarely and may be days old.

## Composite on-chain score (1–10)

Start at 10 and subtract: authority red flag −5 each; LP unlocked −4; top-10 wallet
>40% −3 (25–40% −1.5); largest wallet >10% −1.5 (5–10% −0.5); bundle/sniper cluster
>20% of supply −2; creator with ≥3 prior launches that rugged or died before
graduating −2; dev selling −2; distribution signal (real whale net-sells in the last
72 h, or `sellp_band` = distribution) −1.5. Floor at 1. Show the arithmetic.

**Missing inputs:** a missing check deducts nothing, but the score becomes an
upper bound. If ≥2 of {LP, creator, forensics, holder data <24 h old} are unavailable,
cap confidence at **low** and say "upper bound" next to the score.

## Output

```
## On-chain read: <SYMBOL> (<address>)  — market data as of <UTC>; on-chain facts as of <UTC>
Authority/LP/metadata: ...   Score: x/10 (<arithmetic>)  [upper bound?]
Holders: top10 wallet x%, largest x%, count trend ... (age)
Creator: ...                 Forensics: ...
Flow: accumulation | distribution | unclear (evidence)
Against this read: <≥1 disconfirming data point>
Signal over <horizon>: constructive | neutral | risk  (confidence low/med/high)
Unavailable / simulated (ignored): <list>
```
