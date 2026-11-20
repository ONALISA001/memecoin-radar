"""Risk alerts → Telegram with AI read (no network: LLM and Telegram are stubbed).
Run from backend/:  python -m pytest tests/test_risk_alerts.py -q
"""
import asyncio
import time

import pytest

from app import config
from app.database import Base, SessionLocal, engine
from app.engine import telegram
from app.models import RiskEvent, Token, TrendScore
from app.providers import llm as L
from app.services import risk_alerts as RA

Base.metadata.create_all(engine)
NOW = int(time.time())


class FakeLLM:
    def __init__(self, reply=None, fail=False):
        self.reply, self.fail, self.calls, self.seen = reply, fail, 0, []

    async def complete(self, system, user, model, max_tokens=400):
        self.calls += 1
        self.seen.append(user)
        if self.fail:
            raise L.LLMError("boom")
        return self.reply or ('<think>hmm</think>{"action": "MONITOR", "take": "Liquidity was '
                              'pulled fast, typical of a rug.", "check": "Watch the LP wallet."}')


@pytest.fixture
def env(monkeypatch):
    monkeypatch.setattr(config, "TELEGRAM_BOT_TOKEN", "123:SECRET")
    monkeypatch.setattr(config, "TELEGRAM_CHAT_ID", "-1003788114535")
    monkeypatch.setattr(config, "RISK_ALERT_TELEGRAM", True)
    monkeypatch.setattr(config, "RISK_ALERT_AI_PER_TICK", 3)
    monkeypatch.setattr(config, "RISK_ALERT_AI_DAILY_MAX", 40)
    sent = []
    monkeypatch.setattr(telegram, "send_text", lambda text, **k: sent.append(text) or {"ok": True})
    with SessionLocal() as db:
        for m in (RiskEvent, TrendScore, Token):
            db.query(m).delete()
        db.commit()
    return sent


def add_token(db, addr, symbol, hard_fails=()):
    t = Token(address=addr, symbol=symbol, chain="solana")
    db.add(t); db.flush()
    db.add(TrendScore(token_id=t.id, ts=NOW, trend=50, fomo=40, risk=88 if hard_fails else 72,
                      risk_class="HIGH", components_json={
                          "summary": {"price": 0.001, "mcap": 400_000, "liquidity": 9_000},
                          "risk_flags": ["Liquidity declined -60% in 3h"],
                          "risk_hard_fails": list(hard_fails)}))
    return t


def ev(db, t, sev="high", cat="liquidity", msg="Liquidity dropped -60% over 3 hours.", ts=NOW):
    db.add(RiskEvent(token_id=t.id, ts=ts, category=cat, severity=sev, message=msg))


def test_high_events_sent_once_with_ai(env):
    with SessionLocal() as db:
        t = add_token(db, "Real111", "RUG")
        ev(db, t); ev(db, t, cat="concentration", msg="Top-10 wallets control ≈70% of supply.")
        ev(db, t, sev="warn", cat="contract", msg="Mint authority reported enabled.")
        db.commit()
    llm = FakeLLM()
    res = asyncio.run(RA.run_risk_alerts(SessionLocal, llm=llm, now=NOW))
    assert res["sent"] == 1 and res["ai"] == 1 and llm.calls == 1
    msg = env[0]
    assert "RISK ALERT · $RUG" in msg and "Top-10 wallets" in msg and "Liquidity dropped" in msg
    assert "Mint authority" not in msg                       # warn-level is dashboard-only
    assert "AI read" in msg and "MONITOR" in msg and "Watch the LP wallet" in msg
    assert "<think>" not in msg and "not a buy or sell signal" in msg
    # second run: nothing new
    res = asyncio.run(RA.run_risk_alerts(SessionLocal, llm=llm, now=NOW))
    assert res["sent"] == 0 and len(env) == 1
    with SessionLocal() as db:
        highs = db.query(RiskEvent).filter(RiskEvent.severity == "high").all()
        assert all(e.telegram_sent for e in highs) and all(e.ai_note.startswith("MONITOR") for e in highs)


def test_hard_fail_forces_avoid(env):
    with SessionLocal() as db:
        t = add_token(db, "Real222", "HONEY", hard_fails=["No sell route"])
        ev(db, t, msg="Jupiter routes a buy but finds no way to sell back (honeypot-like).")
        db.commit()
    asyncio.run(RA.run_risk_alerts(SessionLocal, llm=FakeLLM(), now=NOW))
    assert "🚨" in env[0] and "AVOID" in env[0] and "MONITOR" not in env[0]


def test_ai_failure_still_sends(env):
    with SessionLocal() as db:
        ev(db, add_token(db, "Real333", "X")); db.commit()
    res = asyncio.run(RA.run_risk_alerts(SessionLocal, llm=FakeLLM(fail=True), now=NOW))
    assert res["sent"] == 1 and res["ai_errors"] == 1 and "AI read" not in env[0]


def test_ai_budget_and_mock_and_stale(env, monkeypatch):
    monkeypatch.setattr(config, "RISK_ALERT_AI_PER_TICK", 1)
    with SessionLocal() as db:
        ev(db, add_token(db, "RealA", "A")); ev(db, add_token(db, "RealB", "B"))
        ev(db, add_token(db, "MockZZZ", "SIM"))                       # simulated token
        ev(db, add_token(db, "RealOld", "OLD"), ts=NOW - 3 * 3600)     # stale event
        db.commit()
    llm = FakeLLM()
    res = asyncio.run(RA.run_risk_alerts(SessionLocal, llm=llm, now=NOW))
    assert res["sent"] == 2 and llm.calls == 1
    assert sum("AI read" in m for m in env) == 1
    assert not any("$SIM" in m or "$OLD" in m for m in env)


def test_disabled_without_telegram(env, monkeypatch):
    monkeypatch.setattr(config, "TELEGRAM_BOT_TOKEN", "")
    assert asyncio.run(RA.run_risk_alerts(SessionLocal, llm=FakeLLM()))["status"] == "disabled"


def test_html_escaped(env):
    with SessionLocal() as db:
        ev(db, add_token(db, "Real444", "<b>"), msg="a & b <x>"); db.commit()
    asyncio.run(RA.run_risk_alerts(SessionLocal, llm=FakeLLM(), now=NOW))
    assert "$&lt;b&gt;" in env[0] and "a &amp; b &lt;x&gt;" in env[0]
