"""FOMO MCP server (read-only): lets Claude read your MemeCoin Radar app while you chat.

Every tool is a GET against the app's public API, signed in as a read-only visitor, so
nothing here can change settings, trade, delete or spend anything. Run it locally:

    python fomo_mcp.py                      # stdio, started by Claude Desktop / Claude Code

Settings (environment variables, all optional):
    FOMO_API_URL   API base, default https://memecoinrada.netlify.app/api
    FOMO_TIMEOUT   seconds per request, default 45 (a sleeping free server can be slow to wake)
"""
from __future__ import annotations

import asyncio
import os
import re
import time
from typing import Any, Literal

import logging

import httpx
from mcp.server.fastmcp import FastMCP

API = os.getenv("FOMO_API_URL", "https://memecoinrada.netlify.app/api").rstrip("/")
TIMEOUT = float(os.getenv("FOMO_TIMEOUT", "45"))
ADDR_RE = re.compile(r"^[1-9A-HJ-NP-Za-km-z]{32,44}$")
DROP_KEYS = {"image_url", "url", "components_json", "mock_notice", "checklist", "metrics", "pair_address",
             "created_ts", "graduated_ts", "token_id", "chain", "holders_capped", "ts"}
MAX_LIST = 25
logging.getLogger("httpx").setLevel(logging.WARNING)   # no request URLs in the logs

mcp = FastMCP("fomo-radar", instructions=(
    "Read-only access to the user's MemeCoin Radar (FOMO) app: Solana memecoin launches, risk and "
    "momentum scores, priority coins, whale distribution, verdicts and the paper-trading lab. "
    "All trading in the app is simulated. Treat everything as research, not financial advice, and "
    "say so when the user asks whether to buy. Scores: risk 0-100 (higher = riskier), momentum 0-100, "
    "sellp = selling pressure 0-100. Prices in USD."))

_token: dict[str, Any] = {"value": None, "at": 0.0}


class FomoError(RuntimeError):
    pass


async def _auth(client: httpx.AsyncClient) -> str:
    """A read-only visitor token (re-issued every 6 h)."""
    if _token["value"] and time.time() - _token["at"] < 6 * 3600:
        return _token["value"]
    r = await client.post(f"{API}/auth/demo")
    r.raise_for_status()
    _token.update(value=r.json()["token"], at=time.time())
    return _token["value"]


async def _get(path: str, params: dict | None = None, auth: bool = False) -> Any:
    params = {k: v for k, v in (params or {}).items() if v is not None and v != ""}
    async with httpx.AsyncClient(timeout=TIMEOUT, headers={"Accept": "application/json",
                                                         "User-Agent": "fomo-mcp/1.0"}) as c:
        headers = {"Authorization": f"Bearer {await _auth(c)}"} if auth else {}
        for attempt in range(2):
            try:
                r = await c.get(f"{API}{path}", params=params, headers=headers)
            except httpx.TimeoutException:
                if attempt:
                    raise FomoError("The FOMO server did not answer in time (it may be waking up). Try again.")
                continue
            if r.status_code == 401 and auth and not attempt:
                _token["value"] = None
                headers = {"Authorization": f"Bearer {await _auth(c)}"}
                continue
            if r.status_code == 404:
                raise FomoError("Not found. The coin may not be tracked by FOMO yet.")
            if r.status_code == 429:
                raise FomoError("FOMO's rate limit was hit. Wait a minute and try again.")
            if r.status_code >= 400:
                raise FomoError(f"FOMO API error {r.status_code}: {r.text[:200]}")
            return r.json()
    raise FomoError("No response from the FOMO server.")


def _slim(o: Any, max_list: int = MAX_LIST, depth: int = 0) -> Any:
    """Smaller answers: drop images/urls/raw blobs and empty values, round floats, cap lists."""
    if isinstance(o, dict):
        out = {}
        for k, v in o.items():
            if k in DROP_KEYS or v is None or v == [] or v == {} or v == "":
                continue
            out[k] = _slim(v, max_list, depth + 1)
        return out
    if isinstance(o, list):
        return [_slim(x, max_list, depth + 1) for x in o[:max_list]]
    if isinstance(o, float):
        return round(o, 6) if abs(o) < 1 else round(o, 2)
    return o


def _addr(a: str) -> str:
    a = (a or "").strip()
    if not ADDR_RE.match(a):
        raise FomoError("That does not look like a Solana token address. Use search_tokens to find one by name.")
    return a


Window = Literal["1h", "2h", "4h", "6h", "12h", "24h"]


# ------------------------------------------------------------------ market
@mcp.tool()
async def market_overview() -> dict:
    """Overall memecoin market mood: phase (risk-on/off), breadth, average trend, volume,
    liquidity and the top narratives."""
    return _slim(await _get("/market/overview"))


@mcp.tool()
async def launch_radar(tab: Literal["new", "bonding", "graduated", "hot"] = "hot",
                       min_mcap_usd: float = 0, limit: int = 15) -> dict:
    """Brand-new pump.fun coins seen seconds after creation.
    tab: new (newest first), bonding (still on the bonding curve, closest to graduating),
    graduated (just moved to a DEX), hot (under 6h old, busy, mostly buyers, no severe launch flags).
    Rows have age_secs, mcap, 5m/1h/6h/24h % change, 1h buys/sells, curve progress % and launch flags."""
    d = await _get("/launches", {"tab": tab, "limit": max(1, min(limit, 50)), "min_mcap": min_mcap_usd})
    st = d.get("status") or {}
    return _slim({"tab": tab, "total": d.get("total"), "counts": d.get("counts"),
                  "feed": {"live": st.get("connected"), "seconds_since_event": st.get("seconds_since_event"),
                           "sol_usd": st.get("sol_usd")},
                  "items": [{k: v for k, v in r.items() if k not in ("creator", "has_market_data")}
                            for r in d.get("items", [])]})


@mcp.tool()
async def priority_coins(window: Window = "4h", which: Literal["passing", "early"] = "passing",
                         limit: int = 10) -> dict:
    """FOMO's Priority coins: tracked coins that pass every quality gate (risk < 50, liquidity >= $15K,
    no distribution, no Avoid/Rug verdict, momentum or constructive events, no severe red flags),
    ranked by a transparent score. which=early: young coins whose momentum is climbing fast before
    they meet the full bar. Includes the 7-day track record of earlier picks (judged 1h and 4h later)."""
    d = await _get("/signals/priority", {"window": window, "limit": max(1, min(limit, 50))})
    items = d.get("items" if which == "passing" else "early", [])
    for r in items:
        r.pop("checks", None)
        r["factors"] = [f"{f.get('label')}: {f.get('points'):+}" for f in r.get("factors", [])]
        r["flags"] = [f.get("label") if isinstance(f, dict) else f for f in r.get("flags", [])]
    return _slim({"window": window, "list": which, "passed": d.get("passed"), "early_count": d.get("early_count"),
                  "checked": d.get("checked"), "gates": d.get("gates"), "track_record": d.get("track_record"),
                  "items": items,
                  "near_misses": [{"symbol": n.get("symbol"), "address": n.get("address"), "missed": n.get("missed")}
                                  for n in d.get("near_misses", [])][:8] if which == "passing" else None})


@mcp.tool()
async def trending_tokens(limit: int = 15, max_risk: float = 100, min_liquidity_usd: float = 0) -> dict:
    """Tokens FOMO tracks, ranked by trend score, with risk, FOMO, momentum, price change, volume and liquidity."""
    d = await _get("/tokens/trending", {"limit": max(1, min(limit, 50)), "max_risk": max_risk,
                                         "min_liquidity": min_liquidity_usd})
    return _slim({"count": d.get("count"), "items": d.get("items", [])[:limit]})


@mcp.tool()
async def new_coins(sort: Literal["newest", "momentum", "volume", "liquidity", "holder_growth", "buy_pressure",
                                  "price_increase", "vol_accel", "low_risk", "activity"] = "newest",
                    max_age_hours: float | None = None,
                    source: Literal["", "new_pool", "telegram", "promoted"] = "", limit: int = 15) -> dict:
    """Recently launched coins FOMO is tracking with full scores (fresh pools, Telegram calls, promoted)."""
    d = await _get("/tokens/new", {"sort": sort, "max_age_h": max_age_hours, "source": source,
                                    "limit": max(1, min(limit, 50))})
    return _slim({"items": d.get("items", [])[:limit]})


@mcp.tool()
async def distribution_watch(window: Window = "4h", limit: int = 15) -> dict:
    """Coins under selling pressure / whale distribution, worst first (sellp = selling pressure 0-100)."""
    d = await _get("/market/distribution", {"window": window, "limit": max(1, min(limit, 50))})
    return _slim({"window": window, "total": d.get("total"), "items": d.get("items", [])[:limit]})


@mcp.tool()
async def intel_feed(tone: Literal["", "constructive", "risk"] = "", since_minutes: int = 120,
                     address: str = "", limit: int = 20) -> dict:
    """The intelligence feed: momentum accelerations, distribution warnings, liquidity changes and other
    events, most important first. tone=constructive or risk filters; address limits it to one coin."""
    d = await _get("/signals/feed", {"tone": tone, "since_secs": since_minutes * 60,
                                      "address": _addr(address) if address else None,
                                      "limit": max(1, min(limit, 60))})
    return _slim({"items": [{k: e.get(k) for k in ("ts", "symbol", "address", "title", "detail", "severity",
                                                   "category", "importance", "confidence", "why", "concern")}
                            for e in d.get("items", [])]})


@mcp.tool()
async def narratives() -> dict:
    """Trending narratives (themes) and how fast mentions are growing."""
    d = await _get("/narratives/trending")
    return _slim(d if isinstance(d, dict) else {"items": d}, max_list=12)


@mcp.tool()
async def news(symbol: str = "", limit: int = 10) -> dict:
    """Recent crypto headlines (last 7 days); symbol keeps the ones that mention that coin."""
    return _slim(await _get("/news", {"symbol": symbol, "limit": max(1, min(limit, 40))}))


# ------------------------------------------------------------------ one coin
@mcp.tool()
async def search_tokens(query: str) -> dict:
    """Find a tracked coin by symbol, name or address."""
    return _slim(await _get("/search", {"q": query.strip()[:64]}))


@mcp.tool()
async def token_report(address: str) -> dict:
    """Everything FOMO knows about one coin: market data, scores, risk breakdown, holders, on-chain
    checks, launch forensics, creator history, the latest verdict and the AI analysis.
    Parts that are unavailable are listed under `missing`."""
    a = _addr(address)
    parts = {"token": f"/tokens/{a}", "risk": f"/tokens/{a}/risk", "verdict": f"/tokens/{a}/verdict",
             "holders": f"/tokens/{a}/holders", "onchain": f"/tokens/{a}/onchain-risk",
             "launch_forensics": f"/tokens/{a}/launch-forensics", "creator": f"/tokens/{a}/creator",
             "analysis": f"/tokens/{a}/analysis", "why_trending": f"/tokens/{a}/why"}
    res = await asyncio.gather(*(_get(p) for p in parts.values()), return_exceptions=True)
    out: dict[str, Any] = {"address": a}
    missing = []
    for name, r in zip(parts, res):
        if isinstance(r, Exception):
            if name == "token":
                raise r
            missing.append(name)
        else:
            out[name] = r
    out["missing"] = missing
    return _slim(out, max_list=12)


@mcp.tool()
async def price_history(address: str, timeframe: Literal["15m", "1h", "4h", "12h", "24h", "7d"] = "24h") -> dict:
    """Price history of a tracked coin as (timestamp, price) points."""
    d = await _get(f"/tokens/{_addr(address)}/history", {"metric": "price", "tf": timeframe})
    pts = d.get("points") or d.get("items") or d
    if isinstance(pts, list) and len(pts) > 60:            # keep ~60 evenly spaced points
        step = len(pts) / 60
        pts = [pts[int(i * step)] for i in range(60)] + [pts[-1]]
    return _slim({"address": address, "timeframe": timeframe, "points": pts}, max_list=80)


@mcp.tool()
async def launch_candles(address: str, timeframe: Literal["1m", "5m", "15m", "1h"] = "5m") -> dict:
    """OHLCV candles for a coin on the launch radar (t, o, h, l, c, v; newest last)."""
    d = await _get(f"/launches/{_addr(address)}/candles", {"tf": timeframe})
    return _slim(d, max_list=150)


# ------------------------------------------------------------------ verdicts and paper trading
@mcp.tool()
async def verdicts(rating: str = "", hours: float | None = None, limit: int = 20) -> dict:
    """Latest FOMO verdict per coin (e.g. STRONG_WATCH, WATCH, NEUTRAL, AVOID, RUG_RISK), newest first."""
    d = await _get("/verdicts", {"rating": rating.upper() or None, "hours": hours, "limit": max(1, min(limit, 50))})
    return _slim(d, max_list=limit)


@mcp.tool()
async def verdict_scorecard(hours: float | None = None, days: int = 7) -> dict:
    """How accurate FOMO's verdicts were afterwards, per rating and per source (AI vs rules)."""
    return _slim(await _get("/verdicts/scorecard", {"hours": hours, "days": None if hours else days}))


@mcp.tool()
async def paper_trading_status() -> dict:
    """The paper (simulated) trading account: cash, equity, P&L, win rate and open positions."""
    summary, positions = await asyncio.gather(_get("/paper/summary", auth=True),
                                              _get("/paper/positions", auth=True))
    return _slim({"summary": summary, "open_positions": positions})


@mcp.tool()
async def paper_trades(status: Literal["all", "open", "closed"] = "closed", limit: int = 20) -> dict:
    """Recent paper trades with entry/exit, P&L and exit reason."""
    return _slim(await _get("/paper/trades", {"status": status, "limit": max(1, min(limit, 100))}, auth=True),
                 max_list=limit)


@mcp.tool()
async def strategy_lab() -> dict:
    """Strategy Lab: every paper strategy (A-G and challengers) side by side, the ranking by
    risk-adjusted results, and the supervisor's pending proposals (retire / challenger / promote / fix)."""
    lab, review, sup = await asyncio.gather(_get("/paper/lab", auth=True),
                                            _get("/paper/lab/review", {"ai": "false"}, auth=True),
                                            _get("/paper/supervisor", auth=True), return_exceptions=True)
    ok = lambda r: None if isinstance(r, Exception) else r  # noqa: E731
    return _slim({"lab": ok(lab), "review": ok(review), "supervisor": ok(sup)}, max_list=20)


@mcp.tool()
async def watchlist() -> dict:
    """Coins on the app's watchlist with their latest intelligence and priority status."""
    d = await _get("/watchlist", auth=True)
    if not (d or {}).get("items"):
        return {"items": [], "note": "The watchlist is empty."}
    return _slim(d)


def main() -> None:
    mcp.run()


if __name__ == "__main__":
    main()
