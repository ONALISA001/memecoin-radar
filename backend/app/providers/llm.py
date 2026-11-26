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
                    return strip_reasoning("".join(b.get("text", "") for b in body.get("content") or []
                                                   if b.get("type") == "text"))
                base = (config.LLM_BASE_URL or "https://api.openai.com/v1").rstrip("/")
                r = await c.post(f"{base}/chat/completions",
                                 headers={"Authorization": f"Bearer {config.LLM_API_KEY}"},
                                 json={**config.LLM_EXTRA_BODY,
                                       "model": model, "max_tokens": max_tokens, "temperature": 0.2,
                                       "messages": [{"role": "system", "content": system},
                                                    {"role": "user", "content": user}]})
                r.raise_for_status()
                body = r.json()
                u = body.get("usage") or {}
                self.tokens_in += int(u.get("prompt_tokens") or 0)
                self.tokens_out += int(u.get("completion_tokens") or 0)
                # reasoning models on OpenAI-compatible hosts may also send `reasoning_content`
                # separately; only `content` is the answer
                return strip_reasoning(((body.get("choices") or [{}])[0].get("message") or {}).get("content") or "")
        except Exception as exc:
            raise LLMError(_scrub(f"{type(exc).__name__}: {exc}")[:200]) from None


THINK_RE = re.compile(r"<(think|thinking|reasoning)>.*?</\1>", re.S | re.I)


def strip_reasoning(text: str) -> str:
    """Drop the visible reasoning that 'thinking' models (DeepSeek-R1, Qwen3, Nemotron
    reasoning...) put before their answer, so only the answer is used."""
    text = THINK_RE.sub("", text or "")
    for tag in ("</think>", "</thinking>", "</reasoning>"):   # opening tag cut off / prefilled
        if tag in text.lower():
            text = text[text.lower().rindex(tag) + len(tag):]
    return text.strip()


def _json_objects(text: str):
    """Every balanced {...} block, in order (string-aware)."""
    depth, start, in_str, esc = 0, None, False, False
    for i, ch in enumerate(text):
        if in_str:
            if esc:
                esc = False
            elif ch == "\\":
                esc = True
            elif ch == '"':
                in_str = False
            continue
        if ch == '"' and depth:
            in_str = True
        elif ch == "{":
            if depth == 0:
                start = i
            depth += 1
        elif ch == "}" and depth:
            depth -= 1
            if depth == 0:
                yield text[start:i + 1]


def extract_json(text: str, want_key: str | None = "rating") -> dict:
    """The answer's JSON object. Tolerates ```json fences, chatter and reasoning text:
    prefers the LAST object that parses and has `want_key`, else the last that parses."""
    text = strip_reasoning(text)
    parsed = []
    for chunk in _json_objects(text):
        try:
            obj = json.loads(chunk)
        except json.JSONDecodeError:
            continue
        if isinstance(obj, dict):
            parsed.append(obj)
    if not parsed:
        raise LLMError("no JSON object in reply")
    keyed = [o for o in parsed if want_key and want_key in o]
    return (keyed or parsed)[-1]
