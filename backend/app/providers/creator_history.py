"""Creator history — the earlier launches of a coin's creator, from REAL on-chain data.

Pipeline (cheapest first; every call is counted and budgeted by the caller):
  1. Helius: the creator wallet's most recent transactions (newest first).
     * getTransactionsForAddress (Developer plan+): 100 full transactions per call
     * free-plan fallback: getSignaturesForAddress + getTransaction per signature
  2. In those transactions, a LAUNCH is one the creator paid for that initialises a new
     token mint (spl-token / Token-2022 `initializeMint*`, top-level or inner). This works
     for pump.fun, letsbonk and any launchpad, with no program-specific parsing.
  3. For each launched mint, the creator's own buys/sells/moves in it, from his SOL and
     token balance changes in the same transactions. Newest-first sampling means everything
     he did AFTER a launch we found is also in the sample.
  4. DexScreener (batch, 30 per call): is each coin still trading, did it graduate off the
     bonding curve, liquidity and market cap now.
  5. GeckoTerminal daily candles for the biggest few: peak market cap.
Nothing is simulated. Unknowns stay None.
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass, field

import httpx

from ..config import (CREATOR_PEAK_LOOKUPS, CREATOR_RPC_TX, CREATOR_TX_SCAN, CREATOR_USE_GTFA,
                      DEXSCREENER_BASE, GECKOTERMINAL_BASE, HELIUS_API_KEY, HELIUS_RPC_URL)
from ..engine.dev_score import CoinRecord
from .launch_forensics import _keys, fee_payer, token_deltas

TOKEN_PROGRAMS = {"spl-token", "spl-token-2022"}
MINT_INIT = {"initializeMint", "initializeMint2"}
# DexScreener dex ids that are a bonding curve, not an open market
CURVE_DEXES = {"pumpfun", "launchlab", "moonshot", "boop", "believe", "letsbonk", "bonkfun"}
SOL_DUST = 0.002          # SOL changes smaller than this are fees, not trades


# ---------------------------------------------------------------- pure parsing
def _all_instructions(tx: dict):
    msg = ((tx.get("transaction") or {}).get("message") or {})
    yield from msg.get("instructions") or []
    for inner in (tx.get("meta") or {}).get("innerInstructions") or []:
        yield from inner.get("instructions") or []


def minted_in(tx: dict) -> list[str]:
    """Mints initialised in this transaction."""
    out = []
    for ix in _all_instructions(tx):
        p = ix.get("parsed")
        if ix.get("program") in TOKEN_PROGRAMS and isinstance(p, dict) and p.get("type") in MINT_INIT:
            mint = (p.get("info") or {}).get("mint")
            if mint and mint not in out:
                out.append(mint)
    return out


def sol_delta(tx: dict, wallet: str) -> float:
    """Change in the wallet's SOL balance in this transaction (fees included)."""
    meta = tx.get("meta") or {}
    keys = _keys(tx)
    try:
        i = keys.index(wallet)
        return (meta["postBalances"][i] - meta["preBalances"][i]) / 1e9
    except (ValueError, KeyError, IndexError, TypeError):
        return 0.0


def find_launches(txs: list[dict], creator: str) -> dict[str, dict]:
    """{mint: {launch_ts, signature}} for mints the creator launched (he paid the fee)."""
    out: dict[str, dict] = {}
    for tx in txs:
        if (tx.get("meta") or {}).get("err") or fee_payer(tx) != creator:
            continue
        for mint in minted_in(tx):
            ts = tx.get("blockTime")
            if ts is None:
                continue
            if mint not in out or ts < out[mint]["launch_ts"]:
                out[mint] = {"launch_ts": float(ts),
                             "signature": ((tx.get("transaction") or {}).get("signatures") or [None])[0]}
    return out


def build_records(txs: list[dict], creator: str, launches: dict[str, dict]) -> dict[str, CoinRecord]:
    """The creator's own activity in each coin he launched."""
    recs = {m: CoinRecord(mint=m, launch_ts=v["launch_ts"]) for m, v in launches.items()}
    for tx in sorted(txs, key=lambda t: t.get("blockTime") or 0):
        if (tx.get("meta") or {}).get("err") or tx.get("blockTime") is None:
            continue
        ts = float(tx["blockTime"])
        touched = [m for m in recs if any(
            b.get("mint") == m for side in ("preTokenBalances", "postTokenBalances")
            for b in (tx.get("meta") or {}).get(side) or [])]
        if not touched:
            continue
        dsol = sol_delta(tx, creator)
        for m in touched:
            r = recs[m]
            dtok = token_deltas(tx, m).get(creator, 0.0)
            since = max(0.0, ts - r.launch_ts)
            if dtok > 0 and dsol < -SOL_DUST:                     # bought
                r.sol_in += -dsol / len(touched)
                if r.first_buy_s is None or since < r.first_buy_s:
                    r.first_buy_s = since
            elif dtok < 0 and dsol > SOL_DUST:                    # sold
                r.sol_out += dsol / len(touched)
                if r.first_sell_s is None or since < r.first_sell_s:
                    r.first_sell_s = since
            elif dtok < 0:                                         # supply left, no SOL back
                r.moved_out = True
    for r in recs.values():
        r.sol_in, r.sol_out = round(r.sol_in, 6), round(r.sol_out, 6)
    return recs


def apply_market(recs: dict[str, CoinRecord], pairs: list[dict]) -> dict[str, dict]:
    """Fill graduation / liquidity / market cap from DexScreener pairs.
    Returns {mint: best pair} for the peak lookups."""
    by_mint: dict[str, list[dict]] = {}
    for p in pairs or []:
        mint = (p.get("baseToken") or {}).get("address")
        if mint in recs:
            by_mint.setdefault(mint, []).append(p)
    best = {}
    for mint, r in recs.items():
        ps = by_mint.get(mint) or []
        if not ps:
            r.graduated = False      # unknown to DexScreener: never reached an open market
            continue
        r.has_market = True
        r.graduated = any((p.get("dexId") or "").lower() not in CURVE_DEXES for p in ps)
        top = max(ps, key=lambda p: float((p.get("liquidity") or {}).get("usd") or 0))
        r.liquidity_usd = float((top.get("liquidity") or {}).get("usd") or 0)
        r.mcap_usd = float(top.get("marketCap") or top.get("fdv") or 0) or None
        r.symbol = ((top.get("baseToken") or {}).get("symbol") or "")[:24]
        best[mint] = top
    return best


def peak_from_ohlcv(payload: dict, supply: float) -> float | None:
    rows = ((((payload or {}).get("data") or {}).get("attributes") or {}).get("ohlcv_list")) or []
    highs = [float(r[2]) for r in rows if isinstance(r, list) and len(r) >= 3 and r[2]]
    return round(max(highs) * supply, 2) if highs and supply > 0 else None


@dataclass
class CreatorHistory:
    status: str                              # ok | error
    creator: str
    error: str = ""
    method: str = ""
    txs_scanned: int = 0
    history_complete: bool = False           # reached the wallet's first transaction
    career_start_ts: float | None = None
    records: list[CoinRecord] = field(default_factory=list)
    calls: dict = field(default_factory=dict)


# ---------------------------------------------------------------- I/O
_GTFA_UNAVAILABLE = False


class CreatorHistoryProvider:
    def __init__(self, api_key: str | None = None, timeout: float = 15.0,
                 transport: httpx.AsyncBaseTransport | None = None, pace: float = 0.12) -> None:
        self.key = HELIUS_API_KEY if api_key is None else api_key
        self.url = HELIUS_RPC_URL.format(key=self.key)
        self.timeout, self.transport, self.pace = timeout, transport, pace
        self.calls = {"helius": 0, "dexscreener": 0, "geckoterminal": 0}

    async def _rpc(self, client, method: str, params):
        self.calls["helius"] += 1
        r = await client.post(self.url, json={"jsonrpc": "2.0", "id": 1, "method": method, "params": params})
        r.raise_for_status()
        body = r.json()
        if body.get("error"):
            raise RuntimeError(f"{method}: {str(body['error'].get('message', 'rpc error'))[:120]}")
        if self.pace:
            await asyncio.sleep(self.pace)
        return body.get("result")

    async def recent_txs(self, client, wallet: str) -> tuple[list[dict], bool, str]:
        """(newest-first transactions, reached the wallet's first tx, method)."""
        global _GTFA_UNAVAILABLE
        if CREATOR_USE_GTFA and not _GTFA_UNAVAILABLE:
            try:
                txs, token = [], None
                while len(txs) < CREATOR_TX_SCAN:
                    opts = {"transactionDetails": "full", "sortOrder": "desc",
                            "limit": min(100, CREATOR_TX_SCAN - len(txs)),
                            "encoding": "jsonParsed", "maxSupportedTransactionVersion": 0}
                    if token:
                        opts["paginationToken"] = token
                    res = await self._rpc(client, "getTransactionsForAddress", [wallet, opts]) or {}
                    data = res.get("data")
                    if not isinstance(data, list):
                        raise RuntimeError("unexpected getTransactionsForAddress shape")
                    txs += data
                    token = res.get("paginationToken")
                    if not token or not data:
                        return txs, True, "gtfa"
                return txs, False, "gtfa"
            except (RuntimeError, httpx.HTTPStatusError):
                _GTFA_UNAVAILABLE = True
        sigs = await self._rpc(client, "getSignaturesForAddress", [wallet, {"limit": CREATOR_RPC_TX}]) or []
        complete = len(sigs) < CREATOR_RPC_TX
        txs = []
        for s in sigs:
            if s.get("err"):
                continue
            tx = await self._rpc(client, "getTransaction",
                                 [s["signature"], {"encoding": "jsonParsed", "maxSupportedTransactionVersion": 0}])
            if tx:
                txs.append(tx)
        return txs, complete, "rpc"

    async def _dex_pairs(self, client, mints: list[str]) -> list[dict]:
        pairs = []
        for i in range(0, len(mints), 30):
            self.calls["dexscreener"] += 1
            r = await client.get(f"{DEXSCREENER_BASE}/tokens/v1/solana/" + ",".join(mints[i:i + 30]))
            r.raise_for_status()
            body = r.json()
            pairs += body if isinstance(body, list) else (body.get("pairs") or [])
        return pairs

    async def _peak(self, client, pair: dict) -> float | None:
        pool = pair.get("pairAddress")
        price = float(pair.get("priceUsd") or 0)
        fdv = float(pair.get("fdv") or pair.get("marketCap") or 0)
        if not pool or price <= 0 or fdv <= 0:
            return None
        self.calls["geckoterminal"] += 1
        r = await client.get(f"{GECKOTERMINAL_BASE}/networks/solana/pools/{pool}/ohlcv/day",
                             params={"aggregate": 1, "limit": 1000, "currency": "usd"},
                             headers={"accept": "application/json"})
        r.raise_for_status()
        await asyncio.sleep(2.1)          # GeckoTerminal free tier: 30 calls/min
        return peak_from_ohlcv(r.json(), fdv / price)

    async def history(self, creator: str, exclude: set[str] | None = None) -> CreatorHistory:
        """Earlier launches of `creator` (mints in `exclude`, e.g. the coin being judged, skipped)."""
        if not self.key:
            return CreatorHistory(status="error", creator=creator, error="no HELIUS_API_KEY")
        try:
            async with httpx.AsyncClient(timeout=self.timeout, transport=self.transport) as client:
                txs, complete, method = await self.recent_txs(client, creator)
                launches = find_launches(txs, creator)
                career = min((v["launch_ts"] for v in launches.values()), default=None)
                for m in exclude or ():
                    launches.pop(m, None)
                recs = build_records(txs, creator, launches)
                if recs:
                    try:
                        best = apply_market(recs, await self._dex_pairs(client, list(recs)))
                        order = sorted(best, key=lambda m: -(recs[m].mcap_usd or 0)
                                       - (1e12 if recs[m].graduated else 0))
                        for m in order[:CREATOR_PEAK_LOOKUPS]:
                            try:
                                recs[m].peak_mcap_usd = await self._peak(client, best[m])
                            except Exception:
                                continue
                    except Exception:
                        pass          # market data is optional; conduct still scores
                return CreatorHistory(status="ok", creator=creator, method=method, txs_scanned=len(txs),
                                      history_complete=complete, career_start_ts=career,
                                      records=list(recs.values()), calls=dict(self.calls))
        except Exception as exc:
            msg = f"{type(exc).__name__}: {exc}".replace(self.key, "***") if self.key else str(exc)
            return CreatorHistory(status="error", creator=creator, error=msg[:200], calls=dict(self.calls))
