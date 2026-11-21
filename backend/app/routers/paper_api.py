"""Paper-trading API (v4 §11–§19, §23–§25, §32). Virtual funds only."""
from __future__ import annotations

import time

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import PlainTextResponse
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..database import get_db
from ..models import (BacktestRun, DemoAccount, EquitySnapshot, PaperPosition,
                      PaperSignal, PaperTradeEvent, PriceSnapshot, Token, User)
from ..security import get_current_user, rate_limit, require_owner, require_user
from ..services.ingestion import latest_market_map
from ..paper import engine as PE
from ..paper import config as PC
from ..paper import backtest as BT

router = APIRouter(dependencies=[Depends(rate_limit), Depends(get_current_user)])


def _acct(db: Session, user: User) -> DemoAccount:
    return PE.get_or_create_account(db, user.id)


def _summary_map(db: Session) -> dict:
    return latest_market_map(db)


def _pos_dict(p: PaperPosition, mkt: float | None = None) -> dict:
    d = {"id": p.id, "token_id": p.token_id, "status": p.status,
         "entry_ts": p.entry_ts, "signal_price": p.signal_price,
         "entry_price": p.entry_price, "qty": p.qty, "size_usd": p.size_usd,
         "cost_usd": p.cost_usd, "entry_fee": p.entry_fee,
         "slippage_pct": p.slippage_pct, "stop_price": p.stop_price,
         "target_price": p.target_price, "trail_pct": p.trail_pct,
         "ai_confidence": p.ai_confidence, "risk_score": p.risk_score,
         "market_regime": p.market_regime,
         "exit_ts": p.exit_ts, "exit_price": p.exit_price,
         "exit_reason": p.exit_reason, "exit_fee": p.exit_fee,
         "realized_pnl": p.realized_pnl, "pnl_pct": p.pnl_pct,
         "hold_secs": p.hold_secs, "strategy_version_id": p.strategy_version_id,
         "signal_id": p.signal_id}
    if mkt is not None and p.status == "open":
        d["current_price"] = mkt
        d["mark_value"] = round(p.qty * mkt, 4)
        d["unrealized"] = round((mkt - p.entry_price) * p.qty, 4)
        d["unrealized_pct"] = round(100 * (mkt - p.entry_price) / max(p.entry_price, 1e-12), 2)
        d["hold_secs"] = max(0, PE._now_ts_utc() - p.entry_ts)
    return d


def _sym_map(db: Session, rows) -> dict:
    ids = {r.token_id for r in rows}
    return {t.id: t.symbol or t.address[:8] for t in
            db.execute(select(Token).where(Token.id.in_(ids))).scalars().all()} if ids else {}


# ------------------------------------------------------------------ summary / portfolio
@router.get("/paper/summary")
def paper_summary(db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    acct = _acct(db, user)
    market = _summary_map(db)
    pf = PE.portfolio_state(db, acct, market)
    closed = db.execute(select(PaperPosition).where(
        PaperPosition.account_id == acct.id, PaperPosition.status == "closed")
        .order_by(PaperPosition.exit_ts.desc()).limit(400)).scalars().all()
    wins = [p for p in closed if (p.realized_pnl or 0) >= 0]
    realized = sum(p.realized_pnl or 0 for p in closed)
    gross_w = sum(p.realized_pnl or 0 for p in wins)
    gross_l = abs(sum(p.realized_pnl or 0 for p in closed if (p.realized_pnl or 0) < 0))
    start = acct.starting_cash or 1.0
    return {
        "mode": "PAPER TRADING — SIMULATION — VIRTUAL FUNDS ONLY",
        "ng_onboarding": False,
        "label": acct.label, "settings": PE.settings_for(acct),
        "strategy": _strategy(db, acct),
        "virtual_equity": round(pf["equity"], 2), "available_cash": round(pf["cash"], 2),
        "invested": round(pf["invested"], 2), "unrealized": round(pf["unrealized"], 2),
        "realized_pnl": round(realized, 2),
        "total_pnl": round(pf["equity"] - start, 2),
        "return_pct": round(100 * (pf["equity"] - start) / start, 2),
        "drawdown_pct": round(pf["drawdown_pct"], 2),
        "open_positions": pf["open_count"],
        "closed_trades": len(closed),
        "win_rate_pct": round(100 * len(wins) / len(closed), 1) if closed else None,
        "profit_factor": round(gross_w / gross_l, 2) if gross_l > 0 else (None if gross_w == 0 else float("inf")),
        "consec_wins": acct.consec_wins or 0, "consec_losses": acct.consec_losses or 0,
        "day_realized": round(acct.day_realized or 0, 2),
        "halt": PE.halt_status(acct),     # auto-resuming cool-off (loss streak / drawdown)
        "ts": int(time.time()),
    }


def _strategy(db, acct) -> dict:
    from ..models import StrategyVersion
    ver = db.get(StrategyVersion, acct.strategy_version_id) if acct.strategy_version_id else None
    return {"name": f"{ver.name} v{ver.version}", "id": ver.id} if ver else {"name": "n/a", "id": None}


@router.get("/paper/equity")
def paper_equity(hours: int = Query(24, le=24 * 30),
                 db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    acct = _acct(db, user)
    since = int(time.time()) - hours * 3600
    rows = db.execute(select(EquitySnapshot).where(
        EquitySnapshot.account_id == acct.id, EquitySnapshot.ts >= since)
        .order_by(EquitySnapshot.ts.asc())).scalars().all()
    return {"items": [{"ts": r.ts, "equity": r.equity, "cash": r.cash, "invested": r.invested,
                       "unrealized": r.unrealized, "drawdown_pct": r.drawdown_pct}
                      for r in rows],
            "starting": acct.starting_cash}


# ------------------------------------------------------------------ positions / trades
@router.get("/paper/positions")
def paper_positions(db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    acct = _acct(db, user)
    market = _summary_map(db)
    pos = db.execute(select(PaperPosition).where(
        PaperPosition.account_id == acct.id, PaperPosition.status == "open")
        .order_by(PaperPosition.entry_ts.desc())).scalars().all()
    syms = _sym_map(db, pos)
    items = []
    for p in pos:
        d = _pos_dict(p, PE._price(market, p.token_id) or None)
        d["symbol"] = syms.get(p.token_id)
        items.append(d)
    return {"items": items, "ts": int(time.time())}


@router.get("/paper/trades")
def paper_trades(status: str = Query("all", pattern="^(all|open|closed)$"),
                 limit: int = Query(100, le=500), offset: int = 0,
                 db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    acct = _acct(db, user)
    q = select(PaperPosition).where(PaperPosition.account_id == acct.id)
    if status != "all":
        q = q.where(PaperPosition.status == status)
    total = db.execute(select(func.count(PaperPosition.id)).where(
        PaperPosition.account_id == acct.id)).scalar()
    rows = db.execute(q.order_by(PaperPosition.entry_ts.desc())
                      .limit(limit).offset(offset)).scalars().all()
    syms = _sym_map(db, rows)
    addr = {t.id: t.address for t in db.execute(
        select(Token).where(Token.id.in_({r.token_id for r in rows}))).scalars().all()} if rows else {}
    items = []
    for p in rows:
        d = _pos_dict(p)
        d["symbol"] = syms.get(p.token_id)
        d["address"] = addr.get(p.token_id)
        items.append(d)
    return {"items": items, "total": total or 0,
            "note": "virtual ledger — no real transactions ever occurred"}


@router.get("/paper/trades/{position_id}")
def paper_trade_detail(position_id: int, db: Session = Depends(get_db),
                       user: User = Depends(get_current_user)):
    acct = _acct(db, user)
    p = db.execute(select(PaperPosition).where(
        PaperPosition.id == position_id, PaperPosition.account_id == acct.id)).scalar_one_or_none()
    if not p:
        raise HTTPException(404, "trade not found")
    sig = db.get(PaperSignal, p.signal_id) if p.signal_id else None
    tok = db.get(Token, p.token_id)
    events = db.execute(select(PaperTradeEvent).where(
        PaperTradeEvent.position_id == p.id).order_by(PaperTradeEvent.ts.asc())).scalars().all()
    from ..models import StrategyVersion
    ver = db.get(StrategyVersion, p.strategy_version_id) if p.strategy_version_id else None
    from ..models import PaperTradeReview
    from ..paper import postmortem as PM
    review = db.execute(select(PaperTradeReview).where(
        PaperTradeReview.position_id == p.id)).scalar_one_or_none()
    return {"trade": _pos_dict(p), "review": PM.review_dict(review),
            "symbol": tok.symbol if tok else None, "address": tok.address if tok else None,
            "strategy": {"name": f"{ver.name} v{ver.version}", "id": ver.id} if ver else None,
            "signal": {"id": sig.id, "ts": sig.ts, "kind": sig.kind, "action": sig.action,
                       "confidence": sig.confidence, "importance": sig.importance,
                       "metrics": sig.metrics_json or {}, "why": sig.why_json or [],
                       "concern": sig.concern, "risk_score": sig.risk_score,
                       "status": sig.status, "risk_decision": sig.risk_decision_json}
                       if sig else None,
            "why": p.why_json or [], "signal_metrics": p.metrics_json or {},
            "events": [{"id": e.id, "ts": e.ts, "kind": e.kind, "title": e.title,
                        "detail": e.detail} for e in events],
            "timeline_note": "All timestamps UTC epoch seconds (§32)"}


# ------------------------------------------------------------------ post-mortems
@router.get("/paper/postmortems")
def paper_postmortems(account: str = Query("main", pattern="^[a-z_]{1,32}$"),
                      days: int = Query(30, ge=1, le=365),
                      db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    """Why trades won or lost: one cause per closed trade, rolled up, with opt-in
    setting suggestions. `account` is "main" or a Strategy Lab preset key."""
    from ..paper import lab as LAB, postmortem as PM
    if account == "main":
        acct = _acct(db, user)
    else:
        acct = LAB.lab_accounts(db, user.id).get(account)
        if acct is None:
            raise HTTPException(404, "that Strategy Lab account is not running")
    out = PM.summary(db, acct.id, PE.settings_for(acct), days)
    out["account"] = {"key": account, "label": acct.label or "Main account"}
    db.commit()
    return out


# ------------------------------------------------------------------ signals (§4/§25)
@router.get("/paper/signals")
def paper_signals(status: str = Query("all", pattern="^(all|approved|rejected)$"),
                  limit: int = Query(60, le=200),
                  db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    acct = _acct(db, user)
    q = select(PaperSignal).where(PaperSignal.account_id == acct.id)
    if status != "all":
        q = q.where(PaperSignal.status == status)
    rows = db.execute(q.order_by(PaperSignal.ts.desc()).limit(limit)).scalars().all()
    syms = _sym_map(db, rows)
    return {"items": [{"id": s.id, "ts": s.ts, "token_id": s.token_id,
                       "symbol": syms.get(s.token_id), "kind": s.kind, "action": s.action,
                       "confidence": s.confidence, "importance": s.importance,
                       "price": s.price, "status": s.status,
                       "reject_reason": s.reject_reason,
                       "risk_checks": (s.risk_decision_json or {}).get("checks", []),
                       "priority": s.importance_score} for s in rows]}


@router.get("/paper/signals/stats")
def paper_signal_stats(db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    """§25: rejection analytics — total/executed/rejected, reasons."""
    acct = _acct(db, user)
    rows = db.execute(select(PaperSignal).where(PaperSignal.account_id == acct.id)).scalars().all()
    by_reason: dict[str, int] = {}
    approved = rejected = 0
    for s in rows:
        if s.status == "approved":
            approved += 1
        else:
            rejected += 1
            by_reason[s.reject_reason or "unspecified"] = by_reason.get(s.reject_reason or "unspecified", 0) + 1
    return {"total_signals": len(rows), "executed": approved, "rejected": rejected,
            "rejection_rate_pct": round(100 * rejected / len(rows), 1) if rows else None,
            "top_rejection_reasons": sorted(by_reason.items(), key=lambda kv: -kv[1])[:10]}


# ------------------------------------------------------------------ timeline (§14)
@router.get("/paper/timeline")
def paper_timeline(kinds: str | None = None, limit: int = Query(120, le=300),
                   db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    acct = _acct(db, user)
    q = select(PaperTradeEvent).where(PaperTradeEvent.account_id == acct.id)
    if kinds:
        ks = [k.strip().upper() for k in kinds.split(",") if k.strip()]
        q = q.where(PaperTradeEvent.kind.in_(ks))
    rows = db.execute(q.order_by(PaperTradeEvent.ts.desc(), PaperTradeEvent.id.desc())
                      .limit(limit)).scalars().all()
    return {"items": [{"id": e.id, "ts": e.ts, "kind": e.kind, "title": e.title,
                       "detail": e.detail, "position_id": e.position_id,
                       "signal_id": e.signal_id} for e in rows],
            "kinds": ["SIGNAL", "RISK", "BUY", "SELL", "POSITION", "PROFIT", "LOSS", "SYSTEM"]}


# ------------------------------------------------------------------ analytics (§16–§19)
@router.get("/paper/analytics")
def paper_analytics(db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    acct = _acct(db, user)
    closed = db.execute(select(PaperPosition).where(
        PaperPosition.account_id == acct.id, PaperPosition.status == "closed")).scalars().all()
    if not closed:
        return {"empty": True, "note": "No closed trades yet — analytics populate as trades close."}
    pnls = [p.realized_pnl or 0 for p in closed]
    pcts = [p.pnl_pct or 0 for p in closed]
    wins = [p for p in closed if (p.realized_pnl or 0) >= 0]
    losses = [p for p in closed if (p.realized_pnl or 0) < 0]
    hold = sorted((p.hold_secs or 0) for p in closed)
    fees = sum((p.entry_fee or 0) + (p.exit_fee or 0) for p in closed)
    slippage_cost = sum(abs((p.entry_price or 0) - (p.signal_price or 0)) * p.qty +
                        abs((p.exit_price or 0) - _est_ref(p)) * p.qty for p in closed)

    # confidence buckets (§17)
    buckets = [(0, 50), (50, 60), (60, 70), (70, 80), (80, 90), (90, 101)]
    conf_stats = []
    for lo, hi in buckets:
        grp = [p for p in closed if lo <= (p.ai_confidence or 0) < hi]
        if not grp:
            conf_stats.append({"bucket": f"{lo}–{min(hi, 100)}%", "trades": 0, "win_rate": None,
                               "avg_return": None, "avg_loss": None, "profit_factor": None, "total_pnl": 0})
            continue
        gw = sum(p.realized_pnl or 0 for p in grp if (p.realized_pnl or 0) >= 0)
        gl = sum(p.realized_pnl or 0 for p in grp if (p.realized_pnl or 0) < 0)
        ns = [p.pnl_pct for p in grp if (p.realized_pnl or 0) < 0]
        conf_stats.append({
            "bucket": f"{lo}–{min(hi, 100)}%", "trades": len(grp),
            "win_rate": round(100 * sum(1 for p in grp if (p.realized_pnl or 0) >= 0) / len(grp), 1),
            "avg_return": round(sum(p.pnl_pct or 0 for p in grp) / len(grp), 2),
            "avg_loss": round(sum(ns) / len(ns), 2) if ns else None,
            "profit_factor": round(gw / abs(gl), 2) if gl < 0 else None,
            "total_pnl": round(sum(p.realized_pnl or 0 for p in grp), 2)})

    # signal effectiveness (§18) — key metric presence at entry
    def eff(pred, label):
        grp = [p for p in closed if pred(p)]
        wn = sum(1 for p in grp if (p.realized_pnl or 0) >= 0)
        return {"label": label, "trades": len(grp),
                "win_rate": round(100 * wn / len(grp), 1) if grp else None,
                "avg_return": round(sum(p.pnl_pct or 0 for p in grp) / len(grp), 2) if grp else None,
                "total_pnl": round(sum(p.realized_pnl or 0 for p in grp), 2) if grp else None}

    met = lambda p, k, lo: (p.metrics_json or {}).get(k) is not None and (p.metrics_json or {}).get(k, 0) >= lo
    effects = [
        eff(lambda p: met(p, "momentum", 65), "Momentum strong (≥65)"),
        eff(lambda p: met(p, "volume_g_1h", 40), "Volume accelerating (+40%)"),
        eff(lambda p: met(p, "buy_share", 55), "Buy share ≥55%"),
        eff(lambda p: met(p, "holders_g_1h", 5), "Holder growth ≥5%/h"),
        eff(lambda p: met(p, "liq_health", 55), "Liquidity healthy (≥55)"),
        eff(lambda p: met(p, "momentum", 65) and met(p, "volume_g_1h", 40), "Momentum + Volume"),
        eff(lambda p: (p.metrics_json or {}).get("sell_pressure", 100) < 40, "Low sell pressure (<40)"),
        eff(lambda p: p.market_regime == "BULLISH", "Bullish regime"),
        eff(lambda p: _verdict(p) == "STRONG_WATCH", "AI verdict: Strong watch"),
        eff(lambda p: _verdict(p) == "WATCH", "AI verdict: Watch"),
        eff(lambda p: _verdict(p) in ("NEUTRAL", "AVOID", "RUG_RISK"), "AI verdict: Neutral or worse"),
        eff(lambda p: _verdict(p) is None, "No AI verdict at entry"),
    ]
    pnl_sorted = sorted(pnls)
    med = pnl_sorted[len(pnl_sorted) // 2] if pnl_sorted else 0
    gross_w = sum(p for p in pnls if p > 0)
    gross_l = abs(sum(p for p in pnls if p < 0))
    return {
        "trades": len(closed), "wins": len(wins), "losses": len(losses),
        "win_rate_pct": round(100 * len(wins) / len(closed), 1),
        "avg_win": round(sum(p.realized_pnl for p in wins) / len(wins), 2) if wins else None,
        "avg_loss": round(sum(p.realized_pnl for p in losses) / len(losses), 2) if losses else None,
        "largest_win": round(max(pnls), 2), "largest_loss": round(min(pnls), 2),
        "profit_factor": round(gross_w / gross_l, 2) if gross_l > 0 else None,
        "total_pnl": round(sum(pnls), 2),
        "median_pnl": round(med, 2),
        "avg_pnl_pct": round(sum(pcts) / len(pcts), 2),
        "avg_hold_secs": round(sum(hold) / len(hold)),
        "median_hold_secs": hold[len(hold) // 2] if hold else None,
        "total_fees": round(fees, 2),
        "est_slippage_cost": round(slippage_cost, 2),
        "net_pnl_after_costs": round(sum(pnls), 2),
        "exit_reasons": _by(closed, lambda p: p.exit_reason or "?"),
        "strategy_versions": _by(closed, lambda p: str(p.strategy_version_id)),
        "confidence_analysis": conf_stats,
        "signal_effectiveness": effects,
    }


def _verdict(p):
    return ((p.metrics_json or {}).get("verdict") or {}).get("rating")


def _by(rows, key):
    out: dict[str, int] = {}
    for r in rows:
        out[key(r)] = out.get(key(r), 0) + 1
    return out


def _est_ref(p) -> float:
    """Reference (pre-slip) exit price ≈ exit / (1 − slip); used for slip-cost est."""
    return (p.exit_price or 0) / max(1 - (p.slippage_pct or 0) / 100, 1e-9) if p.exit_price else 0


@router.get("/paper/analytics/calendar")
def paper_calendar(db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    """Daily/weekly/monthly realized P&L (§11)."""
    acct = _acct(db, user)
    closed = db.execute(select(PaperPosition).where(
        PaperPosition.account_id == acct.id, PaperPosition.status == "closed")).scalars().all()
    by_day: dict[str, float] = {}
    for p in closed:
        d = time.strftime("%Y-%m-%d", time.gmtime(p.exit_ts or p.entry_ts))
        by_day[d] = round(by_day.get(d, 0) + (p.realized_pnl or 0), 2)
    days = sorted(by_day.items())
    week = sum(v for d, v in days if d >= time.strftime("%Y-%m-%d", time.gmtime(time.time() - 7 * 86400)))
    month = sum(v for d, v in days if d >= time.strftime("%Y-%m-%d", time.gmtime(time.time() - 30 * 86400)))
    today = by_day.get(time.strftime("%Y-%m-%d", time.gmtime()), 0)
    return {"daily": [{"day": d, "pnl": v} for d, v in days],
            "today": today, "weekly": round(week, 2), "monthly": round(month, 2)}


# ------------------------------------------------------------------ settings (§3)
@router.get("/paper/settings")
def paper_settings(db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    acct = _acct(db, user)
    return {"defaults": PC.DEFAULT_SETTINGS, "overrides": acct.settings_json or {},
            "effective": PE.settings_for(acct), "strategy": _strategy(db, acct)}


@router.post("/paper/settings", dependencies=[Depends(require_user)])
def paper_settings_update(overrides: dict, db: Session = Depends(get_db),
                          user: User = Depends(get_current_user)):
    acct = _acct(db, user)
    # merge: the form only sends the fields it shows; keep other stored rules
    # (e.g. a Strategy Lab preset's on/off filters) instead of silently dropping them
    version = PE.apply_settings(db, acct, {**(acct.settings_json or {}), **(overrides or {})})
    db.commit()
    return {"ok": True, "strategy": version, "effective": PE.settings_for(acct)}


# ---------------------------------------------------------------- strategy lab
@router.get("/paper/lab")
def paper_lab(db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    """Side-by-side results of every Strategy Lab preset plus the main account."""
    from ..paper import lab as LAB
    out = LAB.compare(db, user.id, _summary_map(db))
    db.commit()
    return out


@router.get("/paper/lab/review")
async def paper_lab_review(ai: bool = True, db: Session = Depends(get_db),
                           user: User = Depends(get_current_user)):
    """AI verdict on the Strategy Lab: ranks every strategy account by risk-adjusted
    results, compares the AI-verdict strategy with the baseline and, when an LLM key is
    set, adds a short written summary (cached; built only from these stats)."""
    from ..config import LLM_JUDGE_MODEL
    from ..paper import lab as LAB, lab_review as LR
    from ..providers import llm as llm_mod
    rev = LR.review(LAB.compare(db, user.id, _summary_map(db)))
    db.commit()
    rev["ai_text"] = None
    if ai and rev.get("available") and llm_mod.enabled():
        try:
            rev["ai_text"] = await LR.ai_summary(rev, llm_mod.LLMClient(), LLM_JUDGE_MODEL)
            rev["source"] = "rules+llm"
        except llm_mod.LLMError as exc:
            rev["ai_error"] = str(exc)[:120]
    return rev


@router.post("/paper/lab/start", dependencies=[Depends(require_user)])
def paper_lab_start(db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    from ..paper import lab as LAB
    accts = LAB.start_lab(db, user.id)
    db.commit()
    return {"ok": True, "running": sorted(accts)}


@router.post("/paper/lab/reset", dependencies=[Depends(require_owner)])
def paper_lab_reset(db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    from ..paper import lab as LAB
    LAB.reset_lab(db, user.id)
    db.commit()
    return {"ok": True}


@router.post("/paper/lab/apply/{key}", dependencies=[Depends(require_owner)])
def paper_lab_apply(key: str, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    """Copy a preset's rules onto the main account (the main account's history is kept)."""
    from ..paper import config as PCFG, lab as LAB
    try:
        version = LAB.apply_to_main(db, user.id, key)
    except KeyError:
        raise HTTPException(404, "unknown strategy")
    db.commit()
    return {"ok": True, "strategy": version, "effective": PE.settings_for(PE.get_or_create_account(db, user.id))}


# ---------------------------------------------------------------- strategy supervisor
@router.get("/paper/supervisor")
def paper_supervisor(db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    """Pending proposals (retire / challenger / promote / main-account fix) and the decision log."""
    from ..paper import supervisor as SUP
    return SUP.listing(db, user.id)


@router.post("/paper/supervisor/review", dependencies=[Depends(require_owner)])
async def paper_supervisor_review(db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    """Run a supervisor review now (it also runs on its own every few hours)."""
    from ..config import LLM_JUDGE_MODEL
    from ..database import SessionLocal
    from ..paper import supervisor as SUP
    from ..providers import llm as llm_mod
    res = SUP.review(db, user.id, _summary_map(db))
    db.commit()
    SUP._LAST_REVIEW[user.id] = res["ts"]
    if llm_mod.enabled() and res["created"]:
        try:
            await SUP.add_ai_notes(SessionLocal, [p["id"] for p in res["created"]], llm_mod.LLMClient(),
                                   LLM_JUDGE_MODEL)
        except llm_mod.LLMError:
            pass
    return {**res, **SUP.listing(db, user.id)}


@router.post("/paper/supervisor/proposals/{proposal_id}/{action}", dependencies=[Depends(require_owner)])
def paper_supervisor_decide(proposal_id: int, action: str, db: Session = Depends(get_db),
                            user: User = Depends(get_current_user)):
    """approve = carry the proposal out; reject = dismiss it (not re-proposed for a few days)."""
    from ..paper import supervisor as SUP
    if action not in ("approve", "reject"):
        raise HTTPException(404, "unknown action")
    try:
        p = SUP.decide(db, user.id, proposal_id, action == "approve")
    except LookupError:
        raise HTTPException(404, "proposal not found")
    except ValueError as exc:
        raise HTTPException(409, str(exc))
    db.commit()
    return {"ok": True, "proposal": SUP.proposal_dict(p)}


@router.post("/paper/resume", dependencies=[Depends(require_user)])
def paper_resume(db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    """End a cool-off pause now (resets the loss streak / re-anchors drawdown)."""
    acct = _acct(db, user)
    done = PE.resume_trading(db, acct, int(time.time()), _summary_map(db), manual=True)
    db.commit()
    return {"ok": True, "resumed": done, "halt": PE.halt_status(acct)}


@router.post("/paper/reset", dependencies=[Depends(require_owner)])
def paper_reset(starting_cash: float = 10000.0, db: Session = Depends(get_db),
                user: User = Depends(get_current_user)):
    acct = _acct(db, user)
    PE.reset_account(db, acct, max(100.0, min(starting_cash, 1_000_000)))
    db.commit()
    return {"ok": True, "starting_cash": acct.starting_cash}


@router.post("/paper/trades/{position_id}/close", dependencies=[Depends(require_user)])
def paper_manual_close(position_id: int, db: Session = Depends(get_db),
                       user: User = Depends(get_current_user)):
    acct = _acct(db, user)
    market = _summary_map(db)
    p = PE.manual_close(db, acct, position_id, int(time.time()), market)
    if not p:
        raise HTTPException(404, "open position not found")
    db.commit()
    return {"ok": True, "exit_price": p.exit_price, "pnl": p.realized_pnl,
            "reason": p.exit_reason}


# ------------------------------------------------------------------ export (§24)
@router.get("/paper/export")
def paper_export(what: str = Query("trades", pattern="^(trades|signals|events|metrics)$"),
                 fmt: str = Query("csv", pattern="^(csv|json)$"),
                 db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    acct = _acct(db, user)
    if what == "trades":
        rows = db.execute(select(PaperPosition).where(
            PaperPosition.account_id == acct.id).order_by(PaperPosition.entry_ts.asc())).scalars().all()
        syms = _sym_map(db, rows)
        data = [{"trade_id": p.id, "token": syms.get(p.token_id),
                 "entry_utc": _uts(p.entry_ts), "signal_price": p.signal_price,
                 "exec_price": p.entry_price, "qty": p.qty, "size_usd": p.size_usd,
                 "confidence": p.ai_confidence, "risk": p.risk_score,
                 "regime": p.market_regime, "stop": p.stop_price, "target": p.target_price,
                 "slippage_pct": p.slippage_pct, "entry_fee": p.entry_fee,
                 "exit_utc": _uts(p.exit_ts) if p.exit_ts else "", "exit_price": p.exit_price,
                 "exit_reason": p.exit_reason, "exit_fee": p.exit_fee,
                 "pnl": p.realized_pnl, "pnl_pct": p.pnl_pct, "hold_secs": p.hold_secs,
                 "strategy_version_id": p.strategy_version_id, "status": p.status} for p in rows]
    elif what == "signals":
        rows = db.execute(select(PaperSignal).where(
            PaperSignal.account_id == acct.id).order_by(PaperSignal.ts.asc())).scalars().all()
        data = [{"signal_id": s.id, "ts_utc": _uts(s.ts), "kind": s.kind, "action": s.action,
                 "confidence": s.confidence, "importance": s.importance, "price": s.price,
                 "status": s.status, "reject_reason": s.reject_reason} for s in rows]
    elif what == "events":
        rows = db.execute(select(PaperTradeEvent).where(
            PaperTradeEvent.account_id == acct.id).order_by(PaperTradeEvent.ts.asc())).scalars().all()
        data = [{"ts_utc": _uts(e.ts), "kind": e.kind, "title": e.title,
                 "detail": e.detail, "position_id": e.position_id} for e in rows]
    else:
        data = paper_analytics(db, user)  # type: ignore[assignment]
        data = [data] if isinstance(data, dict) else data
    if fmt == "json":
        return {"account": acct.label, "simulation": True, "items": data}
    # CSV
    if not data:
        return PlainTextResponse("empty\n", media_type="text/csv")
    cols = list(data[0].keys())
    import csv, io
    buf = io.StringIO()
    w = csv.DictWriter(buf, fieldnames=cols, extrasaction="ignore")
    w.writeheader()
    for r in data:
        w.writerow({k: r.get(k) for k in cols})
    return PlainTextResponse(buf.getvalue(), media_type="text/csv")


def _uts(ts: int | None) -> str:
    return time.strftime("%Y-%m-%d %H:%M:%S UTC", time.gmtime(ts)) if ts else ""


# ------------------------------------------------------------------ backtest (§20)
def _compare_job(hours: int, capital: float, keys: tuple) -> dict:
    from ..database import SessionLocal
    end_ts = int(time.time())
    with SessionLocal() as db:
        return BT.compare_family(db, end_ts - hours * 3600, end_ts, capital, keys)


def _backtest_job(user_id: int, start_ts: int, end_ts: int, cap: float, overrides: dict, mode: str) -> dict:
    from ..database import SessionLocal
    with SessionLocal() as db:
        result = BT.run(db, start_ts, end_ts, cap, overrides, mode=mode)
        run = BacktestRun(user_id=user_id, mode=mode, start_ts=start_ts, end_ts=end_ts,
                          params_json={"capital": cap, "strategy": overrides},
                          result_json=result["summary"], frames_json=result.get("frames"),
                          status="done", note=result.get("note"))
        db.add(run)
        db.commit()
        return {"run_id": run.id, "mode": mode, **result}


def _submit(user: User, kind: str, fn, *args) -> dict:
    from ..paper import jobs as JOBS
    try:
        return JOBS.submit(user.id, kind, fn, *args)
    except RuntimeError as exc:
        raise HTTPException(429, str(exc))


@router.post("/paper/backtest/compare", dependencies=[Depends(require_owner)])
def paper_backtest_compare(body: dict, user: User = Depends(get_current_user)):
    """Replay the momentum-strategy family (Baseline + A–F, or ?keys) over the same window.
    {"background": true} returns a job id to poll at /paper/backtest/jobs/{id} (long windows
    outlast the hosting proxy's request timeout)."""
    hours = min(max(int(body.get("hours") or 72), 1), 24 * 30)
    capital = min(float(body.get("capital") or 10000.0), 1_000_000)
    keys = tuple(k for k in (body.get("keys") or PC.MOMENTUM_FAMILY) if k in PC.PRESETS)[:10] or PC.MOMENTUM_FAMILY
    if body.get("background"):
        return _submit(user, "compare", _compare_job, hours, capital, keys)
    return _compare_job(hours, capital, keys)


@router.post("/paper/backtest", dependencies=[Depends(require_owner)])
def paper_backtest(body: dict, user: User = Depends(get_current_user)):
    start_ts = int(body.get("start_ts") or (time.time() - 86400))
    end_ts = int(body.get("end_ts") or time.time())
    hours = body.get("hours")
    if hours:
        end_ts = int(time.time())
        start_ts = end_ts - min(int(hours), 24 * 30) * 3600
    cap = min(float(body.get("capital") or 10000.0), 1_000_000)
    overrides = {k: v for k, v in (body.get("strategy") or {}).items() if k in PC.DEFAULT_SETTINGS}
    mode = body.get("mode", "backtest") if body.get("mode") in ("backtest", "replay") else "backtest"
    if body.get("background"):
        return _submit(user, "backtest", _backtest_job, user.id, start_ts, end_ts, cap, overrides, mode)
    return _backtest_job(user.id, start_ts, end_ts, cap, overrides, mode)


@router.get("/paper/backtest/jobs/{job_id}")
def paper_backtest_job(job_id: str, user: User = Depends(get_current_user)):
    """Status (queued / running / done / error) and, when done, the result of a backtest job."""
    from ..paper import jobs as JOBS
    j = JOBS.get(job_id, user.id)
    if j is None:
        raise HTTPException(404, "job not found (it may have expired or the server restarted) — run it again")
    return j
