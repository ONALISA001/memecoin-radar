"""Alert condition evaluator + delivery channels (§16).

Conditions: {metric, op, value, scope, address?}
Metrics: trend_score | fomo_score | risk_score | mentions_growth_pct |
volume_growth_pct | holder_growth_pct | liquidity_drop_pct | new_narrative
Channels: browser (frontend polls /alerts/events + Notification API),
webhook (POST JSON — Discord/Slack-compatible). Telegram/Email are Phase-5
adapters against the same Channel interface.
"""
from __future__ import annotations

import json
import time

import httpx
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..config import ALERT_COOLDOWN_SECONDS
from ..models import Alert, AlertEvent

OPS = {
    "gt": lambda v, x: v > x,
    "gte": lambda v, x: v >= x,
    "lt": lambda v, x: v < x,
    "lte": lambda v, x: v <= x,
}


def metric_value(metric: str, payload: dict) -> float | None:
    d = payload["d"]
    return {
        "trend_score": payload["trend"],
        "fomo_score": payload["fomo"],
        "risk_score": payload["risk"],
        "mentions_growth_pct": d.get("mentions_g_1h", 0.0),
        "volume_growth_pct": d.get("volume_g_1h", 0.0),
        "holder_growth_pct": d.get("holders_g_1h", 0.0),
        "liquidity_drop_pct": max(0.0, -d.get("liq_chg_3h", 0.0)),
    }.get(metric)


def format_message(alert: Alert, symbol: str, payload: dict, value: float) -> dict:
    c = alert.condition_json or {}
    d = payload["d"]
    return {
        "title": "🚨 MEMECOIN RADAR",
        "alert": alert.name,
        "token": symbol,
        "address": payload["address"],
        "trigger": f"{c.get('metric')} {c.get('op')} {c.get('value')} (observed {value:.1f})",
        "trend": payload["trend"], "fomo": payload["fomo"], "risk": payload["risk"],
        "social_growth": f"{d.get('mentions_g_1h', 0):+.0f}%",
        "volume_growth": f"{d.get('volume_g_1h', 0):+.0f}%",
        "holder_growth": f"{d.get('holders_g_1h', 0):+.1f}%",
        "risk_note": (payload.get("risk_flags") or ["No major flags"])[0],
        "ts": int(time.time()),
    }


def deliver(channel: dict, message: dict) -> dict:
    ctype = (channel or {}).get("type", "browser")
    if ctype == "webhook" and channel.get("url"):
        try:
            text = (f"{message['title']}\n{message['alert']}\n${message['token']} — "
                    f"Trend {message['trend']:.0f}/100 · FOMO {message['fomo']:.0f}/100 · "
                    f"Risk {message['risk']:.0f}/100\nTrigger: {message['trigger']}\n"
                    f"Risk: {message['risk_note']}")
            httpx.post(channel["url"], json={"content": text}, timeout=5.0)
            return {"type": "webhook", "ok": True}
        except Exception as exc:  # never let delivery break the pipeline
            return {"type": "webhook", "ok": False, "error": str(exc)[:200]}
    return {"type": "browser", "ok": True}


def evaluate_alerts(db: Session, token_payloads: list[dict], now: int,
                    new_narratives: list[str]) -> int:
    alerts = db.execute(select(Alert).where(Alert.enabled == True)).scalars().all()  # noqa: E712
    created = 0
    for alert in alerts:
        c = alert.condition_json or {}
        metric, op, value = c.get("metric"), c.get("op", "gt"), float(c.get("value", 0))
        fn = OPS.get(op, OPS["gt"])

        # narrative-scope alerts
        if metric == "new_narrative":
            for name in new_narratives:
                last_ts = db.execute(
                    select(func.max(AlertEvent.ts)).where(AlertEvent.alert_id == alert.id)
                ).scalar() or 0
                if now - last_ts < ALERT_COOLDOWN_SECONDS:
                    continue
                msg = {"title": "🚨 MEMECOIN RADAR", "alert": alert.name,
                       "trigger": "new_narrative", "narrative": name, "ts": now}
                db.add(AlertEvent(alert_id=alert.id, token_id=None, ts=now,
                                  payload_json=msg, delivered_json=deliver(alert.channel_json or {}, msg)))
                created += 1
            continue

        for p in token_payloads:
            scope = c.get("scope", "all")
            if scope == "address" and c.get("address") and c["address"] != p["address"]:
                continue
            if scope == "watchlist" and not p.get("watched"):
                continue
            v = metric_value(metric, p)
            if v is None or not fn(v, value):
                continue
            last_ts = db.execute(
                select(func.max(AlertEvent.ts)).where(
                    AlertEvent.alert_id == alert.id, AlertEvent.token_id == p["token_id"])
            ).scalar() or 0
            if now - last_ts < ALERT_COOLDOWN_SECONDS:
                continue
            msg = format_message(alert, p["symbol"], p, v)
            result = deliver(alert.channel_json or {}, msg)
            db.add(AlertEvent(alert_id=alert.id, token_id=p["token_id"], ts=now,
                              payload_json=msg, delivered_json=result))
            created += 1
    return created


VALID_METRICS = {"trend_score", "fomo_score", "risk_score", "mentions_growth_pct",
                 "volume_growth_pct", "holder_growth_pct", "liquidity_drop_pct",
                 "new_narrative"}
