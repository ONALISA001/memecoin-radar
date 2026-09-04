"""Simulated execution (v4 §7/§8). Memecoin-aware cost model:

    slippage_pct = base
                 + impact_weight × 100 × size_usd / pool_liquidity_usd   (price impact)
                 + vol_weight × |price_change_1h|                        (fast tape)
    (both capped; then clamped to a sane range)

Every fill:  exec_price = ref_price × (1 ± slip)   (sign follows the trade side)
fees charged as fee_pct of notional on both entry and exit.
Nothing here ever touches a chain, wallet or order book (§26).
"""
from __future__ import annotations

import math


def slippage_pct(size_usd: float, liquidity_usd: float, price_chg_1h: float,
                 side: str, exec_cfg: dict) -> float:
    base = float(exec_cfg.get("base_slippage_pct", 0.35))
    impact = 0.0
    liq = max(float(liquidity_usd or 0.0), 1.0)
    impact = float(exec_cfg.get("impact_weight", 0.6)) * 100.0 * float(size_usd) / liq
    impact = min(impact, float(exec_cfg.get("impact_cap_pct", 6.0)))
    vol = min(float(exec_cfg.get("vol_weight", 0.02)) * abs(float(price_chg_1h or 0.0)),
              float(exec_cfg.get("vol_cap_pct", 2.0)))
    slip = base + impact + vol
    # thin books are penalized regardless of order size
    if liq < 10000:
        slip += 1.0
    elif liq < 30000:
        slip += 0.35
    return min(slip, 12.0)


def fee_usd(notional: float, exec_cfg: dict) -> float:
    return round(notional * float(exec_cfg.get("fee_pct", 0.3)) / 100.0, 6)


def buy_fill(ref_price: float, size_usd: float, liquidity_usd: float,
             price_chg_1h: float, exec_cfg: dict) -> dict:
    """Simulate a BUY fill. Fees are deducted from the notional first, which
    slightly reduces quantity bought (§7 example math)."""
    slip = slippage_pct(size_usd, liquidity_usd, price_chg_1h, "BUY", exec_cfg)
    exec_price = ref_price * (1.0 + slip / 100.0)
    fee = fee_usd(size_usd, exec_cfg)
    cost = max(size_usd - fee, 0.0)
    qty = cost / exec_price if exec_price > 0 else 0.0
    return {"exec_price": exec_price, "slip_pct": round(slip, 3),
            "fee": fee, "cost": cost, "qty": qty}


def sell_fill(ref_price: float, qty: float, liquidity_usd: float,
              price_chg_1h: float, exec_cfg: dict) -> dict:
    notional = qty * ref_price
    slip = slippage_pct(notional, liquidity_usd, price_chg_1h, "SELL", exec_cfg)
    exec_price = ref_price * (1.0 - slip / 100.0)
    gross = qty * exec_price
    fee = fee_usd(gross, exec_cfg)
    return {"exec_price": exec_price, "slip_pct": round(slip, 3),
            "fee": fee, "proceeds": max(gross - fee, 0.0)}
