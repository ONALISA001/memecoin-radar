"""Helius (Solana RPC) — REAL on-chain risk facts for a token mint.

Two standard Solana JSON-RPC calls per check (so ~2 Helius credits):
  * getAccountInfo(mint, jsonParsed)  -> mint/freeze authority, supply, program
  * getTokenLargestAccounts(mint)     -> top token accounts -> concentration

Honest caveat (surfaced in the API): the largest token accounts usually include
the liquidity-pool vault and burn/dead wallets, so top1/top10 % can overstate
real holder concentration. Treat it as an upper bound, not a verdict.

The API key never leaves the backend and is scrubbed from any error text.
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass

import httpx

from ..config import HELIUS_API_KEY, HELIUS_RPC_URL

TOKEN_2022 = "TokenzQdBNbLqP5VEhdkAS6EPFLC1PHnBqCXEpPxuEb"


@dataclass
class OnchainRisk:
    ok: bool
    error: str = ""
    mint_authority: str | None = None
    freeze_authority: str | None = None
    supply: float = 0.0
    decimals: int = 0
    top1_pct: float = 0.0
    top10_pct: float = 0.0
    holders_sampled: int = 0
    token_2022: bool = False


def enabled() -> bool:
    return bool(HELIUS_API_KEY)


def _scrub(msg: str, key: str) -> str:
    return msg.replace(key, "***") if key else msg


class HeliusProvider:
    name = "helius"
    kind = "onchain"

    def __init__(self, api_key: str | None = None, timeout: float = 10.0,
                 transport: httpx.AsyncBaseTransport | None = None) -> None:
        self.key = HELIUS_API_KEY if api_key is None else api_key
        self.url = HELIUS_RPC_URL.format(key=self.key)
        self.timeout = timeout
        self.transport = transport

    async def _rpc(self, client: httpx.AsyncClient, method: str, params: list):
        r = await client.post(self.url, json={
            "jsonrpc": "2.0", "id": 1, "method": method, "params": params})
        r.raise_for_status()
        body = r.json()
        if body.get("error"):
            raise RuntimeError(str(body["error"].get("message", "rpc error"))[:120])
        return body.get("result")

    async def check_token(self, mint: str) -> OnchainRisk:
        if not self.key:
            return OnchainRisk(ok=False, error="no HELIUS_API_KEY")
        try:
            async with httpx.AsyncClient(timeout=self.timeout,
                                         transport=self.transport) as client:
                acct = await self._rpc(client, "getAccountInfo",
                                       [mint, {"encoding": "jsonParsed"}])
                value = (acct or {}).get("value")
                if not value:
                    return OnchainRisk(ok=False, error="mint account not found")
                parsed = (value.get("data") or {}).get("parsed") or {}
                if parsed.get("type") != "mint":
                    return OnchainRisk(ok=False, error="address is not a token mint")
                info = parsed.get("info") or {}
                decimals = int(info.get("decimals") or 0)
                supply = float(info.get("supply") or 0) / (10 ** decimals)
                await asyncio.sleep(0.2)                 # be gentle on the free tier
                largest = await self._rpc(client, "getTokenLargestAccounts", [mint])
            amounts = sorted(
                (float(a.get("uiAmountString") or a.get("uiAmount") or 0)
                 for a in (largest or {}).get("value") or []), reverse=True)
            top1 = (amounts[0] / supply * 100) if supply and amounts else 0.0
            top10 = (sum(amounts[:10]) / supply * 100) if supply and amounts else 0.0
            return OnchainRisk(
                ok=True,
                mint_authority=info.get("mintAuthority") or None,
                freeze_authority=info.get("freezeAuthority") or None,
                supply=supply, decimals=decimals,
                top1_pct=round(min(top1, 100.0), 2),
                top10_pct=round(min(top10, 100.0), 2),
                holders_sampled=len(amounts),
                token_2022=value.get("owner") == TOKEN_2022,
            )
        except Exception as exc:                          # never leak the key
            return OnchainRisk(ok=False, error=_scrub(f"{type(exc).__name__}: {exc}", self.key)[:200])


def risk_flags(r) -> list[dict]:
    """Plain-language flags from a stored/returned check. Works on OnchainRisk
    or a TokenRiskCheck row (same attribute names)."""
    flags: list[dict] = []
    if not r.ok:
        return flags
    if r.mint_authority:
        flags.append({"id": "mint_authority_active", "severity": "high",
                      "message": "Mint authority is still active: the creator can mint more supply."})
    if r.freeze_authority:
        flags.append({"id": "freeze_authority_active", "severity": "high",
                      "message": "Freeze authority is active: the creator can freeze holder accounts."})
    if getattr(r, "token_2022", False):
        flags.append({"id": "token_2022", "severity": "medium",
                      "message": "Token-2022 program: may carry extensions such as transfer fees. Check manually."})
    if r.top1_pct >= 50:
        flags.append({"id": "top1_concentration", "severity": "high",
                      "message": f"Largest account holds {r.top1_pct:.0f}% of supply (may be the pool vault)."})
    elif r.top10_pct >= 80:
        flags.append({"id": "top10_concentration", "severity": "medium",
                      "message": f"Top 10 accounts hold {r.top10_pct:.0f}% of supply (may include pool/burn accounts)."})
    return flags


def risk_label(r) -> str:
    if not r.ok:
        return "unknown"
    sev = {f["severity"] for f in risk_flags(r)}
    return "high" if "high" in sev else "medium" if "medium" in sev else "low"
