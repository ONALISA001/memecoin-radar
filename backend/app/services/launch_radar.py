"""Launch radar: see new Solana memecoins seconds after they are created.

How it works
  1. A live websocket (PumpPortal's free data feed) pushes every pump.fun coin creation
     and every migration (graduation from the bonding curve to a DEX). One connection,
     kept open, reconnects on its own with back-off.
  2. Every LAUNCH_ENRICH_SECONDS the newest and most active launches are looked up on
     DexScreener in batches of 30 (it lists bonding-curve pairs within minutes): buys and
     sells, volume, price changes, market cap, liquidity, image and pair address.
  3. Candles are fetched on demand from GeckoTerminal for the coin's pair and cached.
  4. Launches that look strong ("hot") are handed to the normal ingestion pipeline as
     fresh-pool candidates, so they get the full risk / momentum / verdict treatment.

Everything here lives in memory (a bounded book of recent launches); a restart starts a
fresh book. Nothing about the user is ever sent to these services.
"""
from __future__ import annotations

import asyncio
import json
import logging
import math
import time
from collections import OrderedDict, deque
from dataclasses import dataclass, field
from typing import Any

import httpx

from ..config import (DEXSCREENER_BASE, GECKOTERMINAL_BASE, LAUNCH_BOOK_MAX, LAUNCH_ENRICH_BATCHES,
                      LAUNCH_ENRICH_SECONDS, LAUNCH_HOT_SLOTS, LAUNCH_STREAM_ENABLED, LAUNCH_STREAM_URL,
                      LAUNCH_TRADE_SUBS)
from ..providers.base import RawToken

log = logging.getLogger("radar.launches")

# pump.fun bonding curve constants (tokens have 6 decimals, 1B supply)
SUPPLY = 1_000_000_000
V_TOKENS_START = 1_073_000_000
TOKENS_FOR_SALE = 793_100_000
K_CURVE = 30 * V_TOKENS_START              # virtual SOL x virtual tokens at launch
GRADUATED_DEXES = {"pumpswap", "raydium", "meteora", "orca", "raydium-cpmm", "raydium-clmm"}

HOT_MAX_AGE_H = 6
HOT_MIN_TXNS_H1 = 40
HOT_MIN_BUY_SHARE = 0.55
HOT_MIN_MCAP = 8_000
SERIAL_LAUNCHES_24H = 3
DEV_BIG_BUY_PCT = 10.0


@dataclass
class Launch:
    mint: str
    symbol: str = ""
    name: str = ""
    uri: str = ""
    creator: str = ""
    created_ts: float = 0.0
    pool: str = "pump"
    bonding_key: str = ""
    mcap_sol: float = 0.0
    v_tokens: float | None = None
    dev_buy_tokens: float = 0.0
    dev_buy_sol: float = 0.0
    dev_sold_sol: float = 0.0
    graduated: bool = False
    graduated_ts: float | None = None
    pair_address: str = ""
    dex_id: str = ""
    image_url: str = ""
    dex_ts: float = 0.0                    # last DexScreener refresh (0 = never)
    dex: dict[str, Any] = field(default_factory=dict)
    live_buys: int = 0
    live_sells: int = 0
    live_buy_sol: float = 0.0
    live_sell_sol: float = 0.0
    traders: set = field(default_factory=set)
    last_trade_ts: float = 0.0
    peak_mcap_usd: float = 0.0


BOOK: "OrderedDict[str, Launch]" = OrderedDict()
CREATORS: dict[str, deque] = {}
STATUS: dict[str, Any] = {"enabled": LAUNCH_STREAM_ENABLED, "connected": False, "connected_since": None,
                          "last_event_ts": None, "creates": 0, "trades": 0, "migrations": 0,
                          "reconnects": 0, "last_error": None, "enriched_ts": None, "sol_usd": None}
_CANDLE_CACHE: dict[tuple[str, str], tuple[float, list]] = {}


def reset() -> None:
    """Empty the book (tests)."""
    BOOK.clear(); CREATORS.clear(); _CANDLE_CACHE.clear()
    STATUS.update(creates=0, trades=0, migrations=0, sol_usd=None, last_event_ts=None)


def _f(v: Any) -> float:
    try:
        x = float(v)
        return x if math.isfinite(x) else 0.0
    except (TypeError, ValueError):
        return 0.0


# ------------------------------------------------------------------ curve maths
def progress_from_v_tokens(v_tokens: float) -> float:
    """Share (0-100) of the bonding curve sold. 100 = ready to graduate."""
    return round(max(0.0, min(100.0, (V_TOKENS_START - v_tokens) / TOKENS_FOR_SALE * 100)), 1)


def progress_from_mcap_sol(mcap_sol: float) -> float | None:
    """On the curve price = K / v_tokens^2, so market cap (SOL) pins down v_tokens."""
    if mcap_sol <= 0:
        return None
    return progress_from_v_tokens(math.sqrt(K_CURVE * SUPPLY / mcap_sol))


def progress(l: Launch) -> float | None:
    if l.graduated:
        return 100.0
    if l.v_tokens:
        return progress_from_v_tokens(l.v_tokens)
    if l.mcap_sol:
        return progress_from_mcap_sol(l.mcap_sol)
    mc = l.dex.get("mcap_usd")
    sol = STATUS.get("sol_usd")
    return progress_from_mcap_sol(mc / sol) if mc and sol else None


# ------------------------------------------------------------------ stream messages
def _trim(now: float) -> None:
    while len(BOOK) > LAUNCH_BOOK_MAX:
        BOOK.popitem(last=False)
    for c in [c for c, q in CREATORS.items() if q and q[-1] < now - 86400]:
        CREATORS.pop(c, None)


def handle_message(m: dict, now: float | None = None) -> str | None:
    """Apply one stream message. Returns what it was (create / buy / sell / migrate) or None."""
    now = now or time.time()
    if not isinstance(m, dict):
        return None
    kind = str(m.get("txType") or "").lower()
    mint = m.get("mint")
    if not mint or not isinstance(mint, str):
        return None
    STATUS["last_event_ts"] = now
    if kind == "create":
        l = BOOK.get(mint) or Launch(mint=mint)
        l.symbol = str(m.get("symbol") or "")[:24]
        l.name = str(m.get("name") or "")[:64]
        l.uri = str(m.get("uri") or "")[:300]
        l.creator = str(m.get("traderPublicKey") or "")
        l.created_ts = l.created_ts or now
        l.pool = str(m.get("pool") or "pump")
        l.bonding_key = str(m.get("bondingCurveKey") or "")
        l.mcap_sol = _f(m.get("marketCapSol"))
        l.v_tokens = _f(m.get("vTokensInBondingCurve")) or None
        l.dev_buy_tokens = _f(m.get("initialBuy"))
        l.dev_buy_sol = _f(m.get("solAmount"))
        BOOK[mint] = l
        BOOK.move_to_end(mint)
        if l.creator:
            q = CREATORS.setdefault(l.creator, deque(maxlen=50))
            q.append(now)
        STATUS["creates"] += 1
        _trim(now)
        return "create"
    l = BOOK.get(mint)
    if kind in ("buy", "sell"):
        if l is None:
            return None
        sol = _f(m.get("solAmount"))
        trader = m.get("traderPublicKey")
        if kind == "buy":
            l.live_buys += 1; l.live_buy_sol += sol
        else:
            l.live_sells += 1; l.live_sell_sol += sol
            if trader and trader == l.creator:
                l.dev_sold_sol += sol
        if trader and len(l.traders) < 2000:
            l.traders.add(trader)
        if m.get("marketCapSol") is not None:
            l.mcap_sol = _f(m.get("marketCapSol"))
        if m.get("vTokensInBondingCurve") is not None:
            l.v_tokens = _f(m.get("vTokensInBondingCurve")) or l.v_tokens
        l.last_trade_ts = now
        STATUS["trades"] += 1
        return kind
    if kind in ("migrate", "migration", "complete") or m.get("method") == "migrate":
        if l is None:
            return None
        l.graduated, l.graduated_ts = True, l.graduated_ts or now
        STATUS["migrations"] += 1
        return "migrate"
    return None


# ------------------------------------------------------------------ DexScreener enrichment
def apply_dex_pairs(pairs: list[dict], now: float | None = None) -> int:
    """Fold DexScreener pairs into the book. Picks a graduated (DEX) pair over the curve."""
    now = now or time.time()
    best: dict[str, dict] = {}
    for p in pairs or []:
        addr = ((p or {}).get("baseToken") or {}).get("address")
        if addr not in BOOK:
            continue
        cur = best.get(addr)
        rank = (p.get("dexId") in GRADUATED_DEXES, _f((p.get("liquidity") or {}).get("usd")))
        if cur is None or rank > (cur.get("dexId") in GRADUATED_DEXES, _f((cur.get("liquidity") or {}).get("usd"))):
            best[addr] = p
        pu, pn = _f(p.get("priceUsd")), _f(p.get("priceNative"))
        if pu and pn and (p.get("quoteToken") or {}).get("symbol") == "SOL":
            STATUS["sol_usd"] = round(pu / pn, 2)
    for addr, p in best.items():
        l = BOOK[addr]
        tx, vol, ch = p.get("txns") or {}, p.get("volume") or {}, p.get("priceChange") or {}
        mcap = _f(p.get("marketCap") or p.get("fdv"))
        l.dex = {
            "price_usd": _f(p.get("priceUsd")), "mcap_usd": mcap,
            "liq_usd": _f((p.get("liquidity") or {}).get("usd")),
            **{f"vol_{w}": _f(vol.get(w)) for w in ("m5", "h1", "h6", "h24")},
            **{f"buys_{w}": int(_f((tx.get(w) or {}).get("buys"))) for w in ("m5", "h1", "h24")},
            **{f"sells_{w}": int(_f((tx.get(w) or {}).get("sells"))) for w in ("m5", "h1", "h24")},
            **{f"chg_{w}": (_f(ch[w]) if w in ch else None) for w in ("m5", "h1", "h6", "h24")},
        }
        l.peak_mcap_usd = max(l.peak_mcap_usd, mcap)
        l.pair_address = p.get("pairAddress") or l.pair_address
        l.dex_id = p.get("dexId") or l.dex_id
        img = (p.get("info") or {}).get("imageUrl")
        if img:
            l.image_url = img
        if l.dex_id in GRADUATED_DEXES and not l.graduated:
            l.graduated, l.graduated_ts = True, now
        if not l.symbol:
            l.symbol = ((p.get("baseToken") or {}).get("symbol") or "")[:24]
            l.name = ((p.get("baseToken") or {}).get("name") or "")[:64]
        l.dex_ts = now
    STATUS["enriched_ts"] = now
    return len(best)


def select_for_enrich(now: float | None = None, n: int | None = None) -> list[str]:
    """Which mints to refresh next. Graduated and active launches first, then the newest
    ones at least a minute old (DexScreener needs a moment to list a pair). The least
    recently refreshed go first so every candidate gets its turn."""
    now = now or time.time()
    n = n or LAUNCH_ENRICH_BATCHES * 30
    pri: list[tuple[tuple, str]] = []
    for l in BOOK.values():
        age = now - l.created_ts
        if age < 60 or age > 24 * 3600:
            continue
        if now - l.dex_ts < LAUNCH_ENRICH_SECONDS:
            continue
        active = (l.dex.get("buys_h1", 0) + l.dex.get("sells_h1", 0)) or (l.live_buys + l.live_sells)
        dead = l.dex_ts and active == 0 and age > 1800
        if dead:
            continue
        tier = 0 if l.graduated else 1 if active >= 20 else 2 if age < 900 else 3
        pri.append(((tier, l.dex_ts, -l.created_ts), l.mint))
    pri.sort()
    return [m for _, m in pri[:n]]


# ------------------------------------------------------------------ derived views
def _txns_h1(l: Launch) -> tuple[int, int]:
    if l.dex:
        return l.dex.get("buys_h1", 0), l.dex.get("sells_h1", 0)
    return l.live_buys, l.live_sells


def flags(l: Launch, now: float | None = None) -> list[dict]:
    """Quick launch checks. Severe ones keep a coin out of Hot."""
    now = now or time.time()
    out: list[dict] = []
    recent = [t for t in CREATORS.get(l.creator, ()) if t >= now - 86400]
    if len(recent) >= SERIAL_LAUNCHES_24H:
        out.append({"id": "serial", "label": f"creator launched {len(recent)} coins in 24h", "severe": True})
    dev_pct = l.dev_buy_tokens / SUPPLY * 100
    if dev_pct >= DEV_BIG_BUY_PCT:
        out.append({"id": "dev_big", "label": f"creator bought {dev_pct:.0f}% at launch", "severe": dev_pct >= 20})
    if l.dev_sold_sol > 0:
        out.append({"id": "dev_sold", "label": f"creator sold {l.dev_sold_sol:.2f} SOL", "severe": True})
    b, s = _txns_h1(l)
    if b + s >= 20 and s > b * 1.5:
        out.append({"id": "sellers", "label": "sellers outnumber buyers", "severe": False})
    mc, peak = l.dex.get("mcap_usd", 0), l.peak_mcap_usd
    if peak >= 20_000 and mc and mc < peak * 0.5:
        out.append({"id": "dumped", "label": f"down {100 - mc / peak * 100:.0f}% from its peak", "severe": True})
    return out


def hot_score(l: Launch, now: float | None = None) -> float | None:
    """0-100 for launches worth a closer look; None when it does not qualify."""
    now = now or time.time()
    age_h = (now - l.created_ts) / 3600
    b, s = _txns_h1(l)
    mc = l.dex.get("mcap_usd", 0) or (l.mcap_sol * (STATUS.get("sol_usd") or 0))
    if age_h > HOT_MAX_AGE_H or b + s < HOT_MIN_TXNS_H1 or mc < HOT_MIN_MCAP:
        return None
    share = b / (b + s)
    if share < HOT_MIN_BUY_SHARE or any(f["severe"] for f in flags(l, now)):
        return None
    pr = progress(l) or 0
    chg = l.dex.get("chg_h1") or 0
    score = (min(1, (b + s) / 400) * 30 + (share - 0.5) * 2 * 25 + pr / 100 * 20
             + max(0, min(1, chg / 100)) * 15 + (10 if l.graduated else 0) + max(0, 1 - age_h / HOT_MAX_AGE_H) * 10)
    return round(min(100.0, score), 1)


def row(l: Launch, now: float | None = None) -> dict:
    now = now or time.time()
    b, s = _txns_h1(l)
    sol = STATUS.get("sol_usd") or 0
    mc = l.dex.get("mcap_usd") or (round(l.mcap_sol * sol, 0) if sol and l.mcap_sol else None)
    return {
        "mint": l.mint, "symbol": l.symbol, "name": l.name, "image_url": l.image_url or None,
        "creator": l.creator, "age_secs": int(now - l.created_ts), "created_ts": int(l.created_ts),
        "mcap_usd": mc, "liq_usd": l.dex.get("liq_usd"), "price_usd": l.dex.get("price_usd"),
        "vol_h1": l.dex.get("vol_h1"), "vol_h24": l.dex.get("vol_h24"),
        "buys_h1": b, "sells_h1": s, "buys_m5": l.dex.get("buys_m5"), "sells_m5": l.dex.get("sells_m5"),
        "chg_m5": l.dex.get("chg_m5"), "chg_h1": l.dex.get("chg_h1"), "chg_h6": l.dex.get("chg_h6"),
        "chg_h24": l.dex.get("chg_h24"), "progress": progress(l), "graduated": l.graduated,
        "graduated_ts": int(l.graduated_ts) if l.graduated_ts else None,
        "dex": l.dex_id or l.pool, "pair_address": l.pair_address or l.bonding_key or None,
        "has_market_data": bool(l.dex), "dev_buy_pct": round(l.dev_buy_tokens / SUPPLY * 100, 1),
        "flags": flags(l, now), "hot_score": hot_score(l, now),
    }


TABS = ("new", "bonding", "graduated", "hot")


def listing(tab: str = "new", limit: int = 50, now: float | None = None, min_mcap: float = 0) -> dict:
    now = now or time.time()
    ls = list(BOOK.values())
    if tab == "new":
        ls = sorted(ls, key=lambda l: -l.created_ts)
    elif tab == "bonding":
        ls = [l for l in ls if not l.graduated and now - l.created_ts < 6 * 3600 and l.dex_ts
              and sum(_txns_h1(l)) > 0]
        ls.sort(key=lambda l: -(progress(l) or 0))
    elif tab == "graduated":
        ls = sorted((l for l in ls if l.graduated), key=lambda l: -(l.graduated_ts or 0))
    elif tab == "hot":
        scored = [(hot_score(l, now), l) for l in ls]
        ls = [l for sc, l in sorted(((sc, l) for sc, l in scored if sc is not None), key=lambda x: -x[0])]
    rows = [row(l, now) for l in ls]
    if min_mcap:
        rows = [r for r in rows if (r["mcap_usd"] or 0) >= min_mcap]
    hour = [l for l in BOOK.values() if now - l.created_ts < 3600]
    return {"tab": tab, "items": rows[:limit], "total": len(rows), "status": status(now),
            "counts": {"launched_1h": len(hour), "graduated_1h": sum(1 for l in BOOK.values()
                                                                    if l.graduated_ts and now - l.graduated_ts < 3600),
                       "tracked": len(BOOK)}}


def status(now: float | None = None) -> dict:
    now = now or time.time()
    s = dict(STATUS)
    s["seconds_since_event"] = int(now - s["last_event_ts"]) if s.get("last_event_ts") else None
    s["source"] = "PumpPortal live feed + DexScreener + GeckoTerminal"
    return s


def hot_raw_tokens(n: int | None = None, now: float | None = None) -> list[RawToken]:
    """Hot launches as fresh-pool candidates for the main ingestion pipeline."""
    now = now or time.time()
    n = LAUNCH_HOT_SLOTS if n is None else n
    scored = sorted(((hot_score(l, now), l) for l in BOOK.values()), key=lambda x: -(x[0] or -1))
    return [RawToken(address=l.mint, chain="solana", name=l.name, symbol=l.symbol, image_url=l.image_url,
                     dex=l.dex_id, pair_address=l.pair_address, deployer=l.creator,
                     token_created_at=int(l.created_ts * 1000), extra={"source": "new_pool", "via": "launch_radar"})
            for sc, l in scored if sc is not None][:n]


# ------------------------------------------------------------------ candles
CANDLE_TF = {"1m": ("minute", 1, 120), "5m": ("minute", 5, 144), "15m": ("minute", 15, 96), "1h": ("hour", 1, 72)}


def parse_ohlcv(payload: dict) -> list[dict]:
    rows = (((payload or {}).get("data") or {}).get("attributes") or {}).get("ohlcv_list") or []
    out = [{"t": int(r[0]), "o": _f(r[1]), "h": _f(r[2]), "l": _f(r[3]), "c": _f(r[4]), "v": _f(r[5])}
           for r in rows if isinstance(r, (list, tuple)) and len(r) >= 6]
    return sorted(out, key=lambda c: c["t"])


async def candles(mint: str, tf: str = "5m", transport: httpx.AsyncBaseTransport | None = None) -> dict:
    l = BOOK.get(mint)
    pair = (l.pair_address or l.bonding_key) if l else None
    if tf not in CANDLE_TF:
        raise ValueError("bad timeframe")
    if not pair:
        return {"mint": mint, "tf": tf, "candles": [], "note": "No trading pair known for this coin yet."}
    key = (pair, tf)
    hit = _CANDLE_CACHE.get(key)
    if hit and time.time() - hit[0] < 45:
        return {"mint": mint, "tf": tf, "candles": hit[1], "pair": pair}
    unit, agg, limit = CANDLE_TF[tf]
    async with httpx.AsyncClient(timeout=10, transport=transport, headers={"Accept": "application/json"}) as c:
        r = await c.get(f"{GECKOTERMINAL_BASE}/networks/solana/pools/{pair}/ohlcv/{unit}",
                        params={"aggregate": agg, "limit": limit, "currency": "usd"})
    if r.status_code == 404:
        return {"mint": mint, "tf": tf, "candles": [], "pair": pair,
                "note": "Candles appear a few minutes after the first trades."}
    r.raise_for_status()
    cs = parse_ohlcv(r.json())
    _CANDLE_CACHE[key] = (time.time(), cs)
    if len(_CANDLE_CACHE) > 300:
        for k in sorted(_CANDLE_CACHE, key=lambda k: _CANDLE_CACHE[k][0])[:100]:
            _CANDLE_CACHE.pop(k, None)
    return {"mint": mint, "tf": tf, "candles": cs, "pair": pair}


# ------------------------------------------------------------------ background loops
def _trade_keys(now: float) -> list[str]:
    """Launches whose every trade we want live: hot ones, then active graduates/bonding."""
    hot = [r.address for r in hot_raw_tokens(LAUNCH_TRADE_SUBS, now)]
    rest = sorted((l for l in BOOK.values() if l.mint not in hot and now - l.created_ts < 3 * 3600
                   and sum(_txns_h1(l)) >= 20), key=lambda l: -sum(_txns_h1(l)))
    return (hot + [l.mint for l in rest])[:LAUNCH_TRADE_SUBS]


async def stream_loop() -> None:
    if not LAUNCH_STREAM_ENABLED:
        log.info("launch radar: off (LAUNCH_STREAM_ENABLED=0)")
        return
    import websockets
    backoff = 2
    while True:
        subs: set[str] = set()
        try:
            async with websockets.connect(LAUNCH_STREAM_URL, open_timeout=20, ping_interval=30,
                                          max_size=2 ** 20) as ws:
                await ws.send(json.dumps({"method": "subscribeNewToken"}))
                await ws.send(json.dumps({"method": "subscribeMigration"}))
                STATUS.update(connected=True, connected_since=time.time(), last_error=None)
                log.info("launch radar: connected")
                backoff = 2
                next_subs = time.time() + 30
                while True:
                    try:
                        raw = await asyncio.wait_for(ws.recv(), timeout=10)
                        try:
                            handle_message(json.loads(raw))
                        except (ValueError, TypeError):
                            pass
                    except asyncio.TimeoutError:
                        pass
                    now = time.time()
                    if now >= next_subs:            # keep live trade subscriptions on the best launches
                        want = set(_trade_keys(now))
                        add, drop = list(want - subs), list(subs - want)
                        if drop:
                            await ws.send(json.dumps({"method": "unsubscribeTokenTrade", "keys": drop}))
                        if add:
                            await ws.send(json.dumps({"method": "subscribeTokenTrade", "keys": add}))
                        subs = want
                        next_subs = now + 60
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001  the feed must never take the app down
            STATUS.update(connected=False, last_error=f"{type(exc).__name__}: {str(exc)[:120]}")
            STATUS["reconnects"] += 1
            log.warning("launch radar: stream dropped (%s); retrying in %ss", type(exc).__name__, backoff)
        STATUS["connected"] = False
        await asyncio.sleep(backoff)
        backoff = min(60, backoff * 2)


async def enrich_once(transport: httpx.AsyncBaseTransport | None = None) -> int:
    mints = select_for_enrich()
    if not mints:
        return 0
    pairs: list[dict] = []
    async with httpx.AsyncClient(timeout=10, transport=transport) as c:
        for i in range(0, len(mints), 30):
            r = await c.get(f"{DEXSCREENER_BASE}/tokens/v1/solana/{','.join(mints[i:i + 30])}")
            if r.status_code == 200:
                d = r.json()
                pairs += d if isinstance(d, list) else (d.get("pairs") or [])
    now = time.time()
    for m in mints:                              # checked, even when not listed yet
        if m in BOOK and not BOOK[m].dex_ts:
            BOOK[m].dex_ts = now if now - BOOK[m].created_ts > 600 else 0.0
    return apply_dex_pairs(pairs, now)


async def enrich_loop() -> None:
    if not LAUNCH_STREAM_ENABLED:
        return
    while True:
        await asyncio.sleep(LAUNCH_ENRICH_SECONDS)
        try:
            await enrich_once()
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001
            log.warning("launch radar: enrich failed (%s)", type(exc).__name__)
