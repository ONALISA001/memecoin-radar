"""Paper-trading engine (v4 phases 1–3). Runs inside the ingestion tick:

    new enriched SignalEvents (v3 intel)  → PaperSignal (deduped by event id)
      → risk engine approval / rejection  → simulated execution (slippage+fees)
      → position management (SL/TP/trailing/signal/health/time exits)
      → ledger + timeline events + equity snapshots + live-feed integration

All money is virtual; no chain interaction exists anywhere in this package.
"""
from __future__ import annotations

import logging
import time
from datetime import datetime, timezone

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError

from ..models import (DemoAccount, EquitySnapshot, PaperPosition, PaperSignal,
                      PaperTradeEvent, SignalEvent, StrategyVersion, Token, TrendScore)
from . import confluence as CF
from . import config as PC
from . import execution as X
from . import risk as R
from . import filters as F
from . import postmortem as PM
from . import themes as TH
from . import winners as W
from ..providers import sol_market as SOL
from types import SimpleNamespace

_feed_on = True     # Strategy Lab accounts don't post into the public intel feed

log = logging.getLogger("paper")

EXIT_REASONS = ("STOP_LOSS", "TAKE_PROFIT", "TRAILING_STOP", "SELL_SIGNAL",
                "CONFIDENCE_COLLAPSE", "RISK_EXIT", "LIQUIDITY_EXIT", "MAX_HOLD",
                "MANUAL")


# ------------------------------------------------------------------ setup
def get_or_create_account(db, user_id: int, start_cash: float = 10000.0) -> DemoAccount:
    acct = db.execute(select(DemoAccount).where(
        DemoAccount.user_id == user_id, DemoAccount.active == True,  # noqa: E712
        DemoAccount.strategy_key.is_(None))
        .order_by(DemoAccount.id.desc()).limit(1)).scalar_one_or_none()
    if acct is None:
        ver = _strategy_version(db, PC.merged_settings(None))
        acct = DemoAccount(user_id=user_id, label="AI Paper Trader",
                           starting_cash=start_cash, cash=start_cash,
                           settings_json={}, strategy_version_id=ver.id,
                           # start at the present edge — never trade stale
                           # hours-old signals (§31/§34)
                           last_event_id=db.execute(select(func.max(SignalEvent.id))).scalar() or 0)
        db.add(acct)
        db.flush()
        _event(db, acct, None, None, "SYSTEM",
               "Demo account created — virtual funds only",
               f"Starting capital ${start_cash:,.2f}. PAPER TRADING · SIMULATION · no real funds ever touch a market.",
               {"starting_capital": start_cash})
    return acct


def _strategy_version(db, settings: dict) -> StrategyVersion:
    import hashlib, json as _json
    h = hashlib.sha1(_json.dumps(settings, sort_keys=True).encode()).hexdigest()[:8]
    ver = db.execute(select(StrategyVersion).where(StrategyVersion.name == PC.STRATEGY_NAME)
                     .order_by(StrategyVersion.id.desc()).limit(1)).scalar_one_or_none()
    if ver is None:
        ver = StrategyVersion(name=PC.STRATEGY_NAME, version=PC.STRATEGY_BASE_VERSION,
                              config_json=settings)
        db.add(ver)
        db.flush()
    else:
        cur = hashlib.sha1(_json.dumps(ver.config_json, sort_keys=True).encode()).hexdigest()[:8]
        if cur != h and settings != PC.merged_settings(ver.config_json):
            base = ver.version.split(".")
            base[-1] = str(int(base[-1]) + 1) if base[-1].isdigit() else "1"
            new = StrategyVersion(name=PC.STRATEGY_NAME, version=".".join(base),
                                  config_json=settings)
            db.add(new)
            db.flush()
            return new
    return ver


def apply_settings(db, acct: DemoAccount, overrides: dict) -> str:
    """Persist user settings; bumps strategy version if risk/strategy knobs changed (§22)."""
    acct.settings_json = overrides or {}
    settings = PC.merged_settings(overrides)
    ver = _strategy_version(db, settings)
    acct.strategy_version_id = ver.id
    return f"{ver.name} v{ver.version}"


def settings_for(acct: DemoAccount) -> dict:
    return PC.merged_settings(acct.settings_json or {})


# ------------------------------------------------------------------ helpers
def _event(db, acct, pos, sig, kind: str, title: str, detail: str, data: dict) -> PaperTradeEvent:
    ev = PaperTradeEvent(account_id=acct.id,
                         position_id=getattr(pos, "id", None),
                         signal_id=getattr(sig, "id", None),
                         ts=_now_ts, kind=kind, title=title[:190],
                         detail=(detail or "")[:495], data_json=data or {})
    db.add(ev)
    return ev


_now_ts = 0  # set per tick — single clock per tick keeps timestamps consistent
_breadth: float | None = None  # market-wide share of coins up 24h, set per tick

# settings that need a confluence snapshot (skip the extra queries when none is on)
_SNAP_KEYS = ("min_momentum", "momentum_rising", "min_volume_accel_pct", "require_bullish_regime",
              "block_distribution_regime", "max_entry_sell_pressure", "min_market_breadth_pct",
              "confluence_sizing", "momentum_exit_below", "regime_exit", "time_stop_hours")


def _needs_snap(settings: dict) -> bool:
    return any(settings.get(k) for k in _SNAP_KEYS)


def momentum_history(db, token_id: int, now: int, n: int = 3) -> list[float]:
    """Earlier momentum readings for a coin (oldest → newest), from stored scores before `now`."""
    rows = db.execute(select(TrendScore.components_json).where(
        TrendScore.token_id == token_id, TrendScore.ts < now, TrendScore.kind == "live")
        .order_by(TrendScore.ts.desc()).limit(n)).scalars().all()
    vals = [((c or {}).get("summary") or {}).get("momentum") for c in rows]
    return [float(v) for v in reversed(vals) if v is not None]


def snapshot(db, token_id: int, mkt: dict, now: int, metrics: dict | None = None) -> dict:
    """Live confluence snapshot for one coin (see paper/confluence.py)."""
    mom = mkt.get("momentum")
    hist = momentum_history(db, token_id, now)
    if mom is not None:
        hist = hist + [float(mom)]
    vol = mkt.get("volume_g_1h")
    if vol is None and metrics:
        vol = metrics.get("volume_g_1h")
    return {"momentum": mom, "momentum_hist": hist, "volume_g_1h": vol,
            "regime": mkt.get("regime", "NEUTRAL"), "sellp": mkt.get("sellp"),
            "price_chg_1h": mkt.get("price_chg_1h"), "breadth": _breadth}


def _now_ts_utc() -> int:
    return int(time.time())


def _feed_row(db, token_id: int, kind: str, key: str, title: str, detail: str,
              severity: str, importance: str, importance_score: int, confidence,
              metrics: dict):
    """Project paper-trading events into the Live Intelligence Feed (§15)."""
    if not _feed_on:
        return
    db.add(SignalEvent(
        token_id=token_id, ts=_now_ts, kind=kind, key=key, title=title[:190],
        detail=(detail or "")[:590], severity=severity, metrics_json=metrics or {},
        category="paper", importance=importance, importance_score=importance_score,
        confidence=round(confidence) if confidence else None,
        signal_text=(detail or "")[:290], why_json=[], concern=None,
        chain_json=None, checklist_json=[]))


def portfolio_state(db, acct, market: dict) -> dict:
    pos = db.execute(select(PaperPosition).where(
        PaperPosition.account_id == acct.id,
        PaperPosition.status == "open")).scalars().all()
    invested, unreal = 0.0, 0.0
    for p in pos:
        px = _price(market, p.token_id)
        invested += p.qty * px
        unreal += (px - p.entry_price) * p.qty
    equity = acct.cash + invested
    peak = max(acct.peak_equity or equity, equity)
    dd = 100.0 * (peak - equity) / peak if peak > 0 else 0.0
    return {"positions": pos, "open_count": len(pos), "invested": invested,
            "unrealized": unreal, "equity": equity, "cash": acct.cash,
            "drawdown_pct": dd, "peak": peak}


def _price(market: dict, token_id: int) -> float:
    return float((market.get(token_id) or {}).get("price") or 0.0)


def _market_regime(mi: dict | None) -> str:
    sig = ((mi or {}).get("confluence") or {}).get("signal")
    return {"emerging_momentum": "BULLISH", "potential_distribution": "DISTRIBUTION"}.get(sig, "NEUTRAL")


# ------------------------------------------------------------------ tick
def run_tick(db, now: int, market: dict) -> dict:
    """Process all accounts for one ingestion tick. Idempotent via the
    per-account last_event_id cursor + unique (account, source_event)."""
    global _now_ts, _breadth
    _now_ts = now
    _breadth = CF.breadth(market)
    # per-tick caches are keyed by `now`, a 5-min bucket that several ticks can share: start fresh
    TH._CACHE.clear()
    W._CACHE.clear()
    stats = {"signals": 0, "approved": 0, "rejected": 0, "bought": 0, "sold": 0, "accounts": 0}
    # id order: accounts created later (e.g. the ensemble preset) see earlier accounts' buys this tick
    accounts = db.execute(select(DemoAccount).where(DemoAccount.active == True)  # noqa: E712
                          .order_by(DemoAccount.id)).scalars().all()
    if not accounts:
        return stats
    stats["accounts"] = len(accounts)
    for acct in accounts:
        try:
            _run_account(db, acct, now, market, stats)
        except Exception as exc:  # one account must never break the tick
            log.exception("paper tick failed for account %s: %s", acct.id, exc)
    try:
        with db.begin_nested():
            stats["reviewed"] = PM.review_due(db, now)
    except Exception as exc:      # noqa: BLE001
        log.warning("post-mortem review pass failed: %s", exc)
    return stats


def halt_status(acct: DemoAccount, now: int | None = None) -> dict:
    now = now or _now_ts_utc()
    active = bool(acct.halt_until and now < acct.halt_until)
    return {"active": active, "reason": acct.halt_reason if active else None,
            "until": acct.halt_until if active else None,
            "seconds_left": max(0, (acct.halt_until or 0) - now) if active else 0}


def resume_trading(db, acct: DemoAccount, now: int, market: dict, manual: bool = False) -> bool:
    """End a pause: reset the loss streak / re-anchor drawdown to current equity."""
    if not acct.halt_until:
        return False
    reason = acct.halt_reason
    if reason == "loss_streak":
        acct.consec_losses = 0
    elif reason == "drawdown":
        acct.peak_equity = portfolio_state(db, acct, market)["equity"]
    acct.halt_until, acct.halt_reason = None, None
    _event(db, acct, None, None, "SYSTEM", "▶️ TRADING RESUMED" + (" (manual)" if manual else ""),
           ("Resumed by you." if manual else "Cool-off finished — resuming automatically.")
           + (" Loss streak reset to 0." if reason == "loss_streak" else
              " Drawdown is now measured from current equity." if reason == "drawdown" else ""),
           {"reason": reason, "manual": manual})
    return True


def _update_brakes(db, acct: DemoAccount, settings: dict, now: int, market: dict) -> None:
    """Temporary circuit breakers. Without these a tripped brake was permanent:
    no trades => no winning close => streak/drawdown never reset."""
    if acct.halt_until:
        recovered = (acct.halt_reason == "drawdown" and
                     portfolio_state(db, acct, market)["drawdown_pct"] < float(settings["max_drawdown_pct"]))
        if now >= acct.halt_until or recovered:     # open positions climbed back => lift early
            resume_trading(db, acct, now, market)
        else:
            return
    streak, max_streak = acct.consec_losses or 0, int(settings["max_consecutive_losses"])
    if max_streak > 0 and streak >= max_streak:
        hours = float(settings.get("loss_streak_cooldown_hours", 3.0))
        acct.halt_until, acct.halt_reason = now + int(hours * 3600), "loss_streak"
        _event(db, acct, None, None, "RISK", f"⏸️ TRADING PAUSED — {streak} losses in a row",
               f"No new buys for {hours:g}h; open positions are still managed. Resumes automatically at "
               f"{_utc(acct.halt_until)} UTC.", {"reason": "loss_streak", "until": acct.halt_until})
        return
    dd, max_dd = portfolio_state(db, acct, market)["drawdown_pct"], float(settings["max_drawdown_pct"])
    if max_dd > 0 and dd >= max_dd:
        acct.halt_until, acct.halt_reason = now - (now % 86400) + 86400, "drawdown"
        _event(db, acct, None, None, "RISK", f"⏸️ TRADING PAUSED — drawdown {dd:.1f}%",
               f"Drawdown reached the {max_dd:.0f}% limit. No new buys until {_utc(acct.halt_until)} UTC "
               f"(next day); then drawdown is re-measured from current equity.",
               {"reason": "drawdown", "until": acct.halt_until, "drawdown_pct": dd})


def _utc(ts: int) -> str:
    import datetime as _dt
    return _dt.datetime.fromtimestamp(ts, _dt.timezone.utc).strftime("%Y-%m-%d %H:%M")


def _run_account(db, acct: DemoAccount, now: int, market: dict, stats: dict) -> None:
    global _feed_on
    _feed_on = acct.strategy_key is None
    settings = settings_for(acct)
    day = now - (now % 86400)
    if acct.day_ts != day:
        acct.day_ts = day
        acct.day_realized = 0.0
    _update_brakes(db, acct, settings, now, market)

    # ① ingest new buy/sell intel events since cursor (idempotent)
    rows = db.execute(select(SignalEvent).where(
        SignalEvent.id > (acct.last_event_id or 0),
        SignalEvent.category.isnot(None),
        SignalEvent.category != "paper",
        SignalEvent.kind.in_(PC.BUY_KINDS + PC.SELL_KINDS))
        .order_by(SignalEvent.id.asc()).limit(500)).scalars().all()
    sell_hits: dict[int, SignalEvent] = {}
    for ev in rows:
        if ev.kind in PC.SELL_KINDS and (ev.severity == "danger" or ev.importance_score >= 60):
            prev = sell_hits.get(ev.token_id)
            if prev is None or (ev.importance_score or 0) > (prev.importance_score or 0):
                sell_hits[ev.token_id] = ev
        allowed = settings.get("allowed_kinds") or []
        if ev.kind in PC.BUY_KINDS and (not allowed or ev.kind in allowed):
            sig = _ingest_buy_signal(db, acct, ev, now, market, stats)
            if sig is not None:
                stats["signals"] += 1
                _risk_and_maybe_execute(db, acct, settings, sig, ev, now, market, stats)
    acct.last_event_id = max([acct.last_event_id or 0] + [r.id for r in rows])
    if settings.get("pullback_entries"):
        _pullback_entries(db, acct, settings, now, market, stats)
    if settings.get("priority_entries"):
        _priority_entries(db, acct, settings, now, market, stats)
    if settings.get("ensemble_entries"):
        _ensemble_entries(db, acct, settings, now, market, stats)
    if settings.get("graduation_entries"):
        win = float(settings.get("graduation_window_min") or 30)
        for tid, mins in TH.graduations(db, now, win):
            _market_entry(db, acct, settings, now, market, stats, tid, "graduation", win, 65.0,
                          [{"label": "graduated to a DEX", "passed": True, "value": f"{mins:.0f} min ago"},
                           {"label": "inside entry window", "passed": mins <= win, "value": f"≤{win:g} min"}],
                          {"graduated_min_ago": mins}, "Graduation", "just left the bonding curve")
    if settings.get("telegram_entries"):
        win = float(settings.get("tg_call_window_min") or 30)
        need = int(settings.get("tg_min_channels") or 2)
        calls = TH.telegram_calls(db, now, win)
        if calls:
            ids = dict(db.execute(select(Token.address, Token.id).where(Token.address.in_(list(calls)))).all())
            for addr, c in sorted(calls.items(), key=lambda kv: -kv[1]["channels"]):
                if addr in ids and c["channels"] >= need:
                    _market_entry(db, acct, settings, now, market, stats, ids[addr], "telegram_call", win,
                                  min(90.0, 50.0 + 10.0 * c["channels"]),
                                  [{"label": "called by channels", "passed": True, "value": ", ".join(c["channel_names"])},
                                   {"label": "channels", "passed": True, "value": f"{c['channels']} vs min {need}"}],
                                  {"tg_channels": c["channels"]}, "Telegram call",
                                  f"called by {c['channels']} channels")

    # ② manage open positions (always — even while new buys are paused)
    pf = portfolio_state(db, acct, market)
    for p in list(pf["positions"]):
        _manage_exit(db, acct, settings, p, now, market, sell_hits, stats)
    _update_brakes(db, acct, settings, now, market)     # losses realized this tick

    # ③ equity snapshot + drawdown peak (daily cadence of writes is enough at
    #    5-min ticks; still idempotent via unique (account, ts))
    pf = portfolio_state(db, acct, market)
    acct.peak_equity = max(acct.peak_equity or 0.0, pf["peak"])
    if not db.execute(select(EquitySnapshot.id).where(
            EquitySnapshot.account_id == acct.id, EquitySnapshot.ts == now)).first():
        snap = EquitySnapshot(account_id=acct.id, ts=now, equity=round(pf["equity"], 4),
                              cash=round(pf["cash"], 4), invested=round(pf["invested"], 4),
                              unrealized=round(pf["unrealized"], 4),
                              realized_cum=round((pf["equity"] - pf["unrealized"])
                                                 - acct.starting_cash - 0, 4),
                              drawdown_pct=round(pf["drawdown_pct"], 3))
        try:
            with db.begin_nested():  # savepoint — duplicate snapshot (same tick
                db.add(snap)          # reprocessed in this session) is ignored
        except IntegrityError:
            pass


def _pullback_entries(db, acct, settings: dict, now: int, market: dict, stats: dict) -> None:
    """Buy-the-dip entries built from market data (no intel event): coin in a 24h
    uptrend, dipped 6–20% over 1h, 15m bounce under way. One per coin per cooldown."""
    cool = int(float(settings["pullback_cooldown_h"]) * 3600)
    recent = {tid for (tid,) in db.execute(select(PaperSignal.token_id).where(
        PaperSignal.account_id == acct.id, PaperSignal.kind == "pullback_bounce",
        PaperSignal.ts >= now - cool)).all()}
    for token_id, mkt in market.items():
        if token_id in recent or not mkt.get("price"):
            continue
        ok, checklist = F.pullback_candidate(settings, mkt)
        if not ok:
            continue
        passed = sum(1 for c in checklist if c["passed"])
        sym = _symbol(db, token_id)
        sig = PaperSignal(
            account_id=acct.id, token_id=token_id, ts=now, action="BUY", kind="pullback_bounce",
            source_event_id=None, price=_price(market, token_id), confidence=60 + 4 * passed,
            importance="MEDIUM", importance_score=60, risk_score=mkt.get("risk"),
            market_regime=mkt.get("regime", "NEUTRAL"),
            metrics_json={"liquidity": mkt.get("liq"), "volume_24h": mkt.get("volume_24h"),
                          "mcap": mkt.get("mcap"), "momentum": mkt.get("momentum")},
            why_json=[f"✓ {c['label']} ({c['value']})" for c in checklist], status="pending")
        db.add(sig)
        db.flush()
        stats["signals"] += 1
        ev = SimpleNamespace(title=f"Trend pullback — ${sym}", signal_text="uptrend dip with bounce",
                             checklist_json=checklist, kind="pullback_bounce")
        _risk_and_maybe_execute(db, acct, settings, sig, ev, now, market, stats)


_PRIO_CACHE: dict[tuple, dict] = {}


def _priority_list(db, now: int, window_h: int) -> dict:
    """The Priority coins list, computed once per tick per window (shared by accounts)."""
    from ..services import market_views as MV
    key = (now, int(window_h))
    if key not in _PRIO_CACHE:
        _PRIO_CACHE.clear()
        win = f"{int(window_h)}h" if f"{int(window_h)}h" in MV.WINDOWS_S else "4h"
        _PRIO_CACHE[key] = MV.priority_now(db, win, limit=20)
    return _PRIO_CACHE[key]


def _priority_entries(db, acct, settings: dict, now: int, market: dict, stats: dict) -> None:
    """Buy candidates from the Priority coins list. Each coin still goes through the full
    risk engine and this account's own entry rules (momentum, regime, cooldown …)."""
    cool = int(float(settings.get("priority_signal_cooldown_h") or 2) * 3600)
    recent = {tid for (tid,) in db.execute(select(PaperSignal.token_id).where(
        PaperSignal.account_id == acct.id, PaperSignal.kind == "priority_coin",
        PaperSignal.ts >= now - cool)).all()}
    held = {tid for (tid,) in db.execute(select(PaperPosition.token_id).where(
        PaperPosition.account_id == acct.id, PaperPosition.status == "open")).all()}
    min_score = float(settings.get("priority_min_score") or 0)
    for c in _priority_list(db, now, int(settings.get("priority_window_h") or 4))["items"]:
        tid = c.get("token_id")
        if tid is None or tid in recent or tid in held or (c.get("score") or 0) < min_score:
            continue
        mkt = market.get(tid) or {}
        if not mkt.get("price"):
            continue
        checklist = [{"label": k["label"], "passed": k["passed"], "value": k["value"]} for k in c["checks"]]
        sig = PaperSignal(
            account_id=acct.id, token_id=tid, ts=now, action="BUY", kind="priority_coin",
            source_event_id=None, price=_price(market, tid), confidence=float(c.get("score") or 0),
            importance="HIGH", importance_score=int(c.get("score") or 0), risk_score=mkt.get("risk"),
            market_regime=mkt.get("regime", "NEUTRAL"),
            metrics_json={"liquidity": mkt.get("liq"), "volume_24h": mkt.get("volume_24h"),
                          "mcap": mkt.get("mcap"), "momentum": mkt.get("momentum"),
                          "sell_pressure": mkt.get("sellp"), "volume_g_1h": mkt.get("volume_g_1h"),
                          "priority_score": c.get("score"), "verdict_at_list": c.get("verdict")},
            why_json=[f"✓ {k['label']} ({k['value']})" for k in checklist], status="pending")
        db.add(sig)
        db.flush()
        stats["signals"] += 1
        ev = SimpleNamespace(title=f"Priority coin — ${c.get('symbol')}",
                             signal_text=f"passes every priority gate (score {c.get('score'):.0f})",
                             checklist_json=checklist, kind="priority_coin")
        _risk_and_maybe_execute(db, acct, settings, sig, ev, now, market, stats)


def _market_entry(db, acct, settings: dict, now: int, market: dict, stats: dict, tid: int, kind: str,
                  cooldown_min: float, confidence: float, checklist: list[dict], extra: dict,
                  title: str, text: str) -> None:
    """One buy candidate from a non-intel source (graduation, Telegram call). Skips coins already
    held or signalled for this kind within cooldown_min; then the full risk engine decides."""
    mkt = market.get(tid) or {}
    if not mkt.get("price"):
        return
    if db.execute(select(PaperPosition.id).where(PaperPosition.account_id == acct.id, PaperPosition.token_id == tid,
                                                 PaperPosition.status == "open")).first():
        return
    if db.execute(select(PaperSignal.id).where(PaperSignal.account_id == acct.id, PaperSignal.token_id == tid,
                                               PaperSignal.kind == kind,
                                               PaperSignal.ts >= now - int(cooldown_min * 60))).first():
        return
    sig = PaperSignal(
        account_id=acct.id, token_id=tid, ts=now, action="BUY", kind=kind, source_event_id=None,
        price=_price(market, tid), confidence=confidence, importance="MEDIUM", importance_score=int(confidence),
        risk_score=mkt.get("risk"), market_regime=mkt.get("regime", "NEUTRAL"),
        metrics_json={"liquidity": mkt.get("liq"), "volume_24h": mkt.get("volume_24h"), "mcap": mkt.get("mcap"),
                      "momentum": mkt.get("momentum"), **extra},
        why_json=[f"✓ {c['label']} ({c['value']})" for c in checklist], status="pending")
    db.add(sig)
    db.flush()
    stats["signals"] += 1
    ev = SimpleNamespace(title=f"{title} — ${_symbol(db, tid)}", signal_text=text, checklist_json=checklist, kind=kind)
    _risk_and_maybe_execute(db, acct, settings, sig, ev, now, market, stats)


ENSEMBLE_SIZE = {2: 0.6, 3: 0.8}       # votes → size multiplier; 4+ = full size


def ensemble_votes(db, acct, now: int, window_s: int) -> dict[int, list[str]]:
    """{token_id: [labels of OTHER accounts of this user that opened it within the window]}.
    Accounts run in id order, so ones processed earlier in this tick already count."""
    rows = db.execute(select(PaperPosition.token_id, DemoAccount.id, DemoAccount.label, DemoAccount.strategy_key)
                      .join(DemoAccount, DemoAccount.id == PaperPosition.account_id)
                      .where(DemoAccount.user_id == acct.user_id, DemoAccount.id != acct.id,
                             DemoAccount.active == True,  # noqa: E712
                             PaperPosition.entry_ts >= now - window_s, PaperPosition.entry_ts <= now)).all()
    out: dict[int, dict[int, str]] = {}
    for tid, aid, label, key in rows:
        out.setdefault(tid, {})[aid] = (label or key or "main").replace("Lab · ", "")
    return {tid: sorted(v.values()) for tid, v in out.items()}


def _ensemble_entries(db, acct, settings: dict, now: int, market: dict, stats: dict) -> None:
    """Buy candidates that several other paper accounts opened recently. One signal per coin
    per window; each still goes through the full risk engine and this account's own rules."""
    window = int(float(settings.get("ensemble_window_min") or 30) * 60)
    need = int(settings.get("ensemble_min_votes") or 2)
    recent = {tid for (tid,) in db.execute(select(PaperSignal.token_id).where(
        PaperSignal.account_id == acct.id, PaperSignal.kind == "ensemble_vote",
        PaperSignal.ts >= now - window)).all()}
    held = {tid for (tid,) in db.execute(select(PaperPosition.token_id).where(
        PaperPosition.account_id == acct.id, PaperPosition.status == "open")).all()}
    for tid, voters in ensemble_votes(db, acct, now, window).items():
        if len(voters) < need or tid in recent or tid in held:
            continue
        mkt = market.get(tid) or {}
        if not mkt.get("price"):
            continue
        n = len(voters)
        # two real checks: the risk engine's min_bullish_signals counts passed checklist items
        checklist = [{"label": "accounts that bought it", "passed": True, "value": ", ".join(voters)},
                     {"label": "votes", "passed": n >= need, "value": f"{n} vs min {need} in {window // 60} min"}]
        sig = PaperSignal(
            account_id=acct.id, token_id=tid, ts=now, action="BUY", kind="ensemble_vote",
            source_event_id=None, price=_price(market, tid), confidence=min(95.0, 50.0 + 10.0 * n),
            importance="HIGH" if n >= 3 else "MEDIUM", importance_score=min(95, 50 + 10 * n),
            risk_score=mkt.get("risk"), market_regime=mkt.get("regime", "NEUTRAL"),
            metrics_json={"liquidity": mkt.get("liq"), "volume_24h": mkt.get("volume_24h"),
                          "mcap": mkt.get("mcap"), "momentum": mkt.get("momentum"),
                          "ensemble_votes": n, "ensemble_voters": voters},
            why_json=[f"✓ {n} accounts bought it in the last {window // 60} min: {', '.join(voters)}"],
            status="pending")
        db.add(sig)
        db.flush()
        stats["signals"] += 1
        ev = SimpleNamespace(title=f"Ensemble vote — ${_symbol(db, tid)}",
                             signal_text=f"{n} paper strategies bought it", checklist_json=checklist,
                             kind="ensemble_vote")
        _risk_and_maybe_execute(db, acct, settings, sig, ev, now, market, stats)


def _ingest_buy_signal(db, acct, ev: SignalEvent, now: int, market: dict,
                       stats: dict) -> PaperSignal | None:
    """SignalEvent → PaperSignal with dedupe (§31). Returns None if duplicate
    or if the account already holds this token (no pyramiding)."""
    if db.execute(select(PaperSignal.id).where(
            PaperSignal.account_id == acct.id,
            PaperSignal.source_event_id == ev.id)).first():
        return None
    mkt = market.get(ev.token_id) or {}
    m = dict(ev.metrics_json or {})
    # merge live market facts (execution layer needs pool + volume figures)
    m.setdefault("liquidity", mkt.get("liq"))
    m.setdefault("volume_24h", mkt.get("volume_24h"))
    m.setdefault("mcap", mkt.get("mcap"))
    checks = ev.checklist_json or []
    why = [f"✓ {c['label']} ({c['value']})" if c.get("passed") else f"⚠ {c['label']} ({c['value']})"
           for c in checks]
    sig = PaperSignal(
        account_id=acct.id, token_id=ev.token_id, ts=now, action="BUY",
        kind=ev.kind, source_event_id=ev.id, price=_price(market, ev.token_id),
        confidence=ev.confidence or 0, importance=ev.importance,
        importance_score=ev.importance_score or 0,
        risk_score=mkt.get("risk"),
        market_regime=mkt.get("regime", "NEUTRAL"),
        metrics_json=m, why_json=why, concern=ev.concern, status="pending")
    db.add(sig)
    try:
        with db.begin_nested():  # savepoint: a duplicate insert rolls back only
            db.flush()           # this signal, never the whole tick (§31/§33)
    except IntegrityError:
        # unique (account, source_event) — event already processed this session
        db.expunge(sig)
        db.session.remove if False else None
        return None
    return sig


def _risk_and_maybe_execute(db, acct, settings, sig: PaperSignal, ev: SignalEvent,
                            now: int, market: dict, stats: dict) -> None:
    sym = _symbol(db, sig.token_id)
    pf = portfolio_state(db, acct, market)
    halt = halt_status(acct, now)
    acct_state = {"trading_allowed": not (acct.settings_json or {}).get("paused", False)
                  and not halt["active"],
                  "halt": halt,
                  "day_realized": acct.day_realized or 0.0,
                  "consec_losses": acct.consec_losses or 0}
    sig_metrics = dict(sig.metrics_json or {})
    # tag every trade with the AI verdict it was opened under (for analytics), rules or not
    verdict = F.latest_verdict(db, sig.token_id, now)
    if verdict:
        sig_metrics["verdict"] = verdict
    sol = SOL.current()        # SOL's regime at entry, tagged on every trade for analytics/learning
    if sol:
        sig_metrics["sol"] = {k: sol.get(k) for k in ("regime", "chg_24h", "funding_1h_pct")}
    # Telegram call facts at entry, on every trade: do called coins win or lose? (Winning patterns)
    tok0 = db.get(Token, sig.token_id)
    call = TH.telegram_calls(db, now, 60).get(tok0.address) if tok0 is not None else None
    sig_metrics["telegram"] = ({"channels": call["channels"], "min_since_call": round((now - call["first_ts"]) / 60, 1)}
                               if call else {"channels": 0})
    checks = ev.checklist_json or []
    decision = R.evaluate_signal(
        settings, {"confidence": sig.confidence, "risk_score": sig.risk_score or 100,
                   "metrics": sig_metrics,
                   "why_passed": sum(1 for c in checks if c.get("passed")),
                   "importance": sig.importance}, pf, acct_state)
    # strategy-lab rules (only those switched on in settings)
    tok = db.get(Token, sig.token_id)
    if tok is not None:
        for name, ok, detail in F.strategy_checks(db, settings, tok, market.get(sig.token_id) or {}, now):
            R.chk(decision, name, ok, f"{name}: {detail}")
    # third-batch rules (need the whole market map / call feed, so they live here, not in filters)
    extra_checks = []
    if settings.get("narrative_rotation"):
        extra_checks.append(TH.narrative_check(db, sig.token_id, market, now,
                                               int(settings.get("narrative_top_k") or 2),
                                               int(settings.get("narrative_min_members") or 3)))
    tg_mode = int(settings.get("tg_call_filter") or 0)
    if tg_mode and tok is not None:
        win = float(settings.get("tg_call_window_min") or 30)
        extra_checks.append(TH.telegram_check(tg_mode, TH.telegram_calls(db, now, win).get(tok.address),
                                              int(settings.get("tg_min_channels") or 2), win, now))
    for name, ok, detail in extra_checks:          # same "rule: why" format as the other filters
        R.chk(decision, name, ok, f"{name}: {detail}")
    snap = None
    if _needs_snap(settings):
        snap = snapshot(db, sig.token_id, market.get(sig.token_id) or {}, now, sig_metrics)
        for name, ok, detail in CF.entry_checks(settings, snap):
            R.chk(decision, name, ok, f"{name}: {detail}")
        mult, n_fac = CF.size_multiplier(settings, snap)
        sig_metrics["confluence_factors"] = n_fac
        if mult is None:
            R.chk(decision, "confluence", False,
                  f"confluence: only {n_fac} of 4 factors (min {int(settings.get('confluence_min_factors') or 3)})")
    cool = float(settings.get("reentry_cooldown_h") or 0)
    if cool > 0:
        last = db.execute(select(func.max(PaperPosition.entry_ts)).where(
            PaperPosition.account_id == acct.id, PaperPosition.token_id == sig.token_id)).scalar()
        ok = last is None or now - last >= cool * 3600
        R.chk(decision, "re-entry cooldown", ok,
              "re-entry cooldown: " + ("no recent entry" if last is None else
                                       f"last entry {(now - last) / 3600:.1f}h ago vs {cool:g}h"))
    learned = None
    if settings.get("learn_from_winners"):
        mkt0 = market.get(sig.token_id) or {}
        cand = W.features(sig.kind, (sig_metrics.get("verdict") or {}).get("rating"), sig.market_regime,
                          sig_metrics.get("momentum", mkt0.get("momentum")), mkt0.get("price_chg_1h"),
                          mkt0.get("liq"), mkt0.get("buy_share_pct"), mkt0.get("sellp"), sig.confidence,
                          (sig_metrics.get("sol") or {}).get("regime"),
                          (sig_metrics.get("telegram") or {}).get("channels"))
        learned = W.judge(settings, cand, W.history(db, acct.user_id, now,
                                                    float(settings.get("winners_lookback_days") or 0)))
        sig_metrics["winners"] = {k: learned.get(k) for k in ("similar", "wins", "win_rate", "avg_pct")}
        R.chk(decision, "similar trades won", learned["ok"], f"similar trades won: {learned['reason']}")
    # already holding this token?
    if decision["approved"] and db.execute(select(PaperPosition.id).where(
            PaperPosition.account_id == acct.id, PaperPosition.token_id == sig.token_id,
            PaperPosition.status == "open")).first():
        R.chk(decision, "no duplicate token position", False,
              f"already holding ${sym}")
    if not decision["approved"]:
        reason = decision["rejects"][0] if decision["rejects"] else "rejected"
        sig.status, sig.reject_reason, sig.risk_decision_json = "rejected", reason, decision
        stats["rejected"] += 1
        _event(db, acct, None, sig, "RISK", f"🚫 SIGNAL REJECTED — ${sym}",
               f"{ev.title}. Reason: {reason}. Confidence {sig.confidence:.0f}% vs required "
               f"{settings['min_confidence']:.0f}% (full checklist stored).",
               {"checks": decision["checks"]})
        db.flush()
        return
    # position sizing: proposed → capped by per-trade loss limit
    size = R.position_size(settings, pf)
    stop_pct = float(settings["stop_loss_pct"])
    size = R.loss_cap_size(settings, pf, size, stop_pct)
    if settings.get("verdict_sizing") and verdict:
        mult = F.verdict_size_mult(verdict)
        size = round(size * mult, 2)
        sig_metrics["verdict_size_mult"] = mult
    votes = sig_metrics.get("ensemble_votes")
    if votes and settings.get("ensemble_sizing"):
        emult = ENSEMBLE_SIZE.get(int(votes), 1.0)
        if emult != 1.0:
            size = round(size * emult, 2)
            sig_metrics["ensemble_size_mult"] = emult
    if learned and learned.get("size_mult") and settings.get("winners_sizing"):
        size = round(size * learned["size_mult"], 2)
        sig_metrics["winners_size_mult"] = learned["size_mult"]
    if snap is not None and settings.get("confluence_sizing"):
        cmult, _ = CF.size_multiplier(settings, snap)
        if cmult:
            size = round(size * cmult, 2)
            sig_metrics["confluence_size_mult"] = cmult
    if sol and sol["regime"] == "FLAT" and int(settings.get("sol_regime_filter") or 0) == 1:
        smult = float(settings.get("sol_flat_size_mult") or 1.0)
        if smult != 1.0:
            size = round(size * smult, 2)
            sig_metrics["sol_size_mult"] = smult
    cap = float(settings.get("max_round_trip_cost_pct") or 0)
    if cap > 0:
        capped, est = F.cost_capped_size(size, market.get(sig.token_id) or {}, settings["execution"], cap)
        sig_metrics["est_round_trip_pct"] = est
        if capped is None:
            sig.status = "rejected"
            sig.reject_reason = f"round-trip cost ~{est:.1f}% even at the minimum order vs max {cap:g}%"
            sig.risk_decision_json = decision
            stats["rejected"] += 1
            _event(db, acct, None, sig, "RISK", f"🚫 SIGNAL REJECTED — ${sym}",
                   f"{ev.title}. Estimated round trip ~{est:.1f}% (fees + slippage for this pool) "
                   f"is above the {cap:g}% cap.", {"est_round_trip_pct": est})
            db.flush()
            return
        if capped < size:
            sig_metrics["cost_capped_from"] = size
            size = capped
    if size < float(settings["execution"]["min_order_usd"]):
        sig.status = "rejected"
        sig.reject_reason = f"order ${size:.2f} below minimum ${settings['execution']['min_order_usd']:.0f}"
        sig.risk_decision_json = decision
        stats["rejected"] += 1
        _event(db, acct, None, sig, "RISK", f"🚫 SIGNAL REJECTED — ${sym}",
               f"Computed size ${size:.2f} below minimum order size "
               f"(insufficient free cash above reserve).", {"size": size})
        db.flush()
        return
    price = _price(market, sig.token_id)
    if price <= 0:
        sig.status = "rejected"
        sig.reject_reason = "market data unavailable at decision time"
        sig.risk_decision_json = decision
        stats["rejected"] += 1
        _event(db, acct, None, sig, "RISK", f"🚫 SIGNAL SKIPPED — ${sym}",
               "WAITING FOR MARKET DATA — no fill attempted (§34).", {})
        db.flush()
        return
    mkt = market.get(sig.token_id) or {}
    # what the market looked like at entry (post-mortems judge "chased the pump" etc. from this)
    sig_metrics["signal_kind"] = sig.kind
    sig_metrics["entry_market"] = {k: mkt.get(k) for k in ("price_chg_1h", "price_chg_15m", "price_chg_24h",
                                                           "liq", "buy_share_pct", "vol_liq_ratio", "sellp")}
    fill = X.buy_fill(price, size, mkt.get("liq") or 0, mkt.get("price_chg_1h") or 0,
                      settings["execution"])
    acct.cash = round(acct.cash - size, 6)
    ver_id = acct.strategy_version_id
    pos = PaperPosition(
        account_id=acct.id, signal_id=sig.id, strategy_version_id=ver_id,
        token_id=sig.token_id, entry_ts=now, signal_price=price,
        entry_price=fill["exec_price"], qty=fill["qty"], size_usd=size,
        cost_usd=fill["cost"], entry_fee=fill["fee"], slippage_pct=fill["slip_pct"],
        stop_price=round(fill["exec_price"] * (1 - stop_pct / 100.0), 12),
        target_price=(round(fill["exec_price"] * (1 + float(settings["take_profit_pct"]) / 100.0), 12)
                      if float(settings["take_profit_pct"]) > 0 else None),     # 0 = let it run
        trail_pct=float(settings["trailing_stop_pct"]) or None,
        peak_price=fill["exec_price"],
        ai_confidence=sig.confidence, risk_score=sig.risk_score,
        market_regime=sig.market_regime, metrics_json=sig_metrics,
        why_json=sig.why_json, max_hold_secs=int(settings["max_hold_hours"] * 3600))
    db.add(pos)
    sig.status = "approved"
    sig.risk_decision_json = decision
    db.flush()
    stats["approved"] += 1
    stats["bought"] += 1
    detail = (f"Signal ${price:.8g} → fill ${fill['exec_price']:.8g} (slip {fill['slip_pct']:.2f}%) · "
              f"size ${size:,.2f} · fee ${fill['fee']:.2f} · stop ${pos.stop_price:.8g} · "
              f"target {f'${pos.target_price:.8g}' if pos.target_price else 'none (trailing)'} · "
              f"regime {sig.market_regime}")
    if verdict:
        detail += (f" · AI verdict {verdict['rating'].replace('_', ' ').title()} ({verdict['confidence']})"
                   + (f", size ×{sig_metrics['verdict_size_mult']:g}" if "verdict_size_mult" in sig_metrics else ""))
    _event(db, acct, pos, sig, "BUY", f"🟢 PAPER BUY — ${sym}",
           f"{ev.title} · conf {sig.confidence:.0f}% · {detail}", {
               "size": size, **fill, "stop": pos.stop_price, "target": pos.target_price})
    _feed_row(db, pos.token_id, "paper_buy", f"pos{pos.id}",
              f"🤖 PAPER BUY — ${sym} · conf {sig.confidence:.0f}%",
              f"AI paper trader bought ${size:,.0f} (simulated, virtual funds): {ev.signal_text or ev.title}",
              "positive", sig.importance or "LOW", min(70, (sig.importance_score or 35) - 10),
              sig.confidence, {"size_usd": size, "exec_price": fill["exec_price"],
                               "stop": pos.stop_price, "target": pos.target_price,
                               "signal_kind": sig.kind, "regime": sig.market_regime})


def _manage_exit(db, acct, settings, p: PaperPosition, now: int, market: dict,
                 sell_hits: dict, stats: dict) -> bool:
    mkt = market.get(p.token_id) or {}
    px = _price(market, p.token_id)
    if px <= 0:
        return False
    p.peak_price = max(p.peak_price or p.entry_price, px)
    p.trough_price = min(p.trough_price or p.entry_price, px)
    be = float(settings.get("breakeven_at_pct") or 0)
    if be > 0 and p.peak_price >= p.entry_price * (1 + be / 100.0):
        ex = settings["execution"]
        rt = 2 * (float(ex.get("fee_pct", 0.3)) + float(ex.get("base_slippage_pct", 1.0))
                  + float(ex.get("half_spread_pct", 0.0)))
        be_px = round(p.entry_price * (1 + rt / 100.0), 12)   # entry + round-trip costs
        if (p.stop_price or 0) < be_px:
            p.stop_price = be_px
    arm = 1 + max(2.0, float(settings.get("trail_activate_pct") or 0)) / 100.0
    reason = None
    if px <= (p.stop_price or 0):
        reason = "BREAKEVEN_STOP" if be > 0 and p.stop_price >= p.entry_price else "STOP_LOSS"
    elif p.target_price and px >= p.target_price:
        reason = "TAKE_PROFIT"
    elif p.trail_pct and px <= p.peak_price * (1 - p.trail_pct / 100.0) and p.peak_price > p.entry_price * arm:
        reason = "TRAILING_STOP"
    elif p.token_id in sell_hits:
        reason = "SELL_SIGNAL"
    elif _needs_snap(settings) and (cf := CF.exit_reason(
            settings, {"momentum": mkt.get("momentum"), "regime": mkt.get("regime")},
            p.entry_ts, now, p.entry_price, px)):
        reason = cf
    elif (mkt.get("momentum") is not None and p.metrics_json.get("momentum") is not None
          and mkt["momentum"] <= p.metrics_json["momentum"] - float(settings["confidence_collapse_pts"])):
        reason = "CONFIDENCE_COLLAPSE"
    elif mkt.get("sellp") is not None and mkt["sellp"] >= float(settings["sell_pressure_exit"]):
        reason = "RISK_EXIT"
    elif (mkt.get("liq_chg_3h") is not None
          and mkt["liq_chg_3h"] <= float(settings["liquidity_exit_pct"])):
        reason = "LIQUIDITY_EXIT"
    elif settings.get("verdict_exit") and _downgraded(db, p, now):
        reason = "VERDICT_EXIT"
    elif now - p.entry_ts >= (p.max_hold_secs or 86400):
        reason = "MAX_HOLD"
    if reason is None:
        return False
    _close_position(db, acct, settings, p, px, mkt, now, reason, stats)
    return True


def _downgraded(db, p: PaperPosition, now: int) -> bool:
    """True if the coin got a NEW verdict after entry rating it Avoid / Rug risk."""
    v = F.latest_verdict(db, p.token_id, now)
    entered = ((p.metrics_json or {}).get("verdict") or {}).get("id")
    return bool(v and v["rating"] in F.BAD_VERDICTS and v["created_ts"] >= p.entry_ts and v["id"] != entered)


def _close_position(db, acct, settings, p: PaperPosition, px: float, mkt: dict,
                    now: int, reason: str, stats: dict) -> None:
    fill = X.sell_fill(px, p.qty, mkt.get("liq") or 0, mkt.get("price_chg_1h") or 0,
                       settings["execution"])
    proceeds = fill["proceeds"]
    pnl = round(proceeds - p.cost_usd - 0, 6)   # entry fee already inside cost math
    pnl_pct = 100.0 * pnl / max(p.size_usd, 1e-9)
    p.status = "closed"
    p.exit_ts = now
    p.exit_price = fill["exec_price"]
    p.exit_reason = reason
    p.exit_fee = fill["fee"]
    p.realized_pnl = pnl
    p.pnl_pct = round(pnl_pct, 3)
    p.hold_secs = now - p.entry_ts
    acct.cash = round(acct.cash + proceeds, 6)
    acct.day_realized = round((acct.day_realized or 0) + pnl, 6)
    if pnl >= 0:
        acct.consec_wins = (acct.consec_wins or 0) + 1
        acct.consec_losses = 0
    else:
        acct.consec_losses = (acct.consec_losses or 0) + 1
        acct.consec_wins = 0
    stats["sold"] += 1
    try:
        with db.begin_nested():          # a post-mortem must never break a close
            db.flush()
            PM.review_position(db, p, now)
    except Exception as exc:             # noqa: BLE001
        log.warning("post-mortem at close failed for position %s: %s", p.id, exc)
    sym = _symbol(db, p.token_id)
    reason_txt = {"STOP_LOSS": "Stop loss reached", "TAKE_PROFIT": "Take-profit reached",
                  "BREAKEVEN_STOP": "Break-even stop (trade had moved into profit)",
                  "TRAILING_STOP": "Trailing stop triggered",
                  "SELL_SIGNAL": "AI sell signal / distribution event",
                  "CONFIDENCE_COLLAPSE": "AI confidence collapsed vs entry",
                  "RISK_EXIT": "Risk threshold exceeded (sell pressure)",
                  "LIQUIDITY_EXIT": "Liquidity deteriorated",
                  "MAX_HOLD": "Maximum holding period reached",
                  "VERDICT_EXIT": "AI verdict downgraded to Avoid / Rug risk",
                  "MOMENTUM_EXIT": "Momentum faded below the exit level",
                  "REGIME_EXIT": "Coin regime turned to distribution",
                  "TIME_STOP": "Time stop: trade never got going",
                  "MANUAL": "Closed manually (simulated)"}.get(reason, reason)
    kind = "PROFIT" if pnl >= 0 else "LOSS"
    detail = (f"{reason_txt} · entry ${p.entry_price:.8g} → exit ${fill['exec_price']:.8g} "
              f"(slip {fill['slip_pct']:.2f}%) · fees ${(p.entry_fee + fill['fee']):.2f} · "
               f"P&L {pnl:+.2f} ({pnl_pct:+.1f}%) · held {p.hold_secs // 60}m")
    _event(db, acct, p, None, "SELL",
           f"{'💰 PROFIT REALIZED' if pnl >= 0 else '🔻 LOSS REALIZED'} — ${sym} {pnl:+.2f}",
           detail, {"pnl": pnl, "pnl_pct": pnl_pct, "reason": reason,
                    "entry_fee": p.entry_fee, "exit_fee": fill["fee"]})
    total_fee = (p.entry_fee or 0) + fill["fee"]
    _feed_row(db, p.token_id, f"paper_{'win' if pnl >= 0 else 'loss'}", f"pos{p.id}",
              f"{'💰' if pnl >= 0 else '🔻'} PAPER {'PROFIT' if pnl >= 0 else 'LOSS'} — ${sym} {pnl:+.0f}$ ({pnl_pct:+.0f}%)",
              f"{reason_txt} after {max(p.hold_secs // 60, 1)}m (simulated, virtual funds). "
              f"Entry conf {p.ai_confidence:.0f}%, risk {p.risk_score or 0:.0f}. Fees+slip modeled (${total_fee:.2f} fees).",
              "positive" if pnl >= 0 else "danger",
              "HIGH" if abs(pnl_pct) >= 15 else "MEDIUM",
              min(85, 55 + int(abs(pnl_pct))), p.ai_confidence,
              {"pnl": pnl, "pnl_pct": pnl_pct, "hold_secs": p.hold_secs,
               "exit_reason": reason, "entry_price": p.entry_price,
               "exit_price": p.exit_price})


def _symbol(db, token_id: int) -> str:
    sym = db.execute(select(Token.symbol).where(Token.id == token_id)).scalar()
    return sym or str(token_id)


def manual_close(db, acct, position_id: int, now: int, market: dict) -> PaperPosition | None:
    global _now_ts
    _now_ts = now
    p = db.execute(select(PaperPosition).where(
        PaperPosition.id == position_id, PaperPosition.account_id == acct.id,
        PaperPosition.status == "open")).scalar_one_or_none()
    if not p:
        return None
    settings = settings_for(acct)
    stats: dict = {"sold": 0}
    _close_position(db, acct, settings, p, _price(market, p.token_id) or p.entry_price,
                    market.get(p.token_id) or {}, now, "MANUAL", stats)
    return p


def reset_account(db, acct: DemoAccount, start_cash: float = 10000.0) -> None:
    """§23: hard reset — clear positions, signals, events, equity history."""
    from ..models import EquitySnapshot as Eq, PaperTradeEvent as PTE, PaperTradeReview as PTR
    # children first: timeline events + reviews reference positions, positions reference signals
    for model, col in ((PaperTradeEvent, PTE.account_id), (PTR, PTR.account_id),
                       (EquitySnapshot, Eq.account_id),
                       (PaperPosition, PaperPosition.account_id),
                       (PaperSignal, PaperSignal.account_id)):
        db.execute(model.__table__.delete().where(col == acct.id))
    acct.cash = acct.starting_cash = start_cash
    acct.peak_equity = start_cash
    acct.halt_until, acct.halt_reason = None, None
    acct.day_realized = 0.0
    acct.consec_losses = acct.consec_wins = 0
    acct.last_event_id = db.execute(select(func.max(SignalEvent.id))).scalar() or 0
    _event(db, acct, None, None, "SYSTEM", "Demo account reset",
           f"Portfolio cleared. New starting capital ${start_cash:,.2f} (virtual).",
           {"starting_capital": start_cash})
