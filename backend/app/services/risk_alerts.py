"""High-severity risk events → Telegram, with a short AI read (Nov 2026).

Each tick, after the risk engine has written RiskEvents:
  * picks HIGH-severity events not yet sent (rug-style liquidity pulls, honeypots,
    concentrated supply, dangerous Token-2022 extensions) on real tokens only
  * groups them per token → one Telegram message per token
  * asks the LLM (LLM_QUICK_MODEL, e.g. the NVIDIA model) for a 1–2 sentence read
    of what the evidence means, an action label (AVOID / CAUTION / MONITOR) and what
    to verify. Capped per tick and per 24 h; a rug alert never waits for the AI —
    over the cap, or if the AI fails, the alert goes out without the AI line.

The RiskEvent 6 h dedupe per (token, category) still applies, so one rug is one alert.
Events older than RISK_ALERT_MAX_AGE_MIN are never sent (no backlog flood on deploy).
"""
from __future__ import annotations

import asyncio
import html
import json
import logging
import time

from sqlalchemy import func, select

from .. import config
from ..engine import telegram
from ..models import RiskEvent, Token, TokenVerdict, TrendScore
from ..providers import llm as llm_mod
from .contract_facts import is_mock

log = logging.getLogger("radar.risk_alerts")

ACTIONS = ("AVOID", "CAUTION", "MONITOR")
ACTION_EMOJI = {"AVOID": "🔴", "CAUTION": "🟠", "MONITOR": "🟡"}
CATEGORY_LABEL = {"liquidity": "Liquidity", "concentration": "Holder concentration",
                  "contract": "Contract", "deployer": "Deployer", "whale": "Whales",
                  "social": "Trading/social", "anomaly": "Anomaly"}

SYSTEM = (
    "You are the risk analyst of a Solana meme-coin research tool. You get REAL measured "
    "risk evidence for one token. Explain in plain English, for a retail trader, what "
    "the evidence most likely means and how serious it is. Use only the evidence given; "
    "never invent numbers, never predict price, never tell anyone to buy or sell. "
    'Reply with ONLY a JSON object: {"action": "AVOID" | "CAUTION" | "MONITOR", '
    '"take": "<max 2 short sentences>", "check": "<one thing to verify next, max 1 sentence>"}'
)


def _e(v) -> str:
    return html.escape(str(v or ""), quote=False)


def _latest_score(db, token_id: int) -> TrendScore | None:
    return db.execute(select(TrendScore).where(TrendScore.token_id == token_id)
                      .order_by(TrendScore.ts.desc()).limit(1)).scalars().first()


def _latest_verdict(db, token_id: int) -> TokenVerdict | None:
    return db.execute(select(TokenVerdict).where(TokenVerdict.token_id == token_id)
                      .order_by(TokenVerdict.id.desc()).limit(1)).scalars().first()


def ai_used_24h(db, now: int | None = None) -> int:
    """AI notes written in the last 24 h (one per alerted token batch)."""
    now = now or int(time.time())
    rows = db.execute(select(RiskEvent.token_id, RiskEvent.ts).where(
        RiskEvent.ai_note.is_not(None), RiskEvent.ts >= now - 86400)
        .group_by(RiskEvent.token_id, RiskEvent.ts)).all()
    return len(rows)


def build_evidence(token: Token, events: list[RiskEvent], score: TrendScore | None,
                   verdict: TokenVerdict | None) -> dict:
    comp = (score.components_json or {}) if score else {}
    s = comp.get("summary") or {}
    return {
        "token": {"symbol": token.symbol, "name": token.name, "dex": token.dex,
                  "age_hours": round((time.time() * 1000 - token.token_created_at) / 3.6e6, 1)
                  if token.token_created_at else None},
        "market": {"price_usd": s.get("price"), "mcap_usd": s.get("mcap"),
                   "liquidity_usd": s.get("liquidity"), "volume_24h_usd": s.get("volume_24h"),
                   "price_change_1h_pct": s.get("price_chg_1h")},
        "new_high_severity_events": [{"category": ev.category, "message": ev.message} for ev in events],
        "risk_engine": {"score": score.risk if score else None,
                        "band": score.risk_class if score else None,
                        "flags": (comp.get("risk_flags") or [])[:8],
                        "hard_fails": comp.get("risk_hard_fails") or []},
        "latest_verdict": {"rating": verdict.rating, "reason": verdict.reason} if verdict else None,
    }


async def ai_read(evidence: dict, llm) -> dict:
    raw = await llm.complete(SYSTEM, json.dumps(evidence, default=str), config.LLM_QUICK_MODEL,
                             max_tokens=700)   # room for reasoning models before the JSON
    out = llm_mod.extract_json(raw, want_key="action")
    action = str(out.get("action", "")).upper().strip()
    if action not in ACTIONS:
        action = "CAUTION"
    # a hard fail (no sell route, big round-trip loss, high fee) is never softened by the AI
    if evidence["risk_engine"]["hard_fails"]:
        action = "AVOID"
    take = str(out.get("take") or "").strip()[:400]
    check = str(out.get("check") or "").strip()[:200]
    if not take:
        raise llm_mod.LLMError("empty take")
    return {"action": action, "take": take, "check": check}


def format_message(token: Token, events: list[RiskEvent], score: TrendScore | None,
                   ai: dict | None, verdict: TokenVerdict | None) -> str:
    hard = bool(score and (score.components_json or {}).get("risk_hard_fails"))
    head = "🚨" if hard else "⚠️"
    risk = f" · Risk <b>{score.risk:.0f}</b>/100 ({_e(score.risk_class)})" if score and score.risk is not None else ""
    lines = [f"{head} <b>RISK ALERT · ${_e(token.symbol or '?')}</b>{risk}"]
    for ev in events:
        lines.append(f"• <b>{_e(CATEGORY_LABEL.get(ev.category, ev.category))}:</b> {_e(ev.message)}")
    s = ((score.components_json or {}).get("summary") or {}) if score else {}
    if s.get("liquidity") is not None or s.get("mcap") is not None:
        bits = []
        if s.get("liquidity") is not None:
            bits.append(f"Liq ${s['liquidity']:,.0f}")
        if s.get("mcap") is not None:
            bits.append(f"MCap ${s['mcap']:,.0f}")
        lines.append(" · ".join(bits))
    if ai:
        lines.append(f"\n🤖 <b>AI read: {ACTION_EMOJI.get(ai['action'], '')} {ai['action']}</b>")
        lines.append(_e(ai["take"]))
        if ai.get("check"):
            lines.append(f"🔎 Check: {_e(ai['check'])}")
    if verdict and verdict.rating not in (None, "REVIEW"):
        lines.append(f"Last FOMO verdict: {_e(verdict.rating)}")
    lines += ["⚠️ Research alert, not a buy or sell signal.",
              f"<code>{_e(token.address)}</code>",
              f'<a href="https://dexscreener.com/solana/{_e(token.address)}">DexScreener</a>']
    return "\n".join(lines)[:telegram.MAX_LEN]


async def run_risk_alerts(session_factory, llm=None, now: int | None = None) -> dict:
    res = {"tokens": 0, "sent": 0, "failed": 0, "ai": 0, "ai_errors": 0}
    if not config.RISK_ALERT_TELEGRAM or not telegram.is_configured():
        res["status"] = "disabled"
        return res
    now = now or int(time.time())
    with session_factory() as db:
        rows = db.execute(select(RiskEvent, Token).join(Token, Token.id == RiskEvent.token_id).where(
            RiskEvent.severity == "high",
            RiskEvent.telegram_sent.is_not(True),
            RiskEvent.ts >= now - int(config.RISK_ALERT_MAX_AGE_MIN * 60))
            .order_by(RiskEvent.ts.desc())).all()
        groups: dict[int, tuple[Token, list[RiskEvent]]] = {}
        for ev, tok in rows:
            if is_mock(tok):           # real tokens only — never alert on simulated data
                continue
            groups.setdefault(tok.id, (tok, []))[1].append(ev)
        ai_left = max(0, config.RISK_ALERT_AI_DAILY_MAX - ai_used_24h(db, now))
    res["tokens"] = len(groups)
    use_llm = llm is not None or (config.RISK_ALERT_AI and llm_mod.enabled())
    if use_llm and llm is None:
        llm = llm_mod.LLMClient(timeout=30.0)
    ai_budget = min(config.RISK_ALERT_AI_PER_TICK, ai_left) if use_llm else 0

    for tok_id, (tok, evs) in list(groups.items())[:config.TELEGRAM_MAX_PER_TICK]:
        with session_factory() as db:
            score, verdict = _latest_score(db, tok_id), _latest_verdict(db, tok_id)
            ai = None
            if ai_budget > 0:
                ai_budget -= 1
                try:
                    ai = await ai_read(build_evidence(tok, evs, score, verdict), llm)
                    res["ai"] += 1
                except Exception as exc:       # the alert still goes out without the AI line
                    res["ai_errors"] += 1
                    log.warning("risk alert AI failed for %s: %s", tok.symbol, str(exc)[:120])
            text = format_message(tok, evs, score, ai, verdict)
            r = await asyncio.to_thread(telegram.send_text, text)
            ids = [e.id for e in evs]
            for ev in db.execute(select(RiskEvent).where(RiskEvent.id.in_(ids))).scalars():
                if r.get("ok"):
                    ev.telegram_sent = True
                if ai:
                    ev.ai_note = f"{ai['action']}: {ai['take']}"[:600]
            db.commit()
            res["sent" if r.get("ok") else "failed"] += 1
    return res
