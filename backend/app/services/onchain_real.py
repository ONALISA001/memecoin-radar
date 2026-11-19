"""REAL on-chain activity for real tokens (replaces the on-chain simulator).

Per 5-minute bucket we store, in HolderSnapshot:
  * new_wallets    = unique BUYERS in the last 5 min      (GeckoTerminal)
  * active_wallets = unique buyers + unique sellers, 5 min (GeckoTerminal)
  * holders        = latest real holder count            (Helius)
  * top10_pct      = latest top-10 REAL-wallet share      (Helius, pools/burn excluded)
  * whale_net_usd  = change in that top-10 share × market cap, booked in the bucket
                     where a new Helius check landed (>0 = big wallets accumulated)
  * deployer_pct   = None (no real source yet)
Unknown values are stored as NULL, never invented, and the engine skips them.

Holder growth for scoring comes from real_holder_stats (counts ≥5h apart) as an
hourly RATE, because counts are refreshed only every few hours: using the raw
step would turn each re-check into a fake one-hour holder spike.
"""
from __future__ import annotations

import datetime as dt

from sqlalchemy import select

from ..models import HolderSnapshot, Token, TokenRiskCheck


def _epoch(d) -> int:
    if d is None:
        return 0
    if d.tzinfo is None:
        d = d.replace(tzinfo=dt.timezone.utc)
    return int(d.timestamp())


def wallet_checks(db, token_ids: list[int]) -> dict[int, list[TokenRiskCheck]]:
    """Successful checks with real-wallet concentration, oldest → newest."""
    if not token_ids:
        return {}
    rows = db.execute(select(TokenRiskCheck).where(
        TokenRiskCheck.ok.is_(True), TokenRiskCheck.token_id.in_(token_ids),
        TokenRiskCheck.top10_wallet_pct.isnot(None))
        .order_by(TokenRiskCheck.token_id, TokenRiskCheck.checked_at, TokenRiskCheck.id)).scalars().all()
    out: dict[int, list] = {}
    for r in rows:
        out.setdefault(r.token_id, []).append(r)
    return out


def whale_flow_usd(checks: list[TokenRiskCheck], mcap: float, bucket_start: int, bucket_s: int = 300) -> float | None:
    """Top-10 real-wallet share change × mcap, if a NEW check landed in this bucket."""
    if not checks:
        return None
    last = checks[-1]
    landed = _epoch(last.checked_at)
    # booked exactly once: in the bucket right after the one the check landed in
    # (buckets are rewritten every tick, so this is idempotent)
    if len(checks) < 2 or bucket_start != (landed // bucket_s) * bucket_s + bucket_s:
        return 0.0
    prev = checks[-2]
    return round((last.top10_wallet_pct - prev.top10_wallet_pct) / 100.0 * (mcap or 0.0), 2)


def real_holder_row(token: Token, tick: dict | None, checks: list[TokenRiskCheck],
                    holders: dict | None, now_b: int) -> HolderSnapshot:
    tick = tick or {}
    has_gt = "buyers_m5" in tick
    b, s = tick.get("buyers_m5"), tick.get("sellers_m5")
    last = checks[-1] if checks else None
    return HolderSnapshot(
        token_id=token.id, ts=now_b,
        holders=holders["count"] if holders else None,
        top10_pct=last.top10_wallet_pct if last else None,
        deployer_pct=None,
        new_wallets=b if has_gt else None,
        active_wallets=(b + s) if has_gt else None,
        whale_net_usd=whale_flow_usd(checks, tick.get("mcap") or 0.0, now_b),
    )


def apply_real_holder_growth(ctx, holders: dict | None) -> None:
    """Overwrite holder-growth inputs with real Helius-based rates (0 when unknown)."""
    d = ctx.d
    if holders and holders.get("growth_pct") is not None and holders.get("span_h"):
        per_h = holders["growth_pct"] / holders["span_h"]
        d["holders_g_1h"], d["holders_g_15m"] = round(per_h, 3), round(per_h / 4, 3)
        d["holders_g_24h"] = holders["growth_pct"]
    else:
        d["holders_g_1h"] = d["holders_g_15m"] = d["holders_g_24h"] = 0.0
    if holders:
        d["holders_now"] = holders["count"]
    ctx.sources["onchain"] = "helius+geckoterminal"
