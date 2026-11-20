"""AI verdict on the Strategy Lab: how do the strategy accounts trade, side by side?

    lab stats (compare()) ─► rules review (always) ─► optional LLM write-up (cached)

* review() is deterministic: every running strategy gets a status from its own numbers
  (too early / promising / proven edge / underperforming / losing), the field is ranked by a
  risk-adjusted score, and the AI-verdict strategy is compared head-to-head with the baseline.
* With an LLM key, a short plain-language summary is written from the SAME stats JSON
  (never from anything else) and cached, so page refreshes don't spend tokens.

Simulation results with virtual funds. Not financial advice.
"""
from __future__ import annotations

import hashlib
import json
import time

MIN_TRADES = 30          # same bar as the Lab's "leading" badge
EARLY_TRADES = 10

_CACHE: dict[str, tuple[float, str]] = {}
_CACHE_TTL = 1800.0


def _status(r: dict) -> tuple[str, str]:
    t = r.get("trades") or 0
    ret, pf = r.get("return_pct") or 0.0, r.get("profit_factor")
    if t < EARLY_TRADES:
        return "too_early", f"only {t} closed trade{'s' if t != 1 else ''} — too early to judge"
    if r.get("edge") == "positive" and t >= MIN_TRADES:
        return "proven_edge", "95% range of its average trade is above 0 over enough trades"
    if r.get("edge") == "negative" or (pf is not None and pf < 0.8 and ret < 0):
        return "losing", f"losing money (profit factor {pf if pf is not None else '—'}, return {ret:+.1f}%)"
    if ret > 0 and (pf is None or pf >= 1.1):
        return "promising", f"profitable so far ({ret:+.1f}%), not yet proven"
    return "underperforming", f"roughly flat or slightly negative ({ret:+.1f}%)"


def _score(r: dict) -> float:
    """Risk-adjusted ranking: return per unit of drawdown, nudged by profit factor,
    and shrunk toward 0 when the sample is small."""
    ret, dd = r.get("return_pct") or 0.0, max(r.get("max_drawdown_pct") or 0.0, 2.0)
    pf = r.get("profit_factor") or 1.0
    shrink = min(1.0, (r.get("trades") or 0) / MIN_TRADES)
    return round(shrink * (ret / dd * 10 + (min(pf, 3.0) - 1) * 5), 2)


def _exit_note(r: dict) -> str | None:
    ex = r.get("exits") or {}
    total = sum(ex.values())
    if total < 5:
        return None
    stops = ex.get("STOP_LOSS", 0) / total
    time_out = ex.get("MAX_HOLD", 0) / total
    if stops >= 0.5:
        return f"{stops:.0%} of exits are stop-losses — entries are often wrong or stops too tight"
    if time_out >= 0.4:
        return f"{time_out:.0%} of exits are time-outs — trades rarely reach target or stop"
    winners = (ex.get("TAKE_PROFIT", 0) + ex.get("TRAILING_STOP", 0)) / total
    if winners >= 0.4:
        return f"{winners:.0%} of exits are targets or trailing stops — winners are being captured"
    return None


def review(lab: dict) -> dict:
    """lab: the output of lab.compare()."""
    rows = [r for r in lab.get("presets") or [] if r.get("running")]
    main = lab.get("main")
    if main:
        rows = rows + [main]
    if not rows:
        return {"available": False, "reason": "Start the Strategy Lab to get a verdict.", "strategies": []}
    strategies = []
    for r in rows:
        status, why = _status(r)
        strategies.append({"key": r["key"], "name": r["name"], "status": status, "why": why,
                           "score": _score(r), "trades": r.get("trades") or 0,
                           "return_pct": r.get("return_pct"), "win_rate_pct": r.get("win_rate_pct"),
                           "profit_factor": r.get("profit_factor"),
                           "max_drawdown_pct": r.get("max_drawdown_pct"), "exit_note": _exit_note(r)})
    ranked = sorted(strategies, key=lambda s: s["score"], reverse=True)
    for i, s in enumerate(ranked, 1):
        s["rank"] = i
    judged = [s for s in ranked if s["status"] != "too_early"]
    insights: list[str] = []
    if judged:
        best, worst = judged[0], judged[-1]
        insights.append(f"Best risk-adjusted so far: {best['name']} "
                        f"({(best['return_pct'] or 0):+.1f}%, max drawdown {best['max_drawdown_pct'] or 0:.1f}%, "
                        f"{best['trades']} trades).")
        if worst is not best:
            insights.append(f"Weakest: {worst['name']} ({(worst['return_pct'] or 0):+.1f}%) — {worst['why']}.")
    else:
        insights.append("No strategy has 10 closed trades yet; every number below is mostly noise.")
    by_key = {s["key"]: s for s in strategies}
    ai, base = by_key.get("ai_verdict"), by_key.get("baseline")
    head_to_head = None
    if ai and base:
        d_ret = (ai["return_pct"] or 0) - (base["return_pct"] or 0)
        d_win = None if ai["win_rate_pct"] is None or base["win_rate_pct"] is None \
            else round(ai["win_rate_pct"] - base["win_rate_pct"], 1)
        enough = ai["trades"] >= EARLY_TRADES and base["trades"] >= EARLY_TRADES
        head_to_head = {"return_diff_pp": round(d_ret, 2), "win_rate_diff_pp": d_win, "enough_data": enough,
                        "read": ("too few trades on one side to compare" if not enough else
                                 "the verdict filter is adding value so far" if d_ret > 1 else
                                 "the verdict filter is costing return so far" if d_ret < -1 else
                                 "no clear difference yet")}
        insights.append(f"AI Verdict Follower vs Baseline: {d_ret:+.1f} pp return"
                        + (f", {d_win:+.1f} pp win rate" if d_win is not None else "")
                        + f" — {head_to_head['read']}.")
    for s in ranked:
        if s["exit_note"] and s["status"] != "too_early":
            insights.append(f"{s['name']}: {s['exit_note']}.")
    leader = next((s for s in ranked if s["status"] == "proven_edge"), None)
    verdict = (f"{leader['name']} has a proven edge over {leader['trades']} trades." if leader else
               f"{judged[0]['name']} leads, but no strategy has a proven edge yet." if judged else
               "Too early: keep the lab running until each strategy has at least 30 closed trades.")
    return {"available": True, "verdict": verdict, "insights": insights[:6], "strategies": ranked,
            "head_to_head": head_to_head, "min_trades": MIN_TRADES, "source": "rules",
            "disclaimer": "Simulated trades with virtual funds and realistic costs. Not financial advice."}


SYSTEM = ("You review paper-trading strategies for a research dashboard. Use ONLY the JSON stats given. "
          "Never invent numbers. Small samples (<30 trades) are mostly luck: say so. Never tell anyone to "
          "buy or sell a coin or to trade with real money. Plain text, at most 120 words, no headings.")


async def ai_summary(rev: dict, llm, model: str) -> str | None:
    """LLM write-up of the review, cached by the stats fingerprint. Raises LLMError."""
    slim = {"verdict": rev["verdict"], "head_to_head": rev.get("head_to_head"),
            "strategies": [{k: s.get(k) for k in ("name", "status", "rank", "trades", "return_pct",
                                                   "win_rate_pct", "profit_factor", "max_drawdown_pct",
                                                   "exit_note")} for s in rev["strategies"]]}
    key = hashlib.sha1(json.dumps(slim, sort_keys=True, default=str).encode()).hexdigest()
    hit = _CACHE.get(key)
    if hit and time.time() - hit[0] < _CACHE_TTL:
        return hit[1]
    from ..providers.llm import strip_reasoning
    text = strip_reasoning(await llm.complete(
        SYSTEM, "Compare how these strategies trade and what the results so far do and don't show:\n"
        + json.dumps(slim, default=str), model, 300)).strip()[:900]
    _CACHE[key] = (time.time(), text)
    return text
