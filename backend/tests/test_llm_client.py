"""LLM client: extra request body for reasoning models; errors never reach verdict text."""
import asyncio
import json

import httpx

from app import config
from app.providers import llm as L


def _capture():
    seen = {}

    def handler(req: httpx.Request) -> httpx.Response:
        seen["body"] = json.loads(req.content)
        return httpx.Response(200, json={"choices": [{"message": {"content": '{"rating": "WATCH"}'}}]})
    return seen, httpx.MockTransport(handler)


def test_extra_body_is_merged_into_openai_requests(monkeypatch):
    monkeypatch.setattr(config, "LLM_PROVIDER", "openai")
    monkeypatch.setattr(config, "LLM_EXTRA_BODY", {"chat_template_kwargs": {"thinking": False}})
    seen, transport = _capture()
    out = asyncio.run(L.LLMClient(transport=transport).complete("sys", "user", "m", 50))
    assert out == '{"rating": "WATCH"}'
    assert seen["body"]["chat_template_kwargs"] == {"thinking": False}
    assert seen["body"]["model"] == "m" and seen["body"]["max_tokens"] == 50


def test_extra_body_cannot_override_core_fields(monkeypatch):
    monkeypatch.setattr(config, "LLM_PROVIDER", "openai")
    monkeypatch.setattr(config, "LLM_EXTRA_BODY", {"model": "other", "max_tokens": 9999})
    seen, transport = _capture()
    asyncio.run(L.LLMClient(transport=transport).complete("sys", "user", "m", 50))
    assert seen["body"]["model"] == "m" and seen["body"]["max_tokens"] == 50


def test_llm_error_is_not_shown_in_verdict_reason(monkeypatch):
    from app.services import ai_verdict as AV

    class Boom:
        calls = 0

        async def complete(self, *a, **k):
            raise L.LLMError("HTTPStatusError: Client error '410 Gone' for url 'https://x'")

    tok = type("T", (), {"id": 1, "launch_alert": None})()
    item = {"token": tok, "evidence": {}, "hash": "h", "llm_only": False, "launch_expected": False,
            "score": None}
    stored = []
    monkeypatch.setattr(config, "VERDICT_ENABLED", True)
    monkeypatch.setattr(config, "VERDICT_TRACK_RECORD", False)
    monkeypatch.setattr(config, "VERDICT_TELEGRAM", False)
    monkeypatch.setattr(AV, "collect", lambda db: [item])
    monkeypatch.setattr(AV, "llm_used_24h", lambda db: 0)
    monkeypatch.setattr(AV.V, "missing_checks", lambda ev, le: [])
    monkeypatch.setattr(AV.V, "gate", lambda ev, le: None)
    monkeypatch.setattr(AV.V, "rules_verdict", lambda ev, missing: {
        "rating": "NEUTRAL", "source": "rules", "reason": "No major flags."})
    monkeypatch.setattr(AV, "_store", lambda db, it, out, model: stored.append(out))

    class DB:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def commit(self):
            pass

    res = asyncio.run(AV.run_verdicts(lambda: DB(), llm=Boom()))
    assert res["llm_errors"] == 1 and "410" in res["llm_error"]
    assert stored and "410" not in stored[0]["reason"] and "HTTP" not in stored[0]["reason"]
    assert "AI review unavailable" in stored[0]["reason"]
