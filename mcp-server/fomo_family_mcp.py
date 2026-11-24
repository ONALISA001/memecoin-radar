"""fomo.family-style market data MCP server (read-only, public APIs only).

Gives Claude the same kind of data the fomo.family Discover page shows, taken straight from
the public, documented sources behind it, not from fomo's private backend:

    Perps   (Crypto / Stocks / Commodities / FX / Indices)  -> Hyperliquid info API
    Tokens  (Trending / Graduated / Bonding)                 -> GeckoTerminal (Solana, pump.fun)
    Search, token detail, boosted tokens                     -> DexScreener

Nothing here signs in anywhere, places orders or moves funds. Run it locally:

    python fomo_family_mcp.py               # stdio, started by Claude Desktop / Claude Code

Settings (environment variables, all optional):
    FOMO_FAMILY_TIMEOUT   seconds per request, default 20
"""
from __future__ import annotations

import logging
import os
import re
import time
from typing import Any, Literal

import httpx
from mcp.server.fastmcp import FastMCP

HL = "https://api.hyperliquid.xyz/info"
GT = "https://api.geckoterminal.com/api/v2"
DS = "https://api.dexscreener.com"
TIMEOUT = float(os.getenv("FOMO_FAMILY_TIMEOUT", "20"))
SOL_ADDR = re.compile(r"^[1-9A-HJ-NP-Za-km-z]{32,44}$")
logging.getLogger("httpx").setLevel(logging.WARNING)

# HIP-3 "xyz" venue lists stocks, commodities, FX and indices together; split them like fomo's tabs.
COMMODITIES = {"GOLD", "SILVER", "CL", "BRENTOIL", "COPPER", "NATGAS", "URANIUM", "ALUMINIUM",
               "PLATINUM", "PALLADIUM", "WHEAT", "CORN", "SOYBEAN", "COFFEE", "SUGAR", "COCOA"}
FX = {"EUR", "JPY", "GBP", "KRW", "CHF", "AUD", "CAD", "CNH", "MXN", "INR", "DXY"}
INDICES = {"XYZ100", "SPX", "SP500", "NDX", "US500", "US100", "DJI", "RUT", "VIX", "JP225", "DE40"}

mcp = FastMCP("fomo-family", instructions=(
    "Read-only market data like the fomo.family Discover page, from public APIs: Hyperliquid perps "
    "(crypto, stocks, commodities, FX, indices) and Solana memecoins (trending, pump.fun bonding, "
    "graduated) from GeckoTerminal/DexScreener. Prices in USD; changes in percent. This cannot trade. "
    "Memecoins are extremely risky: present data as research, never as financial advice."))


class DataError(RuntimeError):
    pass


_cache: dict[str, tuple[float, Any]] = {}


async def _request(method: str, url: str, *, params: dict | None = None, json: Any = None,
                   ttl: float = 15) -> Any:
    key = f"{method} {url} {sorted((params or {}).items())} {json}"
    hit = _cache.get(key)
    if hit and time.time() - hit[0] < ttl:
        return hit[1]
    source = "Hyperliquid" if url.startswith(HL) else "GeckoTerminal" if url.startswith(GT) else "DexScreener"
    try:
        async with httpx.AsyncClient(timeout=TIMEOUT, headers={
                "Accept": "application/json", "User-Agent": "fomo-family-mcp/1.0"}) as c:
            r = await c.request(method, url, params=params, json=json)
    except httpx.TimeoutException:
        raise DataError(f"{source} did not answer within {TIMEOUT:.0f}s. Try again.")
    except httpx.HTTPError as e:
        raise DataError(f"Could not reach {source}: {type(e).__name__}.")
    if r.status_code == 429:
        raise DataError(f"{source} rate limit hit (GeckoTerminal allows ~30 calls/min). Wait a minute.")
    if r.status_code == 404:
        raise DataError(f"{source}: not found.")
    if r.status_code >= 400:
        raise DataError(f"{source} error {r.status_code}: {r.text[:200]}")
    data = r.json()
    _cache[key] = (time.time(), data)
    return data


def _f(x: Any) -> float | None:
    try:
        return float(x)
    except (TypeError, ValueError):
        return None


def _r(x: float | None, nd: int = 2) -> float | None:
    return None if x is None else round(x, nd)


def _price(x: float | None) -> float | None:
    """Keep significant digits for tiny memecoin prices."""
    if x is None:
        return None
    return float(f"{x:.6g}") if abs(x) < 1 else round(x, 4)


# ---------------------------------------------------------------- perps (Hyperliquid)

def _category(dex: str, base: str) -> str:
    if not dex:
        return "crypto"
    if base in COMMODITIES:
        return "commodities"
    if base in FX:
        return "fx"
    if base in INDICES:
        return "indices"
    return "stocks"


async def _perp_rows(dex: str = "") -> list[dict]:
    body: dict[str, Any] = {"type": "metaAndAssetCtxs"}
    if dex:
        body["dex"] = dex
    meta, ctxs = await _request("POST", HL, json=body)
    rows = []
    for u, c in zip(meta["universe"], ctxs):
        if u.get("isDelisted"):
            continue
        name = u["name"]
        base = name.split(":", 1)[1] if ":" in name else name
        mark, prev = _f(c.get("markPx")), _f(c.get("prevDayPx"))
        oi = _f(c.get("openInterest"))
        funding = _f(c.get("funding"))
        rows.append({
            "symbol": name,
            "base": base,
            "venue": dex or "hyperliquid",
            "category": _category(dex, base),
            "max_leverage": u.get("maxLeverage"),
            "price": _price(mark),
            "change_24h_pct": _r((mark / prev - 1) * 100) if mark and prev else None,
            "volume_24h_usd": _r(_f(c.get("dayNtlVlm")), 0),
            "open_interest_usd": _r(oi * mark, 0) if oi is not None and mark else None,
            "funding_1h_pct": _r(funding * 100, 5) if funding is not None else None,
            "oracle_price": _price(_f(c.get("oraclePx"))),
        })
    return rows


async def _perp_dexes() -> list[str]:
    data = await _request("POST", HL, json={"type": "perpDexs"}, ttl=3600)
    return [d["name"] for d in data if d and d.get("name")]


SortPerps = Literal["volume", "open_interest", "change", "gainers", "losers"]


def _sort_perps(rows: list[dict], sort: SortPerps) -> list[dict]:
    if sort == "change":
        return sorted(rows, key=lambda r: abs(r["change_24h_pct"] or 0), reverse=True)
    key = {"volume": "volume_24h_usd", "open_interest": "open_interest_usd"}.get(sort, "change_24h_pct")
    rows = [r for r in rows if r.get(key) is not None]
    return sorted(rows, key=lambda r: r[key], reverse=sort != "losers")


@mcp.tool(annotations={"readOnlyHint": True, "openWorldHint": True})
async def perps_markets(category: Literal["all", "crypto", "stocks", "commodities", "fx", "indices"] = "crypto",
                        sort: SortPerps = "volume", limit: int = 20) -> dict:
    """List Hyperliquid perpetual markets like fomo's Perps tab.

    category: crypto = main Hyperliquid perps (BTC, ETH, HYPE, SOL…); stocks/commodities/fx/indices
    come from the HIP-3 'xyz' venue (TSLA, NVDA, GOLD, EUR, XYZ100…); all = both.
    sort: volume | open_interest | change (biggest absolute move) | gainers | losers.
    Returns price, 24h change %, 24h volume, open interest (USD), hourly funding %, max leverage.
    """
    limit = max(1, min(limit, 100))
    rows: list[dict] = []
    if category in ("all", "crypto"):
        rows += await _perp_rows("")
    if category != "crypto":
        rows += [r for r in await _perp_rows("xyz") if category == "all" or r["category"] == category]
    rows = _sort_perps(rows, sort)
    return {"category": category, "sort": sort, "count": len(rows[:limit]), "total_markets": len(rows),
            "markets": rows[:limit], "source": "Hyperliquid public info API"}


async def _find_perp(symbol: str) -> dict:
    s = symbol.strip().upper()
    if ":" in s:
        dex, base = s.split(":", 1)
        rows = await _perp_rows(dex.lower())
        target = f"{dex.lower()}:{base}"
        match = [r for r in rows if r["symbol"].lower() == target.lower()]
    else:
        match = [r for r in await _perp_rows("") if r["base"] == s]
        if not match:
            match = [r for r in await _perp_rows("xyz") if r["base"] == s]
        if not match:
            for dex in (await _perp_dexes()):
                if dex == "xyz":
                    continue
                match = [r for r in await _perp_rows(dex) if r["base"] == s]
                if match:
                    break
    if not match:
        raise DataError(f"No Hyperliquid perp named '{symbol}'. Use perps_markets or search_perps to find it "
                        "(stocks look like 'TSLA' or 'xyz:TSLA').")
    return match[0]


@mcp.tool(annotations={"readOnlyHint": True, "openWorldHint": True})
async def search_perps(query: str, limit: int = 15) -> dict:
    """Find perp markets whose symbol contains `query` across Hyperliquid and all HIP-3 venues
    (e.g. 'BTC', 'NVDA', 'GOLD')."""
    q = query.strip().upper()
    if not q:
        raise DataError("Give a symbol fragment, e.g. 'SOL' or 'TSLA'.")
    out: list[dict] = []
    for dex in [""] + await _perp_dexes():
        try:
            out += [r for r in await _perp_rows(dex) if q in r["base"]]
        except DataError:
            continue
    out.sort(key=lambda r: (r["base"] != q, -(r["volume_24h_usd"] or 0)))
    return {"query": query, "count": len(out[:limit]), "markets": out[:max(1, min(limit, 50))]}


Interval = Literal["1m", "5m", "15m", "1h", "4h", "1d"]
_IV_SECONDS = {"1m": 60, "5m": 300, "15m": 900, "1h": 3600, "4h": 14400, "1d": 86400}


@mcp.tool(annotations={"readOnlyHint": True, "openWorldHint": True})
async def perp_detail(symbol: str, interval: Interval = "1h", candles: int = 24) -> dict:
    """One perp in depth: price, 24h change, volume, open interest, funding, leverage, plus recent
    OHLCV candles. symbol: 'BTC', 'HYPE', 'TSLA' or venue-qualified 'xyz:TSLA'. candles: 0-200."""
    market = await _find_perp(symbol)
    out: dict[str, Any] = {"market": market}
    n = max(0, min(candles, 200))
    if n:
        end = int(time.time() * 1000)
        start = end - n * _IV_SECONDS[interval] * 1000
        raw = await _request("POST", HL, json={"type": "candleSnapshot", "req": {
            "coin": market["symbol"], "interval": interval, "startTime": start, "endTime": end}})
        out["candles"] = [{"t": time.strftime("%Y-%m-%d %H:%M", time.gmtime(c["t"] / 1000)),
                           "o": _price(_f(c["o"])), "h": _price(_f(c["h"])), "l": _price(_f(c["l"])),
                           "c": _price(_f(c["c"])), "v": _r(_f(c["v"]), 4)} for c in raw[-n:]]
        out["candle_interval"] = interval
        out["times_utc"] = True
    return out


# ---------------------------------------------------------------- memecoins (GeckoTerminal / DexScreener)

def _age(created: str | None) -> str | None:
    if not created:
        return None
    try:
        ts = time.mktime(time.strptime(created.replace("Z", ""), "%Y-%m-%dT%H:%M:%S")) - time.timezone
    except ValueError:
        return None
    s = max(0, time.time() - ts)
    for unit, size in (("d", 86400), ("h", 3600), ("m", 60)):
        if s >= size:
            return f"{int(s // size)}{unit}"
    return f"{int(s)}s"


def _gt_pools(payload: dict) -> list[dict]:
    tokens = {t["id"]: t["attributes"] for t in payload.get("included", []) if t.get("type") == "token"}
    rows = []
    for p in payload.get("data", []):
        a = p["attributes"]
        rel = p.get("relationships", {})
        base_id = rel.get("base_token", {}).get("data", {}).get("id", "")
        tok = tokens.get(base_id, {})
        tx = a.get("transactions", {}).get("h24", {})
        chg = a.get("price_change_percentage", {})
        rows.append({
            "symbol": tok.get("symbol") or a.get("name", "").split(" / ")[0],
            "name": tok.get("name"),
            "token_address": tok.get("address") or base_id.split("_", 1)[-1],
            "pool_address": a.get("address"),
            "dex": rel.get("dex", {}).get("data", {}).get("id"),
            "price_usd": _price(_f(a.get("base_token_price_usd"))),
            "market_cap_usd": _r(_f(a.get("market_cap_usd")) or _f(a.get("fdv_usd")), 0),
            "volume_24h_usd": _r(_f(a.get("volume_usd", {}).get("h24")), 0),
            "liquidity_usd": _r(_f(a.get("reserve_in_usd")), 0),
            "change_pct": {k: _f(chg.get(k)) for k in ("m5", "h1", "h6", "h24")},
            "txns_24h": {"buys": tx.get("buys"), "sells": tx.get("sells")},
            "age": _age(a.get("pool_created_at")),
            "created_at": a.get("pool_created_at"),
        })
    return rows


TokenSort = Literal["volume", "newest", "transactions"]
_GT_SORT = {"volume": "h24_volume_usd_desc", "transactions": "h24_tx_count_desc"}


async def _dex_pools(dex: str, sort: TokenSort, limit: int) -> list[dict]:
    if sort == "newest":
        # Per-DEX listings can't sort by age; scan Solana's newest pools and keep this DEX's.
        rows: list[dict] = []
        for page in (1, 2, 3, 4, 5):
            payload = await _request("GET", f"{GT}/networks/solana/new_pools",
                                     params={"include": "base_token,dex", "page": page}, ttl=30)
            rows += [r for r in _gt_pools(payload) if r["dex"] == dex]
            if len(rows) >= limit or not payload.get("data"):
                break
        return rows[:limit]
    payload = await _request("GET", f"{GT}/networks/solana/dexes/{dex}/pools",
                             params={"include": "base_token,dex", "sort": _GT_SORT[sort], "page": 1},
                             ttl=30)
    return _gt_pools(payload)[:limit]


@mcp.tool(annotations={"readOnlyHint": True, "openWorldHint": True})
async def trending_tokens(limit: int = 15, duration: Literal["5m", "1h", "6h", "24h"] = "1h") -> dict:
    """Trending Solana memecoins right now (like fomo's Trending tab), ranked by GeckoTerminal.
    Returns symbol, token address, price, market cap, 24h volume, liquidity, % changes, age."""
    payload = await _request("GET", f"{GT}/networks/solana/trending_pools",
                             params={"include": "base_token,dex", "duration": duration}, ttl=30)
    rows = _gt_pools(payload)[:max(1, min(limit, 20))]
    return {"duration": duration, "count": len(rows), "tokens": rows, "source": "GeckoTerminal"}


@mcp.tool(annotations={"readOnlyHint": True, "openWorldHint": True})
async def graduated_tokens(sort: TokenSort = "volume", limit: int = 15) -> dict:
    """pump.fun coins that graduated to PumpSwap (like fomo's Graduated tab).
    sort: volume (24h) | newest (just graduated) | transactions."""
    rows = await _dex_pools("pumpswap", sort, max(1, min(limit, 20)))
    return {"sort": sort, "count": len(rows), "tokens": rows, "source": "GeckoTerminal (PumpSwap pools)"}


@mcp.tool(annotations={"readOnlyHint": True, "openWorldHint": True})
async def bonding_tokens(sort: TokenSort = "volume", limit: int = 15) -> dict:
    """pump.fun coins still on the bonding curve, not yet graduated (like fomo's Bonding tab).
    sort: volume (24h) | newest (just launched) | transactions."""
    rows = await _dex_pools("pump-fun", sort, max(1, min(limit, 20)))
    return {"sort": sort, "count": len(rows), "tokens": rows, "source": "GeckoTerminal (pump.fun bonding curve)"}


def _ds_pair(p: dict) -> dict:
    base = p.get("baseToken", {})
    tx = p.get("txns", {}).get("h24", {})
    created = p.get("pairCreatedAt")
    return {
        "symbol": base.get("symbol"),
        "name": base.get("name"),
        "token_address": base.get("address"),
        "chain": p.get("chainId"),
        "dex": p.get("dexId"),
        "pair_address": p.get("pairAddress"),
        "quote": p.get("quoteToken", {}).get("symbol"),
        "price_usd": _price(_f(p.get("priceUsd"))),
        "market_cap_usd": _r(_f(p.get("marketCap")) or _f(p.get("fdv")), 0),
        "volume_24h_usd": _r(_f(p.get("volume", {}).get("h24")), 0),
        "liquidity_usd": _r(_f(p.get("liquidity", {}).get("usd")), 0),
        "change_pct": {k: p.get("priceChange", {}).get(k) for k in ("m5", "h1", "h6", "h24")},
        "txns_24h": {"buys": tx.get("buys"), "sells": tx.get("sells")},
        "age": _age(time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(created / 1000))) if created else None,
        "url": p.get("url"),
        "socials": [s.get("url") for s in (p.get("info") or {}).get("socials", []) if s.get("url")]
                   + [w.get("url") for w in (p.get("info") or {}).get("websites", []) if w.get("url")],
    }


@mcp.tool(annotations={"readOnlyHint": True, "openWorldHint": True})
async def search_tokens(query: str, chain: str = "solana", limit: int = 10) -> dict:
    """Search tokens by symbol, name or address (like fomo's search box), via DexScreener.
    chain: 'solana' (default), 'base', 'ethereum', 'bsc'… or 'any'. Ranked by 24h volume; best pair per token."""
    if not query.strip():
        raise DataError("Give a symbol, name or contract address to search for.")
    data = await _request("GET", f"{DS}/latest/dex/search", params={"q": query.strip()})
    pairs = [p for p in data.get("pairs") or [] if chain == "any" or p.get("chainId") == chain]
    best: dict[str, dict] = {}
    for p in pairs:
        addr = p.get("baseToken", {}).get("address")
        if addr and (addr not in best or (_f(p.get("liquidity", {}).get("usd")) or 0)
                     > (_f(best[addr].get("liquidity", {}).get("usd")) or 0)):
            best[addr] = p
    # Rank by real trading activity: copycat tickers often have liquidity but no volume.
    rows = sorted((_ds_pair(p) for p in best.values()),
                  key=lambda r: (-(r["volume_24h_usd"] or 0), -(r["liquidity_usd"] or 0)))
    rows = rows[:max(1, min(limit, 30))]
    return {"query": query, "chain": chain, "count": len(rows), "tokens": rows}


@mcp.tool(annotations={"readOnlyHint": True, "openWorldHint": True})
async def token_detail(address: str, chain: str = "solana") -> dict:
    """Full market picture for one token by contract address: every trading pair with price,
    market cap, volume, liquidity, % changes, buys/sells, age and social links (DexScreener)."""
    a = address.strip()
    if chain == "solana" and not SOL_ADDR.match(a):
        raise DataError("That is not a Solana address. Use search_tokens to find the address first.")
    pairs = await _request("GET", f"{DS}/tokens/v1/{chain}/{a}")
    if not pairs:
        raise DataError(f"No trading pairs found for {a} on {chain}.")
    rows = sorted((_ds_pair(p) for p in pairs), key=lambda r: -(r["liquidity_usd"] or 0))
    return {"token_address": a, "chain": chain, "main_pair": rows[0], "other_pairs": rows[1:10],
            "total_liquidity_usd": _r(sum(r["liquidity_usd"] or 0 for r in rows), 0)}


@mcp.tool(annotations={"readOnlyHint": True, "openWorldHint": True})
async def boosted_tokens(chain: str = "solana", limit: int = 15) -> dict:
    """Tokens with the most paid DexScreener boosts right now. Boosts are paid promotion,
    not organic demand; treat them as marketing signal, not quality signal."""
    data = await _request("GET", f"{DS}/token-boosts/top/v1", ttl=60)
    items = [d for d in data if chain == "any" or d.get("chainId") == chain][:max(1, min(limit, 30))]
    by_chain: dict[str, list[str]] = {}
    for d in items:
        by_chain.setdefault(d["chainId"], []).append(d["tokenAddress"])
    markets: dict[str, dict] = {}
    for ch, addrs in by_chain.items():
        for i in range(0, len(addrs), 30):
            for p in await _request("GET", f"{DS}/tokens/v1/{ch}/{','.join(addrs[i:i + 30])}") or []:
                addr = p.get("baseToken", {}).get("address")
                if addr and (addr not in markets or (_f(p.get("liquidity", {}).get("usd")) or 0)
                             > (markets[addr]["liquidity_usd"] or 0)):
                    markets[addr] = _ds_pair(p)
    rows = [{"boost_total": d.get("totalAmount"), **(markets.get(d["tokenAddress"])
             or {"token_address": d["tokenAddress"], "chain": d["chainId"]})} for d in items]
    return {"chain": chain, "count": len(rows), "tokens": rows}


def main() -> None:
    mcp.run()


if __name__ == "__main__":
    main()
