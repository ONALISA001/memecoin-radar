"""REAL crypto news from publisher RSS/Atom feeds — free, no API key.

Ported from the /api/news Cloudflare function in analyze-memecoin-socials
(functions/api/news.js): same publisher list, same tolerant regex reader
(RSS <item> and Atom <entry>), one failing publisher never empties the feed.

Only the headline, link, publisher and time are kept — never article bodies.
Sentiment is a transparent keyword score on the headline (+ RSS summary, which
is read but not stored), matched on whole words so "ban" doesn't fire on "bank".
"""
from __future__ import annotations

import asyncio
import datetime as dt
import html
import re
from dataclasses import dataclass, field
from email.utils import parsedate_to_datetime

import httpx

from .base import HEALTH

SOURCES = [
    ("CoinDesk", "https://www.coindesk.com/arc/outboundfeeds/rss/"),
    ("Cointelegraph", "https://cointelegraph.com/rss"),
    ("Decrypt", "https://decrypt.co/feed"),
    ("The Block", "https://www.theblock.co/rss.xml"),
    ("The Defiant", "https://thedefiant.io/feed"),
    ("Bitcoin Magazine", "https://bitcoinmagazine.com/feed"),
    ("Blockworks", "https://blockworks.co/feed"),
]
PER_SOURCE = 25
UA = "memecoin-radar/1.0 (news reader; +https://github.com/ONALISA001/memecoin-radar)"

BULLISH = ["surge", "surges", "surged", "rally", "rallies", "rallied", "gain", "gains", "soar", "soars",
           "jump", "jumps", "breakout", "all-time high", "adoption", "approval", "approved",
           "bull", "bullish", "inflows", "listing", "lists", "launches", "partnership", "pump"]
BEARISH = ["drop", "drops", "dropped", "crash", "crashes", "plunge", "plunges", "slump", "tumble",
           "hack", "hacked", "exploit", "exploited", "lawsuit", "sued", "ban", "bans", "banned",
           "bear", "bearish", "outflows", "rug", "rugpull", "scam", "fraud", "delist", "liquidated",
           "liquidations", "dump", "dumps", "probe", "charges", "selloff"]
_BULL_RE = re.compile(r"\b(" + "|".join(BULLISH) + r")\b", re.I)
_BEAR_RE = re.compile(r"\b(" + "|".join(BEARISH) + r")\b", re.I)
_TAG = re.compile(r"<[^>]*>")
_CDATA = re.compile(r"<!\[CDATA\[([\s\S]*?)\]\]>")
_ITEM = re.compile(r"<(item|entry)(?:\s[^>]*)?>([\s\S]*?)</\1>", re.I)
_HREF = re.compile(r"""<link[^>]*\shref=["']([^"']+)["']""", re.I)


@dataclass
class Article:
    source: str
    title: str
    url: str
    ts: int                     # unix seconds (0 = unknown)
    sentiment: float            # -1 (bearish) .. +1 (bullish)
    text: str = field(default="", repr=False)   # title + summary, for matching only (not stored)


def sentiment_score(text: str) -> float:
    bull, bear = len(_BULL_RE.findall(text or "")), len(_BEAR_RE.findall(text or ""))
    return 0.0 if bull == bear else round((bull - bear) / (bull + bear), 2)


def _text_of(item: str, tag: str) -> str:
    m = re.search(rf"<{re.escape(tag)}(?:\s[^>]*)?>([\s\S]*?)</{re.escape(tag)}>", item, re.I)
    if not m:
        return ""
    raw = _CDATA.sub(r"\1", m.group(1))
    return re.sub(r"\s+", " ", html.unescape(_TAG.sub("", raw))).strip()


def _parse_time(s: str) -> int:
    if not s:
        return 0
    try:
        d = parsedate_to_datetime(s)                      # RSS: RFC 822
    except (TypeError, ValueError, IndexError):
        try:
            d = dt.datetime.fromisoformat(s.replace("Z", "+00:00"))   # Atom: ISO 8601
        except ValueError:
            return 0
    if d.tzinfo is None:
        d = d.replace(tzinfo=dt.timezone.utc)
    return int(d.timestamp())


def parse_feed(xml: str, source: str) -> list[Article]:
    out = []
    for m in _ITEM.finditer(xml or ""):
        item = m.group(2)
        title = _text_of(item, "title")[:300]
        href = _HREF.search(item)
        link = _text_of(item, "link") or (html.unescape(href.group(1)) if href else "")
        if not title or not link.startswith(("http://", "https://")):
            continue
        summary = (_text_of(item, "description") or _text_of(item, "summary")
                   or _text_of(item, "content:encoded"))[:600]
        when = (_text_of(item, "pubDate") or _text_of(item, "dc:date")
                or _text_of(item, "published") or _text_of(item, "updated"))
        text = f"{title} {summary}"
        out.append(Article(source=source, title=title, url=link[:512], ts=_parse_time(when),
                           sentiment=sentiment_score(text), text=text))
        if len(out) >= PER_SOURCE:
            break
    return out


async def fetch_news(sources=SOURCES, timeout: float = 8.0,
                     transport: httpx.AsyncBaseTransport | None = None) -> tuple[list[Article], dict]:
    """All feeds concurrently. Returns (articles, {source: count | 'error: ...'}). Never raises."""
    async with httpx.AsyncClient(timeout=timeout, transport=transport, follow_redirects=True,
                                 headers={"user-agent": UA, "accept":
                                          "application/rss+xml, application/xml;q=0.9, */*;q=0.8"}) as c:
        async def one(name, url):
            try:
                r = await c.get(url)
                r.raise_for_status()
                return name, parse_feed(r.text, name), None
            except Exception as exc:
                return name, [], f"{type(exc).__name__}"[:60]
        results = await asyncio.gather(*(one(n, u) for n, u in sources))
    status, articles, seen = {}, [], set()
    for name, arts, err in results:
        status[name] = f"error: {err}" if err else len(arts)
        for a in arts:
            key = (a.source + "|" + a.title).lower()
            if key not in seen:
                seen.add(key)
                articles.append(a)
    ok = sum(1 for v in status.values() if not isinstance(v, str))
    HEALTH.report("news-rss", "news", ok > 0, f"{len(articles)} articles from {ok}/{len(sources)} publishers")
    return articles, status
