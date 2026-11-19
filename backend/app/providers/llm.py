"""Minimal LLM client for the AI verdict (no SDK dependency).

Providers
  * anthropic — Messages API (LLM_API_KEY = an Anthropic key)
  * openai    — any OpenAI-compatible /chat/completions endpoint (OpenAI, OpenRouter,
                Groq, DeepSeek, Together...). Set LLM_BASE_URL and the model names.
The key stays server-side and is scrubbed from every error.
"""
from __future__ import annotations

import json
import re

import httpx

from .. import config

ANTHROPIC_URL = "https://api.anthropic.com/v1/messages"


class LLMError(Exception):
    pass


def enabled() -> bool:
    return bool(config.LLM_API_KEY and config.LLM_PROVIDER in ("anthropic", "openai")
                and config.LLM_QUICK_MODEL and config.LLM_JUDGE_MODEL)


def _scrub(msg: str) -> str:
    k = config.LLM_API_KEY
    return msg.replace(k, "***") if k else msg


class LLMClient:
    def __init__(self, transport: httpx.AsyncBaseTransport | None = None, timeout: float = 45.0) -> None:
        self.transport, self.timeout = transport, timeout
        self.calls = 0
        self.tokens_in = self.tokens_out = 0

    async def complete(self, system: str, user: str, model: str, max_tokens: int = 400) -> str:
        self.calls += 1
        try:
            async with httpx.AsyncClient(timeout=self.timeout, transport=self.transport) as c:
                if config.LLM_PROVIDER == "anthropic":
                    r = await c.post(ANTHROPIC_URL, headers={
                        "x-api-key": config.LLM_API_KEY, "anthropic-version": "2023-06-01",
                        "content-type": "application/json"},
                        json={"model": model, "max_tokens": max_tokens, "temperature": 0.2,
                              "system": system, "messages": [{"role": "user", "content": user}]})
                    r.raise_for_status()
                    body = r.json()
                    u = body.get("usage") or {}
                    self.tokens_in += int(u.get("input_tokens") or 0)
                    self.tokens_out += int(u.get("output_tokens") or 0)
                    return "".join(b.get("text", "") for b in body.get("content") or []
                                   if b.get("type") == "text").strip()
                base = (config.LLM_BASE_URL or "https://api.openai.com/v1").rstrip("/")
                r = await c.post(f"{base}/chat/completions",
                                 headers={"Authorization": f"Bearer {config.LLM_API_KEY}"},
                                 json={"model": model, "max_tokens": max_tokens, "temperature": 0.2,
                                       "messages": [{"role": "system", "content": system},
                                                    {"role": "user", "content": user}]})
                r.raise_for_status()
                body = r.json()
                u = body.get("usage") or {}
                self.tokens_in += int(u.get("prompt_tokens") or 0)
                self.tokens_out += int(u.get("completion_tokens") or 0)
                return (((body.get("choices") or [{}])[0].get("message") or {}).get("content") or "").strip()
        except Exception as exc:
            raise LLMError(_scrub(f"{type(exc).__name__}: {exc}")[:200]) from None


def extract_json(text: str) -> dict:
    """First JSON object in a model reply (tolerates ```json fences and chatter)."""
    m = re.search(r"\{.*\}", text or "", re.S)
    if not m:
        raise LLMError("no JSON object in reply")
    try:
        out = json.loads(m.group(0))
    except json.JSONDecodeError as exc:
        raise LLMError(f"bad JSON: {exc}") from None
    if not isinstance(out, dict):
        raise LLMError("JSON is not an object")
    return out
