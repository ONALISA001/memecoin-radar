"""Real news → narratives and tokens.

Every NEWS_REFRESH_MINUTES the tick fetches the publisher feeds
(providers/news_rss.py) and stores new headlines as NewsArticle rows:

* narrative = the seeded narrative whose NEWS terms the headline matches best
  (stricter than the token-name keywords: "ai" alone or "cat" would match half
  of all crypto news, so news needs specific terms like "ai agent" / "memecoin");
* entities  = tracked token symbols mentioned as a $CASHTAG, or as a whole word
  when the symbol is long and distinctive enough;
* sentiment = the headline keyword score (-1..1).

The engine's existing news inputs (FOMO news component, narrative news
activity, signal confirmation "news" check) then run on REAL coverage instead of
the simulator. Simulated mock-news rows are deleted once real news is on.
"""
from __future__ import annotations

import re
import time

from sqlalchemy import delete, select

from ..models import NewsArticle, Token

# Specific, news-grade terms per seeded narrative (engine/narratives.SEED_NARRATIVES).
NEWS_TERMS: dict[str, list[str]] = {
    "AI Agents": ["ai agent", "ai agents", "agentic", "ai16z", "aixbt", "virtuals protocol", "eliza"],
    "AI & Compute": ["nvidia", "gpu", "gpus", "ai compute", "ai token", "ai tokens", "openai", "anthropic"],
    "Animals": ["dogecoin", "doge", "shiba inu", "shib", "pepe", "bonk", "dogwifhat", "wif", "popcat",
                "pengu", "pudgy penguins", "moo deng", "floki"],
    "Politics & Figures": ["trump", "maga", "election", "politifi", "white house", "congress", "senate",
                           "elon musk", "musk"],
    "Gaming": ["gamefi", "web3 gaming", "crypto gaming", "blockchain game", "play-to-earn"],
    "Internet Culture": ["memecoin", "memecoins", "meme coin", "meme coins", "pump.fun", "pumpfun",
                         "viral", "tiktok"],
    "DePIN & Infra": ["depin", "helium", "render network", "decentralized physical"],
    "Celebrity": ["celebrity", "kanye", "drake", "taylor swift", "kardashian", "influencer"],
}
_TERM_RE = {n: re.compile(r"(?<![\w$])(" + "|".join(re.escape(t) for t in ts) + r")(?![\w])", re.I)
            for n, ts in NEWS_TERMS.items()}
# Symbols that are ordinary words/tickers in news even in capitals.
_SYMBOL_STOP = {"THE", "AND", "FOR", "NEW", "ALL", "ONE", "CEO", "USA", "SEC", "ETF", "NFT", "DAO",
                "BTC", "ETH", "SOL", "USD", "API", "BIG", "WIN", "TOP", "BUY", "NOW", "GOD", "MOON",
                "PUMP", "MEME", "COIN", "CASH", "GOLD", "BANK", "SAFE", "OPEN", "TRUMP", "WORLD"}


def match_narratives(text: str) -> list[tuple[str, int]]:
    """[(narrative, hits)] best first."""
    hits = [(n, len(rx.findall(text))) for n, rx in _TERM_RE.items()]
    return sorted([h for h in hits if h[1]], key=lambda h: -h[1])


def match_symbols(text: str, symbols: list[str]) -> list[str]:
    found = []
    for s in symbols:
        s = (s or "").strip()
        if len(s) < 2 or not s.isalnum():
            continue
        if re.search(rf"\${re.escape(s)}\b", text, re.I):
            found.append(s.upper())
        elif len(s) >= 4 and s.upper() not in _SYMBOL_STOP and re.search(rf"(?<![\w$]){re.escape(s.upper())}(?!\w)", text):
            found.append(s.upper())
    return sorted(set(found))


def store_articles(db, articles, now: int, retention_days: int = 7) -> dict:
    """Insert new headlines (deduped by URL), tag narratives/symbols, prune old
    rows and any simulated mock-news. Returns counts."""
    symbols = [s for (s,) in db.execute(select(Token.symbol)).all() if s]
    urls = [a.url for a in articles]
    existing = set()
    for i in range(0, len(urls), 200):
        existing |= {u for (u,) in db.execute(select(NewsArticle.url)
                                              .where(NewsArticle.url.in_(urls[i:i + 200]))).all()}
    added = tagged = 0
    for a in articles:
        if a.url in existing:
            continue
        existing.add(a.url)
        ts = a.ts if 0 < a.ts <= now + 600 else now      # unknown / future-dated => fetch time
        if ts < now - retention_days * 86400:
            continue
        narrs = match_narratives(a.text)
        syms = match_symbols(a.text, symbols)
        db.add(NewsArticle(ts=ts, source=a.source[:64], title=a.title[:300], url=a.url[:512],
                           narrative=narrs[0][0] if narrs else None, sentiment=a.sentiment,
                           relevance=round(min(1.0, 0.4 + 0.2 * narrs[0][1]), 2) if narrs else 0.0,
                           entities_json=syms))
        added += 1
        tagged += 1 if (narrs or syms) else 0
    mock = db.execute(delete(NewsArticle).where(NewsArticle.url.like("https://mocknews.example/%"))).rowcount
    old = db.execute(delete(NewsArticle).where(NewsArticle.ts < now - retention_days * 86400)).rowcount
    return {"added": added, "tagged": tagged, "removed_mock": mock or 0, "pruned": old or 0}


class NewsSchedule:
    """Process-local refresh timer (a restart simply fetches again)."""
    def __init__(self):
        self.last = 0.0

    def due(self, minutes: float, now: float | None = None) -> bool:
        return (now or time.time()) - self.last >= minutes * 60

    def mark(self, now: float | None = None):
        self.last = now or time.time()


SCHEDULE = NewsSchedule()
