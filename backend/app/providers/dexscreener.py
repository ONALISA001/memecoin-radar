"""DexScreener market-data provider — REAL data, free, no API key.

Endpoints used (public):
  GET /token-boosts/top/v1          → currently boosted tokens (discovery)
  GET /token-profiles/latest/v1     → newest token profiles   (discovery)
  GET /tokens/v1/{chain}/{addrs}    → pair stats for up to 30 addresses

Boosts/profiles are PAID promotion, so they surface coins late. Fresh pools
from GeckoTerminal (providers/geckoterminal.py) are merged in by ingestion.

Published limits are ~300 req/min for token endpoints, 60 req/min for boost
endpoints; we make ≤ 4 requests per 60-second tick and back off on errors.
"""
from __future__ import annotations

import asyncio
from typing import Any

import httpx

from ..config import (DEXSCREENER_BASE, MAX_MCAP_USD, MAX_TRACKED_TOKENS,
                      MIN_LIQUIDITY_USD, SOLANA_ONLY)
from .base import HEALTH, MarketTick, RawToken

# Stablecoins / wrapped majors are never meme-coin candidates.
EXCLUDED_SYMBOLS = {
    "USDC", "USDT", "USDS", "PYUSD", "USDE", "USDY", "USD1", "FDUSD", "DAI", "TUSD", "EURC",
    "UXD", "USDH", "USDG", "SOL", "WSOL", "JITOSOL", "MSOL", "BSOL", "JUPSOL", "INF",
    "WBTC", "CBBTC", "TBTC", "WETH", "ETH", "BTC",
}
EXCLUDED_MINTS = {
    "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v",   # USDC
    "Es9vMFrzaCERmJfrF4H2FYD4KCoNkY11McCe8BenwNYB",   # USDT
    "So11111111111111111111111111111111111111112",    # wSOL
}


def exclusion_reason(symbol: str, address: str, mcap: float, liq: float) -> str | None:
    """Why a token must not enter the radar universe (None = keep)."""
    if address in EXCLUDED_MINTS or (symbol or "").upper() in EXCLUDED_SYMBOLS:
        return "stablecoin/major"
    if mcap and mcap > MAX_MCAP_USD:
        return "market cap above ceiling"
    if liq < MIN_LIQUIDITY_USD:
        return "liquidity below floor"
    return None


CHAINS = ("solana",) if SOLANA_ONLY else ("solana", "ethereum", "base", "bsc")


class DexscreenerProvider:
    name = "dexscreener"
    kind = "market"

    def __init__(self, timeout: float = 10.0) -> None:
        self.timeout = timeout

    async def _get(self, client: httpx.AsyncClient, path: str) -> Any:
        r = await client.get(f"{DEXSCREENER_BASE}{path}")
        r.raise_for_status()
        return r.json()

    async def discover(self) -> list[RawToken]:
        """Discover candidate meme coins from boosted + latest profiles."""
        out: dict[tuple[str, str], RawToken] = {}
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            boosted, profiles = await asyncio.gather(
                self._get(client, "/token-boosts/top/v1"),
                self._get(client, "/token-profiles/latest/v1"),
                return_exceptions=True,
            )
        for source, payload in (("boost", boosted), ("profile", profiles)):
            if isinstance(payload, Exception) or not isinstance(payload, list):
                continue
            for it in payload:
                chain = str(it.get("chainId", "")).lower()
                if chain == "bsc":
                    chain = "bnb"  # UI label
                if chain not in CHAINS and not (chain == "bnb" and not SOLANA_ONLY):
                    continue
                addr = it.get("tokenAddress") or ""
                if not addr:
                    continue
                icon = it.get("icon") or ""
                if icon and not icon.startswith("http"):
                    icon = f"https://cdn.dexscreener.com/cms/images/{icon}"
                key = (chain, addr)
                out.setdefault(key, RawToken(
                    address=addr, chain=chain,
                    name="", symbol="", image_url=icon,
                    url=it.get("url") or "",
                    description=(it.get("description") or "")[:600],
                    extra={"boosts": it.get("totalAmount") or it.get("amount") or 0, "source": source},
                ))
        HEALTH.report(self.name, self.kind, True, f"discovered {len(out)} candidates")
        # rank by boosts to prioritise currently-attended tokens
        ranked = sorted(out.values(), key=lambda t: t.extra.get("boosts", 0), reverse=True)
        return ranked[:MAX_TRACKED_TOKENS]

    async def fetch_market(self, tokens: list[RawToken]) -> dict[str, MarketTick]:
        """Enrich tokens with pair stats; also fills name/symbol/pair metadata."""
        ticks: dict[str, MarketTick] = {}
        by_chain: dict[str, list[RawToken]] = {}
        for t in tokens:
            chain = "bsc" if t.chain == "bnb" else t.chain
            by_chain.setdefault(chain, []).append(t)

        async with httpx.AsyncClient(timeout=self.timeout) as client:
            tasks = []
            for chain, group in by_chain.items():
                for i in range(0, len(group), 30):
                    chunk = group[i:i + 30]
                    addrs = ",".join(t.address for t in chunk)
                    tasks.append(self._get(client, f"/tokens/v1/{chain}/{addrs}"))
            results = await asyncio.gather(*tasks, return_exceptions=True)
        failures = 0
        skipped = 0
        for res in results:
            if isinstance(res, Exception) or res is None:
                failures += 1
                continue
            pairs = res if isinstance(res, list) else res.get("pairs") or []
            best: dict[str, dict] = {}
            for p in pairs:
                base = p.get("baseToken") or {}
                addr = base.get("address")
                if not addr:
                    continue
                liq = (p.get("liquidity") or {}).get("usd") or 0
                if addr not in best or liq > ((best[addr].get("liquidity") or {}).get("usd") or 0):
                    best[addr] = p
            for t in tokens:
                p = best.get(t.address)
                if not p:
                    continue
                _mc = float(p.get("marketCap") or p.get("fdv") or 0)
                _lq = float((p.get("liquidity") or {}).get("usd") or 0)
                _sym = (p.get("baseToken") or {}).get("symbol") or ""
                if exclusion_reason(_sym, t.address, _mc, _lq):
                    skipped += 1
                    continue
                vol = p.get("volume") or {}
                pc = p.get("priceChange") or {}
                tx = p.get("txns") or {}
                h1 = tx.get("h1") or {}
                h24 = tx.get("h24") or {}
                m5 = tx.get("m5") or {}
                ticks[t.address] = MarketTick(
                    price_usd=float(p.get("priceUsd") or 0),
                    mcap=float(p.get("marketCap") or p.get("fdv") or 0),
                    fdv=float(p.get("fdv") or 0),
                    liquidity_usd=float((p.get("liquidity") or {}).get("usd") or 0),
                    volume_h24=float(vol.get("h24") or 0),
                    volume_h6=float(vol.get("h6") or 0),
                    volume_h1=float(vol.get("h1") or 0),
                    volume_m5=float(vol.get("m5") or 0),
                    pc_m5=float(pc.get("m5") or 0),
                    pc_h1=float(pc.get("h1") or 0),
                    pc_h6=float(pc.get("h6") or 0),
                    pc_h24=float(pc.get("h24") or 0),
                    txns_h1_buys=int(h1.get("buys") or 0),
                    txns_h1_sells=int(h1.get("sells") or 0),
                    txns_h24_buys=int(h24.get("buys") or 0),
                    txns_h24_sells=int(h24.get("sells") or 0),
                    txns_m5_buys=int(m5.get("buys") or 0),
                    txns_m5_sells=int(m5.get("sells") or 0),
                    pair_liquidity_usd=float((p.get("liquidity") or {}).get("usd") or 0),
                )
                # metadata fill
                base = p.get("baseToken") or {}
                t.name = t.name or base.get("name") or ""
                t.symbol = t.symbol or base.get("symbol") or ""
                t.dex = p.get("dexId") or ""
                t.pair_address = p.get("pairAddress") or ""
                t.url = t.url or p.get("url") or ""
                t.token_created_at = p.get("pairCreatedAt")
        ok = failures == 0
        HEALTH.report(self.name + ":pairs", self.kind, ok,
                      f"{len(ticks)} pairs, {skipped} filtered out, {failures} chunk failures")
        return ticks
