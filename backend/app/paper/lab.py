"""Strategy Lab — every preset trades in its own $10k virtual account, side by
side with the main account, on the same live market. Nothing here touches the
main account except apply_to_main(), which the user triggers explicitly."""
from __future__ import annotations

import time

from sqlalchemy import func, select

from ..models import DemoAccount, EquitySnapshot, PaperPosition, PaperSignal, SignalEvent, StrategyVersion
from . import config as PC
from . import engine as PE

LAB_CASH = 10000.0


def _version(db, key: str) -> StrategyVersion:
    name = PC.PRESETS[key]["name"]
    settings = PC.merged_settings(PC.PRESETS[key]["settings"])
    ver = db.execute(select(StrategyVersion).where(StrategyVersion.name == name)
                     .order_by(StrategyVersion.id.desc()).limit(1)).scalar_one_or_none()
    if ver is None or ver.config_json != settings:
        n = db.execute(select(func.count()).select_from(StrategyVersion)
                       .where(StrategyVersion.name == name)).scalar() or 0
        ver = StrategyVersion(name=name, version=f"1.{n}", config_json=settings)
        db.add(ver)
        db.flush()
    return ver


def lab_accounts(db, user_id: int) -> dict[str, DemoAccount]:
    rows = db.execute(select(DemoAccount).where(DemoAccount.user_id == user_id,
                                                DemoAccount.active == True,  # noqa: E712
                                                DemoAccount.strategy_key.isnot(None))).scalars().all()
    return {a.strategy_key: a for a in rows}


def start_lab(db, user_id: int) -> dict[str, DemoAccount]:
    """Create any missing preset accounts. Idempotent; existing ones keep running."""
    have = lab_accounts(db, user_id)
    # a preset the owner retired (supervisor proposal) stays retired
    retired = {k for (k,) in db.execute(select(DemoAccount.strategy_key).where(
        DemoAccount.user_id == user_id, DemoAccount.active == False,  # noqa: E712
        DemoAccount.strategy_key.isnot(None))).all()}
    edge = db.execute(select(func.max(SignalEvent.id))).scalar() or 0   # never trade stale signals
    for key, p in PC.PRESETS.items():
        if key in have or key in retired:
            continue
        acct = DemoAccount(user_id=user_id, label=f"Lab · {p['name']}", strategy_key=key,
                           starting_cash=LAB_CASH, cash=LAB_CASH, peak_equity=LAB_CASH,
                           settings_json=dict(p["settings"]), strategy_version_id=_version(db, key).id,
                           last_event_id=edge)
        db.add(acct)
        db.flush()
        PE._event(db, acct, None, None, "SYSTEM", f"Strategy Lab started — {p['name']}",
                  f"{p['summary']} Virtual funds only (${LAB_CASH:,.0f}).", {"preset": key})
        have[key] = acct
    return have


def reset_lab(db, user_id: int) -> None:
    for acct in lab_accounts(db, user_id).values():
        PE.reset_account(db, acct, LAB_CASH)
        acct.settings_json = dict(PC.PRESETS[acct.strategy_key]["settings"]) if acct.strategy_key in PC.PRESETS \
            else acct.settings_json


def mean_ci(values: list[float], n_boot: int = 2000, seed: int = 7) -> tuple[float, float] | None:
    """95% bootstrap interval for the average trade return (needs ≥10 trades).
    If the whole range is above 0 the strategy has shown an edge; if it spans 0
    the results so far are compatible with luck."""
    if len(values) < 10:
        return None
    import random
    rnd = random.Random(seed)
    n = len(values)
    means = sorted(sum(rnd.choice(values) for _ in range(n)) / n for _ in range(n_boot))
    return round(means[int(0.025 * n_boot)], 2), round(means[int(0.975 * n_boot) - 1], 2)


def _stats(db, acct: DemoAccount, market: dict) -> dict:
    pf = PE.portfolio_state(db, acct, market)
    closed = db.execute(select(PaperPosition).where(PaperPosition.account_id == acct.id,
                                                    PaperPosition.status == "closed")).scalars().all()
    pnls = [p.realized_pnl or 0.0 for p in closed]
    rets = [p.pnl_pct or 0.0 for p in closed]
    ci = mean_ci(rets)
    wins, losses = [x for x in pnls if x >= 0], [x for x in pnls if x < 0]
    max_dd = db.execute(select(func.max(EquitySnapshot.drawdown_pct))
                        .where(EquitySnapshot.account_id == acct.id)).scalar() or 0.0
    signals = db.execute(select(func.count()).select_from(PaperSignal)
                         .where(PaperSignal.account_id == acct.id)).scalar() or 0
    start = acct.starting_cash or LAB_CASH
    exits: dict[str, int] = {}
    for p in closed:
        exits[p.exit_reason or "?"] = exits.get(p.exit_reason or "?", 0) + 1
    return {
        "equity": round(pf["equity"], 2), "return_pct": round(100 * (pf["equity"] - start) / start, 2),
        "trades": len(closed), "open": pf["open_count"], "signals_seen": signals,
        "win_rate_pct": round(100 * len(wins) / len(closed), 1) if closed else None,
        "profit_factor": (round(sum(wins) / abs(sum(losses)), 2) if losses else None),
        "avg_win": round(sum(wins) / len(wins), 2) if wins else None,
        "avg_loss": round(sum(losses) / len(losses), 2) if losses else None,
        "best": round(max(pnls), 2) if pnls else None, "worst": round(min(pnls), 2) if pnls else None,
        "max_drawdown_pct": round(max(max_dd, pf["drawdown_pct"]), 2),
        "exits": exits, "halt": PE.halt_status(acct),
        "avg_trade_pct": round(sum(rets) / len(rets), 2) if rets else None,
        "avg_trade_ci95": list(ci) if ci else None,
        "edge": (None if not ci else "positive" if ci[0] > 0 else "negative" if ci[1] < 0 else "unclear"),
    }


def compare(db, user_id: int, market: dict) -> dict:
    accts = lab_accounts(db, user_id)
    main = PE.get_or_create_account(db, user_id)
    retired = {k for (k,) in db.execute(select(DemoAccount.strategy_key).where(
        DemoAccount.user_id == user_id, DemoAccount.active == False,  # noqa: E712
        DemoAccount.strategy_key.isnot(None))).all()}
    rows = []
    for key, p in PC.PRESETS.items():
        a = accts.get(key)
        row = {"key": key, "name": p["name"], "summary": p["summary"], "running": a is not None,
               "retired": a is None and key in retired,
               "family": "momentum" if (key in PC.MOMENTUM_FAMILY and key != "baseline") or key == "priority"
               else "core",
               "since": int(a.created_at.timestamp()) if a is not None and a.created_at else None}
        if a is not None:
            row.update(_stats(db, a, market))
        rows.append(row)
    # challengers started by approved supervisor proposals
    for key, a in accts.items():
        if key in PC.PRESETS:
            continue
        rows.append({"key": key, "name": (a.label or key).replace("Lab · ", ""),
                     "summary": "Started from a supervisor proposal: a copy of a strategy with one change "
                                "learned from its losing trades.",
                     "running": True, "retired": False, "family": "challenger",
                     "since": int(a.created_at.timestamp()) if a.created_at else None,
                     **_stats(db, a, market)})
    main_row = {"key": "main", "name": "Your main account", "summary": "Your own settings.",
                "running": True, **_stats(db, main, market)}
    # rank by return, but flag samples too small to mean anything; a strategy only
    # "leads" if it has enough trades AND its 95% range of average trade is above 0
    for r in rows:
        r["enough_trades"] = (r.get("trades") or 0) >= 30
        r["proven_edge"] = bool(r["enough_trades"] and r.get("edge") == "positive")
    return {"running": bool(accts), "presets": rows, "main": main_row, "min_trades_for_verdict": 30,
            "ts": int(time.time())}


def apply_to_main(db, user_id: int, key: str) -> str:
    main = PE.get_or_create_account(db, user_id)
    if key in PC.PRESETS:
        return PE.apply_settings(db, main, dict(PC.PRESETS[key]["settings"]))
    ch = lab_accounts(db, user_id).get(key)      # a running challenger
    if ch is None:
        raise KeyError(key)
    return PE.apply_settings(db, main, dict(ch.settings_json or {}))
