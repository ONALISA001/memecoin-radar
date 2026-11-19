"""Telegram delivery adapter (§16 Phase-5 channel).

Reads TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID from the environment only — the
token is never stored in the DB, returned by the API, or written to logs.
Sending never raises: failures come back as {"ok": False, "error": ...} so a
Telegram outage can't break the ingestion tick.
"""
from __future__ import annotations

import html
import logging
import time

import httpx

from .. import config

log = logging.getLogger("radar.telegram")

API = "https://api.telegram.org"
MAX_LEN = 4000  # Telegram hard limit is 4096


def is_configured() -> bool:
    return bool(config.TELEGRAM_BOT_TOKEN and config.TELEGRAM_CHAT_ID)


def _esc(v) -> str:
    return html.escape(str(v), quote=False)


def format_alert(message: dict) -> str:
    """Render the engine's alert message dict as Telegram HTML."""
    if message.get("trigger") == "new_narrative":
        return (f"🚨 <b>MEMECOIN RADAR</b>\n<b>{_esc(message.get('alert', 'Alert'))}</b>\n"
                f"New narrative: <b>{_esc(message.get('narrative', '?'))}</b>")
    addr = message.get("address") or ""
    lines = [
        "🚨 <b>MEMECOIN RADAR</b>",
        f"<b>{_esc(message.get('alert', 'Alert'))}</b>",
        f"<b>${_esc(message.get('token', '?'))}</b>",
        f"Trend <b>{message.get('trend', 0):.0f}</b> · FOMO <b>{message.get('fomo', 0):.0f}</b> · "
        f"Risk <b>{message.get('risk', 0):.0f}</b> /100",
        f"Social {_esc(message.get('social_growth', '-'))} · Vol {_esc(message.get('volume_growth', '-'))} · "
        f"Holders {_esc(message.get('holder_growth', '-'))}",
        f"Trigger: {_esc(message.get('trigger', ''))}",
        f"⚠️ {_esc(message.get('risk_note', ''))}",
    ]
    if addr:
        lines.append(f"<code>{_esc(addr)}</code>")
        lines.append(f'<a href="https://dexscreener.com/search?q={_esc(addr)}">DexScreener</a>')
    return "\n".join(lines)[:MAX_LEN]


def send_text(text: str, *, chat_id: str | None = None, timeout: float = 6.0) -> dict:
    """POST sendMessage. Retries once on HTTP 429 (honouring retry_after, max 5s)."""
    token = config.TELEGRAM_BOT_TOKEN
    chat = chat_id or config.TELEGRAM_CHAT_ID
    if not token or not chat:
        return {"type": "telegram", "ok": False, "error": "not configured"}
    url = f"{API}/bot{token}/sendMessage"
    payload = {"chat_id": chat, "text": text[:MAX_LEN], "parse_mode": "HTML",
               "disable_web_page_preview": True}
    for attempt in (1, 2):
        try:
            r = httpx.post(url, json=payload, timeout=timeout)
            if r.status_code == 429 and attempt == 1:
                wait = 1.0
                try:
                    wait = float(r.json().get("parameters", {}).get("retry_after", 1))
                except Exception:
                    pass
                time.sleep(min(wait, 5.0))
                continue
            if r.status_code == 200 and r.json().get("ok"):
                return {"type": "telegram", "ok": True}
            try:
                desc = r.json().get("description", "")
            except Exception:
                desc = ""
            return {"type": "telegram", "ok": False, "error": f"HTTP {r.status_code} {desc}"[:200]}
        except Exception as exc:
            # str(exc) can embed the request URL (which contains the token) — scrub it.
            err = str(exc).replace(token, "***")[:200]
            log.warning("telegram send failed: %s", err)
            return {"type": "telegram", "ok": False, "error": err}
    return {"type": "telegram", "ok": False, "error": "rate limited"}


def send_alert(message: dict) -> dict:
    return send_text(format_alert(message))
