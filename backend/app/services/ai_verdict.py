"""Schedules AI verdicts, stores them, and sends the Telegram follow-up.

Who gets a verdict each tick (actively tracked real tokens only):
  * never judged, or the evidence changed materially (and the last verdict is at least
    VERDICT_MIN_GAP_MIN old), or the last verdict is older than VERDICT_REFRESH_HOURS
  * launch-alerted coins first, then never-judged, then by Trend Score
Gate verdicts (RUG_RISK / REVIEW) and rule verdicts are free. LLM debates are limited to
VERDICT_LLM_PER_TICK per tick and VERDICT_DAILY_LLM_MAX per 24 h; over the cap the
rule-based verdict is used and labelled as such.
"""
from __future__ import annotations

import asyncio
import datetime as dt
import html
import logging
import time

from sqlalchemy import func, select

from .. import config
from ..engine import telegram
from ..engine import verdict as V
from ..models import Token, TokenVerdict, TrendScore
from ..providers import llm as llm_mod
from .contract_facts import is_mock, latest_contract_facts

log = logging.getLogger("radar.verdict")
ACTIVE_WINDOW_S = 900          # a TrendScore within 15 min = actively tracked


def _naive(d):
    return d.replace(tzinfo=None) if d is not None and d.tzinfo else d


def latest_verdicts(db, token_ids: list[int]) -> dict[int, TokenVerdict]:
    if not token_ids:
        return {}
    sub = (select(TokenVerdict.token_id, func.max(TokenVerdict.id).label("mid"))
           .where(TokenVerdict.token_id.in_(token_ids)).group_by(TokenVerdict.token_id).subquery())
    rows = db.execute(select(TokenVerdict).join(sub, TokenVerdict.id == sub.c.mid)).scalars().all()
    return {r.token_id: r for r in rows}


def _launch_expected(token: Token, facts: dict) -> bool:
    """True if launch forensics should exist (recent launch, enabled, not hard-failed)."""
    if not config.LAUNCH_FORENSICS_ENABLED or not token.token_created_at:
        return False
    age_h = (time.time() * 1000 - token.token_created_at) / 3.6e6
    return age_h <= min(V.LAUNCH_RECENT_H, config.LAUNCH_FORENSICS_MAX_AGE_H)


def collect(db, now: dt.datetime | None = None) -> list[dict]:
    """Tokens due for a verdict, best first, with their evidence."""
    now = now or dt.datetime.utcnow()
    cutoff = int(time.time()) - ACTIVE_WINDOW_S
    sub = (select(TrendScore.token_id, func.max(TrendScore.ts).label("mts"))
           .where(TrendScore.kind == "live", TrendScore.ts >= cutoff).group_by(TrendScore.token_id).subquery())
    scores = db.execute(select(TrendScore).join(sub, (TrendScore.token_id == sub.c.token_id)
                                                & (TrendScore.ts == sub.c.mts))).scalars().all()
    by_tok = {s.token_id: s for s in scores}
    tokens = [t for t in db.execute(select(Token).where(Token.id.in_(list(by_tok)))).scalars().all()
              if not is_mock(t)]
    facts = latest_contract_facts(db, tokens)
    last = latest_verdicts(db, [t.id for t in tokens])
    llm_on = llm_mod.enabled()
    due = []
    for t in tokens:
        ev = V.build_evidence(t, facts[t.id], by_tok[t.id])
        h = V.evidence_hash(ev)
        prev = last.get(t.id)
        llm_only = False
        if prev is not None:
            age_min = (now - _naive(prev.created_at)).total_seconds() / 60 if prev.created_at else 1e9
            fresh = age_min < config.VERDICT_REFRESH_HOURS * 60 and (prev.evidence_hash == h or age_min < config.VERDICT_MIN_GAP_MIN)
            if fresh:
                # A rules verdict given while the AI queue was busy gets its AI review later,
                # on the same evidence. Gate verdicts (rug / review) never need one.
                if not (llm_on and prev.source == "rules" and prev.evidence_hash == h):
                    continue
                llm_only = True
        prio = (1 if llm_only else 0, 0 if t.launch_alert == "sent" else 1, 0 if prev is None else 1,
                -(by_tok[t.id].trend or 0))
        due.append({"prio": prio, "token": t, "evidence": ev, "hash": h, "llm_only": llm_only,
                    "launch_expected": _launch_expected(t, facts[t.id]), "score": by_tok[t.id]})
    due.sort(key=lambda d: d["prio"])
    return due


def llm_used_24h(db) -> int:
    since = dt.datetime.utcnow() - dt.timedelta(hours=24)
    return db.execute(select(func.count()).select_from(TokenVerdict).where(
        TokenVerdict.source == "llm", TokenVerdict.created_at >= since)).scalar() or 0


def _store(db, item: dict, out: dict, model: str | None) -> TokenVerdict:
    s = ((item["score"].components_json or {}).get("summary") or {})
    row = TokenVerdict(token_id=item["token"].id, rating=out["rating"], confidence=out.get("confidence"),
                       source=out["source"], reason=out.get("reason"),
                       positives_json=out.get("positives") or [], risks_json=out.get("risks") or [],
                       bull=out.get("bull"), bear=out.get("bear"), llm_rating=out.get("llm_rating"),
                       model=model, evidence_json=item["evidence"], evidence_hash=item["hash"],
                       price_at=s.get("price"), mcap_at=s.get("mcap"), liquidity_at=s.get("liquidity"),
                       created_at=dt.datetime.utcnow())
    db.add(row)
    return row


async def run_verdicts(session_factory, llm=None) -> dict:
    if not config.VERDICT_ENABLED:
        return {"status": "disabled"}
    with session_factory() as db:
        due = collect(db)
        llm_left = max(0, config.VERDICT_DAILY_LLM_MAX - llm_used_24h(db))
        record = None
        if config.VERDICT_TRACK_RECORD and due:
            from .outcomes import track_record_text
            try:
                record = track_record_text(db)
            except Exception:          # never let the memory block a verdict
                record = None
    use_llm = llm_mod.enabled() or llm is not None
    llm = llm or (llm_mod.LLMClient() if use_llm else None)
    res = {"due": len(due), "gate": 0, "rules": 0, "llm": 0, "llm_errors": 0}
    llm_budget = min(config.VERDICT_LLM_PER_TICK, llm_left) if use_llm else 0
    free_budget = config.VERDICT_RULES_PER_TICK
    for item in due:
        missing = V.missing_checks(item["evidence"], item["launch_expected"])
        out, model = V.gate(item["evidence"], item["launch_expected"]), None
        if out is None:
            if llm_budget > 0:
                llm_budget -= 1
                try:
                    out = await V.debate(item["evidence"], llm, config.LLM_QUICK_MODEL, config.LLM_JUDGE_MODEL,
                                         track_record=record)
                    model = config.LLM_JUDGE_MODEL
                    res["llm"] += 1
                    if missing:
                        out["reason"] += " Provisional: waiting for " + ", ".join(missing) + "."
                except llm_mod.LLMError as exc:
                    res["llm_errors"] += 1
                    if item["llm_only"]:
                        continue    # keep the existing rules verdict
                    out = V.rules_verdict(item["evidence"], missing)
                    out["reason"] += f" (AI unavailable: {str(exc)[:80]})"
            elif item["llm_only"]:
                continue            # already has a rules verdict; wait for an AI slot
            elif use_llm and llm_left <= 0:
                out = V.rules_verdict(item["evidence"], missing)
                out["reason"] += " (daily AI budget used; rule-based verdict)"
            elif use_llm:
                # No AI slot this tick: give a rules verdict now so the coin is never left
                # without a call; the AI reviews it on a later tick (llm_only above).
                out = V.rules_verdict(item["evidence"], missing)
                out["reason"] += " (AI review queued)"
            else:
                out = V.rules_verdict(item["evidence"], missing)
        if out["source"] != "llm":
            if free_budget <= 0:
                continue
            free_budget -= 1
            res[out["source"]] += 1
        with session_factory() as db:
            _store(db, item, out, model)
            db.commit()
    if llm is not None:
        res["llm_calls"] = getattr(llm, "calls", 0)
    if config.VERDICT_TELEGRAM:
        res["telegram"] = await asyncio.to_thread(send_followups, session_factory)
    return res


# ---------------------------------------------------------------- Telegram
EMOJI = {"STRONG_WATCH": "🟢", "WATCH": "🟡", "NEUTRAL": "⚪", "AVOID": "🟠", "RUG_RISK": "🔴"}


def format_verdict(token: Token, v: TokenVerdict) -> str:
    e = lambda x: html.escape(str(x or ""), quote=False)  # noqa: E731
    src = {"llm": "AI debate", "rules": "rules", "gate": "safety gate"}.get(v.source, v.source)
    lines = [f"{EMOJI.get(v.rating, '⚪')} <b>FOMO verdict · ${e(token.symbol or '?')}: {V.LABEL.get(v.rating, v.rating)}</b>"
             f" <i>({e(v.confidence)} confidence, {src})</i>",
             e(v.reason)]
    if v.positives_json:
        lines.append("➕ " + e("; ".join(v.positives_json[:3])))
    if v.risks_json:
        lines.append("➖ " + e("; ".join(v.risks_json[:3])))
    lines += ["⚠️ Research label, not a buy or sell signal.", f"<code>{e(token.address)}</code>"]
    return "\n".join(lines)[:telegram.MAX_LEN]


def is_preliminary(v: TokenVerdict) -> bool:
    """Provisional (on-chain checks pending) or waiting for its AI review: shown in the app,
    but not worth a Telegram message; the follow-up waits for the complete call."""
    reason = v.reason or ""
    return "Provisional:" in reason or "AI review queued" in reason


def send_followups(session_factory) -> dict:
    """One verdict follow-up per launch-alerted coin (first non-REVIEW verdict)."""
    out = {"sent": 0, "failed": 0}
    if not telegram.is_configured():
        return out
    with session_factory() as db:
        sent_tokens = set(db.execute(select(TokenVerdict.token_id).where(
            TokenVerdict.telegram_sent.is_(True))).scalars().all())
        toks = db.execute(select(Token).where(Token.launch_alert == "sent")).scalars().all()
        todo = [t for t in toks if t.id not in sent_tokens]
        last = latest_verdicts(db, [t.id for t in todo])
        for t in todo[:config.TELEGRAM_MAX_PER_TICK]:
            v = last.get(t.id)
            if v is None or v.rating == "REVIEW" or is_preliminary(v):
                continue
            r = telegram.send_text(format_verdict(t, v))
            if r.get("ok"):
                v.telegram_sent = True
                out["sent"] += 1
            else:
                out["failed"] += 1
        db.commit()
    return out
