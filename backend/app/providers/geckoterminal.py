"""GeckoTerminal — REAL new-pool discovery for Solana, free, no API key.

Endpoint used (public, keyless, ~30 calls/min shared per IP, cached ~60s):
  GET /networks/solana/new_pools?include=base_token,dex&page=N

Why: DexScreener's boosts/profiles feeds only list tokens someone PAID to
promote, so the radar used to see coins only after promotion started. New
pools (pump.fun launches, PumpSwap/Raydium/Meteora graduations) show up here
minutes after creation.

Each pool also carries price/liquidity/volume/txns, so a fresh token that
DexScreener hasn't indexed yet still gets REAL market data (as_market_tick).
"""
from __future__ import annotations

import datetime as dt
from typing import Any

import httpx

from ..config import (GECKOTERMINAL_BASE, NEW_POOL_MAX_AGE_H, NEW_POOL_MIN_LIQUIDITY_USD,
                      NEW_POOL_MIN_TXNS_H1, NEW_POOL_PAGES)
from .base import HEALTH, MarketTick, RawToken
from .dexscreener import exclusion_reason

# Quote-side mints: if one of these is the "base" token, the meme is the quote.
QUOTE_MINTS = {
    "So11111111111111111111111111111111111111112",    # wSOL
    "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v",   # USDC
    "Es9vMFrzaCERmJfrF4H2FYD4KCoNkY11McCe8BenwNYB",   # USDT
}


def _f(v: Any) -> float:
    try:
        return float(v or 0)
    except (TypeError, ValueError):
        return 0.0


def _id_addr(rel: dict | None) -> str:
    """'solana_<mint>' -> '<mint>'."""
    rid = ((rel or {}).get("data") or {}).get("id") or ""
    return rid.split("_", 1)[1] if "_" in rid else rid


def _created_ms(iso: str | None) -> int | None:
    if not iso:
        return None
    try:
        d = dt.datetime.fromisoformat(iso.replace("Z", "+00:00"))
    except ValueError:
        return None
    if d.tzinfo is None:
        d = d.replace(tzinfo=dt.timezone.utc)
    return int(d.timestamp() * 1000)


def as_market_tick(attrs: dict) -> MarketTick:
    vol = attrs.get("volume_usd") or {}
    pc = attrs.get("price_change_percentage") or {}
    tx = attrs.get("transactions") or {}
    t = lambda w, k: int(_f((tx.get(w) or {}).get(k)))  # noqa: E731
    liq = _f(attrs.get("reserve_in_usd"))
    return MarketTick(
        price_usd=_f(attrs.get("base_token_price_usd")),
        mcap=_f(attrs.get("market_cap_usd")) or _f(attrs.get("fdv_usd")),
        fdv=_f(attrs.get("fdv_usd")),
        liquidity_usd=liq, pair_liquidity_usd=liq,
        volume_h24=_f(vol.get("h24")), volume_h6=_f(vol.get("h6")),
        volume_h1=_f(vol.get("h1")), volume_m5=_f(vol.get("m5")),
        pc_m5=_f(pc.get("m5")), pc_h1=_f(pc.get("h1")),
        pc_h6=_f(pc.get("h6")), pc_h24=_f(pc.get("h24")),
        txns_h1_buys=t("h1", "buys"), txns_h1_sells=t("h1", "sells"),
        txns_h24_buys=t("h24", "buys"), txns_h24_sells=t("h24", "sells"),
        txns_m5_buys=t("m5", "buys"), txns_m5_sells=t("m5", "sells"),
    )


def parse_new_pools(payload: dict, now_ms: int) -> list[tuple[RawToken, MarketTick]]:
    """Normalise one JSON:API page. Applies the new-pool quality floor:
    age, liquidity, 1h activity, and the stablecoin/major/mcap exclusions."""
    included = {it.get("id"): it.get("attributes") or {} for it in payload.get("included") or []}
    out: list[tuple[RawToken, MarketTick]] = []
    for pool in payload.get("data") or []:
        a = pool.get("attributes") or {}
        rel = pool.get("relationships") or {}
        base, quote = _id_addr(rel.get("base_token")), _id_addr(rel.get("quote_token"))
        if base in QUOTE_MINTS and quote and quote not in QUOTE_MINTS:
            continue        # reversed pair: price fields refer to SOL/USDC, skip rather than guess
        if not base or base in QUOTE_MINTS:
            continue
        created = _created_ms(a.get("pool_created_at"))
        if created is None or (now_ms - created) > NEW_POOL_MAX_AGE_H * 3.6e6:
            continue
        mt = as_market_tick(a)
        if mt.liquidity_usd < NEW_POOL_MIN_LIQUIDITY_USD:
            continue
        if mt.txns_h1_buys + mt.txns_h1_sells < NEW_POOL_MIN_TXNS_H1:
            continue
        tok = included.get(f"solana_{base}") or {}
        sym = tok.get("symbol") or (a.get("name") or "").split(" / ")[0]
        if exclusion_reason(sym, base, mt.mcap, mt.liquidity_usd):
            continue
        dex = ((rel.get("dex") or {}).get("data") or {}).get("id") or ""
        img = tok.get("image_url") or ""
        out.append((RawToken(
            address=base, chain="solana", name=(tok.get("name") or "")[:160], symbol=sym[:32],
            image_url="" if img == "missing.png" else img,
            url=f"https://www.geckoterminal.com/solana/pools/{a.get('address', '')}",
            dex=dex, pair_address=a.get("address") or "", token_created_at=created,
            extra={"source": "new_pool", "pool_created_ms": created},
        ), mt))
    return out


class GeckoTerminalProvider:
    name = "geckoterminal"
    kind = "market"

    def __init__(self, timeout: float = 10.0, transport: httpx.AsyncBaseTransport | None = None):
        self.timeout = timeout
        self.transport = transport

    async def new_pools(self, now_ms: int | None = None) -> list[tuple[RawToken, MarketTick]]:
        """Newest Solana pools that pass the quality floor, newest first. Never raises."""
        import time
        now_ms = now_ms or int(time.time() * 1000)
        found: dict[str, tuple[RawToken, MarketTick]] = {}
        errors = 0
        async with httpx.AsyncClient(timeout=self.timeout, transport=self.transport,
                                     headers={"accept": "application/json"}) as client:
            for page in range(1, NEW_POOL_PAGES + 1):
                try:
                    r = await client.get(f"{GECKOTERMINAL_BASE}/networks/solana/new_pools",
                                         params={"include": "base_token,dex", "page": page})
                    r.raise_for_status()
                    for raw, mt in parse_new_pools(r.json(), now_ms):
                        # one token can have several new pools: keep the deepest
                        if raw.address not in found or mt.liquidity_usd > found[raw.address][1].liquidity_usd:
                            found[raw.address] = (raw, mt)
                except Exception:
                    errors += 1
                    break       # rate-limited or down: don't hammer it
        HEALTH.report(self.name, self.kind, errors == 0,
                      f"{len(found)} fresh pools passed the floor" + (f", {errors} page errors" if errors else ""))
        return sorted(found.values(), key=lambda rm: -(rm[0].token_created_at or 0))
