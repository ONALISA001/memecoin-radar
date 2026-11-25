---
name: social-sentiment
description: Quantify memecoin social and news sentiment — Telegram/X mention velocity, KOL and promo detection, DexScreener boosts, narrative heat, news tone — and map it to a 0–100 sentiment score with contrarian thresholds. Use when assessing hype, FOMO, or whether attention is organic. Adapted from Vibe-Trading's sentiment-analysis and social-media-intelligence skills.
---

# Social & news sentiment for memecoins

Memecoins are attention assets: price follows attention, then attention follows price,
then both collapse. The job is to tell **organic, growing attention** from **paid or
exhausted attention**. Data: `/tokens/{a}/social`, `/news`, `/narratives/trending`,
`intel_feed`, `boosted_tokens` (family MCP), `market_overview`.

## Signals

| Signal | Read |
|---|---|
| Mention velocity (mentions/hour, 1h vs 6h) | accelerating = early; decelerating while price rises = late |
| Unique authors vs total mentions | few authors, many posts ⇒ coordinated shill |
| KOL / promo channel share | high share ⇒ marketing push, often precedes distribution |
| DexScreener boost / paid listing | paid attention, not demand; flag it |
| Narrative heat (`narratives`) | coin riding a hot narrative gets beta; narrative cooling drags it down |
| News tone | listings/partnerships vs exploit/rug/scam headlines |
| Social vs on-chain divergence | mentions up but unique buyers flat ⇒ hype not converting |

## Where the data is (HTTP API) — and what is fake

| Need | Route |
|---|---|
| Posts | `/tokens/{a}/social` → `posts[{ts, author, text, views, url, platform}]`; `source:"none"` means no Telegram channels are configured |
| News | `/news?symbol=` → `items[{ts, source, title, narrative, sentiment (−1..1), symbols}]` |
| Narratives | `/narratives/trending` (not `/narratives`); ignore `AUTO:` noise entries |
| Events | `/signals/feed`, `/signals/pulse` (the MCP `intel_feed` equivalent) |
| Market | `/market/overview` (`phase`, `breadth`, `mock_notice`) |
| Promo | `/tokens/new?source=promoted`, `discovery_source` on the token; DexScreener boosts only via the family MCP `boosted_tokens` |
| Flow proxy | `/tokens/{a}` → `buys_1h`, `sells_1h`, `buy_share`, `volume_1h` vs `volume_24h/24` |

Check `/tokens/{a}` → `data_quality.simulated` first. Fields such as `mentions_1h`,
`social_growth`, `organic`, `fomo`, `trend`, `narratives`, `whale_class` are often
simulated — exclude anything listed there. `/sources` says what is real: there is no
free X/Twitter source, so on-chain unique buyers are the attention proxy.

## Composite sentiment score (0–100)

Weighted: velocity trend 30, author breadth (organic-ness) 20, on-chain conversion
(buyers following mentions) 25, narrative heat 15, news tone 10. Subtract 15 if the
attention is mostly paid/boosted. Show components.

**Unavailable components are dropped and the rest rescaled to 100** (never scored as
zero). Say which were dropped. If more than half the weight is unavailable, confidence
is **low**. When there is no social feed, use the flow proxy for velocity and label it "proxy".

**Signal mapping:** ≥60 with on-chain confirmation → constructive; 40–60 → neutral;
<40, or >80 (crowded) → risk. Judge velocity over the given horizon (hours for memecoins).

- **>80 extreme greed**: crowded, late — contrarian warning, not a buy signal.
- **60–80 warm**: momentum regime; works only with on-chain confirmation.
- **40–60 neutral.**
- **<40 cold**: attention fading; memecoins rarely recover lost attention.

State explicitly whether your read is **momentum-following** or **contrarian**, and why.

## Pitfalls

- Bots inflate counts; prefer unique-author and on-chain conversion metrics.
- A single viral post is an event, not a trend — check the 6h window.
- Absence of social data ≠ zero interest; mark it unavailable.
