"""Launch forensics — REAL wallet-level look at the first moments of a token.

What it answers (from the token's oldest transactions on Solana):
  * creator     — fee payer of the creation transaction, and how much the dev bought
  * bundle      — wallets that received tokens IN THE SAME SLOT as the creation
                  (bought together with the launch, typically a Jito bundle)
  * snipers     — wallets that bought within LAUNCH_SNIPER_SLOTS slots (~0.4 s each)
  * early buyers — the first LAUNCH_EARLY_BUYERS distinct buyers and their share
  * funding     — for the biggest early buyers: is the wallet brand new, who sent it
                  its first SOL, do several early buyers share one funder, and did
                  the CREATOR fund any of them (a dev buying through side wallets)
  * now         — what the creator and the snipers still hold, at check time

Data path (cheapest first):
  1. getTransactionsForAddress(mint, sortOrder=asc)  — one call, Helius Developer plan+
  2. fallback for the free plan: getSignaturesForAddress pages back to the oldest,
     then getTransaction for the first LAUNCH_EARLY_TX transactions.
Program-owned accounts (bonding curves, pools — off-curve PDAs) are never counted
as buyers. Everything is heuristic evidence, not proof; nothing here is simulated.
"""
from __future__ import annotations

import asyncio
from dataclasses import asdict, dataclass, field

import httpx

from ..config import (HELIUS_API_KEY, HELIUS_RPC_URL, LAUNCH_EARLY_BUYERS, LAUNCH_EARLY_TX,
                      LAUNCH_FUNDER_CHECKS, LAUNCH_MAX_SIG_PAGES, LAUNCH_SNIPER_SLOTS,
                      LAUNCH_USE_GTFA)
from .helius import BURN_OWNERS, is_on_curve

FRESH_WALLET_H = 24.0     # first ever transaction less than this long before launch


@dataclass
class LaunchForensics:
    status: str                         # ok | too_active | no_data | error
    error: str = ""
    method: str = ""                    # gtfa | rpc
    creator: str | None = None
    launch_slot: int | None = None
    launch_time: int | None = None      # unix seconds
    dev_buy_pct: float | None = None
    bundle_wallets: int | None = None   # buyers in the creation slot (creator excluded)
    bundle_pct: float | None = None
    sniper_wallets: int | None = None   # buyers within LAUNCH_SNIPER_SLOTS (creator excluded)
    sniper_pct: float | None = None
    early_buyers: int | None = None
    early_pct: float | None = None
    fresh_early_wallets: int | None = None   # of the funder-checked wallets
    shared_funder_wallets: int | None = None  # size of the largest group with one funder
    funded_by_creator: int | None = None
    funder_checked: int | None = None
    dev_holds_pct: float | None = None       # creator's balance now, % of supply
    snipers_hold_pct: float | None = None    # bundle+snipers' balances now, % of supply
    wallets: list[dict] = field(default_factory=list)  # evidence for the UI / audits

    def as_dict(self) -> dict:
        return asdict(self)


# ---------------------------------------------------------------- pure analysis
def _keys(tx: dict) -> list[str]:
    msg = ((tx.get("transaction") or {}).get("message") or {})
    out = []
    for k in msg.get("accountKeys") or []:
        out.append(k.get("pubkey") if isinstance(k, dict) else k)
    return out


def fee_payer(tx: dict) -> str | None:
    k = _keys(tx)
    return k[0] if k else None


def token_deltas(tx: dict, mint: str) -> dict[str, float]:
    """{owner: ui_amount change of `mint`} for one transaction."""
    meta = tx.get("meta") or {}
    bal: dict[tuple, list[float]] = {}
    for side, rows in ((0, meta.get("preTokenBalances")), (1, meta.get("postTokenBalances"))):
        for b in rows or []:
            if b.get("mint") != mint:
                continue
            key = (b.get("accountIndex"), b.get("owner"))
            amt = float(((b.get("uiTokenAmount") or {}).get("uiAmountString")) or
                        (b.get("uiTokenAmount") or {}).get("uiAmount") or 0)
            bal.setdefault(key, [0.0, 0.0])[side] = amt
    out: dict[str, float] = {}
    for (_, owner), (pre, post) in bal.items():
        if owner:
            out[owner] = out.get(owner, 0.0) + (post - pre)
    return out


def _is_wallet(addr: str) -> bool:
    return bool(addr) and addr not in BURN_OWNERS and is_on_curve(addr)


def analyse_launch(txs: list[dict], mint: str, supply: float,
                   sniper_slots: int = LAUNCH_SNIPER_SLOTS,
                   early_n: int = LAUNCH_EARLY_BUYERS) -> LaunchForensics:
    """txs: the token's OLDEST transactions (any order; failed ones are skipped)."""
    ok = [t for t in txs if t and not (t.get("meta") or {}).get("err") and t.get("slot") is not None]
    if not ok or not supply:
        return LaunchForensics(status="no_data")
    ok.sort(key=lambda t: (t["slot"], t.get("_order", 0)))
    first = ok[0]
    creator, slot0 = fee_payer(first), int(first["slot"])
    pct = lambda x: round(min(x / supply * 100, 100.0), 2)  # noqa: E731

    bought: dict[str, dict] = {}            # wallet -> {amount, slot}
    for t in ok:
        for owner, d in token_deltas(t, mint).items():
            if d <= 0 or not _is_wallet(owner):
                continue
            w = bought.setdefault(owner, {"amount": 0.0, "slot": int(t["slot"])})
            w["amount"] += d
    dev = bought.get(creator, {}).get("amount", 0.0)
    others = {w: v for w, v in bought.items() if w != creator}
    bundle = {w: v for w, v in others.items() if v["slot"] == slot0}
    snipe = {w: v for w, v in others.items() if v["slot"] <= slot0 + sniper_slots}
    order = sorted(others.items(), key=lambda kv: (kv[1]["slot"], -kv[1]["amount"]))[:early_n]
    wallets = [{"wallet": w, "slot_offset": v["slot"] - slot0, "pct": pct(v["amount"]),
                "role": "bundle" if w in bundle else "sniper" if w in snipe else "early"}
               for w, v in order]
    if dev:
        wallets.insert(0, {"wallet": creator, "slot_offset": 0, "pct": pct(dev), "role": "creator"})
    return LaunchForensics(
        status="ok", creator=creator, launch_slot=slot0, launch_time=first.get("blockTime"),
        dev_buy_pct=pct(dev),
        bundle_wallets=len(bundle), bundle_pct=pct(sum(v["amount"] for v in bundle.values())),
        sniper_wallets=len(snipe), sniper_pct=pct(sum(v["amount"] for v in snipe.values())),
        early_buyers=len(order), early_pct=pct(sum(v["amount"] for _, v in order)),
        wallets=wallets)


def first_funder(tx: dict, wallet: str) -> str | None:
    """Who sent `wallet` SOL in its first transaction (system transfer), else the fee payer."""
    meta = tx.get("meta") or {}
    ins = list(((tx.get("transaction") or {}).get("message") or {}).get("instructions") or [])
    for inner in meta.get("innerInstructions") or []:
        ins.extend(inner.get("instructions") or [])
    for i in ins:
        p = i.get("parsed") if isinstance(i, dict) else None
        if not isinstance(p, dict):
            continue
        info = p.get("info") or {}
        if p.get("type") in ("transfer", "transferWithSeed", "createAccount") and \
                (info.get("destination") or info.get("newAccount")) == wallet and info.get("source"):
            return info["source"]
    fp = fee_payer(tx)
    return fp if fp and fp != wallet else None


def apply_funding(lf: LaunchForensics, funders: dict[str, dict]) -> None:
    """funders: {wallet: {"funder": str|None, "first_time": int|None, "reached": bool}}"""
    if not funders:
        return
    groups: dict[str, int] = {}
    fresh = by_creator = 0
    for w in lf.wallets:
        f = funders.get(w["wallet"])
        if not f:
            continue
        w["funder"] = f.get("funder")
        w["fresh"] = bool(f.get("reached") and f.get("first_time") and lf.launch_time
                          and lf.launch_time - f["first_time"] < FRESH_WALLET_H * 3600)
        fresh += 1 if w["fresh"] else 0
        if w["role"] != "creator" and f.get("funder"):
            groups[f["funder"]] = groups.get(f["funder"], 0) + 1
            by_creator += 1 if f["funder"] == lf.creator else 0
    lf.funder_checked = sum(1 for w in lf.wallets if w["wallet"] in funders and w["role"] != "creator")
    lf.fresh_early_wallets = fresh
    lf.shared_funder_wallets = max(groups.values(), default=0)
    lf.funded_by_creator = by_creator


def forensic_flags(f) -> list[dict]:
    """Plain-language flags. Works on LaunchForensics or a stored row."""
    if not f or getattr(f, "status", None) != "ok":
        return []
    out = []
    g = lambda k: getattr(f, k, None)  # noqa: E731
    if (g("funded_by_creator") or 0) >= 1:
        out.append({"id": "creator_funded_buyers", "severity": "high",
                    "message": f"The creator funded {g('funded_by_creator')} of the early buyer wallets "
                               "(dev likely buying through side wallets)."})
    if (g("bundle_pct") or 0) >= 15:
        out.append({"id": "bundled_launch", "severity": "high",
                    "message": f"{g('bundle_wallets')} wallets bought {g('bundle_pct'):.0f}% of supply in the "
                               "same block as the launch (bundled)."})
    elif (g("bundle_pct") or 0) >= 5:
        out.append({"id": "bundled_launch", "severity": "medium",
                    "message": f"{g('bundle_wallets')} wallets bought {g('bundle_pct'):.0f}% in the launch block."})
    if (g("sniper_pct") or 0) >= 25:
        out.append({"id": "heavy_sniping", "severity": "high",
                    "message": f"Snipers took {g('sniper_pct'):.0f}% of supply in the first seconds."})
    elif (g("sniper_pct") or 0) >= 10:
        out.append({"id": "heavy_sniping", "severity": "medium",
                    "message": f"Snipers took {g('sniper_pct'):.0f}% of supply in the first seconds."})
    if (g("shared_funder_wallets") or 0) >= 3:
        out.append({"id": "shared_funder", "severity": "high",
                    "message": f"{g('shared_funder_wallets')} early buyers were funded by the same wallet."})
    elif (g("shared_funder_wallets") or 0) == 2:
        out.append({"id": "shared_funder", "severity": "medium",
                    "message": "2 early buyers were funded by the same wallet."})
    chk = g("funder_checked") or 0
    if chk >= 3 and (g("fresh_early_wallets") or 0) / chk >= 0.6:
        out.append({"id": "fresh_wallets", "severity": "medium",
                    "message": f"{g('fresh_early_wallets')} of {chk} big early buyers are brand-new wallets."})
    if (g("snipers_hold_pct") or 0) >= 10:
        out.append({"id": "sniper_overhang", "severity": "medium",
                    "message": f"Launch snipers still hold {g('snipers_hold_pct'):.0f}% of supply (sell pressure)."})
    dev_buy, dev_now = g("dev_buy_pct"), g("dev_holds_pct")
    if dev_buy and dev_buy >= 1 and dev_now is not None and dev_now < dev_buy * 0.2:
        out.append({"id": "dev_sold", "severity": "medium",
                    "message": f"Creator bought {dev_buy:.1f}% at launch and now holds {dev_now:.1f}%."})
    return out


# ---------------------------------------------------------------- Helius I/O
_GTFA_UNAVAILABLE = False      # remembered for the process once the plan refuses it


def _scrub(msg: str, key: str) -> str:
    return msg.replace(key, "***") if key else msg


class LaunchForensicsProvider:
    def __init__(self, api_key: str | None = None, timeout: float = 15.0,
                 transport: httpx.AsyncBaseTransport | None = None, pace: float = 0.12) -> None:
        self.key = HELIUS_API_KEY if api_key is None else api_key
        self.url = HELIUS_RPC_URL.format(key=self.key)
        self.timeout, self.transport, self.pace = timeout, transport, pace
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

    async def _get_tx(self, client, sig: str) -> dict | None:
        return await self._rpc(client, "getTransaction",
                               [sig, {"encoding": "jsonParsed", "maxSupportedTransactionVersion": 0}])

    async def oldest_txs(self, client, address: str, n: int, max_pages: int) -> tuple[list[dict] | None, str]:
        """(oldest n transactions, method). (None, "...") => too many to reach the start."""
        global _GTFA_UNAVAILABLE
        if LAUNCH_USE_GTFA and not _GTFA_UNAVAILABLE:
            try:
                res = await self._rpc(client, "getTransactionsForAddress", [address, {
                    "transactionDetails": "full", "sortOrder": "asc", "limit": n,
                    "encoding": "jsonParsed", "maxSupportedTransactionVersion": 0}])
                data = (res or {}).get("data")
                if isinstance(data, list):
                    return data, "gtfa"
            except (RuntimeError, httpx.HTTPStatusError):
                _GTFA_UNAVAILABLE = True     # free plan: fall back for the rest of the run
        before, page, oldest = None, 0, []
        while page < max_pages:
            opts = {"limit": 1000}
            if before:
                opts["before"] = before
            sigs = await self._rpc(client, "getSignaturesForAddress", [address, opts]) or []
            page += 1
            if not sigs:
                break
            oldest = sigs
            before = sigs[-1]["signature"]
            if len(sigs) < 1000:
                break
        else:
            return None, "rpc"
        tail = [s for s in reversed(oldest) if not s.get("err")][:n]
        txs = []
        for i, s in enumerate(tail):
            tx = await self._get_tx(client, s["signature"])
            if tx:
                tx["_order"] = i
                txs.append(tx)
        return txs, "rpc"

    async def _funding(self, client, wallet: str) -> dict:
        txs, _ = await self.oldest_txs(client, wallet, 1, 2)
        if txs is None:             # long history => established wallet, funder unknown
            return {"funder": None, "first_time": None, "reached": False}
        if not txs:
            return {"funder": None, "first_time": None, "reached": True}
        return {"funder": first_funder(txs[0], wallet), "first_time": txs[0].get("blockTime"), "reached": True}

    async def _balance(self, client, owner: str, mint: str) -> float:
        res = await self._rpc(client, "getTokenAccountsByOwner",
                              [owner, {"mint": mint}, {"encoding": "jsonParsed"}])
        tot = 0.0
        for a in (res or {}).get("value") or []:
            amt = ((((a.get("account") or {}).get("data") or {}).get("parsed") or {})
                   .get("info") or {}).get("tokenAmount") or {}
            tot += float(amt.get("uiAmountString") or amt.get("uiAmount") or 0)
        return tot

    async def analyse(self, mint: str, supply: float) -> LaunchForensics:
        if not self.key:
            return LaunchForensics(status="error", error="no HELIUS_API_KEY")
        try:
            async with httpx.AsyncClient(timeout=self.timeout, transport=self.transport) as client:
                txs, method = await self.oldest_txs(client, mint, LAUNCH_EARLY_TX, LAUNCH_MAX_SIG_PAGES)
                if txs is None:
                    return LaunchForensics(status="too_active", method=method,
                                           error=f"more than {LAUNCH_MAX_SIG_PAGES}k transactions; launch not reached")
                lf = analyse_launch(txs, mint, supply)
                lf.method = method
                if lf.status != "ok":
                    return lf
                # funding of the biggest non-creator early buyers
                big = sorted((w for w in lf.wallets if w["role"] != "creator"), key=lambda w: -w["pct"])
                funders = {}
                for w in big[:LAUNCH_FUNDER_CHECKS]:
                    try:
                        funders[w["wallet"]] = await self._funding(client, w["wallet"])
                    except Exception:
                        continue
                apply_funding(lf, funders)
                # what the creator and the launch-block/sniper wallets hold NOW
                try:
                    if lf.creator:
                        lf.dev_holds_pct = round(await self._balance(client, lf.creator, mint) / supply * 100, 2)
                    held = 0.0
                    for w in [w for w in lf.wallets if w["role"] in ("bundle", "sniper")][:LAUNCH_FUNDER_CHECKS]:
                        held += await self._balance(client, w["wallet"], mint)
                    lf.snipers_hold_pct = round(min(held / supply * 100, 100.0), 2)
                except Exception:
                    pass
                return lf
        except Exception as exc:
            return LaunchForensics(status="error", error=_scrub(f"{type(exc).__name__}: {exc}", self.key)[:200])
