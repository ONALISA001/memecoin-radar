"""Strategy supervisor: watches the Strategy Lab and PROPOSES changes; the owner approves.

Every review (every SUPERVISOR_INTERVAL_H hours, or on demand) looks at each account's
closed trades and its post-mortems, and may propose:

  retire      a lab strategy that is clearly losing over enough trades
  challenger  a copy of the best strategy with ONE change taken from its post-mortems
              (what its losing trades had in common), checked on the backtester first
  promote     a challenger that beat its parent over enough trades: retire the parent
  fix_main    a post-mortem fix for your main account (a setting change)

Nothing changes until a proposal is approved (decide()). Rejected or expired proposals
are not re-proposed for REPROPOSE_AFTER_H hours. Every decision is kept as a log.
Simulation with virtual funds; never real trading.
"""
from __future__ import annotations

import hashlib
import json
import time

from sqlalchemy import select

from ..models import DemoAccount, StrategyProposal
from . import config as PC
from . import engine as PE
from . import lab as LAB
from . import postmortem as PM

MIN_TRADES = 30              # same evidence bar as the Lab's "leading" badge
RETIRE_PF = 0.8              # profit factor below this (with a negative return) = losing
PARENT_MIN_TRADES = 20       # a strategy needs this many trades before it gets a challenger
MAX_CHALLENGERS = 3
EXPIRE_H = 48
REPROPOSE_AFTER_H = 72
PROTECTED = ("baseline",)    # the yardstick is never retired


def _fp(*parts) -> str:
    return hashlib.sha1(json.dumps(parts, sort_keys=True, default=str).encode()).hexdigest()[:24]


def _name(db, acct: DemoAccount) -> str:
    if acct.strategy_key is None:
        return "Your main account"
    if acct.strategy_key in PC.PRESETS:
        return PC.PRESETS[acct.strategy_key]["name"]
    return (acct.label or acct.strategy_key).replace("Lab · ", "")


def _losing(st: dict) -> str | None:
    t = st.get("trades") or 0
    if t < MIN_TRADES:
        return None
    if st.get("edge") == "negative":
        return f"the 95% range of its average trade is below 0 over {t} trades"
    pf, ret = st.get("profit_factor"), st.get("return_pct") or 0
    if pf is not None and pf < RETIRE_PF and ret < 0:
        return f"profit factor {pf} and return {ret:+.1f}% over {t} trades"
    return None


def _score(st: dict) -> float:
    ret, dd = st.get("return_pct") or 0.0, max(st.get("max_drawdown_pct") or 0.0, 2.0)
    return ret / dd * 10 + ((min(st.get("profit_factor") or 1.0, 3.0)) - 1) * 5


def _change_from(sugg: dict) -> dict:
    ch = {sugg["setting"]: sugg["suggested"]}
    ch.update(sugg.get("extra") or {})
    return ch


def _backtest_check(db, parent_settings: dict, change: dict, now: int, hours: int = 168) -> dict:
    """Parent vs challenger on the same 7 days of history (look-ahead free)."""
    from . import backtest as BT
    cache: dict = {}
    a = BT.run(db, now - hours * 3600, now, 10_000, parent_settings, _cache=cache)["summary"]
    b = BT.run(db, now - hours * 3600, now, 10_000, {**parent_settings, **change}, _cache=cache)["summary"]
    keep = ("trades", "win_rate_pct", "total_return_pct", "profit_factor", "max_drawdown_pct")
    enough = (a["trades"] or 0) >= 10 or (b["trades"] or 0) >= 10
    better = (b["total_return_pct"] or 0) >= (a["total_return_pct"] or 0)
    return {"hours": hours, "parent": {k: a.get(k) for k in keep}, "challenger": {k: b.get(k) for k in keep},
            "enough_data": enough, "not_worse": better,
            "read": ("challenger did at least as well on past data" if enough and better else
                     "challenger did WORSE on past data" if enough else
                     "too little history to check on past data")}


def _recent_fps(db, user_id: int, now: int) -> set[str]:
    """Fingerprints that must not be proposed again right now."""
    rows = db.execute(select(StrategyProposal.fingerprint, StrategyProposal.status, StrategyProposal.decided_ts,
                             StrategyProposal.created_ts).where(StrategyProposal.user_id == user_id)).all()
    out = set()
    for fp, status, decided, created in rows:
        if status == "pending":
            out.add(fp)
        elif status in ("rejected", "expired") and now - (decided or created or 0) < REPROPOSE_AFTER_H * 3600:
            out.add(fp)
        elif status == "approved":
            out.add(fp)          # an approved change never needs proposing twice
    return out


def _all_lab(db, user_id: int) -> list[DemoAccount]:
    return db.execute(select(DemoAccount).where(DemoAccount.user_id == user_id,
                                                DemoAccount.strategy_key.isnot(None))).scalars().all()


def review(db, user_id: int, market: dict, now: int | None = None, backtest: bool = True) -> dict:
    """Look at every account and write new proposals. Returns {"created": [...], "checked": n}."""
    now = now or int(time.time())
    expire(db, user_id, now)
    skip = _recent_fps(db, user_id, now)
    accts = [a for a in _all_lab(db, user_id) if a.active]
    main = PE.get_or_create_account(db, user_id)
    stats = {a.id: LAB._stats(db, a, market) for a in accts}
    stats[main.id] = LAB._stats(db, main, market)
    created: list[StrategyProposal] = []

    def propose(kind: str, acct: DemoAccount, title: str, reason: str, evidence: dict, change: dict | None,
                fp: str):
        if fp in skip:
            return
        p = StrategyProposal(user_id=user_id, created_ts=now, kind=kind, fingerprint=fp, account_id=acct.id,
                             title=title[:160], reason=reason, evidence_json=evidence, change_json=change,
                             status="pending")
        db.add(p)
        db.flush()
        skip.add(fp)
        created.append(p)

    challengers = [a for a in accts if a.strategy_key and a.strategy_key.startswith("ch")]
    parent_of = {}
    for a in challengers:
        src = db.get(StrategyProposal, int(a.strategy_key[2:])) if a.strategy_key[2:].isdigit() else None
        if src is not None:
            parent_of[a.id] = src.account_id

    # 1) retire clearly losing strategies (never the baseline yardstick)
    for a in accts:
        if a.strategy_key in PROTECTED:
            continue
        why = _losing(stats[a.id])
        if why:
            st = stats[a.id]
            propose("retire", a, f"Retire {_name(db, a)}", f"It is losing: {why}.",
                    {"stats": {k: st.get(k) for k in ("trades", "win_rate_pct", "return_pct", "profit_factor",
                                                       "max_drawdown_pct", "avg_trade_ci95", "exits")}},
                    None, _fp("retire", a.id))

    # 2) promote a challenger that beat its parent (or retire one that lost to it)
    by_id = {a.id: a for a in accts}
    for c in challengers:
        pid = parent_of.get(c.id)
        parent = by_id.get(pid)
        cs = stats[c.id]
        if parent is None or (cs.get("trades") or 0) < MIN_TRADES:
            continue
        ps = stats[parent.id]
        ev = {"challenger": {k: cs.get(k) for k in ("trades", "return_pct", "profit_factor", "win_rate_pct")},
              "parent": {k: ps.get(k) for k in ("trades", "return_pct", "profit_factor", "win_rate_pct")}}
        if _score(cs) > _score(ps) and (cs.get("return_pct") or 0) > (ps.get("return_pct") or 0):
            propose("promote", c, f"Promote {_name(db, c)} over {_name(db, parent)}",
                    f"Over {cs['trades']} trades the challenger returned {cs.get('return_pct', 0):+.1f}% vs "
                    f"{ps.get('return_pct', 0):+.1f}% for its parent, with a better risk-adjusted score. "
                    "Approving retires the parent; the challenger keeps trading.", ev, {"retire_account": parent.id},
                    _fp("promote", c.id))
        elif _score(cs) < _score(ps):
            propose("retire", c, f"Retire {_name(db, c)} (lost to its parent)",
                    f"Over {cs['trades']} trades it did worse than {_name(db, parent)}; the change didn't help.",
                    ev, None, _fp("retire", c.id))

    # 3) challenger for the best strategy, built from what its own losing trades had in common
    eligible = [a for a in accts if (stats[a.id].get("trades") or 0) >= PARENT_MIN_TRADES
                and (stats[a.id].get("return_pct") or 0) > 0 and not _losing(stats[a.id])]
    testing = [a for a in challengers if "Promoted" not in (a.label or "")]
    if eligible and len(testing) < MAX_CHALLENGERS:
        best = max(eligible, key=lambda a: _score(stats[a.id]))
        settings = PE.settings_for(best)
        suggs = PM.summary(db, best.id, settings, now=now).get("suggestions") or []
        for sg in suggs[:2]:
            change = _change_from(sg)
            if all(settings.get(k) == v for k, v in change.items()):
                continue
            fp = _fp("challenger", best.id, change)
            if fp in skip:
                continue
            check = _backtest_check(db, dict(best.settings_json or {}), change, now) if backtest else None
            if check and check["enough_data"] and not check["not_worse"]:
                continue        # the fix looked worse on past data: don't propose it
            propose("challenger", best,
                    f"Test {_name(db, best)} with {sg['setting'].replace('_', ' ')} "
                    f"{sg['current']} → {sg['suggested']}",
                    f"Learned from its losses: {sg['why']} Trade-off: {sg['tradeoff']} Runs as a separate "
                    "$10k challenger; the original keeps trading unchanged.",
                    {"cause": sg.get("cause"), "parent_stats": {k: stats[best.id].get(k) for k in
                     ("trades", "return_pct", "profit_factor", "win_rate_pct")}, "backtest": check},
                    {"settings": {**(best.settings_json or {}), **change}, "diff": change}, fp)
            break               # one challenger per review keeps the lab readable

    # 4) post-mortem fixes for the main account
    msettings = PE.settings_for(main)
    for sg in (PM.summary(db, main.id, msettings, now=now).get("suggestions") or [])[:2]:
        change = _change_from(sg)
        if all(msettings.get(k) == v for k, v in change.items()):
            continue
        propose("fix_main", main, f"Main account: {sg['setting'].replace('_', ' ')} {sg['current']} → "
                                  f"{sg['suggested']}",
                f"Learned from your trades: {sg['why']} Trade-off: {sg['tradeoff']}",
                {"cause": sg.get("cause")}, {"diff": change}, _fp("fix_main", main.id, change))
    return {"created": [proposal_dict(p) for p in created], "checked": len(accts) + 1, "ts": now}


def expire(db, user_id: int, now: int) -> int:
    rows = db.execute(select(StrategyProposal).where(
        StrategyProposal.user_id == user_id, StrategyProposal.status == "pending",
        StrategyProposal.created_ts < now - EXPIRE_H * 3600)).scalars().all()
    for p in rows:
        p.status, p.decided_ts = "expired", now
    return len(rows)


def decide(db, user_id: int, proposal_id: int, approve: bool, now: int | None = None) -> StrategyProposal:
    """Approve (carry it out) or reject a pending proposal."""
    now = now or int(time.time())
    p = db.execute(select(StrategyProposal).where(StrategyProposal.id == proposal_id,
                                                  StrategyProposal.user_id == user_id)).scalar_one_or_none()
    if p is None:
        raise LookupError("proposal not found")
    if p.status != "pending":
        raise ValueError(f"proposal is already {p.status}")
    p.decided_ts = now
    if not approve:
        p.status = "rejected"
        db.flush()
        return p
    acct = db.get(DemoAccount, p.account_id)
    result: dict = {}
    if p.kind == "retire":
        acct.active = False
        PE._event(db, acct, None, None, "SYSTEM", f"⏹️ STRATEGY RETIRED — {_name(db, acct)}",
                  f"Retired by your approval: {p.reason}", {"proposal": p.id})
        result = {"retired_account": acct.id}
    elif p.kind == "challenger":
        settings = dict((p.change_json or {}).get("settings") or {})
        new = DemoAccount(user_id=user_id, label=f"Lab · Challenger #{p.id} · {_name(db, acct)}"[:64],
                          strategy_key=f"ch{p.id}", starting_cash=LAB.LAB_CASH, cash=LAB.LAB_CASH,
                          peak_equity=LAB.LAB_CASH, settings_json=settings,
                          last_event_id=max(acct.last_event_id or 0, 0))
        db.add(new)
        db.flush()
        PE.apply_settings(db, new, settings)
        PE._event(db, new, None, None, "SYSTEM", f"🧪 CHALLENGER STARTED — {_name(db, new)}",
                  f"Copy of {_name(db, acct)} with {p.change_json.get('diff')}. Virtual funds only.",
                  {"proposal": p.id})
        result = {"challenger_account": new.id}
    elif p.kind == "promote":
        parent = db.get(DemoAccount, (p.change_json or {}).get("retire_account"))
        if parent is not None:
            parent.active = False
            PE._event(db, parent, None, None, "SYSTEM", f"⏹️ STRATEGY REPLACED — {_name(db, parent)}",
                      f"Replaced by {_name(db, acct)} (your approval).", {"proposal": p.id})
        acct.label = (acct.label or "").replace("Challenger", "Promoted")[:64]
        result = {"retired_account": parent.id if parent else None, "promoted_account": acct.id}
    elif p.kind == "fix_main":
        diff = (p.change_json or {}).get("diff") or {}
        version = PE.apply_settings(db, acct, {**(acct.settings_json or {}), **diff})
        result = {"applied": diff, "strategy_version": version}
    p.status, p.result_json = "approved", result
    db.flush()
    return p


def proposal_dict(p: StrategyProposal) -> dict:
    return {"id": p.id, "kind": p.kind, "status": p.status, "title": p.title, "reason": p.reason,
            "evidence": p.evidence_json, "change": (p.change_json or {}).get("diff") if p.change_json else None,
            "ai_note": p.ai_note, "created_ts": p.created_ts, "decided_ts": p.decided_ts,
            "result": p.result_json, "account_id": p.account_id}


def listing(db, user_id: int, now: int | None = None) -> dict:
    now = now or int(time.time())
    rows = db.execute(select(StrategyProposal).where(StrategyProposal.user_id == user_id)
                      .order_by(StrategyProposal.id.desc()).limit(100)).scalars().all()
    pending = [proposal_dict(p) for p in rows if p.status == "pending"]
    log = [proposal_dict(p) for p in rows if p.status != "pending"][:30]
    return {"pending": pending, "log": log, "last_review_ts": _LAST_REVIEW.get(user_id),
            "rules": {"min_trades": MIN_TRADES, "retire_profit_factor": RETIRE_PF,
                      "parent_min_trades": PARENT_MIN_TRADES, "max_challengers": MAX_CHALLENGERS,
                      "expire_h": EXPIRE_H, "repropose_after_h": REPROPOSE_AFTER_H},
            "ts": now}


_LAST_REVIEW: dict[int, int] = {}


SYSTEM = ("You explain paper-trading strategy changes for a research dashboard. Use ONLY the JSON given. "
          "Never invent numbers. Say plainly when the sample is small. Never tell anyone to trade real money. "
          "Plain text, at most 60 words.")


async def add_ai_notes(session_factory, ids: list[int], llm, model: str, limit: int = 3) -> int:
    """Optional: a short LLM explanation on new proposals (only from their own evidence)."""
    from ..providers.llm import strip_reasoning
    done = 0
    for pid in ids[:limit]:
        with session_factory() as db:
            p = db.get(StrategyProposal, pid)
            if p is None or p.ai_note:
                continue
            payload = json.dumps({"kind": p.kind, "title": p.title, "reason": p.reason,
                                  "evidence": p.evidence_json, "change": (p.change_json or {}).get("diff")},
                                 default=str)[:3000]
            text = strip_reasoning(await llm.complete(SYSTEM, "Explain this proposal and its risk:\n" + payload,
                                                      model, 160)).strip()[:500]
            p.ai_note = text
            db.commit()
            done += 1
    return done


async def run_scheduled(session_factory, market: dict, interval_h: float, llm=None, model: str = "") -> dict:
    """Ingestion hook: review every owner-visible account set at most every interval_h hours."""
    from ..models import User
    now = int(time.time())
    out = {"reviewed": 0, "created": 0, "ai_notes": 0}
    with session_factory() as db:
        users = [u for (u,) in db.execute(select(DemoAccount.user_id).where(
            DemoAccount.strategy_key.isnot(None), DemoAccount.active == True).distinct()).all()]  # noqa: E712
    new_ids: list[int] = []
    for uid in users:
        if now - _LAST_REVIEW.get(uid, 0) < interval_h * 3600:
            continue
        with session_factory() as db:
            if db.get(User, uid) is None:
                continue
            res = review(db, uid, market, now)
            db.commit()
        _LAST_REVIEW[uid] = now
        out["reviewed"] += 1
        out["created"] += len(res["created"])
        new_ids += [p["id"] for p in res["created"]]
    if llm is not None and new_ids:
        try:
            out["ai_notes"] = await add_ai_notes(session_factory, new_ids, llm, model)
        except Exception as exc:  # noqa: BLE001  never let notes break the tick
            out["ai_error"] = str(exc)[:120]
    return out
