"""Authenticated user API: visitor/owner login, watchlist (§15), alerts (§16)."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import func, select
from sqlalchemy.orm import Session

import time

from ..config import DEMO_MODE, OWNER_KEY
from pydantic import BaseModel, Field
from ..engine import telegram
from ..database import get_db
from ..models import Alert, AlertEvent, Token, TrendScore, User, Watchlist
from ..schemas import AlertCreate, AlertPatch, WatchlistAdd
from ..security import (ROLE_OWNER, check_owner_key, get_current_user, issue_token,
                        login_rate_limit, rate_limit, require_owner)

router = APIRouter(dependencies=[Depends(rate_limit)])


@router.post("/auth/demo")
def demo_login(db: Session = Depends(get_db)):
    """Visitor login: a READ-ONLY JWT for the seeded user (browse dashboards).
    Disabled when DEMO_MODE=0."""
    if not DEMO_MODE:
        raise HTTPException(403, "demo mode disabled")
    user = db.execute(select(User).where(User.email == "demo@memecoin.radar")).scalar_one_or_none()
    if not user:
        raise HTTPException(500, "demo user missing — run seed.py")
    return {"token": issue_token(user), "user": {"id": user.id, "email": user.email}}


class OwnerLogin(BaseModel):
    key: str = Field(min_length=1, max_length=256)


@router.post("/auth/login", dependencies=[Depends(login_rate_limit)])
def owner_login(body: OwnerLogin, db: Session = Depends(get_db)):
    """Owner login: the right OWNER_KEY gets a token that can change data."""
    if not OWNER_KEY:
        raise HTTPException(503, "owner login not configured (set OWNER_KEY on the server)")
    if not check_owner_key(body.key):
        raise HTTPException(401, "wrong key")
    user = db.execute(select(User).where(User.email == "demo@memecoin.radar")).scalar_one_or_none()
    if not user:
        raise HTTPException(500, "user missing — run seed.py")
    return {"token": issue_token(user, role=ROLE_OWNER), "role": ROLE_OWNER,
            "user": {"id": user.id, "email": user.email}}


# ------------------------------------------------------------- watchlist
@router.get("/watchlist")
def watchlist(db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    rows = db.execute(
        select(Watchlist, Token).join(Token, Token.id == Watchlist.token_id)
        .where(Watchlist.user_id == user.id)
    ).all()
    sub = (select(TrendScore.token_id, func.max(TrendScore.ts).label("mts"))
           .group_by(TrendScore.token_id).subquery())
    # intelligence context: latest verdict, 24h event flow, priority gates (same as /signals/priority)
    from ..services import market_views as MV
    from ..services.ai_verdict import latest_verdicts
    from .data_api import _event_flow, card
    verdicts = latest_verdicts(db, [t.id for _, t in rows])
    flow = _event_flow(db, [t.id for _, t in rows], 86400)
    items = []
    for w, t in rows:
        sc = db.execute(
            select(TrendScore)
            .join(sub, (TrendScore.token_id == sub.c.token_id) & (TrendScore.ts == sub.c.mts))
            .where(TrendScore.token_id == t.id)
        ).scalar_one_or_none()
        hist = db.execute(
            select(TrendScore.ts, TrendScore.trend, TrendScore.fomo, TrendScore.risk)
            .where(TrendScore.token_id == t.id).order_by(TrendScore.ts.desc()).limit(12)
        ).all()
        c = card(t, sc) if sc else {}
        v = verdicts.get(t.id)
        ev = flow.get(t.id, {"positive": 0, "danger": 0, "positive_importance": 0.0})
        checks = MV.priority_checks(c, v.rating if v else None, ev) if sc else []
        items.append({
            "address": t.address, "symbol": t.symbol, "name": t.name, "chain": t.chain,
            "image_url": t.image_url, "added_at": w.created_at,
            "intel": {k: c.get(k) for k in (
                "price", "price_change_h1", "price_change_h24", "liquidity", "volume_1h", "momentum",
                "momentum_band", "sellp", "sellp_band", "liq_health", "liq_band", "whale_class",
                "ath_status", "holder_growth", "buy_share", "confluence")} if sc else {},
            "verdict": None if v is None else {"rating": v.rating, "confidence": v.confidence,
                                               "source": v.source, "reason": v.reason,
                                               "created_at": v.created_at.isoformat() if v.created_at else None},
            "events_24h": {"up": ev["positive"], "down": ev["danger"]},
            "priority": {"passed": bool(checks) and all(k["passed"] for k in checks),
                         "score": MV.priority_score(c, v.rating if v else None, ev) if sc else None,
                         "failed": [k["label"] for k in checks if not k["passed"]]},
            "trend": sc.trend if sc else None, "fomo": sc.fomo if sc else None,
            "risk": sc.risk if sc else None,
            "fomo_class": sc.fomo_class if sc else None,
            "risk_class": sc.risk_class if sc else None,
            "summary": (sc.components_json or {}).get("summary", {}) if sc else {},
            # score trajectory, oldest → newest ("52 → 64 → 73 → 81", §15)
            "score_history": [{"ts": h.ts, "trend": h.trend, "fomo": h.fomo, "risk": h.risk}
                              for h in reversed(hist)],
        })
    return {"items": items}


@router.get("/signals/watchlist")
def watchlist_signals(hours: int = 24, min_importance: str = "MEDIUM",
                      db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    """Watchlist intelligence (v3 §30): recent events across the user's
    watched tokens, grouped per token with a headline state."""
    import time as _time
    from ..models import SignalEvent
    from .data_api import _IMPORTANCE_ORDER, _event_json
    now = int(_time.time())
    wrows = db.execute(
        select(Watchlist, Token).join(Token, Token.id == Watchlist.token_id)
        .where(Watchlist.user_id == user.id)).all()
    imp_min = _IMPORTANCE_ORDER.get(min_importance.upper(), 1)
    min_score = [35, 60, 85][imp_min - 1] if imp_min else 0
    tokens = []
    for w, t in wrows:
        evs = db.execute(
            select(SignalEvent).where(
                SignalEvent.token_id == t.id,
                SignalEvent.ts >= now - min(hours, 72) * 3600,
                SignalEvent.importance_score >= min_score)
            .order_by(SignalEvent.ts.desc()).limit(20)).scalars().all()
        best = max((e.importance_score or 0 for e in evs), default=0)
        danger = sum(1 for e in evs if e.severity == "danger")
        positive = sum(1 for e in evs if e.severity == "positive")
        headline = ("investigate — risk signals active" if danger >= 2 else
                    "distribution watch" if danger == 1 else
                    "momentum building" if positive >= 2 else
                    "quiet" if not evs else "mixed signals")
        tokens.append({"address": t.address, "symbol": t.symbol, "name": t.name,
                       "image_url": t.image_url, "added_at": w.created_at,
                       "events": [_event_json(e, t.symbol, t.address) for e in evs],
                       "event_count": len(evs), "max_importance": best,
                       "danger_count": danger, "positive_count": positive,
                       "headline": headline})
    tokens.sort(key=lambda x: (x["max_importance"], x["event_count"]), reverse=True)
    return {"items": tokens, "hours": hours, "min_importance": min_importance.upper(),
            "ts": now}


@router.post("/watchlist", dependencies=[Depends(require_owner)])
def watchlist_add(body: WatchlistAdd, db: Session = Depends(get_db),
                  user: User = Depends(get_current_user)):
    token = db.execute(select(Token).where(Token.address == body.address)).scalar_one_or_none()
    if not token:
        raise HTTPException(404, "token not tracked")
    exists = db.execute(select(Watchlist).where(
        Watchlist.user_id == user.id, Watchlist.token_id == token.id)).scalar_one_or_none()
    if exists:
        return {"ok": True, "already": True}
    db.add(Watchlist(user_id=user.id, token_id=token.id))
    db.commit()
    return {"ok": True}


@router.delete("/watchlist/{address}", dependencies=[Depends(require_owner)])
def watchlist_remove(address: str, db: Session = Depends(get_db),
                     user: User = Depends(get_current_user)):
    token = db.execute(select(Token).where(Token.address == address)).scalar_one_or_none()
    if not token:
        raise HTTPException(404, "token not tracked")
    db.execute(Watchlist.__table__.delete().where(
        Watchlist.user_id == user.id, Watchlist.token_id == token.id))
    db.commit()
    return {"ok": True}


# ------------------------------------------------------------- alerts
def _alert_out(a: Alert) -> dict:
    return {"id": a.id, "name": a.name, "condition": a.condition_json,
            "channel": {k: v for k, v in (a.channel_json or {}).items() if k != "url"},
            "has_webhook": bool((a.channel_json or {}).get("url")),
            "enabled": bool(a.enabled), "created_at": a.created_at}


@router.get("/alerts")
def alerts_list(db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    rows = db.execute(select(Alert).where(Alert.user_id == user.id)
                      .order_by(Alert.created_at.desc())).scalars().all()
    return {"items": [_alert_out(a) for a in rows],
            "metrics": ["trend_score", "fomo_score", "risk_score", "mentions_growth_pct",
                        "volume_growth_pct", "holder_growth_pct", "liquidity_drop_pct",
                        "new_narrative"]}


@router.post("/alerts", dependencies=[Depends(require_owner)])
def alerts_create(body: AlertCreate, db: Session = Depends(get_db),
                  user: User = Depends(get_current_user)):
    count = db.execute(select(func.count(Alert.id)).where(Alert.user_id == user.id)).scalar() or 0
    if count >= 50:
        raise HTTPException(400, "alert limit reached (50)")
    condition = {"metric": body.metric, "op": body.op, "value": body.value,
                 "scope": body.scope}
    if body.scope == "address":
        if not body.address:
            raise HTTPException(400, "address required for scope=address")
        condition["address"] = body.address
    channel = {"type": body.channel_type}
    if body.channel_type == "webhook":
        if not body.webhook_url:
            raise HTTPException(400, "webhook_url required")
        channel["url"] = body.webhook_url
    if body.channel_type == "telegram" and not telegram.is_configured():
        raise HTTPException(400, "telegram is not configured on the server "
                                 "(set TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID)")
    a = Alert(user_id=user.id, name=body.name, condition_json=condition,
              channel_json=channel, enabled=True)
    db.add(a)
    db.commit()
    return _alert_out(a)


@router.patch("/alerts/{alert_id}", dependencies=[Depends(require_owner)])
def alerts_patch(alert_id: int, body: AlertPatch, db: Session = Depends(get_db),
                 user: User = Depends(get_current_user)):
    a = db.get(Alert, alert_id)
    if not a or a.user_id != user.id:
        raise HTTPException(404, "alert not found")
    if body.enabled is not None:
        a.enabled = body.enabled
    if body.name:
        a.name = body.name
    db.commit()
    return _alert_out(a)


@router.delete("/alerts/{alert_id}", dependencies=[Depends(require_owner)])
def alerts_delete(alert_id: int, db: Session = Depends(get_db),
                  user: User = Depends(get_current_user)):
    a = db.get(Alert, alert_id)
    if not a or a.user_id != user.id:
        raise HTTPException(404, "alert not found")
    db.delete(a)
    db.commit()
    return {"ok": True}


_tg_last_test = 0.0


@router.get("/alerts/telegram/status")
def telegram_status(user: User = Depends(get_current_user)):
    """Whether the server has Telegram credentials (never returns them)."""
    return {"configured": telegram.is_configured()}


@router.post("/alerts/telegram/test", dependencies=[Depends(require_owner)])
def telegram_test(user: User = Depends(get_current_user)):
    """Send a test message to the configured chat so the setup can be verified."""
    if not telegram.is_configured():
        raise HTTPException(400, "telegram is not configured on the server "
                                 "(set TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID)")
    global _tg_last_test
    now = time.time()
    if now - _tg_last_test < 30:   # the demo login is public: don't let it spam the chat
        raise HTTPException(429, "wait 30s between telegram tests")
    _tg_last_test = now
    res = telegram.send_text("✅ <b>MemeCoin Radar</b> — Telegram alerts connected.")
    if not res.get("ok"):
        raise HTTPException(502, f"telegram send failed: {res.get('error')}")
    return {"ok": True}


@router.get("/alerts/events")
def alert_events(since: int = 0, db: Session = Depends(get_db),
                 user: User = Depends(get_current_user)):
    """Events for the notification bell (browser channel polls this)."""
    rows = db.execute(
        select(AlertEvent, Token.symbol)
        .join(Alert, Alert.id == AlertEvent.alert_id)
        .outerjoin(Token, Token.id == AlertEvent.token_id)
        .where(Alert.user_id == user.id, AlertEvent.ts > since)
        .order_by(AlertEvent.ts.desc()).limit(50)
    ).all()
    unread = db.execute(
        select(func.count(AlertEvent.id)).join(Alert, Alert.id == AlertEvent.alert_id)
        .where(Alert.user_id == user.id, AlertEvent.read == False)  # noqa: E712
    ).scalar() or 0
    return {"items": [{"id": e.id, "ts": e.ts, "payload": e.payload_json,
                       "symbol": sym, "delivered": e.delivered_json, "read": bool(e.read)}
                      for e, sym in rows], "unread": unread}


@router.post("/admin/backtest/run", dependencies=[Depends(require_owner)])
def admin_backtest_run(user: User = Depends(get_current_user)):
    """Manually (re)run retro score backfill + outcome labeling."""
    from ..services.backfill_scores import run_retro_backfill
    return run_retro_backfill()


@router.post("/alerts/events/read-all", dependencies=[Depends(require_owner)])
def alert_events_read(db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    rows = db.execute(
        select(AlertEvent).join(Alert, Alert.id == AlertEvent.alert_id)
        .where(Alert.user_id == user.id, AlertEvent.read == False)  # noqa: E712
    ).scalars().all()
    for e in rows:
        e.read = True
    db.commit()
    return {"ok": True, "marked": len(rows)}
