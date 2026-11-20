"""Creator (dev) track record: REAL history of every token a wallet created.

GMGN-style "dev" view, built on our own data instead of GMGN's:

  1. Recent transactions OF THE CREATOR WALLET (newest first, Helius):
       * getTransactionsForAddress (Developer plan+, 100 full transactions per call)
       * free-plan fallback: getSignaturesForAddress + getTransaction per signature
  2. A transaction is a token CREATION when the creator paid the fee and it ran an
     SPL / Token-2022 `initializeMint` or `initializeMint2` (outer or inner, which
     covers pump.fun, LaunchLab, Moonshot... and plain mints).
  3. What each of those tokens looks like NOW (DexScreener, 30 mints per call, free):
       graduated  — has a pool on a real AMM (PumpSwap, Raydium, Meteora, Orca)
       alive      — still has $10k+ liquidity or a $50k+ market cap
       dead       — older than a day with under $1k liquidity, or no market at all
       faded      — older than a day, somewhere in between
       young      — under a day old: too early to judge, left out of the rates

Only the most recent CREATOR_TX_LIMIT transactions are read, so for a very busy
wallet the counts are "at least" (history_truncated=True). Everything here is real
chain or market data, and it is evidence, not proof: a dead coin is not always a rug.
"""
from __future__ import annotations

import asyncio
import time
from dataclasses import asdict, dataclass, field

import httpx

from ..config import (CREATOR_TX_LIMIT, CREATOR_USE_GTFA, DEXSCREENER_BASE, HELIUS_API_KEY,
                      HELIUS_RPC_URL)
from .launch_forensics import fee_payer

MINT_INIT = {"initializeMint", "initializeMint2"}
TOKEN_PROGRAMS = {"spl-token", "spl-token-2022"}
GRADUATED_DEXES = {"pumpswap", "raydium", "meteora", "orca"}
YOUNG_H = 24.0
ALIVE_LIQ, ALIVE_MCAP = 10_000.0, 50_000.0
DEAD_LIQ = 1_000.0
DEX_BATCH = 30


@dataclass
class CreatorHistory:
    status: str                          # ok | error | no_data
    error: str = ""
    method: str = ""                     # gtfa | rpc
    txs_scanned: int = 0
    history_truncated: bool = False      # more history exists than was read
    launches: list[dict] = field(default_factory=list)   # newest first

    def as_dict(self) -> dict:
        return asdict(self)


# ---------------------------------------------------------------- pure analysis
def _instructions(tx: dict) -> list[dict]:
    msg = ((tx.get("transaction") or {}).get("message") or {})
    ins = list(msg.get("instructions") or [])
    for inner in (tx.get("meta") or {}).get("innerInstructions") or []:
        ins.extend(inner.get("instructions") or [])
    return [i for i in ins if isinstance(i, dict)]


def find_creations(txs: list[dict], creator: str) -> list[dict]:
    """Tokens `creator` created in these transactions: [{mint, time, slot}], newest first."""
    out: dict[str, dict] = {}
    for tx in txs:
        if not tx or (tx.get("meta") or {}).get("err") or fee_payer(tx) != creator:
            continue
        for i in _instructions(tx):
            p = i.get("parsed")
            if i.get("program") not in TOKEN_PROGRAMS or not isinstance(p, dict) or p.get("type") not in MINT_INIT:
                continue
            mint = (p.get("info") or {}).get("mint")
            if mint and mint not in out:
                out[mint] = {"mint": mint, "time": tx.get("blockTime"), "slot": tx.get("slot")}
    return sorted(out.values(), key=lambda c: -(c["time"] or 0))


def classify(launch: dict, pairs: list[dict], now: float | None = None) -> dict:
    """Adds market facts and a `state` to one launch from its DexScreener pairs."""
    now = now or time.time()
    liq = sum(float((p.get("liquidity") or {}).get("usd") or 0) for p in pairs)
    mcap = max((float(p.get("marketCap") or p.get("fdv") or 0) for p in pairs), default=0.0)
    dexes = sorted({str(p.get("dexId") or "").lower() for p in pairs} - {""})
    sym = next((((p.get("baseToken") or {}).get("symbol")) for p in pairs
                if (p.get("baseToken") or {}).get("address") == launch["mint"]), None)
    age_h = (now - launch["time"]) / 3600 if launch.get("time") else None
    graduated = bool(set(dexes) & GRADUATED_DEXES)
    if age_h is not None and age_h < YOUNG_H:
        state = "young"
    elif liq >= ALIVE_LIQ or mcap >= ALIVE_MCAP:
        state = "alive"
    elif not pairs or liq < DEAD_LIQ:
        state = "dead"
    else:
        state = "faded"
    return {**launch, "symbol": sym, "liquidity_usd": round(liq, 0), "mcap_usd": round(mcap, 0),
            "dexes": dexes, "graduated": graduated, "state": state,
            "age_hours": None if age_h is None else round(age_h, 1)}


def summarise(launches: list[dict], exclude_mint: str | None = None, now: float | None = None,
              truncated: bool = False) -> dict:
    """Counts and a label for the creator's OTHER launches (the token being judged is excluded)."""
    now = now or time.time()
    prior = [l for l in launches if l["mint"] != exclude_mint]
    matured = [l for l in prior if l["state"] != "young"]
    n = len(matured)
    dead = sum(1 for l in matured if l["state"] == "dead")
    alive = sum(1 for l in matured if l["state"] == "alive")
    grad = sum(1 for l in prior if l["graduated"])
    last24 = sum(1 for l in launches if l.get("time") and now - l["time"] < 86400)
    dead_rate = dead / n if n else None
    if last24 >= 5:
        label = "SERIAL_RUGGER"
    elif n >= 3 and dead_rate >= 0.8 and grad == 0:
        label = "SERIAL_RUGGER"
    elif n >= 2 and dead_rate >= 0.6:
        label = "RISKY"
    elif not prior and not truncated:
        label = "FIRST_LAUNCH"
    elif n == 0:
        label = "UNPROVEN"            # earlier launches exist but are all under a day old
    elif grad >= 1 and alive >= 1 and dead_rate <= 0.5:
        label = "PROVEN"
    else:
        label = "MIXED"
    best = max(prior, key=lambda l: l["mcap_usd"] or 0, default=None)
    return {"prior_launches": len(prior), "at_least": truncated, "matured": n, "dead": dead,
            "faded": sum(1 for l in matured if l["state"] == "faded"), "alive": alive,
            "graduated": grad, "young": len(prior) - n, "launches_24h": last24,
            "dead_rate": None if dead_rate is None else round(dead_rate, 2), "label": label,
            "best": None if not best or not best["mcap_usd"] else
            {"mint": best["mint"], "symbol": best.get("symbol"), "mcap_usd": best["mcap_usd"]}}


LABEL_TEXT = {
    "SERIAL_RUGGER": "serial launcher: most earlier coins are dead",
    "RISKY": "most earlier coins died",
    "MIXED": "mixed record",
    "PROVEN": "has graduated coins that are still alive",
    "UNPROVEN": "earlier coins are too new to judge",
    "FIRST_LAUNCH": "first launch from this wallet",
}


def creator_flags(s: dict | None) -> list[dict]:
    """Plain-language flags for the risk engine, alerts and UI."""
    if not s:
        return []
    out, more = [], "at least " if s.get("at_least") else ""
    if s["launches_24h"] >= 5:
        out.append({"id": "launch_spam", "severity": "high",
                    "message": f"The creator launched {s['launches_24h']} tokens in the last 24h."})
    if s["label"] == "SERIAL_RUGGER" and s["matured"]:
        out.append({"id": "serial_rugger", "severity": "high",
                    "message": f"{s['dead']} of the creator's {more}{s['matured']} earlier tokens are dead "
                               f"({s['graduated']} graduated)."})
    elif s["label"] == "RISKY":
        out.append({"id": "dead_launches", "severity": "medium",
                    "message": f"{s['dead']} of the creator's {s['matured']} earlier tokens are dead."})
    return out


# ---------------------------------------------------------------- I/O
_GTFA_UNAVAILABLE = False


def _scrub(msg: str, key: str) -> str:
    return msg.replace(key, "***") if key else msg


class CreatorHistoryProvider:
    def __init__(self, api_key: str | None = None, timeout: float = 15.0,
                 transport: httpx.AsyncBaseTransport | None = None, pace: float = 0.12,
                 tx_limit: int = CREATOR_TX_LIMIT) -> None:
        self.key = HELIUS_API_KEY if api_key is None else api_key
        self.url = HELIUS_RPC_URL.format(key=self.key)
        self.timeout, self.transport, self.pace, self.tx_limit = timeout, transport, pace, tx_limit
        self.calls = 0

    async def _rpc(self, client, method: str, params):
        self.calls += 1
        r = await client.post(self.url, json={"jsonrpc": "2.0", "id": 1, "method": method, "params": params})
        r.raise_for_status()
        body = r.json()
        if body.get("error"):
            raise RuntimeError(f"{method}: {str(body['error'].get('message', 'rpc error'))[:120]}")
        if self.pace:
            await asyncio.sleep(self.pace)
        return body.get("result")

    async def recent_txs(self, client, wallet: str) -> tuple[list[dict], bool, str]:
        """(newest transactions, truncated?, method)."""
        global _GTFA_UNAVAILABLE
        if CREATOR_USE_GTFA and not _GTFA_UNAVAILABLE:
            try:
                txs, token = [], None
                while len(txs) < self.tx_limit:
                    opts = {"transactionDetails": "full", "sortOrder": "desc",
                            "limit": min(100, self.tx_limit - len(txs)),
                            "encoding": "jsonParsed", "maxSupportedTransactionVersion": 0}
                    if token:
                        opts["paginationToken"] = token
                    res = await self._rpc(client, "getTransactionsForAddress", [wallet, opts]) or {}
                    data = res.get("data")
                    if not isinstance(data, list):
                        raise RuntimeError("getTransactionsForAddress: unexpected result")
                    txs.extend(data)
                    token = res.get("paginationToken")
                    if not token or not data:
                        return txs, False, "gtfa"
                return txs, bool(token), "gtfa"
            except (RuntimeError, httpx.HTTPStatusError):
                _GTFA_UNAVAILABLE = True
        sigs = await self._rpc(client, "getSignaturesForAddress",
                               [wallet, {"limit": min(1000, self.tx_limit + 1)}]) or []
        truncated = len(sigs) > self.tx_limit
        txs = []
        for s in sigs[:self.tx_limit]:
            if s.get("err"):
                continue
            tx = await self._rpc(client, "getTransaction", [s["signature"], {
                "encoding": "jsonParsed", "maxSupportedTransactionVersion": 0}])
            if tx:
                txs.append(tx)
        return txs, truncated, "rpc"

    async def markets(self, client, mints: list[str]) -> dict[str, list[dict]]:
        """{mint: [pairs where it is the base token]} from DexScreener."""
        out: dict[str, list[dict]] = {m: [] for m in mints}
        for i in range(0, len(mints), DEX_BATCH):
            chunk = mints[i:i + DEX_BATCH]
            r = await client.get(f"{DEXSCREENER_BASE}/tokens/v1/solana/{','.join(chunk)}")
            r.raise_for_status()
            for p in r.json() or []:
                base = (p.get("baseToken") or {}).get("address")
                if base in out:
                    out[base].append(p)
        return out

    async def analyse(self, creator: str) -> CreatorHistory:
        if not self.key:
            return CreatorHistory(status="error", error="no HELIUS_API_KEY")
        try:
            async with httpx.AsyncClient(timeout=self.timeout, transport=self.transport) as client:
                txs, truncated, method = await self.recent_txs(client, creator)
                if not txs:
                    return CreatorHistory(status="no_data", method=method)
                created = find_creations(txs, creator)
                pairs = await self.markets(client, [c["mint"] for c in created]) if created else {}
                now = time.time()
                return CreatorHistory(status="ok", method=method, txs_scanned=len(txs),
                                      history_truncated=truncated,
                                      launches=[classify(c, pairs.get(c["mint"], []), now) for c in created])
        except Exception as exc:
            return CreatorHistory(status="error", error=_scrub(f"{type(exc).__name__}: {exc}", self.key)[:200])
