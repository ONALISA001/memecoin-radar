"""Schedules launch forensics (providers/launch_forensics.py) and serves the results.

Who gets analysed (credit guard):
  * real Solana tokens with a successful Helius check (we need the supply)
  * not already failed on a hard safety lever (mint/freeze authority, no sell route,
    dangerous Token-2022 extension): those are already HIGH risk, no need to spend
  * launched within LAUNCH_FORENSICS_MAX_AGE_H (older launches: history too long)
  * fresh-pool discoveries first, newest first; LAUNCH_FORENSICS_PER_TICK per tick
Each token is analysed ONCE. Errors and "no data" retry after an hour; "too_active"
(launch out of reach) is final.
"""
from __future__ import annotations

import datetime as dt
import time

from sqlalchemy import select

from .. import config
from ..models import Token, TokenLaunchForensics, TokenRiskCheck
from ..providers import jupiter as jup_mod
from ..providers.helius import ext_names_from_str
from ..providers.launch_forensics import LaunchForensicsProvider, forensic_flags

RETRY_AFTER_H = 1.0
HARD_EXTS = {"permanentDelegate", "nonTransferable", "defaultAccountState", "pausableConfig", "transferHook"}

FIELDS = ("status", "error", "method", "creator", "launch_slot", "launch_time", "dev_buy_pct",
          "bundle_wallets", "bundle_pct", "sniper_wallets", "sniper_pct", "early_buyers", "early_pct",
          "fresh_early_wallets", "shared_funder_wallets", "funded_by_creator", "funder_checked",
          "dev_holds_pct", "snipers_hold_pct")


def hard_failed(chk: TokenRiskCheck) -> bool:
    if chk.mint_authority or chk.freeze_authority:
        return True
    if set(ext_names_from_str(chk.token_extensions) or []) & HARD_EXTS:
        return True
    return jup_mod.exit_verdict(chk.exit_buy_route, chk.exit_sell_route,
                                chk.exit_round_trip_loss_pct) == "fail"


def candidates(db, now: dt.datetime | None = None) -> list[tuple[int, str, float]]:
    """[(token_id, mint, supply)] to analyse this tick, best first."""
    now = now or dt.datetime.utcnow()
    max_age_ms = config.LAUNCH_FORENSICS_MAX_AGE_H * 3.6e6
    rows = {r.token_id: r for r in db.execute(select(TokenLaunchForensics)).scalars().all()}
    toks = db.execute(select(Token).where(Token.chain == "solana")).scalars().all()
    out = []
    for t in toks:
        if (t.address or "").startswith("Mock"):
            continue
        if t.token_created_at and time.time() * 1000 - t.token_created_at > max_age_ms:
            continue
        prev = rows.get(t.id)
        if prev is not None:
            if prev.status in ("ok", "too_active"):
                continue
            seen = prev.analysed_at.replace(tzinfo=None) if prev.analysed_at else None
            if seen and (now - seen).total_seconds() < RETRY_AFTER_H * 3600:
                continue
        chk = db.execute(select(TokenRiskCheck).where(TokenRiskCheck.token_id == t.id,
                                                      TokenRiskCheck.ok.is_(True))
                         .order_by(TokenRiskCheck.checked_at.desc(), TokenRiskCheck.id.desc())
                         .limit(1)).scalar_one_or_none()
        if chk is None or not chk.supply or hard_failed(chk):
            continue
        fresh = t.discovery_source == "new_pool"
        out.append(((0 if fresh else 1), -(t.token_created_at or 0), t.id, t.address, chk.supply))
    out.sort()
    return [(tid, addr, sup) for _, _, tid, addr, sup in out[:config.LAUNCH_FORENSICS_PER_TICK]]


def store(db, token_id: int, lf) -> TokenLaunchForensics:
    row = db.execute(select(TokenLaunchForensics).where(
        TokenLaunchForensics.token_id == token_id)).scalar_one_or_none()
    if row is None:
        row = TokenLaunchForensics(token_id=token_id)
        db.add(row)
    for f in FIELDS:
        v = getattr(lf, f)
        setattr(row, f, v[:200] if isinstance(v, str) and f == "error" else v)
    row.error = row.error or None
    row.wallets_json = lf.wallets[:25]
    row.analysed_at = dt.datetime.utcnow()
    return row


async def run_launch_forensics(session_factory, provider: LaunchForensicsProvider | None = None) -> dict:
    if not config.LAUNCH_FORENSICS_ENABLED:
        return {"status": "disabled"}
    with session_factory() as db:
        todo = candidates(db)
    if not todo:
        return {"analysed": 0}
    prov = provider or LaunchForensicsProvider()
    res = {"analysed": 0, "ok": 0, "other": 0}
    for tid, mint, supply in todo:
        lf = await prov.analyse(mint, supply)
        with session_factory() as db:
            store(db, tid, lf)
            db.commit()
        res["analysed"] += 1
        res["ok" if lf.status == "ok" else "other"] += 1
    res["rpc_calls"] = prov.calls
    return res


def launch_facts(row: TokenLaunchForensics | None) -> dict | None:
    """Compact dict for the risk engine and launch alerts. None = not analysed yet."""
    if row is None:
        return None
    d = {f: getattr(row, f) for f in FIELDS if f != "error"}
    d["flags"] = forensic_flags(row)
    return d


def latest_launch_rows(db, token_ids: list[int]) -> dict[int, TokenLaunchForensics]:
    if not token_ids:
        return {}
    rows = db.execute(select(TokenLaunchForensics).where(
        TokenLaunchForensics.token_id.in_(token_ids))).scalars().all()
    return {r.token_id: r for r in rows}
