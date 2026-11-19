"""Helius (Solana RPC) — REAL on-chain risk facts for a token mint.

Three standard Solana JSON-RPC calls per check (so ~3 Helius credits), plus the DAS
getTokenAccounts pages used for the REAL holder count (up to HELIUS_HOLDER_MAX_PAGES):
  * getAccountInfo(mint, jsonParsed)  -> mint/freeze authority, supply, program
  * getTokenLargestAccounts(mint)     -> top token accounts -> concentration

Plus one getMultipleAccounts call on the largest token accounts to read each
account's OWNER. Owners that are program-derived addresses (off the ed25519
curve: pool vaults, bonding curves, lockers) and burn addresses are excluded,
giving REAL-WALLET concentration (top1_wallet_pct / top10_wallet_pct). The raw
account-level top1/top10 % (which includes the pool) is kept for reference.

The API key never leaves the backend and is scrubbed from any error text.
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass

import httpx

from ..config import HELIUS_API_KEY, HELIUS_HOLDER_MAX_PAGES, HELIUS_RPC_URL
from .jupiter import exit_flags

TOKEN_2022 = "TokenzQdBNbLqP5VEhdkAS6EPFLC1PHnBqCXEpPxuEb"

# Owners whose balance is effectively out of circulation (on-curve, so not caught
# by the PDA test): system program and the well-known incinerator.
BURN_OWNERS = {"11111111111111111111111111111111", "1nc1nerator11111111111111111111111111111111"}

# ---------------------------------------------------------------- base58 / curve
_B58 = "123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz"
_P = 2 ** 255 - 19
_D = (-121665 * pow(121666, _P - 2, _P)) % _P
_SQRT_M1 = pow(2, (_P - 1) // 4, _P)


def b58decode(s: str) -> bytes:
    n = 0
    for ch in s:
        n = n * 58 + _B58.index(ch)
    raw = n.to_bytes((n.bit_length() + 7) // 8, "big") if n else b""
    pad = len(s) - len(s.lstrip("1"))
    return b"\x00" * pad + raw


def is_on_curve(address: str) -> bool:
    """True if the 32-byte address decompresses to an ed25519 point (i.e. it can be
    a wallet with a private key). PDAs — pools, curves, lockers — are off-curve.
    Same test as Solana's Pubkey::is_on_curve. Invalid input => False."""
    try:
        b = b58decode(address)
    except ValueError:
        return False
    if len(b) != 32:
        return False
    y = int.from_bytes(b, "little") & ((1 << 255) - 1)
    if y >= _P:
        return False
    u = (y * y - 1) % _P
    v = (_D * y * y + 1) % _P
    x = (u * pow(v, 3, _P) * pow(u * pow(v, 7, _P), (_P - 5) // 8, _P)) % _P
    vx2 = (v * x * x) % _P
    if vx2 == u:
        return True
    if vx2 == (-u) % _P:          # x * sqrt(-1) is the root
        return True
    return False


def wallet_concentration(holdings: list[tuple[str, float]], supply: float):
    """holdings: [(owner, ui_amount)] for the largest token accounts.
    Returns (top1_wallet_pct, top10_wallet_pct, excluded_pct) where excluded =
    supply held by program-owned (PDA) and burn accounts."""
    if not supply:
        return None, None, None
    wallets: dict[str, float] = {}
    excluded = 0.0
    for owner, amt in holdings:
        if not owner or amt <= 0:
            continue
        if owner in BURN_OWNERS or not is_on_curve(owner):
            excluded += amt
        else:
            wallets[owner] = wallets.get(owner, 0.0) + amt     # one owner, many accounts
    ranked = sorted(wallets.values(), reverse=True)
    pct = lambda x: round(min(x / supply * 100, 100.0), 2)  # noqa: E731
    return (pct(ranked[0]) if ranked else 0.0, pct(sum(ranked[:10])), pct(excluded))


# ---------------------------------------------------------------- Token-2022 extensions
# Extensions that let someone other than the holder control or tax the holder's
# tokens. Metadata/group pointers, memo, interest-bearing etc. are harmless here.
# Severity of each risky extension when it is ACTIVE (authority/program set).
RISKY_EXTENSIONS = {
    "permanentDelegate": ("high", "Permanent delegate set: one address can move or burn anyone's tokens."),
    "transferHook": ("high", "Transfer hook: custom program code runs on every transfer and can block sells."),
    "nonTransferable": ("high", "Non-transferable token: holders cannot sell or send it."),
    "defaultAccountState": ("high", "New token accounts start FROZEN: buyers may be unable to sell."),
    "pausableConfig": ("high", "Pausable: an authority can pause all transfers."),
    "mintCloseAuthority": ("medium", "Mint close authority set: the mint account can be closed."),
}
TRANSFER_FEE_HIGH_BPS = 500     # 5%+ per transfer is a sell tax, not a fee


def analyze_extensions(exts: list | None) -> tuple[list[str], int | None, bool]:
    """Read jsonParsed Token-2022 `info.extensions`.

    Returns (active_risky_names, transfer_fee_bps, fee_mutable):
      * active_risky_names: RISKY_EXTENSIONS keys that are switched on
      * transfer_fee_bps: highest of the current/scheduled fee, None = no fee extension
      * fee_mutable: True if a fee authority can still change the fee
    """
    names: list[str] = []
    fee_bps: int | None = None
    fee_mutable = False
    for e in exts or []:
        if not isinstance(e, dict):
            continue
        kind, st = e.get("extension"), e.get("state") or {}
        if kind == "transferFeeConfig":
            fees = [int((st.get(k) or {}).get("transferFeeBasisPoints") or 0)
                    for k in ("olderTransferFee", "newerTransferFee")]
            fee_bps = max(fees) if fees else 0
            fee_mutable = bool(st.get("transferFeeConfigAuthority"))
        elif kind == "permanentDelegate" and st.get("delegate"):
            names.append(kind)
        elif kind == "transferHook" and st.get("programId"):
            names.append(kind)
        elif kind == "nonTransferable":
            names.append(kind)
        elif kind == "defaultAccountState" and str(st.get("accountState", "")).lower() == "frozen":
            names.append(kind)
        elif kind == "pausableConfig" and (st.get("authority") or st.get("paused")):
            names.append(kind)
        elif kind == "mintCloseAuthority" and st.get("closeAuthority"):
            names.append(kind)
    return names, fee_bps, fee_mutable


def extension_flags(names: list[str] | None, fee_bps: int | None, fee_mutable: bool) -> list[dict]:
    flags = []
    for n in names or []:
        sev, msg = RISKY_EXTENSIONS.get(n, ("medium", f"Token-2022 extension {n} is active."))
        flags.append({"id": f"ext_{n}", "severity": sev, "message": msg})
    if fee_bps:
        sev = "high" if fee_bps >= TRANSFER_FEE_HIGH_BPS else "medium"
        flags.append({"id": "ext_transferFee", "severity": sev,
                      "message": f"Transfer fee of {fee_bps / 100:.2f}% on every transfer (including sells)."})
    if fee_mutable:
        flags.append({"id": "ext_transferFee_mutable", "severity": "medium",
                      "message": "Transfer fee can still be changed by its authority."})
    return flags


def ext_names_from_str(s: str | None) -> list[str] | None:
    """Stored as comma list; None = not analysed (old check), '' = analysed, none risky."""
    if s is None:
        return None
    return [x for x in s.split(",") if x]


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
    holders_count: int | None = None     # REAL distinct wallets with a balance (None = unknown)
    holders_capped: bool = False         # True => at least this many (page cap reached)
    top1_wallet_pct: float | None = None   # largest REAL wallet (pools/PDAs/burn excluded)
    top10_wallet_pct: float | None = None  # top-10 REAL wallets
    excluded_pct: float | None = None      # held by pools / program accounts / burn
    # Token-2022 extensions (None = not analysed; "" = analysed, nothing risky)
    token_extensions: str | None = None
    transfer_fee_bps: int | None = None
    transfer_fee_mutable: bool = False


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

    async def count_holders(self, client: httpx.AsyncClient, mint: str,
                            max_pages: int | None = None) -> tuple[int, bool]:
        """REAL holder count: distinct owner wallets with a non-zero balance, via the
        DAS getTokenAccounts method (1000 accounts per page). Returns (count, capped);
        capped=True means the page limit was hit, so the true number is higher.
        Includes the liquidity-pool vault and other program accounts (usually a handful)."""
        pages = HELIUS_HOLDER_MAX_PAGES if max_pages is None else max_pages
        owners: set[str] = set()
        for page in range(1, pages + 1):
            res = await self._rpc(client, "getTokenAccounts",
                                  {"mint": mint, "page": page, "limit": 1000,
                                   "options": {"showZeroBalance": False}})
            accts = (res or {}).get("token_accounts") or []
            for a in accts:
                if a.get("owner") and float(a.get("amount") or 0) > 0:
                    owners.add(a["owner"])
            if len(accts) < 1000:
                return len(owners), False
            await asyncio.sleep(0.2)
        return len(owners), True

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
                w1 = w10 = excl = None
                try:              # owner lookup is a bonus: never fail the risk check on it
                    accts = [(a.get("address"), float(a.get("uiAmountString") or a.get("uiAmount") or 0))
                             for a in (largest or {}).get("value") or [] if a.get("address")]
                    if accts:
                        await asyncio.sleep(0.2)
                        info_res = await self._rpc(client, "getMultipleAccounts",
                                                   [[a for a, _ in accts], {"encoding": "jsonParsed"}])
                        owners = []
                        for (_, amt), acc in zip(accts, (info_res or {}).get("value") or []):
                            pinfo = (((acc or {}).get("data") or {}).get("parsed") or {}).get("info") or {}
                            owners.append((pinfo.get("owner"), amt))
                        if len(owners) == len(accts):
                            w1, w10, excl = wallet_concentration(owners, supply)
                except Exception:
                    w1 = w10 = excl = None
                holders_count, holders_capped = None, False
                try:                      # holder count is a bonus: never fail the risk check on it
                    await asyncio.sleep(0.2)
                    holders_count, holders_capped = await self.count_holders(client, mint)
                except Exception:
                    holders_count, holders_capped = None, False
            amounts = sorted(
                (float(a.get("uiAmountString") or a.get("uiAmount") or 0)
                 for a in (largest or {}).get("value") or []), reverse=True)
            top1 = (amounts[0] / supply * 100) if supply and amounts else 0.0
            top10 = (sum(amounts[:10]) / supply * 100) if supply and amounts else 0.0
            is_2022 = value.get("owner") == TOKEN_2022
            ext_names, fee_bps, fee_mut = analyze_extensions(info.get("extensions")) if is_2022 else ([], None, False)
            return OnchainRisk(
                token_extensions=",".join(ext_names), transfer_fee_bps=fee_bps,
                transfer_fee_mutable=fee_mut,
                ok=True,
                mint_authority=info.get("mintAuthority") or None,
                freeze_authority=info.get("freezeAuthority") or None,
                supply=supply, decimals=decimals,
                top1_pct=round(min(top1, 100.0), 2),
                top10_pct=round(min(top10, 100.0), 2),
                holders_sampled=len(amounts),
                token_2022=is_2022,
                holders_count=holders_count, holders_capped=holders_capped,
                top1_wallet_pct=w1, top10_wallet_pct=w10, excluded_pct=excl,
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
        names = ext_names_from_str(getattr(r, "token_extensions", None))
        if names is None:          # check made before extensions were analysed
            flags.append({"id": "token_2022", "severity": "medium",
                          "message": "Token-2022 program: may carry extensions such as transfer fees. Check manually."})
        else:
            flags.extend(extension_flags(names, getattr(r, "transfer_fee_bps", None),
                                         bool(getattr(r, "transfer_fee_mutable", False))))
    flags.extend(exit_flags(r))
    w1, w10 = getattr(r, "top1_wallet_pct", None), getattr(r, "top10_wallet_pct", None)
    if w1 is not None and w10 is not None:          # real-wallet view (pools excluded)
        if w1 >= 20:
            flags.append({"id": "top1_wallet_concentration", "severity": "high",
                          "message": f"A single wallet holds {w1:.0f}% of supply (pools and burn excluded)."})
        elif w10 >= 50:
            flags.append({"id": "top10_wallet_concentration", "severity": "high",
                          "message": f"Top 10 wallets hold {w10:.0f}% of supply (pools and burn excluded)."})
        elif w10 >= 30:
            flags.append({"id": "top10_wallet_concentration", "severity": "medium",
                          "message": f"Top 10 wallets hold {w10:.0f}% of supply (pools and burn excluded)."})
    elif r.top1_pct >= 50:
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
