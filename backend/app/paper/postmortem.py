"""Trade post-mortems: why did each paper trade win or lose, and which rule is to blame?

Every closed trade gets a review:

  * at close (provisional) — from what happened WHILE it was open: the best and
    worst point (MFE / MAE vs entry), how far the coin had run before we bought,
    the exit reason and the P&L after costs;
  * POSTMORTEM_HOURS later (final) — plus what the price did AFTER we left:
    did a stopped-out coin go on to hit our target? did it rug right after a
    signal exit saved us? Only stored 5-minute price/liquidity snapshots are used;
    a coin that dropped off tracking is judged on the data that exists and the
    review says how many hours of post-exit data it saw.

One cause per trade (first match wins, so the most actionable blame comes first):

  losses
    RUG                 price -80% or liquidity -80% while we held it
    STOP_THEN_RECOVERED stopped out, then the price reached our target level
    EARLY_EXIT          a signal / risk / time exit, then price went back >= +10% over entry
    GAVE_BACK           was up >= +10% at some point, still closed at a loss
    CHASED              bought after a >= +40% 1h run and it never got >= +3%
    SAVED_BY_EXIT       the exit worked: the price fell >= 20 points further (or rugged) after we left
    TIMED_OUT           max hold ran out
    COSTS               went nowhere (MFE < +5%, loss <= 5%): fees + slippage made it a loss
    FAILED_SETUP        the setup simply did not work; no single rule to blame
  wins
    LEFT_ON_TABLE       fixed take-profit sold, then it ran >= 50 points further
    CLEAN_WIN

summary() rolls the reviews up per account and turns repeated causes into concrete,
OPT-IN setting suggestions. Nothing here changes any setting by itself.
"""
from __future__ import annotations

import statistics
import time

from sqlalchemy import or_, select

from ..models import LiquiditySnapshot, PaperPosition, PaperTradeReview, PriceSnapshot, Token

POSTMORTEM_HOURS = 24          # post-exit window before a review is final
REF_TARGET_PCT = 35.0          # "target level" for trades that had no fixed take-profit
RUG_DROP_PCT = -80.0           # price or liquidity this far down = rug
RECOVER_PCT = 10.0             # EARLY_EXIT: price back this far above entry
GIVEBACK_MFE_PCT = 10.0        # GAVE_BACK: was up at least this much
CHASE_1H_PCT = 40.0            # CHASED: 1h run before entry at least this
CHASE_MAX_MFE_PCT = 3.0        #   ... and never got more than this
SAVED_EXTRA_DROP = 20.0        # SAVED_BY_EXIT: fell this many more points after exit
COSTS_BAND_PCT = 5.0           # COSTS: |loss| and MFE both under this
LEFT_ON_TABLE_PTS = 50.0       # LEFT_ON_TABLE: ran this many points past our exit
PER_TICK = 25                  # reviews per tick (bounded DB work)
MIN_CAUSE_N = 5                # a cause needs this many trades before it drives a suggestion
MIN_CAUSE_SHARE = 0.25         # ... and this share of the losses (or of take-profit wins)

STOP_EXITS = ("STOP_LOSS",)
SIGNAL_EXITS = ("SELL_SIGNAL", "CONFIDENCE_COLLAPSE", "RISK_EXIT", "LIQUIDITY_EXIT",
                "VERDICT_EXIT", "MAX_HOLD")

CAUSES: dict[str, dict] = {
    "RUG": {"outcome": "loss", "label": "Rug or liquidity pull",
            "blame": "safety filters"},
    "STOP_THEN_RECOVERED": {"outcome": "loss", "label": "Stopped out, then it recovered",
                            "blame": "stop too tight"},
    "EARLY_EXIT": {"outcome": "loss", "label": "Sold early on a signal that was wrong",
                   "blame": "exit signals"},
    "GAVE_BACK": {"outcome": "loss", "label": "Was in profit, gave it back",
                  "blame": "no profit protection"},
    "CHASED": {"outcome": "loss", "label": "Bought after the move had happened",
               "blame": "entry chased a pump"},
    "SAVED_BY_EXIT": {"outcome": "loss", "label": "Exit rule saved money",
                      "blame": "none (rule worked)"},
    "TIMED_OUT": {"outcome": "loss", "label": "Max hold ran out",
                  "blame": "hold time"},
    "COSTS": {"outcome": "loss", "label": "Went nowhere; costs made it a loss",
              "blame": "costs / thin pool"},
    "FAILED_SETUP": {"outcome": "loss", "label": "Setup simply failed",
                     "blame": "entry signal quality"},
    "LEFT_ON_TABLE": {"outcome": "win", "label": "Take-profit cut a runner",
                      "blame": "fixed take-profit"},
    "CLEAN_WIN": {"outcome": "win", "label": "Clean win", "blame": "none"},
}


def _pct(price: float | None, entry: float) -> float | None:
    if price is None or not entry:
        return None
    return round(100.0 * (price / entry - 1.0), 2)


def _prices(db, token_id: int, t0: int, t1: int) -> list[tuple[int, float]]:
    rows = db.execute(select(PriceSnapshot.ts, PriceSnapshot.price_usd).where(
        PriceSnapshot.token_id == token_id, PriceSnapshot.ts >= t0, PriceSnapshot.ts <= t1,
        PriceSnapshot.price_usd > 0).order_by(PriceSnapshot.ts.asc())).all()
    return [(int(ts), float(px)) for ts, px in rows]


def _liqs(db, token_id: int, t0: int, t1: int) -> list[tuple[int, float]]:
    rows = db.execute(select(LiquiditySnapshot.ts, LiquiditySnapshot.liquidity_usd).where(
        LiquiditySnapshot.token_id == token_id, LiquiditySnapshot.ts >= t0, LiquiditySnapshot.ts <= t1,
        LiquiditySnapshot.liquidity_usd.isnot(None)).order_by(LiquiditySnapshot.ts.asc())).all()
    return [(int(ts), float(v)) for ts, v in rows]


def entry_chg_1h(p: PaperPosition) -> float | None:
    m = p.metrics_json or {}
    v = (m.get("entry_market") or {}).get("price_chg_1h")
    if v is None:
        v = m.get("price_chg_1h")
    try:
        return None if v is None else float(v)
    except (TypeError, ValueError):
        return None


def facts(db, p: PaperPosition, now: int) -> dict:
    """Everything the classifier needs, from stored real data only."""
    entry = float(p.entry_price or 0)
    exit_ts = int(p.exit_ts or now)
    during = _prices(db, p.token_id, int(p.entry_ts or exit_ts), exit_ts)
    hi = [px for _, px in during] + [x for x in (p.peak_price, p.exit_price) if x]
    lo = [px for _, px in during] + [x for x in (p.trough_price, p.exit_price) if x]
    end = min(now, exit_ts + POSTMORTEM_HOURS * 3600)
    after = [(ts, px) for ts, px in _prices(db, p.token_id, exit_ts + 1, end)]
    target = p.target_price or (entry * (1 + REF_TARGET_PCT / 100.0) if entry else None)

    liq = _liqs(db, p.token_id, int(p.entry_ts or exit_ts) - 900, end)
    liq0 = next((v for ts, v in liq if ts >= (p.entry_ts or 0) - 900), None)
    liq_min_during = min((v for ts, v in liq if ts <= exit_ts), default=None)
    liq_min_after = min((v for ts, v in liq if ts > exit_ts), default=None)
    liq_drop_during = _pct(liq_min_during, liq0) if liq0 else None
    liq_drop_after = _pct(liq_min_after, liq0) if liq0 else None

    mae = _pct(min(lo), entry) if lo else None
    post_min = _pct(min(px for _, px in after), entry) if after else None
    return {
        "pnl_pct": float(p.pnl_pct or 0.0),
        "exit_reason": p.exit_reason,
        "mfe_pct": _pct(max(hi), entry) if hi else None,
        "mae_pct": mae,
        "post_max_pct": _pct(max(px for _, px in after), entry) if after else None,
        "post_min_pct": post_min,
        "post_end_pct": _pct(after[-1][1], entry) if after else None,
        "post_hours": round((after[-1][0] - exit_ts) / 3600.0, 2) if after else 0.0,
        "target_pct": _pct(target, entry) if target else None,
        "reached_target_after": bool(after and target and max(px for _, px in after) >= target),
        "rug_during": bool((mae is not None and mae <= RUG_DROP_PCT)
                           or (liq_drop_during is not None and liq_drop_during <= RUG_DROP_PCT)),
        "rug_after": bool((post_min is not None and post_min <= RUG_DROP_PCT)
                          or (liq_drop_after is not None and liq_drop_after <= RUG_DROP_PCT)),
        "liq_drop_during_pct": liq_drop_during,
        "liq_drop_after_pct": liq_drop_after,
        "entry_chg_1h": entry_chg_1h(p),
        "had_fixed_target": bool(p.target_price),
    }


def classify(f: dict) -> tuple[str, str]:
    """(cause, lesson) for one trade's facts. Pure function: easy to test and tune."""
    pnl, mfe, reason = f["pnl_pct"], f.get("mfe_pct"), f.get("exit_reason")
    post_max, post_min = f.get("post_max_pct"), f.get("post_min_pct")
    chase = f.get("entry_chg_1h")
    if pnl >= 0:
        if f.get("had_fixed_target") and reason == "TAKE_PROFIT" and post_max is not None \
                and post_max >= pnl + LEFT_ON_TABLE_PTS:
            return "LEFT_ON_TABLE", (f"Sold at the {pnl:+.0f}% take-profit; it went on to {post_max:+.0f}% "
                                     f"vs entry within {f.get('post_hours', 0):.0f}h.")
        return "CLEAN_WIN", f"Won {pnl:+.1f}% after costs."
    if f.get("rug_during"):
        what = (f"price fell to {f['mae_pct']:+.0f}%" if (f.get("mae_pct") or 0) <= RUG_DROP_PCT
                else f"liquidity fell {f.get('liq_drop_during_pct') or 0:+.0f}%")
        return "RUG", f"Rugged while held: {what}. Safety filters should have kept us out."
    if reason in STOP_EXITS and f.get("reached_target_after"):
        return "STOP_THEN_RECOVERED", (f"Stopped out at {pnl:+.0f}%, then it reached the "
                                       f"{f.get('target_pct') or REF_TARGET_PCT:+.0f}% target level "
                                       f"(best {post_max:+.0f}%). The stop was inside normal noise.")
    if reason in SIGNAL_EXITS and post_max is not None and post_max >= RECOVER_PCT:
        return "EARLY_EXIT", (f"{(reason or '').replace('_', ' ').lower()} exit at {pnl:+.0f}%, then it went "
                              f"back to {post_max:+.0f}% vs entry. That exit signal was wrong this time.")
    if mfe is not None and mfe >= GIVEBACK_MFE_PCT:
        return "GAVE_BACK", f"Was up {mfe:+.0f}% at best, closed at {pnl:+.0f}%. Nothing locked the gain in."
    if chase is not None and chase >= CHASE_1H_PCT and (mfe is None or mfe < CHASE_MAX_MFE_PCT):
        return "CHASED", (f"Bought after a {chase:+.0f}% 1h run and it never got above "
                          f"{(mfe or 0):+.0f}%. The move had already happened.")
    if post_min is not None and (f.get("rug_after") or post_min <= pnl - SAVED_EXTRA_DROP):
        return "SAVED_BY_EXIT", (f"Lost {pnl:+.0f}%, but it fell to {post_min:+.0f}% after we left"
                                 f"{' (rugged)' if f.get('rug_after') else ''}. The exit did its job.")
    if reason == "MAX_HOLD":
        return "TIMED_OUT", f"Held to the time limit and closed at {pnl:+.0f}%."
    if pnl > -COSTS_BAND_PCT and (mfe is None or mfe < COSTS_BAND_PCT):
        return "COSTS", f"Barely moved (best {(mfe or 0):+.1f}%); fees and slippage made it {pnl:+.1f}%."
    return "FAILED_SETUP", f"Lost {pnl:+.0f}% with no single rule to blame; the entry signal did not work."


def review_position(db, p: PaperPosition, now: int) -> PaperTradeReview | None:
    """Create or refresh the review for one closed trade. Final once the post-exit
    window has passed; never rewritten after that."""
    if p.status != "closed" or not p.entry_price:
        return None
    r = db.execute(select(PaperTradeReview).where(PaperTradeReview.position_id == p.id)).scalar_one_or_none()
    if r is not None and r.status == "final":
        return r
    f = facts(db, p, now)
    cause, lesson = classify(f)
    if r is None:
        r = PaperTradeReview(position_id=p.id, account_id=p.account_id, token_id=p.token_id)
        db.add(r)
    r.exit_ts = p.exit_ts
    r.reviewed_ts = now
    r.status = "final" if now >= (p.exit_ts or now) + POSTMORTEM_HOURS * 3600 else "provisional"
    r.outcome = CAUSES[cause]["outcome"]
    r.cause, r.lesson = cause, lesson[:300]
    r.pnl_pct = f["pnl_pct"]
    r.mfe_pct, r.mae_pct = f["mfe_pct"], f["mae_pct"]
    r.post_max_pct, r.post_min_pct, r.post_end_pct = f["post_max_pct"], f["post_min_pct"], f["post_end_pct"]
    r.post_hours = f["post_hours"]
    r.reached_target_after = f["reached_target_after"]
    r.rugged = bool(f["rug_during"] or f["rug_after"])
    r.entry_chg_1h = f["entry_chg_1h"]
    r.facts_json = f
    return r


def review_due(db, now: int, limit: int = PER_TICK) -> int:
    """Finalize reviews whose post-exit window has passed, and back-fill trades closed
    before post-mortems existed. Bounded per tick; oldest first."""
    cutoff = now - POSTMORTEM_HOURS * 3600
    rows = db.execute(
        select(PaperPosition).outerjoin(PaperTradeReview, PaperTradeReview.position_id == PaperPosition.id)
        .where(PaperPosition.status == "closed",
               or_(PaperTradeReview.id.is_(None),
                   (PaperTradeReview.status == "provisional") & (PaperPosition.exit_ts <= cutoff)))
        .order_by(PaperPosition.exit_ts.asc()).limit(limit)).scalars().all()
    for p in rows:
        review_position(db, p, now)
    return len(rows)


# ---------------------------------------------------------------- roll-up + suggestions
def _q(xs: list[float], q: float) -> float | None:
    xs = sorted(x for x in xs if x is not None)
    if not xs:
        return None
    i = min(len(xs) - 1, max(0, int(round(q * (len(xs) - 1)))))
    return xs[i]


def _suggest(settings: dict, by: dict[str, list], wins: list, losses: list) -> list[dict]:
    """Turn repeated causes into opt-in setting changes. Each one explains its evidence."""
    out: list[dict] = []
    n_loss = len(losses)

    def heavy(cause: str) -> bool:
        k = len(by.get(cause, []))
        return k >= MIN_CAUSE_N and n_loss and k / n_loss >= MIN_CAUSE_SHARE

    if heavy("STOP_THEN_RECOVERED"):
        k = len(by["STOP_THEN_RECOVERED"])
        stops = sum(1 for r in losses if (r.facts_json or {}).get("exit_reason") == "STOP_LOSS")
        win_mae = [-(r.mae_pct or 0) for r in wins if r.mae_pct is not None]
        p80 = _q(win_mae, 0.8)
        cur = float(settings.get("stop_loss_pct") or 0)
        new = round(min(30.0, max(cur + 3.0, (p80 or cur) + 2.0)))
        out.append({"cause": "STOP_THEN_RECOVERED", "setting": "stop_loss_pct", "current": cur, "suggested": new,
                    "why": f"{k} of {stops} stop-outs later reached the target level."
                           + (f" 80% of winners never dipped more than {p80:.0f}% first." if p80 is not None else ""),
                    "tradeoff": "A wider stop means bigger losses on the trades that really fail; the per-trade "
                                "loss cap shrinks position size to compensate."})
    if heavy("GAVE_BACK"):
        mfes = [r.mfe_pct for r in by["GAVE_BACK"] if r.mfe_pct is not None]
        med = statistics.median(mfes) if mfes else GIVEBACK_MFE_PCT
        new = round(max(8.0, med * 0.6))
        out.append({"cause": "GAVE_BACK", "setting": "breakeven_at_pct",
                    "current": float(settings.get("breakeven_at_pct") or 0), "suggested": new,
                    "why": f"{len(by['GAVE_BACK'])} losing trades had been up (median best {med:+.0f}%) before "
                           "turning into losses.",
                    "tradeoff": "Moving the stop to break-even early also closes some trades that would have "
                                "dipped and then won."})
    if heavy("CHASED"):
        runs = [r.entry_chg_1h for r in by["CHASED"] if r.entry_chg_1h is not None]
        cur = float(settings.get("max_chase_pct_1h") or 0)
        new = round(min(CHASE_1H_PCT, _q(runs, 0.25) or CHASE_1H_PCT))
        if cur == 0 or cur > new:
            out.append({"cause": "CHASED", "setting": "max_chase_pct_1h", "current": cur, "suggested": new,
                        "why": f"{len(by['CHASED'])} losses were bought after a big 1h run "
                               f"(median {statistics.median(runs):+.0f}%) and never went higher." if runs else
                               f"{len(by['CHASED'])} losses were bought after a big 1h run.",
                        "tradeoff": "Skips some genuine breakouts that keep running."})
    if heavy("RUG"):
        out.append({"cause": "RUG", "setting": "require_verified_contract",
                    "current": bool(settings.get("require_verified_contract")), "suggested": True,
                    "extra": {"max_top10_wallet_pct": 35.0}, "why": f"{len(by['RUG'])} trades were rugged while held.",
                    "tradeoff": "Fewer trades: coins wait for a Helius check before they can be bought."})
    if heavy("EARLY_EXIT"):
        reasons: dict[str, int] = {}
        for r in by["EARLY_EXIT"]:
            k = (r.facts_json or {}).get("exit_reason") or "?"
            reasons[k] = reasons.get(k, 0) + 1
        worst = max(reasons, key=reasons.get)
        knob = {"RISK_EXIT": ("sell_pressure_exit", 10.0), "CONFIDENCE_COLLAPSE": ("confidence_collapse_pts", 10.0),
                "LIQUIDITY_EXIT": ("liquidity_exit_pct", -10.0), "MAX_HOLD": ("max_hold_hours", None)}.get(worst)
        s = {"cause": "EARLY_EXIT", "why": f"{len(by['EARLY_EXIT'])} exits were followed by a recovery; "
                                           f"most came from {worst.replace('_', ' ').lower()} ({reasons[worst]}).",
             "tradeoff": "Looser exits also hold some coins that keep falling. Check SAVED_BY_EXIT before changing."}
        if knob and knob[1] is not None:
            cur = float(settings.get(knob[0]) or 0)
            s.update({"setting": knob[0], "current": cur, "suggested": round(cur + knob[1], 1)})
        elif knob:
            cur = float(settings.get("max_hold_hours") or 24)
            s.update({"setting": "max_hold_hours", "current": cur, "suggested": round(cur * 1.5, 1)})
        out.append(s)
    if heavy("COSTS"):
        cur = float(settings.get("min_liquidity_usd") or 0)
        out.append({"cause": "COSTS", "setting": "min_liquidity_usd", "current": cur,
                    "suggested": round(max(cur * 2, 20000.0)),
                    "why": f"{len(by['COSTS'])} trades went nowhere and lost only to fees and slippage, which are "
                           "highest in thin pools.",
                    "tradeoff": "Deeper pools mean fewer, older coins."})
    tp_wins = [r for r in wins if (r.facts_json or {}).get("exit_reason") == "TAKE_PROFIT"]
    lot = by.get("LEFT_ON_TABLE", [])
    if len(lot) >= MIN_CAUSE_N and tp_wins and len(lot) / len(tp_wins) >= MIN_CAUSE_SHARE:
        cur_tp = float(settings.get("take_profit_pct") or 0)
        out.append({"cause": "LEFT_ON_TABLE", "setting": "take_profit_pct", "current": cur_tp, "suggested": 0.0,
                    "extra": {"trailing_stop_pct": 20.0, "trail_activate_pct": max(cur_tp, 30.0)},
                    "why": f"{len(lot)} of {len(tp_wins)} take-profit exits kept running at least "
                           f"{LEFT_ON_TABLE_PTS:.0f} points higher.",
                    "tradeoff": "A trailing exit gives back part of every win in exchange for catching runners."})
    return out


def summary(db, account_id: int, settings: dict, days: int = 30, now: int | None = None) -> dict:
    now = now or int(time.time())
    since = now - days * 86400
    rows = db.execute(select(PaperTradeReview, PaperPosition).join(
        PaperPosition, PaperPosition.id == PaperTradeReview.position_id).where(
        PaperTradeReview.account_id == account_id, PaperTradeReview.exit_ts >= since)
        .order_by(PaperTradeReview.exit_ts.desc())).all()
    reviews = [r for r, _ in rows]
    pos = {p.id: p for _, p in rows}
    wins = [r for r in reviews if r.outcome == "win"]
    losses = [r for r in reviews if r.outcome == "loss"]
    by: dict[str, list] = {}
    for r in reviews:
        by.setdefault(r.cause, []).append(r)

    def block(cause: str) -> dict:
        rs = by.get(cause, [])
        usd = round(sum(pos[r.position_id].realized_pnl or 0 for r in rs), 2)
        pool = losses if CAUSES[cause]["outcome"] == "loss" else wins
        return {"cause": cause, "label": CAUSES[cause]["label"], "blame": CAUSES[cause]["blame"],
                "trades": len(rs), "share_pct": round(100 * len(rs) / len(pool), 1) if pool else 0.0,
                "pnl_usd": usd, "avg_pnl_pct": round(statistics.mean(r.pnl_pct or 0 for r in rs), 2) if rs else None}

    loss_causes = sorted((block(c) for c, v in CAUSES.items() if v["outcome"] == "loss" and by.get(c)),
                         key=lambda b: b["pnl_usd"])
    win_causes = [block(c) for c, v in CAUSES.items() if v["outcome"] == "win" and by.get(c)]
    kinds: dict[str, list] = {}
    for r in reviews:
        kinds.setdefault(((pos[r.position_id].metrics_json or {}).get("signal_kind")
                          or (r.facts_json or {}).get("signal_kind") or "?"), []).append(r)
    syms = {t.id: t.symbol or t.address[:8] for t in db.execute(
        select(Token).where(Token.id.in_({r.token_id for r in reviews}))).scalars().all()} if reviews else {}
    return {
        "days": days, "reviewed": len(reviews), "final": sum(1 for r in reviews if r.status == "final"),
        "wins": len(wins), "losses": len(losses), "window_h": POSTMORTEM_HOURS,
        "enough_data": len(reviews) >= 30,
        "loss_causes": loss_causes, "win_causes": win_causes,
        "excursions": {
            "winners_mae_p50": _q([r.mae_pct for r in wins], 0.5), "winners_mae_p20": _q([r.mae_pct for r in wins], 0.2),
            "losers_mfe_p50": _q([r.mfe_pct for r in losses], 0.5), "losers_mfe_p80": _q([r.mfe_pct for r in losses], 0.8),
        },
        "by_signal_kind": sorted(({"kind": k, "trades": len(v),
                                   "win_rate_pct": round(100 * sum(1 for r in v if r.outcome == "win") / len(v), 1),
                                   "avg_pnl_pct": round(statistics.mean(r.pnl_pct or 0 for r in v), 2)}
                                  for k, v in kinds.items()), key=lambda d: d["avg_pnl_pct"]),
        "suggestions": _suggest(settings, by, wins, losses) if reviews else [],
        "recent": [{"position_id": r.position_id, "symbol": syms.get(r.token_id), "exit_ts": r.exit_ts,
                    "status": r.status, "outcome": r.outcome, "cause": r.cause, "label": CAUSES[r.cause]["label"],
                    "lesson": r.lesson, "pnl_pct": r.pnl_pct, "mfe_pct": r.mfe_pct, "mae_pct": r.mae_pct,
                    "post_max_pct": r.post_max_pct, "post_min_pct": r.post_min_pct,
                    "exit_reason": (r.facts_json or {}).get("exit_reason")} for r in reviews[:25]],
        "rules": __doc__.split("One cause per trade")[1].split("summary()")[0].strip(),
        "note": "Suggestions are opt-in and need at least "
                f"{MIN_CAUSE_N} trades with the same cause making up {int(MIN_CAUSE_SHARE * 100)}%+ of losses. "
                "Test a change in a Strategy Lab account before using it on the main one.",
    }


def review_dict(r: PaperTradeReview | None) -> dict | None:
    if r is None:
        return None
    return {"status": r.status, "outcome": r.outcome, "cause": r.cause, "label": CAUSES[r.cause]["label"],
            "blame": CAUSES[r.cause]["blame"], "lesson": r.lesson, "pnl_pct": r.pnl_pct,
            "mfe_pct": r.mfe_pct, "mae_pct": r.mae_pct, "post_max_pct": r.post_max_pct,
            "post_min_pct": r.post_min_pct, "post_end_pct": r.post_end_pct, "post_hours": r.post_hours,
            "reached_target_after": r.reached_target_after, "rugged": r.rugged,
            "entry_chg_1h": r.entry_chg_1h, "window_h": POSTMORTEM_HOURS, "reviewed_ts": r.reviewed_ts}
