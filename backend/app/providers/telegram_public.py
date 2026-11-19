"""REAL Telegram call-channel posts — free, no account, no key.

Reads each watched PUBLIC channel's web preview (https://t.me/s/<channel>), the
page Telegram serves to anyone with a browser (approach from the Radar project's
telegram connector). Only channels the operator lists in TELEGRAM_CALL_CHANNELS.

Coins are identified ONLY by a valid Solana contract address found in the post
or its links (pump.fun / DexScreener / Birdeye / GMGN / Photon URLs). A $TICKER
alone is never treated as identity (rule from solana-social-engine's cas.py).

Caveat: the preview page is not an official API — it can change shape or rate
limit. Failures are reported per channel and never break a tick.
"""
from __future__ import annotations

import asyncio
import datetime as dt
import html as htmllib
import re
from dataclasses import dataclass, field

import httpx

from .base import HEALTH
from .helius import b58decode

CHANNEL_RE = re.compile(r"^[A-Za-z0-9_]{4,64}$")
CA_RE = re.compile(r"(?<![1-9A-HJ-NP-Za-km-z])[1-9A-HJ-NP-Za-km-z]{32,44}(?![1-9A-HJ-NP-Za-km-z])")
NON_MEME = {
    "So11111111111111111111111111111111111111112", "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v",
    "Es9vMFrzaCERmJfrF4H2FYD4KCoNkY11McCe8BenwNYB", "TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA",
    "11111111111111111111111111111111", "TokenzQdBNbLqP5VEhdkAS6EPFLC1PHnBqCXEpPxuEb",
}
UA = "Mozilla/5.0 (compatible; memecoin-radar/1.0; public channel preview reader)"


@dataclass
class TgPost:
    channel: str
    post_id: str            # "<channel>/<n>"
    ts: int
    views: int
    text: str
    mints: list[str] = field(default_factory=list)


def clean_channels(raw: str | list[str]) -> list[str]:
    items = raw.split(",") if isinstance(raw, str) else raw
    out = []
    for c in items:
        c = c.strip().lstrip("@")
        c = re.sub(r"^https?://t\.me/(s/)?", "", c).strip("/")
        if CHANNEL_RE.match(c) and c.lower() not in {x.lower() for x in out}:
            out.append(c)
    return out


def is_valid_mint(s: str) -> bool:
    if s in NON_MEME or not 32 <= len(s) <= 44:
        return False
    try:
        return len(b58decode(s)) == 32
    except ValueError:
        return False


def extract_mints(text: str) -> list[str]:
    return sorted({c for c in CA_RE.findall(text or "") if is_valid_mint(c)})


def _strip(s: str) -> str:
    s = re.sub(r"<br\s*/?>", "\n", s, flags=re.I)
    return re.sub(r"[ \t]+", " ", htmllib.unescape(re.sub(r"<[^>]+>", " ", s))).strip()


def _views(v: str) -> int:
    v = v.strip().upper().replace(",", "")
    try:
        return int(float(v[:-1]) * (1e6 if v.endswith("M") else 1e3)) if v[-1:] in ("M", "K") else int(float(v))
    except ValueError:
        return 0


def parse_channel(page: str, channel: str) -> list[TgPost]:
    """Posts on a t.me/s/<channel> page (newest ~20). Links' hrefs are searched for
    contract addresses too, since calls often only link a pump.fun/DexScreener page."""
    out = []
    for block in page.split('data-post="')[1:]:
        post_id = block[:block.find('"')]
        if "/" not in post_id or not post_id.split("/")[0].lower() == channel.lower():
            continue
        m = re.search(r'tgme_widget_message_text[^>]*>([\s\S]*?)</div>', block)
        text_html = m.group(1) if m else ""
        hrefs = " ".join(re.findall(r'href="([^"]+)"', block))
        text = _strip(text_html)
        t = re.search(r'datetime="([^"]+)"', block)
        try:
            ts = int(dt.datetime.fromisoformat(t.group(1).replace("Z", "+00:00")).timestamp()) if t else 0
        except ValueError:
            ts = 0
        vm = re.search(r'tgme_widget_message_views[^>]*>([^<]+)<', block)
        mints = extract_mints(text + " " + htmllib.unescape(hrefs))
        if not text and not mints:
            continue
        out.append(TgPost(channel=channel, post_id=post_id, ts=ts, views=_views(vm.group(1)) if vm else 0,
                          text=text[:500], mints=mints))
    return out


async def fetch_channels(channels: list[str], timeout: float = 10.0, gap_s: float = 0.6,
                         transport: httpx.AsyncBaseTransport | None = None) -> tuple[list[TgPost], dict]:
    """Sequential (gentle) fetch. Returns (posts, {channel: count | 'error: ...'}). Never raises."""
    posts, status = [], {}
    async with httpx.AsyncClient(timeout=timeout, transport=transport, follow_redirects=True,
                                 headers={"user-agent": UA, "accept-language": "en"}) as client:
        for i, ch in enumerate(channels):
            if i:
                await asyncio.sleep(gap_s)
            try:
                r = await client.get(f"https://t.me/s/{ch}")
                r.raise_for_status()
                got = parse_channel(r.text, ch)
                status[ch] = len(got) if got or "tgme_widget_message" in r.text else "error: not a public channel"
                posts += got
            except Exception as exc:
                status[ch] = f"error: {type(exc).__name__}"
    ok = sum(1 for v in status.values() if not isinstance(v, str))
    HEALTH.report("telegram-public", "social", ok > 0 or not channels,
                  f"{len(posts)} posts from {ok}/{len(channels)} channels")
    return posts, status
