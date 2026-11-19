"""Jupiter — REAL "can you actually get out?" check (the Exit analyst).

Round trip, two quote calls, no wallet and no transaction:
  1. quote  SOL  -> token  for EXIT_CHECK_SOL (default 0.1 SOL)
  2. quote token -> SOL    for exactly the tokens step 1 would give you

round_trip_loss_pct = how much of the SOL you would lose just buying and selling
straight back (pool fees + price impact + any transfer tax the router knows of).

Outcomes
  * sell_route False, buy_route True  -> you can buy but there is no way out (honeypot-like)
  * both False                        -> no Jupiter route at all (very new / dead pool)
  * error                             -> UNKNOWN, never treated as "fine"

Caveat: a quote is not an executed transaction. Freeze authority, transfer hooks
or a frozen default account state can still block a real sell, which is why the
Helius contract checks run alongside this one.
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass, field

import httpx

from ..config import EXIT_CHECK_SOL, JUPITER_API_KEY, JUPITER_QUOTE_URL

SOL_MINT = "So11111111111111111111111111111111111111112"
LAMPORTS = 1_000_000_000
NO_ROUTE_CODES = ("COULD_NOT_FIND_ANY_ROUTE", "NO_ROUTES_FOUND", "TOKEN_NOT_TRADABLE",
                  "ROUTE_PLAN_DOES_NOT_CONSUME_ALL_THE_AMOUNT")

EXIT_LOSS_HIGH_PCT = 25.0     # lose a quarter of a small trade on the round trip
EXIT_LOSS_MEDIUM_PCT = 10.0


class NoRoute(Exception):
    pass


@dataclass
class ExitCheck:
    ok: bool                              # True = both quotes answered (route or definite no-route)
    error: str = ""
    sol_in: float = 0.0
    buy_route: bool | None = None
    sell_route: bool | None = None
    round_trip_loss_pct: float | None = None
    route: str = ""                       # DEX labels used on the sell side, e.g. "Raydium CPMM"
    labels: list[str] = field(default_factory=list)


def enabled() -> bool:
    return bool(JUPITER_QUOTE_URL)


def _scrub(msg: str) -> str:
    return msg.replace(JUPITER_API_KEY, "***") if JUPITER_API_KEY else msg


class JupiterProvider:
    name = "jupiter"
    kind = "onchain"

    def __init__(self, timeout: float = 10.0, transport: httpx.AsyncBaseTransport | None = None,
                 sol_amount: float | None = None) -> None:
        self.timeout = timeout
        self.transport = transport
        self.sol = EXIT_CHECK_SOL if sol_amount is None else sol_amount

    async def _quote(self, client: httpx.AsyncClient, inp: str, out: str, amount: int) -> dict:
        r = await client.get(JUPITER_QUOTE_URL, params={
            "inputMint": inp, "outputMint": out, "amount": str(amount),
            "slippageBps": "500", "restrictIntermediateTokens": "true"})
        if r.status_code == 400:
            try:
                body = r.json()
            except ValueError:
                body = {}
            code = str(body.get("errorCode") or "")
            text = f"{code} {body.get('error', '')}".upper()
            if code in NO_ROUTE_CODES or "ROUTE" in text or "NOT TRADABLE" in text or "NOT_TRADABLE" in text:
                raise NoRoute(code or "no route")
        r.raise_for_status()
        body = r.json()
        if not body.get("outAmount"):
            raise NoRoute("empty quote")
        return body

    async def check_exit(self, mint: str) -> ExitCheck:
        lamports = int(self.sol * LAMPORTS)
        headers = {"x-api-key": JUPITER_API_KEY} if JUPITER_API_KEY else {}
        try:
            async with httpx.AsyncClient(timeout=self.timeout, transport=self.transport,
                                         headers=headers) as client:
                try:
                    buy = await self._quote(client, SOL_MINT, mint, lamports)
                except NoRoute:
                    return ExitCheck(ok=True, sol_in=self.sol, buy_route=False, sell_route=False)
                tokens = int(buy["outAmount"])
                await asyncio.sleep(0.25)                     # stay polite on the free tier
                try:
                    sell = await self._quote(client, mint, SOL_MINT, tokens)
                except NoRoute:
                    return ExitCheck(ok=True, sol_in=self.sol, buy_route=True, sell_route=False)
            back = int(sell["outAmount"])
            loss = max(0.0, (1 - back / lamports) * 100) if lamports else None
            labels = [str(((s or {}).get("swapInfo") or {}).get("label") or "?")
                      for s in sell.get("routePlan") or []]
            return ExitCheck(ok=True, sol_in=self.sol, buy_route=True, sell_route=True,
                             round_trip_loss_pct=round(loss, 2) if loss is not None else None,
                             route=" > ".join(labels)[:120], labels=labels)
        except Exception as exc:
            return ExitCheck(ok=False, sol_in=self.sol,
                             error=_scrub(f"{type(exc).__name__}: {exc}")[:200])


def exit_verdict(buy_route, sell_route, loss) -> str | None:
    """'fail' | 'warn' | 'pass' | None (unknown). Works on stored values."""
    if sell_route is None:
        return None
    if buy_route and not sell_route:
        return "fail"
    if not buy_route:
        return "warn"
    if loss is not None and loss >= EXIT_LOSS_HIGH_PCT:
        return "fail"
    if loss is not None and loss >= EXIT_LOSS_MEDIUM_PCT:
        return "warn"
    return "pass"


def exit_flags(r) -> list[dict]:
    """Flags from a stored TokenRiskCheck row (or anything with exit_* attributes)."""
    buy, sell = getattr(r, "exit_buy_route", None), getattr(r, "exit_sell_route", None)
    loss, sol = getattr(r, "exit_round_trip_loss_pct", None), getattr(r, "exit_sol_in", None) or EXIT_CHECK_SOL
    if sell is None:
        return []
    if buy and not sell:
        return [{"id": "exit_no_sell_route", "severity": "high",
                 "message": "Jupiter can route a BUY but finds no way to SELL it back (honeypot-like)."}]
    if not buy:
        return [{"id": "exit_no_route", "severity": "medium",
                 "message": "Jupiter finds no trade route for this token yet."}]
    if loss is not None and loss >= EXIT_LOSS_HIGH_PCT:
        return [{"id": "exit_round_trip_loss", "severity": "high",
                 "message": f"Buying {sol:g} SOL and selling straight back loses ~{loss:.0f}% (thin pool or sell tax)."}]
    if loss is not None and loss >= EXIT_LOSS_MEDIUM_PCT:
        return [{"id": "exit_round_trip_loss", "severity": "medium",
                 "message": f"Buying {sol:g} SOL and selling straight back loses ~{loss:.0f}%."}]
    return []
