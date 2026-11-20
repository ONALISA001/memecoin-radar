"""Historical strategy backtest + replay frames (v4 §20–§21).

Uses only data that existed at time t: signal events with ts ≤ t, price
snapshots with ts ≤ t, score snapshots (momentum, sell pressure, regime …) with
ts ≤ t and verdicts created ≤ t. Fills use price at/before the signal timestamp.
That is structurally look-ahead-free (§33 reliability, §34 no fabricated results
— if a token lacks price coverage the trade is skipped and counted as data-gap).

The same strategy core as live trading: risk gates (paper/risk.py), the
momentum-confluence rules (paper/confluence.py), market filters (chase, buy
share, wash volume), AI-verdict rules, re-entry cooldown, break-even stop,
trailing activation, momentum / regime / time-stop exits. Not modelled (no
point-in-time history): Helius contract and wallet checks, launch status, coin
age — those settings are reported in `not_modelled`.
"""
from __future__ import annotations

import bisect
import datetime as dt

from sqlalchemy import select

from ..models import PriceSnapshot, SignalEvent, Token, TokenVerdict, TrendScore
from . import config as PC
from . import confluence as CF
from . import execution as X
from . import filters as F
from . import risk as R

SNAP_MAX_AGE = 900          # a score snapshot older than 15 min at time t is "no data"
_UNMODELLED = ("require_verified_contract", "max_top10_wallet_pct", "fresh_pool_only",
               "min_token_age_h", "max_token_age_h", "priority_entries")


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


class History:
    """Point-in-time score snapshots (per coin) + market breadth, for the confluence rules."""

    def __init__(self, db, start: int, end: int):
        self.by_tok: dict[int, list[tuple[int, dict]]] = {}
        up: dict[int, list[int]] = {}
        for tid, ts, comp in db.execute(select(TrendScore.token_id, TrendScore.ts, TrendScore.components_json)
                                        .where(TrendScore.ts >= start - 4 * 3600, TrendScore.ts <= end)
                                        .order_by(TrendScore.ts.asc())).all():
            s = (comp or {}).get("summary") or {}
            vol = s.get("volume_g_1h")
            snap = {"momentum": s.get("momentum"), "sellp": s.get("sellp"),
                    "regime": {"emerging_momentum": "BULLISH", "potential_distribution": "DISTRIBUTION"}
                    .get(s.get("confluence"), "NEUTRAL"),
                    "volume_g_1h": vol if vol is not None else s.get("vol_accel_h1"),
                    "price_chg_1h": s.get("price_change_h1"), "price_chg_24h": s.get("price_change_h24"),
                    "buy_share_pct": None if s.get("buy_share") is None else 100.0 * s["buy_share"],
                    "liq": s.get("liquidity"), "volume_24h": s.get("volume_24h"), "mcap": s.get("mcap")}
            self.by_tok.setdefault(tid, []).append((ts, snap))
            if snap["price_chg_24h"] is not None:
                b = up.setdefault(ts - ts % 300, [0, 0])
                b[0] += int(snap["price_chg_24h"] > 0)
                b[1] += 1
        self.breadth_ts = sorted(up)
        self.breadth_v = [up[t][0] / up[t][1] for t in self.breadth_ts]
        self._ts = {tid: [t for t, _ in rows] for tid, rows in self.by_tok.items()}

    def at(self, tid: int, t: int) -> dict | None:
        ts = self._ts.get(tid)
        if not ts:
            return None
        i = bisect.bisect_right(ts, t) - 1
        if i < 0 or t - ts[i] > SNAP_MAX_AGE:
            return None
        rows = self.by_tok[tid]
        snap = dict(rows[i][1])
        snap["momentum_hist"] = [r[1]["momentum"] for r in rows[max(0, i - 2):i + 1] if r[1]["momentum"] is not None]
        j = bisect.bisect_right(self.breadth_ts, t) - 1
        snap["breadth"] = self.breadth_v[j] if j >= 0 and t - self.breadth_ts[j] <= SNAP_MAX_AGE else None
        return snap


class Verdicts:
    """Latest verdict per coin as of time t."""

    def __init__(self, db, end: int):
        self.rows: dict[int, list[tuple[int, str]]] = {}
        for tid, created, rating in db.execute(select(TokenVerdict.token_id, TokenVerdict.created_at,
                                                      TokenVerdict.rating).order_by(TokenVerdict.id)).all():
            if created is None:
                continue
            ts = int((created if created.tzinfo else created.replace(tzinfo=dt.timezone.utc)).timestamp())
            if ts <= end:
                self.rows.setdefault(tid, []).append((ts, rating))

    def at(self, tid: int, t: int) -> tuple[str, float] | None:
        rows = self.rows.get(tid) or []
        i = bisect.bisect_right([r[0] for r in rows], t) - 1
        return None if i < 0 else (rows[i][1], (t - rows[i][0]) / 3600)


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


def market_checks(S: dict, snap: dict | None) -> list[tuple[str, bool, str]]:
    """Point-in-time versions of the market-based strategy-lab filters."""
    out = []
    snap = snap or {}
    if S.get("max_chase_pct_1h", 0) > 0:
        c = snap.get("price_chg_1h") or 0.0
        out.append(("not chasing", c <= S["max_chase_pct_1h"], f"1h {c:+.0f}%"))
    if S.get("min_buy_share_pct", 0) > 0:
        b = snap.get("buy_share_pct")
        out.append(("buyers in control", b is not None and b >= S["min_buy_share_pct"], f"buys {b}"))
    if S.get("max_vol_liq_ratio", 0) > 0:
        liq, vol = snap.get("liq") or 0, snap.get("volume_24h") or 0
        r = vol / liq if liq > 0 else None
        out.append(("volume vs liquidity", r is not None and r <= S["max_vol_liq_ratio"], f"{r}"))
    return out


def verdict_checks(S: dict, v: tuple[str, float] | None) -> list[tuple[str, bool, str]]:
    vf, block = int(S.get("verdict_filter") or 0), bool(S.get("block_bad_verdicts"))
    if not (vf or block):
        return []
    max_age = float(S.get("verdict_max_age_h") or 0) or 1e9
    fresh = v is not None and v[1] <= max_age
    out = []
    if block:
        out.append(("AI verdict not negative", not (fresh and v[0] in F.BAD_VERDICTS), str(v)))
    if vf:
        out.append(("AI verdict", fresh and v[0] in F.GOOD_VERDICTS.get(vf, F.GOOD_VERDICTS[1]), str(v)))
    return out


def run(db, start: int, end: int, capital: float, overrides: dict, mode: str = "backtest",
        _cache: dict | None = None) -> dict:
    """`_cache` lets compare_family() share the loaded history between strategies."""
    S = PC.merged_settings(overrides)
    cache = _cache if _cache is not None else {}
    if "events" not in cache:
        cache["events"], cache["prices"], cache["syms"] = _load(db, start, end)
    events, prices, syms = cache["events"], cache["prices"], cache["syms"]
    need_hist = any(S.get(k) for k in ("min_momentum", "momentum_rising", "min_volume_accel_pct",
                                       "require_bullish_regime", "block_distribution_regime",
                                       "max_entry_sell_pressure", "min_market_breadth_pct",
                                       "confluence_sizing", "momentum_exit_below", "regime_exit",
                                       "max_chase_pct_1h", "min_buy_share_pct", "max_vol_liq_ratio"))
    if need_hist and "hist" not in cache:
        cache["hist"] = History(db, start, end)
    hist: History | None = cache.get("hist") if need_hist else None
    need_v = bool(S.get("verdict_filter") or S.get("block_bad_verdicts") or S.get("verdict_exit"))
    if need_v and "verdicts" not in cache:
        cache["verdicts"] = Verdicts(db, end)
    verdicts: Verdicts | None = cache.get("verdicts") if need_v else None

    equity_curve: list[tuple[int, float]] = []
    cash = capital
    peak = capital
    max_dd = 0.0
    open_pos: dict[int, dict] = {}
    last_entry: dict[int, int] = {}
    trades: list[dict] = []
    rejected = 0
    reject_reasons: dict[str, int] = {}
    data_gaps = 0
    frames: list[dict] = []
    ex = S["execution"]
    rt_cost = 2 * (float(ex.get("fee_pct", 0.3)) + float(ex.get("base_slippage_pct", 1.0))
                   + float(ex.get("half_spread_pct", 0.0)))
    tp, be = float(S["take_profit_pct"]), float(S.get("breakeven_at_pct") or 0)
    arm = 1 + max(2.0, float(S.get("trail_activate_pct") or 0)) / 100.0

    def reject(why: str):
        nonlocal rejected
        rejected += 1
        reject_reasons[why] = reject_reasons.get(why, 0) + 1

    def eval_exit(p: dict, t: int, px: float, reason: str):
        nonlocal cash
        fill = X.sell_fill(px, p["qty"], 30000, 0, ex)  # historical pool depth unknown → medium book
        proceeds = fill["proceeds"]
        pnl = round(proceeds - p["cost"], 4)
        cash += proceeds
        p.update({"exit_ts": t, "exit_price": fill["exec_price"], "exit_reason": reason,
                  "pnl": pnl, "pnl_pct": round(100 * pnl / max(p["size"], 1e-9), 2),
                  "hold_secs": t - p["entry_ts"]})
        trades.append(p)
        frames.append({"ts": t, "type": "SELL", "token": p["symbol"],
                       "reason": reason, "pnl": round(pnl, 2), "cash": round(cash, 2)})

    price_times = sorted({ts for ser in prices.values() for ts, _ in ser})
    ev_by_ts: dict[int, list] = {}
    for e in events:
        ev_by_ts.setdefault(e.ts, []).append(e)
    merged = sorted(set(price_times) | set(ev_by_ts))

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
            if be > 0 and p["peak"] >= p["entry"] * (1 + be / 100):
                p["stop"] = max(p["stop"], p["entry"] * (1 + rt_cost / 100))
            reason = None
            if px <= p["stop"]:
                reason = "BREAKEVEN_STOP" if be > 0 and p["stop"] >= p["entry"] else "STOP_LOSS"
            elif p["target"] is not None and px >= p["target"]:
                reason = "TAKE_PROFIT"
            elif S["trailing_stop_pct"] > 0 and p["peak"] > p["entry"] * arm \
                    and px <= p["peak"] * (1 - S["trailing_stop_pct"] / 100):
                reason = "TRAILING_STOP"
            elif hist is not None and (cf := CF.exit_reason(S, hist.at(tid, t) or {}, p["entry_ts"], t,
                                                             p["entry"], px)):
                reason = cf
            elif S.get("verdict_exit") and verdicts is not None:
                v = verdicts.at(tid, t)
                if v and v[0] in F.BAD_VERDICTS and v[1] * 3600 <= t - p["entry_ts"]:
                    reason = "VERDICT_EXIT"
            if reason is None and t - p["entry_ts"] >= S["max_hold_hours"] * 3600:
                reason = "MAX_HOLD"
            if reason:
                eval_exit(p, t, px, reason)
                del open_pos[tid]

        # process events at t
        allowed = S.get("allowed_kinds") or []
        for e in ev_by_ts.get(t, []):
            tid = e.token_id
            if e.kind in PC.SELL_KINDS and tid in open_pos:
                ser = prices.get(tid)
                px = ser and price_at(ser, t) or 0
                if px > 0:
                    eval_exit(open_pos.pop(tid), t, px, "SELL_SIGNAL")
                continue
            if e.kind not in PC.BUY_KINDS or tid in open_pos or (allowed and e.kind not in allowed):
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
                reject(next((c["name"] for c in decision["checks"] if not c["passed"]), "risk gate"))
                continue
            snap = hist.at(tid, t) if hist is not None else None
            if hist is not None and snap is None:
                data_gaps += 1
                continue
            extra = []
            if snap is not None:
                if snap.get("volume_g_1h") is None:
                    snap["volume_g_1h"] = m.get("volume_g_1h")
                extra += CF.entry_checks(S, snap) + market_checks(S, snap)
            if verdicts is not None:
                extra += verdict_checks(S, verdicts.at(tid, t))
            cool = float(S.get("reentry_cooldown_h") or 0)
            if cool > 0 and tid in last_entry:
                extra.append(("re-entry cooldown", t - last_entry[tid] >= cool * 3600, ""))
            failed = [name for name, ok, _ in extra if not ok]
            if failed:
                reject(failed[0])
                continue
            mult, n_fac = CF.size_multiplier(S, snap) if snap is not None else (1.0, 0)
            if mult is None:
                reject("confluence")
                continue
            size = min(R.position_size(S, pf), R.loss_cap_size(S, pf, R.position_size(S, pf), S["stop_loss_pct"]))
            size = round(size * mult, 2)
            if size < ex["min_order_usd"]:
                reject("order below minimum")
                continue
            fill = X.buy_fill(px, size, liq, m.get("volume_g_1h") or 0, ex)
            cash -= size
            last_entry[tid] = t
            open_pos[tid] = {
                "token_id": tid, "symbol": syms.get(tid, "?"),
                "entry_ts": t, "entry": fill["exec_price"], "signal_price": px,
                "slip": fill["slip_pct"], "qty": fill["qty"], "size": size,
                "cost": fill["cost"], "fee": fill["fee"],
                "confidence": e.confidence or 0, "kind": e.kind, "factors": n_fac,
                "peak": fill["exec_price"],
                "stop": fill["exec_price"] * (1 - S["stop_loss_pct"] / 100),
                "target": fill["exec_price"] * (1 + tp / 100) if tp > 0 else None}   # 0 = let it run
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
    open_pos.clear()

    closes = [t for t in trades if t.get("pnl") is not None]
    wins = [t for t in closes if t["pnl"] >= 0]
    total_pnl = sum(t["pnl"] for t in closes)
    rets = [t["pnl_pct"] for t in closes]
    exits: dict[str, int] = {}
    for t in closes:
        exits[t["exit_reason"]] = exits.get(t["exit_reason"], 0) + 1
    from .lab import mean_ci
    ci = mean_ci(rets)
    summary = {
        "start_ts": start, "end_ts": end, "starting_capital": capital,
        "ending_equity": round(cash, 2),
        "total_return_pct": round(100 * (cash - capital) / capital, 2),
        "trades": len(closes), "signals_seen": len(events),
        "rejected_signals": rejected, "data_gaps": data_gaps,
        "top_rejections": sorted(reject_reasons.items(), key=lambda kv: -kv[1])[:12],
        "wins": len(wins), "losses": len(closes) - len(wins),
        "win_rate_pct": round(100 * len(wins) / len(closes), 1) if closes else None,
        "profit_factor": round(sum(t["pnl"] for t in wins) /
                               max(abs(sum(t["pnl"] for t in closes if t["pnl"] < 0)), 1e-9), 2)
                               if any(t["pnl"] < 0 for t in closes) else None,
        "max_drawdown_pct": round(max_dd, 2),
        "best_trade": round(max((t["pnl"] for t in closes), default=0), 2),
        "worst_trade": round(min((t["pnl"] for t in closes), default=0), 2),
        "avg_trade": round(total_pnl / len(closes), 2) if closes else None,
        "avg_trade_pct": round(sum(rets) / len(rets), 2) if rets else None,
        "avg_trade_ci95": list(ci) if ci else None,
        "exits": exits,
        "not_modelled": [k for k in _UNMODELLED if S.get(k)],
        "params": S if mode == "backtest" else None,
        "note": ("Backtest uses recorded signal events, stored 5-min price and score snapshots only "
                 "(no look-ahead: signals at ts t, fills at price ≤ t). Same entry rules, sizing and exits "
                 "as live trading; slippage+fees from settings. Pool depth at historical times is "
                 "approximated — medium-book assumption."
                 + (f" {data_gaps} signals skipped for missing price or score data (§34)." if data_gaps else "")),
    }
    out = {"summary": summary,
           "equity_curve": [{"ts": t, "equity": v} for t, v in equity_curve],
           "trades": sorted(closes, key=lambda x: -abs(x.get("pnl") or 0))[:200]}
    if mode == "replay":
        out["frames"] = frames[:2000]
        out["frame_times"] = merged[:: max(1, len(merged) // 360)]
    return out


def compare_family(db, start: int, end: int, capital: float, keys: tuple[str, ...] = PC.MOMENTUM_FAMILY) -> dict:
    """Run several Strategy Lab presets over the SAME window and data, side by side."""
    cache: dict = {}
    rows = []
    for k in keys:
        if k not in PC.PRESETS:
            continue
        res = run(db, start, end, capital, PC.PRESETS[k]["settings"], _cache=cache)
        s = res["summary"]
        rows.append({"key": k, "name": PC.PRESETS[k]["name"], "summary": PC.PRESETS[k]["summary"],
                     **{f: s.get(f) for f in ("trades", "win_rate_pct", "total_return_pct", "profit_factor",
                                              "max_drawdown_pct", "avg_trade_pct", "avg_trade_ci95",
                                              "rejected_signals", "top_rejections", "exits", "data_gaps",
                                              "not_modelled")},
                     "equity_curve": res["equity_curve"]})
    base = next((r for r in rows if r["key"] == "baseline"), None)
    base_rules = {name for name, _ in (base or {}).get("top_rejections") or []}
    for r in rows:
        # the strategy's own filter that blocked most trades (rules the baseline doesn't have)
        own = [x for x in r.get("top_rejections") or [] if x[0] not in base_rules]
        r["own_top_rejection"] = own[0] if own and r["key"] != "baseline" else None
        r["vs_baseline_pp"] = None if base is None or r["key"] == "baseline" else \
            round((r["total_return_pct"] or 0) - (base["total_return_pct"] or 0), 2)
        ci = r.get("avg_trade_ci95")
        r["edge"] = None if not ci else "positive" if ci[0] > 0 else "negative" if ci[1] < 0 else "unclear"
    ranked = sorted([r for r in rows if (r["trades"] or 0) > 0],
                    key=lambda r: ((r["profit_factor"] or 0) if (r["trades"] or 0) >= 10 else -1,
                                   r["total_return_pct"] or 0), reverse=True)
    return {"start_ts": start, "end_ts": end, "capital": capital, "strategies": rows,
            "leader": ranked[0]["key"] if ranked and (ranked[0]["trades"] or 0) >= 10 else None,
            "signals_seen": len(cache.get("events") or []),
            "note": "Same window, same data, same costs for every strategy. Ranked by profit factor once a "
                    "strategy has 10+ trades; fewer trades is noise. Helius contract/wallet checks and coin "
                    "age are not replayed (no point-in-time history)."}
