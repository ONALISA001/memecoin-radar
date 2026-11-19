"""Telegram alert adapter tests (no network: httpx.post is stubbed).
Run from backend/:  python -m pytest tests/test_telegram.py -q
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import httpx
import pytest

from app import config
from app.engine import alerts, telegram

MSG = {"title": "🚨 MEMECOIN RADAR", "alert": "Strong <trend> & more", "token": "PEPE",
       "address": "So1anaAddr123", "trigger": "trend_score gt 75 (observed 81.0)",
       "trend": 81.0, "fomo": 60.0, "risk": 22.0, "social_growth": "+120%",
       "volume_growth": "+90%", "holder_growth": "+3.1%", "risk_note": "No major flags"}


class FakeResp:
    def __init__(self, status=200, body=None):
        self.status_code, self._body = status, body if body is not None else {"ok": True}
    def json(self): return self._body


@pytest.fixture
def configured(monkeypatch):
    monkeypatch.setattr(config, "TELEGRAM_BOT_TOKEN", "123:SECRET")
    monkeypatch.setattr(config, "TELEGRAM_CHAT_ID", "-1003788114535")


def test_not_configured(monkeypatch):
    monkeypatch.setattr(config, "TELEGRAM_BOT_TOKEN", "")
    monkeypatch.setattr(config, "TELEGRAM_CHAT_ID", "")
    assert not telegram.is_configured()
    assert telegram.send_text("x") == {"type": "telegram", "ok": False, "error": "not configured"}


def test_format_escapes_html():
    out = telegram.format_alert(MSG)
    assert "&lt;trend&gt; &amp; more" in out
    assert "<b>$PEPE</b>" in out and "<code>So1anaAddr123</code>" in out


def test_send_ok_payload(configured, monkeypatch):
    seen = {}
    def fake_post(url, json=None, timeout=None):
        seen.update(url=url, json=json); return FakeResp()
    monkeypatch.setattr(httpx, "post", fake_post)
    assert telegram.send_alert(MSG)["ok"] is True
    assert seen["url"].endswith("/bot123:SECRET/sendMessage")
    assert seen["json"]["chat_id"] == "-1003788114535" and seen["json"]["parse_mode"] == "HTML"


def test_send_api_error(configured, monkeypatch):
    monkeypatch.setattr(httpx, "post", lambda *a, **k: FakeResp(400, {"ok": False, "description": "chat not found"}))
    r = telegram.send_text("x")
    assert r["ok"] is False and "chat not found" in r["error"]


def test_token_never_leaks_in_errors(configured, monkeypatch):
    def boom(url, **k): raise httpx.ConnectError(f"failed {url}")
    monkeypatch.setattr(httpx, "post", boom)
    r = telegram.send_text("x")
    assert r["ok"] is False and "SECRET" not in r["error"]


def test_retry_on_429(configured, monkeypatch):
    calls = []
    def fake_post(url, **k):
        calls.append(1)
        return FakeResp(429, {"parameters": {"retry_after": 0}}) if len(calls) == 1 else FakeResp()
    monkeypatch.setattr(httpx, "post", fake_post)
    monkeypatch.setattr(telegram.time, "sleep", lambda s: None)
    assert telegram.send_text("x")["ok"] is True and len(calls) == 2


def test_deliver_budget_caps_messages(configured, monkeypatch):
    monkeypatch.setattr(httpx, "post", lambda *a, **k: FakeResp())
    budget = {"telegram": 2}
    results = [alerts.deliver({"type": "telegram"}, MSG, budget) for _ in range(4)]
    assert [r["ok"] for r in results] == [True, True, False, False]
    assert results[2]["error"] == "tick limit reached"


def test_evaluate_alerts_end_to_end(configured, monkeypatch):
    """A matching rule fires one Telegram message, then the cooldown suppresses repeats."""
    from sqlalchemy import create_engine, select
    from sqlalchemy.orm import sessionmaker
    from app.database import Base
    from app.models import Alert, AlertEvent, Token, User

    eng = create_engine("sqlite://")
    Base.metadata.create_all(eng)
    sent = []
    monkeypatch.setattr(httpx, "post", lambda url, json=None, timeout=None: (sent.append(json), FakeResp())[1])
    with sessionmaker(bind=eng)() as db:
        u = User(email="t@t.io", password_hash="x")
        db.add(u); tok = Token(address="So1anaAddr123", symbol="PEPE", chain="solana"); db.add(tok); db.flush()
        db.add(Alert(user_id=u.id, name="hot", condition_json={"metric": "trend_score", "op": "gt", "value": 70, "scope": "all"},
                     channel_json={"type": "telegram"}, enabled=True))
        db.flush()
        payload = {"token_id": tok.id, "address": tok.address, "symbol": "PEPE", "trend": 81, "fomo": 60, "risk": 20,
                   "risk_flags": [], "d": {}, "mi": {}, "watched": False}
        assert alerts.evaluate_alerts(db, [payload], 10_000, []) == 1
        assert len(sent) == 1 and "PEPE" in sent[0]["text"]
        assert alerts.evaluate_alerts(db, [payload], 10_100, []) == 0      # cooldown
        assert len(sent) == 1
        ev = db.execute(select(AlertEvent)).scalars().all()
        assert len(ev) == 1 and ev[0].delivered_json == {"type": "telegram", "ok": True}
