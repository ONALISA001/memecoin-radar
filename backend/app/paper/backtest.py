"""Historical strategy backtest + replay frames (v4 §20–§21).

Uses only data that existed at time t: signal events with ts ≤ t, price
snapshots with ts ≤ t. Signal ts ≥ decision ts; fills use price at/before the
signal timestamp. That is structurally look-ahead-free (§33 reliability, §34
no fabricated results — if a token lacks price coverage the trade is skipped
and counted as data-gap).

Exit logic in backtest mode is price-driven (stop / target / trailing / max
hold) plus AI sell-signals (their own timestamps) — the same strategy core
used live, reusing the risk/execution modules rather than duplicating logic
(§1: one code path for backtest, paper, replay).
"""
from __future__ import annotations

from sqlalchemy import select

from ..models import SignalEvent, PriceSnapshot, Token
from . import config as PC
from . import execution as X
from . import risk as R


def _load(db, start: int, end: int):
    events = db.execute(select(SignalEvent).where(
        SignalEvent.ts >= start, SignalEvent.ts <= end,
        SignalEvent.category.isnot(None), SignalEvent.category != "paper",
        SignalEvent.kind.in_(PC.BUY_KINDS + PC.SELL_KINDS))
        .order_by(SignalEvent.ts.asc())).scalars().all()
    prices: dict[int, list[tuple[int, float]]] = {}
    for r in db.execute(select(PriceSnapshot.token_id, PriceSnapshot.ts, PriceSnapshot.price_usd)
                        .where(PriceSnapshot.ts >= start - 900, PriceSnapshot.ts <= end)
                        .order_by(PriceSnapshot.ts.asc())).all():
        if r.price_usd:
            prices.setdefault(r.token_id, []).append((r.ts, r.price_usd))
    syms = {t.id: t.symbol or t.address[:8] for t in
            db.execute(select(Token)).scalars().all()}
    return events, prices, syms


def price_at(series: list[tuple[int, float]], ts: int) -> float:
    lo, hi = 0, len(series) - 1
    best = 0.0
    while lo <= hi:
        mid = (lo + hi) // 2
        if series[mid][0] <= ts:
            best = series[mid][1]
            lo = mid + 1
        else:
            hi = mid - 1
    return best


def run(db, start: int, end: int, capital: float, overrides: dict, mode: str = "backtest") -> dict:
    S = PC.merged_settings(overrides)
    events, prices, syms = _load(db, start, end)
    equity_curve: list[tuple[int, float]] = []
    cash = capital
    peak = capital
    max_dd = 0.0
    open_pos: dict[int, dict] = {}
    trades: list[dict] = []
    rejected = 0
    data_gaps = 0
    frames: list[dict] = []

    def eval_exit(p: dict, t: int, px: float, reason: str):
        nonlocal cash, trades, peak, max_dd
        fill = X.sell_fill(px, p["qty"], 30000, 0, S["execution"])  # historical pool depth unknown → medium book
        proceeds = fill["proceeds"]
        pnl = round(proceeds - p["cost"], 4)
        cash += proceeds
        p.update({"exit_ts": t, "exit_price": fill["exec_price"], "exit_reason": reason,
                  "pnl": pnl, "pnl_pct": round(100 * pnl / max(p["size"], 1e-9), 2),
                  "hold_secs": t - p["entry_ts"]})
        trades.append(p)
        frames.append({"ts": t, "type": "SELL", "token": p["symbol"],
                       "reason": reason, "pnl": round(pnl, 2), "cash": round(cash, 2)})

    # master loop over union of event times and price times
    price_times = sorted({ts for ser in prices.values() for ts, _ in ser})
    event_times = [e.ts for e in events]
    ev_by_ts: dict[int, list] = {}
    for e in events:
        ev_by_ts.setdefault(e.ts, []).append(e)
    merged = sorted(set(price_times + event_times))

    for t in merged:
        # exits on open positions first (price info at t)
        for tid in list(open_pos.keys()):
            ser = prices.get(tid)
            if not ser:
                continue
            px = price_at(ser, t)
            if px <= 0:
                continue
            p = open_pos[tid]
            p["peak"] = max(p["peak"], px)
            reason = None
            if px <= p["stop"]:
                reason = "STOP_LOSS"
            elif px >= p["target"]:
                reason = "TAKE_PROFIT"
            elif S["trailing_stop_pct"] > 0 and p["peak"] > p["entry"] * 1.02 \
                    and px <= p["peak"] * (1 - S["trailing_stop_pct"] / 100):
                reason = "TRAILING_STOP"
            elif t - p["entry_ts"] >= S["max_hold_hours"] * 3600:
                reason = "MAX_HOLD"
            if reason:
                eval_exit(p, t, px, reason)
                del open_pos[tid]

        # process events at t
        for e in ev_by_ts.get(t, []):
            tid = e.token_id
            if e.kind in PC.SELL_KINDS and tid in open_pos:
                ser = prices.get(tid)
                px = ser and price_at(ser, t) or 0
                if px > 0:
                    eval_exit(open_pos.pop(tid), t, px, "SELL_SIGNAL")
                continue
            if e.kind not in PC.BUY_KINDS or tid in open_pos:
                continue
            ser = prices.get(tid)
            px = ser and price_at(ser, t) or 0
            m = e.metrics_json or {}
            liq = m.get("liquidity") or 0
            if px <= 0 or liq <= 0:
                data_gaps += 1
                continue
            pf = {"equity": cash + sum(p["qty"] * (price_at(prices[t2], t)
                  if prices.get(t2) else p["entry"]) for t2, p in open_pos.items()),
                  "cash": cash, "invested": sum(p["size"] for p in open_pos.values()),
                  "open_count": len(open_pos), "drawdown_pct": 100 * (peak - cash) / max(peak, 1e-9)}
            checks = e.checklist_json or []
            decision = R.evaluate_signal(
                S, {"confidence": e.confidence or 0,
                    "risk_score": m.get("risk", 50),
                    "metrics": {"liquidity": liq, "volume_24h": m.get("volume_24h") or m.get("vol_15m_avg_usd", 0) * 96,
                                "mcap": m.get("mcap") or 0},
                    "why_passed": sum(1 for c in checks if c.get("passed")),
                    "importance": e.importance},
                pf, {"day_realized": 0, "consec_losses": 0, "trading_allowed": True})
            if not decision["approved"]:
                rejected += 1
                continue
            size = min(R.position_size(S, pf), R.loss_cap_size(S, pf, R.position_size(S, pf), S["stop_loss_pct"]))
            if size < S["execution"]["min_order_usd"]:
                rejected += 1
                continue
            fill = X.buy_fill(px, size, liq, m.get("volume_g_1h") or 0, S["execution"])
            cash -= size
            open_pos[tid] = {
                "token_id": tid, "symbol": syms.get(tid, "?"),
                "entry_ts": t, "entry": fill["exec_price"], "signal_price": px,
                "slip": fill["slip_pct"], "qty": fill["qty"], "size": size,
                "cost": fill["cost"], "fee": fill["fee"],
                "confidence": e.confidence or 0, "kind": e.kind,
                "peak": fill["exec_price"],
                "stop": fill["exec_price"] * (1 - S["stop_loss_pct"] / 100),
                "target": fill["exec_price"] * (1 + S["take_profit_pct"] / 100)}
            frames.append({"ts": t, "type": "BUY", "token": syms.get(tid, "?"),
                           "kind": e.kind, "conf": e.confidence, "size": round(size, 2),
                           "fill": round(fill["exec_price"], 10)})

        # equity mark
        eq = cash + sum(p["qty"] * price_at(prices[t2], t) if prices.get(t2) else p["qty"] * p["entry"]
                        for t2, p in open_pos.items())
        peak = max(peak, eq)
        max_dd = max(max_dd, 100 * (peak - eq) / peak if peak > 0 else 0)
        if t == merged[0] or t == merged[-1] or t - (equity_curve[-1][0] if equity_curve else 0) >= 1800:
            equity_curve.append((t, round(eq, 2)))

    # close any positions still open at end at last available price
    for tid, p in list(open_pos.items()):
        ser = prices.get(tid)
        px = ser and price_at(ser, end) or 0
        if px > 0:
            eval_exit(p, min(end, p["entry_ts"] + 10**9), px, "END_OF_WINDOW")
        else:
            data_gaps += 1
            trades.append({**p, "exit_ts": end, "exit_price": None,
                           "exit_reason": "NO_PRICE_DATA", "pnl": None, "pnl_pct": None,
                           "hold_secs": end - p["entry_ts"]})
        open_pos.clear() and None

    closes = [t for t in trades if t.get("pnl") is not None]
    wins = [t for t in closes if t["pnl"] >= 0]
    total_pnl = sum(t["pnl"] for t in closes)
    summary = {
        "start_ts": start, "end_ts": end, "starting_capital": capital,
        "ending_equity": round(cash, 2),
        "total_return_pct": round(100 * (cash - capital) / capital, 2),
        "trades": len(closes), "signals_seen": len(events),
        "rejected_signals": rejected, "data_gaps": data_gaps,
        "wins": len(wins), "losses": len(closes) - len(wins),
        "win_rate_pct": round(100 * len(wins) / len(closes), 1) if closes else None,
        "profit_factor": round(sum(t["pnl"] for t in wins) /
                               max(abs(sum(t["pnl"] for t in closes if t["pnl"] < 0)), 1e-9), 2)
                               if any(t["pnl"] < 0 for t in closes) else None,
        "max_drawdown_pct": round(max_dd, 2),
        "best_trade": round(max((t["pnl"] for t in closes), default=0), 2),
        "worst_trade": round(min((t["pnl"] for t in closes), default=0), 2),
        "avg_trade": round(total_pnl / len(closes), 2) if closes else None,
        "params": S if mode == "backtest" else None,
        "note": ("Backtest uses recorded signal events + stored 5-min price snapshots only "
                 "(no look-ahead: signals at ts t, fills at price ≤ t). Exit engine models "
                 "stop/target/trailing/max-hold + AI sell-signals; slippage+fees from settings. "
                 "Pool depth at historical times is approximated — medium-book assumption."
                 + (f" {data_gaps} signals skipped for missing price data (§34)." if data_gaps else "")),
    }
    out = {"summary": summary,
           "equity_curve": [{"ts": t, "equity": v} for t, v in equity_curve],
           "trades": sorted(closes, key=lambda x: -abs(x.get("pnl") or 0))[:200]}
    if mode == "replay":
        out["frames"] = frames[:2000]
        out["frame_times"] = merged[:: max(1, len(merged) // 360)]
    return out
