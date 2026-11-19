"""Public read API (§28–§30). All queries go through SQLAlchemy with
allowlisted sort keys — no raw SQL from user input, ever."""
from __future__ import annotations

import time
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import and_, func, select
from sqlalchemy.orm import Session

from ..database import get_db
from ..engine import fomo as fomo_engine
from ..engine import risk as risk_engine
from ..engine import scoring as scoring_engine
from ..engine import signals as signals_engine
from ..engine.ai_analysis import build_analysis, build_why_trending
from ..engine.context import TokenContext
from ..models import (HolderSnapshot, LiquiditySnapshot, Narrative, NewsArticle,
                      PriceSnapshot, RiskEvent, SocialPost, SocialSnapshot, Token,
                      TokenNarrative, TrendScore, VolumeSnapshot, WalletActivity)
from ..services.contract_facts import contract_facts_for
from ..config import NEWS_RSS_ENABLED, TELEGRAM_CALL_CHANNELS, USE_SIMULATED_SOCIAL
from ..security import ALLOWED_SORTS, rate_limit, valid_address

router = APIRouter(dependencies=[Depends(rate_limit)])

TF_SECONDS = {"5m": 300, "15m": 900, "30m": 1800, "1h": 3600, "4h": 14400,
              "12h": 43200, "24h": 86400, "7d": 604800}


# ---------------------------------------------------------------- helpers
def latest_score_rows(db: Session):
    sub = (select(TrendScore.token_id, func.max(TrendScore.ts).label("mts"))
           .group_by(TrendScore.token_id).subquery())
    rows = db.execute(
        select(TrendScore, Token)
        .join(Token, Token.id == TrendScore.token_id)
        .join(sub, and_(TrendScore.token_id == sub.c.token_id,
                        TrendScore.ts == sub.c.mts))
    ).all()
    # Only tokens scored in the latest rounds are "tracked"; tokens that were dropped or
    # filtered out (stablecoins, mega-caps...) simply stop being refreshed and disappear.
    if rows:
        newest = max(sc.ts for sc, _ in rows)
        rows = [r for r in rows if r[0].ts >= newest - 900]
    return rows


# Which card fields are real and which are still simulated (shown in the UI).
DATA_QUALITY = {
    "real": ["price", "mcap", "liquidity", "volume_1h", "volume_24h", "price_change_h1",
             "price_change_h24", "buys_1h", "sells_1h", "txns_1h", "txns_24h", "buy_share",
             "holders", "holder_growth", "holders_g_24h"],
    "simulated": ["mentions_1h", "social_growth",
                  "whale_class", "narratives", "organic", "confirmed", "fomo", "fomo_class",
                  "trend", "confidence", "confluence", "emerging"],
}


def card(token: Token, sc: TrendScore) -> dict:
    c = sc.components_json or {}
    s = c.get("summary", {})
    return {
        "address": token.address, "chain": token.chain, "name": token.name,
        "symbol": token.symbol, "image_url": token.image_url, "dex": token.dex,
        "url": token.url,
        "discovery_source": token.discovery_source,     # new_pool | boost | profile | None
        "launch_alert": token.launch_alert,             # sent = passed launch safety checks
        "trend": sc.trend, "fomo": sc.fomo, "fomo_class": sc.fomo_class,
        "risk": sc.risk, "risk_class": sc.risk_class, "confidence": sc.confidence,
        "emerging": bool(sc.emerging),
        "anomalies": s.get("anomalies", []),
        "narratives": s.get("narratives", []),
        "price": s.get("price"), "mcap": s.get("mcap"), "fdv": s.get("fdv"),
        "liquidity": s.get("liquidity"),
        "volume_24h": s.get("volume_24h"), "volume_1h": s.get("volume_1h"),
        "price_change_h1": s.get("price_change_h1"),
        "price_change_h24": s.get("price_change_h24"),
        "social_growth": s.get("social_growth"), "holder_growth": s.get("holder_growth"),
        "holders": s.get("holders"), "holders_capped": bool(s.get("holders_capped")),
        "mentions_1h": s.get("mentions_1h"),
        "buy_share": s.get("buy_share"),
        "confirmed": s.get("confirmed", 0), "conflicts": s.get("conflicts", 0),
        "age_hours": s.get("age_hours"), "ts": sc.ts,
        "organic": s.get("organic"), "regime": s.get("regime"),
        # v2 market-intelligence headline fields
        "momentum": s.get("momentum"), "momentum_band": s.get("momentum_band"),
        "sellp": s.get("sellp"), "sellp_band": s.get("sellp_band"),
        "liq_health": s.get("liq_health"), "liq_band": s.get("liq_band"),
        "ath_status": s.get("ath_status"), "ath_distance": s.get("ath_distance"),
        "vol_accel_h1": s.get("vol_accel_h1"), "whale_class": s.get("whale_class"),
        "confluence": s.get("confluence"), "reversals": s.get("reversals", 0),
        "price_change_m5": s.get("price_chg_5m"),
        "price_change_m15": s.get("price_chg_15m"),
        "price_change_h4": s.get("price_chg_4h"),
        "sells_1h": s.get("sells_1h"), "buys_1h": s.get("buys_1h"),
        "txns_1h": s.get("txns_1h"), "txns_24h": s.get("txns_24h"),
        "liq_chg_24h": s.get("liq_chg_24h"), "holders_g_24h": s.get("holders_g_24h"),
        "data_quality": DATA_QUALITY,
    }


SORT_KEYS = {
    "trend": lambda c: c["trend"], "fomo": lambda c: c["fomo"], "risk": lambda c: c["risk"],
    "volume": lambda c: c["volume_1h"] or 0, "liquidity": lambda c: c["liquidity"] or 0,
    "mcap": lambda c: c["mcap"] or 0, "price_change": lambda c: c["price_change_h1"] or 0,
    "social_growth": lambda c: c["social_growth"] or 0,
    "holder_growth": lambda c: c["holder_growth"] or 0,
    "age": lambda c: -(c["age_hours"] or 9999),
}


def get_token_or_404(db: Session, address: str) -> Token:
    token = db.execute(select(Token).where(Token.address == address)).scalar_one_or_none()
    if not token:
        raise HTTPException(404, "token not found")
    return token


# ---------------------------------------------------------------- market
@router.get("/market/overview")
def market_overview(db: Session = Depends(get_db)):
    rows = latest_score_rows(db)
    cards = [card(t, sc) for sc, t in rows]
    if not cards:
        return {"tracked": 0}
    trends = sorted(c["trend"] for c in cards)
    breadth = sum(1 for c in cards if (c["price_change_h24"] or 0) > 0) / len(cards)
    top10 = sorted((c["trend"] for c in cards), reverse=True)[:10]
    avg_top = sum(top10) / max(len(top10), 1)
    momentum = max(0.0, min(100.0, 45 + (breadth - 0.5) * 60 + (avg_top - 45) * 0.4))
    narrs = db.execute(select(Narrative).order_by(Narrative.score.desc()).limit(5)).scalars().all()
    return {
        "tracked": len(cards),
        "avg_trend": round(sum(trends) / len(trends), 1),
        "median_trend": trends[len(trends) // 2],
        "breadth": round(breadth, 3),
        "sector_momentum": round(momentum, 1),
        "phase": "RISK-ON" if momentum >= 60 else "NEUTRAL" if momentum >= 45 else "RISK-OFF",
        "emerging": sum(1 for c in cards if c["emerging"]),
        "extreme_fomo": sum(1 for c in cards if c["fomo"] >= 81),
        "high_risk": sum(1 for c in cards if c["risk"] >= 70),
        "anomalies": sum(1 for c in cards if c["anomalies"]),
        "total_volume_24h": round(sum(c["volume_24h"] or 0 for c in cards), 0),
        "total_liquidity": round(sum(c["liquidity"] or 0 for c in cards), 0),
        "top_narratives": [{"id": n.id, "name": n.name, "score": n.score,
                            "mentions_growth": n.mentions_growth,
                            "token_count": n.token_count,
                            "cross_platform": n.cross_platform} for n in narrs],
        "updated_at": max(c["ts"] for c in cards) if cards else None,
        "mock_notice": "Non-market data is simulated (mock providers). See /sources.",
    }


@router.get("/sources")
def sources(db: Session = Depends(get_db)):
    from ..models import DataSource
    rows = db.execute(select(DataSource)).scalars().all()
    return {"sources": [{"name": r.name, "kind": r.kind, "status": r.status,
                         "meta": r.meta_json, "last_ok_at": r.last_ok_at} for r in rows],
            "note": "mock = simulated data (clearly isolated, §30/§39)"}


# ---------------------------------------------------------------- tokens
@router.get("/tokens/trending")
def trending(
    db: Session = Depends(get_db),
    chain: Optional[str] = None,
    min_liquidity: float = 0, max_risk: float = 100, min_trend: float = 0,
    min_fomo: float = 0, min_mcap: float = 0, max_mcap: float = 1e15,
    narrative: Optional[str] = None,
    sort: str = "trend", limit: int = Query(40, le=100),
):
    if sort not in ALLOWED_SORTS:
        raise HTTPException(400, f"sort must be one of {sorted(ALLOWED_SORTS)}")
    cards = [card(t, sc) for sc, t in latest_score_rows(db)]
    if chain:
        cards = [c for c in cards if c["chain"] == chain]
    cards = [c for c in cards if (c["liquidity"] or 0) >= min_liquidity
             and c["risk"] <= max_risk and c["trend"] >= min_trend and c["fomo"] >= min_fomo
             and (c["mcap"] or 0) >= min_mcap and (c["mcap"] or 0) <= max_mcap]
    if narrative:
        cards = [c for c in cards if narrative.lower() in [n.lower() for n in c["narratives"]]]
    cards.sort(key=SORT_KEYS[sort], reverse=True)
    return {"items": cards[:limit], "count": len(cards)}


@router.get("/tokens/emerging")
def emerging(db: Session = Depends(get_db), limit: int = Query(30, le=100)):
    cards = [card(t, sc) for sc, t in latest_score_rows(db)]
    cards = [c for c in cards if c["emerging"]]
    cards.sort(key=lambda c: (c["social_growth"] or 0), reverse=True)
    return {"items": cards[:limit], "count": len(cards),
            "definition": "trend≥45 · FOMO 40–80 · social & volume growth ≥+80% · "
                          "holders growing · momentum strengthening (§10)"}


# NOTE: declared before /tokens/{address} so they don't get swallowed by it.

NEW_SORTS = {
    "newest": lambda c: -(c["age_hours"] or 9e9),
    "momentum": lambda c: c["momentum"] or 0,
    "volume": lambda c: c["volume_24h"] or 0,
    "liquidity": lambda c: c["liquidity"] or 0,
    "holder_growth": lambda c: c["holder_growth"] or 0,
    "buy_pressure": lambda c: c["buy_share"] or 0,
    "price_increase": lambda c: c["price_change_h24"] or 0,
    "vol_accel": lambda c: c["vol_accel_h1"] or 0,
    "low_risk": lambda c: -(c["risk"] or 0),
    "activity": lambda c: c["txns_24h"] or 0,
    "discussed": lambda c: c["mentions_1h"] or 0,
}


@router.get("/tokens/new")
def tokens_new(sort: str = "newest", limit: int = Query(40, le=100),
               max_age_h: float | None = None, source: str | None = None,
               db: Session = Depends(get_db)):
    """New memecoin discovery (v2 §3): recently launched/tracked tokens with
    full momentum + sell-pressure + risk context, sortable by any dimension.
    source=new_pool → found as a fresh pool; source=promoted → boosts/profiles."""
    cards = [card(t, sc) for sc, t in latest_score_rows(db)]
    if source == "new_pool":
        cards = [c for c in cards if c["discovery_source"] == "new_pool"]
    elif source == "telegram":
        cards = [c for c in cards if c["discovery_source"] == "telegram"]
    elif source == "promoted":
        cards = [c for c in cards if c["discovery_source"] in ("boost", "profile")]
    if max_age_h is not None:
        cards = [c for c in cards if (c["age_hours"] or 9e9) <= max_age_h]
    key = NEW_SORTS.get(sort, NEW_SORTS["newest"])
    cards.sort(key=key, reverse=True)
    return {"items": cards[:limit], "sorts": sorted(NEW_SORTS.keys()), "count": len(cards[:limit])}


@router.get("/screener")
def screener(
    db: Session = Depends(get_db),
    min_mcap: float | None = None, max_mcap: float | None = None,
    min_liquidity: float | None = None, max_liquidity: float | None = None,
    min_volume_1h: float | None = None, min_vol_accel: float | None = None,
    min_momentum: float | None = None, max_momentum: float | None = None,
    min_holder_growth: float | None = None, min_buy_share: float | None = None,
    max_sellp: float | None = None, min_sellp: float | None = None,
    max_risk: float | None = None, min_risk: float | None = None,
    min_liq_health: float | None = None,
    max_age_h: float | None = None, min_age_h: float | None = None,
    status: str | None = None,           # comma list: emerging,trending,breakout,near_ath,extended,distribution,declining,newly_launched
    whale: str | None = None,            # accumulating|neutral|distributing|heavy_distribution
    sort: str = "momentum", limit: int = Query(60, le=200),
):
    """Multi-dimensional memecoin screener (v2 §14). All filters combine (AND)."""
    cards = [card(t, sc) for sc, t in latest_score_rows(db)]

    def keep(c):
        if min_mcap is not None and (c["mcap"] or 0) < min_mcap: return False
        if max_mcap is not None and (c["mcap"] or 9e18) > max_mcap: return False
        if min_liquidity is not None and (c["liquidity"] or 0) < min_liquidity: return False
        if max_liquidity is not None and (c["liquidity"] or 9e18) > max_liquidity: return False
        if min_volume_1h is not None and (c["volume_1h"] or 0) < min_volume_1h: return False
        if min_vol_accel is not None and (c["vol_accel_h1"] or 0) < min_vol_accel: return False
        if min_momentum is not None and (c["momentum"] or 0) < min_momentum: return False
        if max_momentum is not None and (c["momentum"] or 9e9) > max_momentum: return False
        if min_holder_growth is not None and (c["holder_growth"] or 0) < min_holder_growth: return False
        if min_buy_share is not None and (c["buy_share"] or 0) * 100 < min_buy_share: return False
        if max_sellp is not None and (c["sellp"] or 0) > max_sellp: return False
        if min_sellp is not None and (c["sellp"] or 0) < min_sellp: return False
        if max_risk is not None and (c["risk"] or 0) > max_risk: return False
        if min_risk is not None and (c["risk"] or 0) < min_risk: return False
        if min_liq_health is not None and (c["liq_health"] or 0) < min_liq_health: return False
        if max_age_h is not None and (c["age_hours"] or 9e9) > max_age_h: return False
        if min_age_h is not None and (c["age_hours"] or 0) < min_age_h: return False
        if whale and (c["whale_class"] or "neutral") not in whale.split(","): return False
        if status:
            sts = set(status.split(","))
            match = (
                ("emerging" in sts and c["emerging"])
                or ("trending" in sts and (c["trend"] or 0) >= 65)
                or ("breakout" in sts and c["ath_status"] == "breaking_out")
                or ("near_ath" in sts and c["ath_status"] == "near_ath")
                or ("extended" in sts and c["ath_status"] in ("extended", "overheated"))
                or ("distribution" in sts and (c["sellp_band"] in ("distribution", "heavy_distribution")
                                               or c["ath_status"] == "reversal_risk"))
                or ("declining" in sts and (c["momentum"] or 0) < 40)
                or ("newly_launched" in sts and (c["age_hours"] or 9e9) < 24)
            )
            if not match: return False
        return True

    cards = [c for c in cards if keep(c)]
    key = NEW_SORTS.get(sort, NEW_SORTS["momentum"])
    cards.sort(key=key, reverse=True)
    return {"items": cards[:limit], "count": len(cards[:limit]), "total_matching": len(cards)}


_SIGNAL_CATEGORIES = {"new", "momentum", "volume", "flow", "whales", "holders",
                      "liquidity", "ath", "reversal", "distribution", "risk", "pattern", "paper"}
_IMPORTANCE_ORDER = {"LOW": 0, "MEDIUM": 1, "HIGH": 2, "CRITICAL": 3}


def _event_json(ev, sym, addr) -> dict:
    return {"id": ev.id, "ts": ev.ts, "symbol": sym, "address": addr,
            "kind": ev.kind, "key": ev.key, "title": ev.title,
            "detail": ev.detail, "severity": ev.severity,
            "metrics": ev.metrics_json or {},
            "category": ev.category, "importance": ev.importance,
            "importance_score": ev.importance_score, "confidence": ev.confidence,
            "signal_text": ev.signal_text, "why": ev.why_json or [],
            "concern": ev.concern, "chain": ev.chain_json,
            "checklist": ev.checklist_json or []}


@router.get("/signals/feed")
def signals_feed(address: str | None = None, limit: int = Query(60, le=300),
                 categories: str | None = None, importance: str | None = None,
                 chain: str | None = None, age: str | None = None,
                 momentum: str | None = None, min_confidence: int = 0,
                 search: str | None = None,
                 sort: str = Query("smart", pattern="^(smart|importance|confidence|recent)$"),
                 since_secs: int | None = None,
                 db: Session = Depends(get_db)):
    """Enriched reverse-chronological intelligence events (v3 §20–§24).

    Filters: categories (csv), importance minimum tier, chain, token age band
    (new<3h / young<24h / established≥24h), current momentum band, min
    confidence, text search. Sort: smart (importance × recency decay, default),
    importance, confidence, recent. Observed/derived/inferred events only."""
    from ..models import SignalEvent
    now = int(time.time())
    cat_set = {c.strip() for c in (categories or "").split(",") if c.strip() in _SIGNAL_CATEGORIES}
    imp_min = _IMPORTANCE_ORDER.get((importance or "").upper(), 0)
    q = (select(SignalEvent, Token.symbol, Token.address)
         .join(Token, Token.id == SignalEvent.token_id))
    if address:
        q = q.where(Token.address == address)
    if cat_set:
        q = q.where(SignalEvent.category.in_(cat_set))
    if imp_min:
        q = q.where(SignalEvent.importance_score >= [35, 60, 85][imp_min - 1])
    if min_confidence:
        q = q.where(SignalEvent.confidence >= min_confidence)
    if chain:
        q = q.where(Token.chain == chain)
    if since_secs:
        q = q.where(SignalEvent.ts >= now - min(since_secs, 86400))
    if search:
        like = f"%{search.strip()[:40]}%"
        q = q.where((SignalEvent.title.like(like)) | (SignalEvent.signal_text.like(like))
                    | (Token.symbol.like(like)))
    rows = db.execute(q.order_by(SignalEvent.ts.desc()).limit(400)).all()

    cards = {}
    if age or momentum:
        for sc, t in latest_score_rows(db):
            cards[t.address] = card(t, sc)

    def band_ok(addr: str) -> bool:
        if not (age or momentum):
            return True
        c = cards.get(addr)
        if not c:
            return False
        if age == "new" and (c.get("age_hours") or 999) >= 3:
            return False
        if age == "young" and (c.get("age_hours") or 999) >= 24:
            return False
        if age == "established" and (c.get("age_hours") or 999) < 24:
            return False
        if momentum and c.get("momentum_band") != momentum:
            return False
        return True

    items = [(ev, sym, addr) for ev, sym, addr in rows if band_ok(addr)]
    if sort == "importance":
        items.sort(key=lambda r: (r[0].importance_score or 0, r[0].ts), reverse=True)
    elif sort == "confidence":
        items.sort(key=lambda r: (r[0].confidence or 0, r[0].ts), reverse=True)
    elif sort == "smart":
        # importance-weighted with 6h recency decay (§24)
        items.sort(key=lambda r: (r[0].importance_score or 0)
                   * max(0.15, 1.0 - (now - r[0].ts) / 21600), reverse=True)
    else:
        items.sort(key=lambda r: r[0].ts, reverse=True)
    out = [_event_json(ev, sym, addr) for ev, sym, addr in items[:limit]]
    return {"items": out, "count": len(out), "ts": now,
            "filters": {"categories": sorted(cat_set), "importance": importance,
                        "chain": chain, "age": age, "momentum": momentum,
                        "min_confidence": min_confidence, "sort": sort}}


@router.get("/signals/pulse")
def signals_pulse(window: str = Query("1h", pattern="^(15m|1h|4h|24h)$"),
                  db: Session = Depends(get_db)):
    """Market Pulse (v3 §22): aggregate view of the intelligence feed over a
    rolling window — event volume, importance mix, tone balance, chains,
    most active tokens."""
    from ..models import SignalEvent
    now = int(time.time())
    since = now - TF_SECONDS[window]
    rows = db.execute(
        select(SignalEvent, Token.symbol).join(Token, Token.id == SignalEvent.token_id)
        .where(SignalEvent.ts >= since)).all()
    by_cat: dict[str, int] = {}
    by_imp = {"LOW": 0, "MEDIUM": 0, "HIGH": 0, "CRITICAL": 0}
    by_tone = {"positive": 0, "info": 0, "warning": 0, "danger": 0}
    per_token: dict[str, dict] = {}
    conf_sum, conf_n = 0.0, 0
    chains = 0
    kind_counts: dict[str, int] = {}
    for ev, sym in rows:
        by_cat[ev.category or "uncategorized"] = by_cat.get(ev.category or "uncategorized", 0) + 1
        by_imp[ev.importance or "LOW"] = by_imp.get(ev.importance or "LOW", 0) + 1
        by_tone[ev.severity] = by_tone.get(ev.severity, 0) + 1
        kind_counts[ev.kind] = kind_counts.get(ev.kind, 0) + 1
        if ev.category == "pattern":
            chains += 1
        if ev.confidence:
            conf_sum += ev.confidence
            conf_n += 1
        pt = per_token.setdefault(sym or "?", {"events": 0, "max_importance": 0,
                                               "danger": 0, "positive": 0})
        pt["events"] += 1
        pt["max_importance"] = max(pt["max_importance"], ev.importance_score or 0)
        pt["danger"] += int(ev.severity == "danger")
        pt["positive"] += int(ev.severity == "positive")
    top_tokens = sorted(per_token.items(), key=lambda kv: (kv[1]["max_importance"], kv[1]["events"]),
                        reverse=True)[:6]
    tone_total = max(sum(by_tone.values()), 1)
    return {"window": window, "since": since, "ts": now,
            "total_events": len(rows), "by_category": by_cat, "by_importance": by_imp,
            "by_tone": by_tone,
            "direction_balance": {
                "positive_share": round(by_tone["positive"] / tone_total, 3),
                "danger_share": round(by_tone["danger"] / tone_total, 3)},
            "chains_detected": chains,
            "avg_confidence": round(conf_sum / conf_n, 1) if conf_n else None,
            "top_tokens": [{"symbol": s, **v} for s, v in top_tokens],
            "top_kinds": sorted(kind_counts.items(), key=lambda kv: -kv[1])[:8]}


@router.get("/signals/quality")
def signals_quality(db: Session = Depends(get_db)):
    """Signal quality dashboard (v3 §50): production stats for the event
    engine — coverage, confidence, dedupe health, labeling progress and the
    accuracy feedback loop from outcome labeling (§49)."""
    from ..models import SignalEvent, SignalEventOutcome
    now = int(time.time())
    day = now - 86400
    rows = db.execute(select(SignalEvent).where(SignalEvent.ts >= day)
                      .order_by(SignalEvent.ts.asc())).scalars().all()
    by_cat: dict[str, int] = {}
    conf_by_cat: dict[str, list[float]] = {}
    for ev in rows:
        c = ev.category or "uncategorized"
        by_cat[c] = by_cat.get(c, 0) + 1
        if ev.confidence:
            conf_by_cat.setdefault(c, []).append(ev.confidence)
    # dedupe health: same (token, kind) events re-firing well inside the
    # cooldown would be duplicates — measured directly (§43 compliance check)
    from ..engine import intel_config as IC
    last_seen: dict[tuple, int] = {}
    dups = 0
    pairs = 0
    for ev in rows:
        if ev.category is None:      # v2 legacy rows are not cooldown-managed
            continue
        k = (ev.token_id, ev.kind)
        prev = last_seen.get(k)
        if prev is not None:
            pairs += 1
            if ev.ts - prev < IC.COOLDOWNS.get(ev.kind, 3600) // 2:
                dups += 1
        last_seen[k] = ev.ts
    # outcome labeling progress + forward returns (§49 feedback loop)
    out_rows = db.execute(
        select(SignalEventOutcome).where(SignalEventOutcome.ts >= day)).scalars().all()
    by_horizon: dict[int, dict] = {}
    by_cat_ret: dict[str, dict[str, list[float]]] = {}
    for o in out_rows:
        h = by_horizon.setdefault(o.horizon_secs, {"labeled": 0, "avg_ret": [], "avg_dd": []})
        h["labeled"] += 1
        if o.fwd_ret_pct is not None:
            h["avg_ret"].append(o.fwd_ret_pct)
        if o.max_dd_pct is not None:
            h["avg_dd"].append(o.max_dd_pct)
        cr = by_cat_ret.setdefault(o.category or "uncategorized", {"ret_1h": [], "ret_4h": []})
        if o.fwd_ret_pct is not None and o.horizon_secs in (3600, 14400):
            cr["ret_1h" if o.horizon_secs == 3600 else "ret_4h"].append(o.fwd_ret_pct)
    horizons = [{"horizon_secs": hs, "labeled": v["labeled"],
                 "avg_fwd_ret_pct": round(sum(v["avg_ret"]) / len(v["avg_ret"]), 2) if v["avg_ret"] else None,
                 "avg_max_dd_pct": round(sum(v["avg_dd"]) / len(v["avg_dd"]), 2) if v["avg_dd"] else None}
                for hs, v in sorted(by_horizon.items())]
    cat_ret = {c: {k: (round(sum(v) / len(v), 2) if v else None) for k, v in m.items()}
               for c, m in by_cat_ret.items()}
    pending = db.execute(
        select(func.count(SignalEvent.id)).where(
            SignalEvent.ts <= now - 900, SignalEvent.category.isnot(None),
            ~select(SignalEventOutcome.id).where(
                SignalEventOutcome.event_id == SignalEvent.id,
                SignalEventOutcome.horizon_secs == 900).exists())
    ).scalar() or 0
    latest_ts = db.execute(select(func.max(SignalEvent.ts))).scalar()
    return {"window": "24h", "ts": now,
            "events_24h": len(rows), "by_category": by_cat,
            "avg_confidence_by_category": {c: round(sum(v) / len(v), 1)
                                           for c, v in conf_by_cat.items()},
            "dedupe": {"same_kind_pairs": pairs, "possible_duplicates": dups,
                       "dup_rate": round(dups / pairs, 4) if pairs else 0.0},
            "labeling": {"outcomes_24h": len(out_rows), "pending_min_horizon": pending},
            "outcome_stats_by_horizon": horizons,
            "avg_forward_return_by_category": cat_ret,
            "freshness_secs": (now - latest_ts) if latest_ts else None}





@router.get("/tokens/compare")
def tokens_compare(addresses: str, db: Session = Depends(get_db)):
    """Side-by-side memecoin comparison (v2 §29): ?addresses=a,b,c (≤5)."""
    addrs = [a.strip() for a in addresses.split(",") if a.strip()][:5]
    rows = latest_score_rows(db)
    by_addr = {t.address: (t, sc) for sc, t in rows}
    found, missing = [], []
    for a in addrs:
        if a in by_addr:
            found.append(card(*by_addr[a]))
        else:
            missing.append(a)
    return {"items": found, "missing": missing,
            "metrics": ["momentum", "sellp", "risk", "trend", "fomo", "liq_health",
                        "liquidity", "mcap", "volume_24h", "vol_accel_h1",
                        "holder_growth", "buy_share", "ath_status", "ath_distance",
                        "whale_class", "organic", "age_hours"]}


@router.get("/tokens/{address}")
def token_detail(address: str, db: Session = Depends(get_db)):
    token = get_token_or_404(db, address)
    sc = db.execute(select(TrendScore).where(TrendScore.token_id == token.id)
                    .order_by(TrendScore.ts.desc()).limit(1)).scalar_one_or_none()
    if not sc:
        raise HTTPException(404, "no scores yet")
    out = card(token, sc)
    out.update({
        "description": token.description, "pair_address": token.pair_address,
        "contract_facts": contract_facts_for(db, token),
        "sources": (sc.components_json or {}).get("sources", {}),
        "archetype": token.archetype,
        "created_ms": token.token_created_at,
    })
    return out


@router.get("/tokens/{address}/scores")
def token_scores(address: str, db: Session = Depends(get_db)):
    """Full explainability payload for every score (§35)."""
    token = get_token_or_404(db, address)
    sc = db.execute(select(TrendScore).where(TrendScore.token_id == token.id)
                    .order_by(TrendScore.ts.desc()).limit(1)).scalar_one_or_none()
    if not sc:
        raise HTTPException(404, "no scores yet")
    c = sc.components_json or {}
    return {
        "address": address, "symbol": token.symbol, "ts": sc.ts,
        "trend": sc.trend, "fomo": sc.fomo, "fomo_class": sc.fomo_class,
        "risk": sc.risk, "risk_class": sc.risk_class, "confidence": sc.confidence,
        "emerging": bool(sc.emerging),
        "trend_components": c.get("trend", {}),
        "fomo_components": c.get("fomo", []),
        "fomo_warning": c.get("fomo_warning"),
        "risk_components": c.get("risk", []),
        "risk_flags": c.get("risk_flags", []),
        "signals": c.get("signals", {}),
        "conflicts": c.get("conflicts", []),
        "forensics": c.get("forensics", {}),
        "market_intel": c.get("market_intel", {}),
        "sources": c.get("sources", {}),
    }


@router.get("/tokens/{address}/history")
def token_history(address: str, metric: str = "price", tf: str = "24h",
                  db: Session = Depends(get_db)):
    token = get_token_or_404(db, address)
    secs = TF_SECONDS.get(tf)
    if not secs:
        raise HTTPException(400, f"tf must be one of {sorted(TF_SECONDS)}")
    since = int(time.time()) - secs
    if metric == "price":
        rows = db.execute(select(PriceSnapshot).where(
            PriceSnapshot.token_id == token.id, PriceSnapshot.ts >= since)
            .order_by(PriceSnapshot.ts)).scalars().all()
        return {"metric": metric, "tf": tf,
                "points": [{"t": r.ts, "price": r.price_usd, "mcap": r.mcap} for r in rows]}
    if metric == "volume":
        rows = db.execute(select(VolumeSnapshot).where(
            VolumeSnapshot.token_id == token.id, VolumeSnapshot.ts >= since)
            .order_by(VolumeSnapshot.ts)).scalars().all()
        return {"metric": metric, "tf": tf,
                "points": [{"t": r.ts, "volume": r.volume, "txns": r.txns,
                            "buys": r.buys, "sells": r.sells} for r in rows]}
    if metric == "liquidity":
        rows = db.execute(select(LiquiditySnapshot).where(
            LiquiditySnapshot.token_id == token.id, LiquiditySnapshot.ts >= since)
            .order_by(LiquiditySnapshot.ts)).scalars().all()
        return {"metric": metric, "tf": tf,
                "points": [{"t": r.ts, "liquidity": r.liquidity_usd} for r in rows]}
    if metric == "social":
        rows = db.execute(select(SocialSnapshot).where(
            SocialSnapshot.token_id == token.id, SocialSnapshot.ts >= since)
            .order_by(SocialSnapshot.ts)).scalars().all()
        return {"metric": metric, "tf": tf,
                "points": [{"t": r.ts, "mentions": r.mentions, "engagement": r.engagement,
                            "uniques": r.unique_accounts, "sentiment": r.sentiment,
                            "influencers": r.influencer_posts} for r in rows]}
    if metric == "holders":
        # REAL points only: one per Helius check (every ~6h), not the simulated snapshots
        from ..models import TokenRiskCheck
        from ..services.holders import epoch
        rows = [r for r in db.execute(select(TokenRiskCheck).where(
            TokenRiskCheck.token_id == token.id, TokenRiskCheck.ok.is_(True),
            TokenRiskCheck.holders_count.isnot(None)).order_by(TokenRiskCheck.checked_at)).scalars().all()
            if epoch(r.checked_at) >= since]
        return {"metric": metric, "tf": tf, "source": "helius", "mock": False,
                "points": [{"t": epoch(r.checked_at), "holders": r.holders_count,
                            "top10": r.top10_wallet_pct if r.top10_wallet_pct is not None else r.top10_pct,
                            "capped": bool(r.holders_capped)} for r in rows]}
    if metric == "score":
        rows = db.execute(select(TrendScore).where(
            TrendScore.token_id == token.id, TrendScore.ts >= since)
            .order_by(TrendScore.ts)).scalars().all()
        return {"metric": metric, "tf": tf,
                "points": [{"t": r.ts, "trend": r.trend, "fomo": r.fomo, "risk": r.risk}
                           for r in rows]}
    raise HTTPException(400, "metric must be price|volume|liquidity|social|holders|score")


@router.get("/tokens/{address}/social")
def token_social(address: str, db: Session = Depends(get_db)):
    token = get_token_or_404(db, address)
    posts = db.execute(select(SocialPost).where(SocialPost.token_id == token.id)
                       .order_by(SocialPost.ts.desc()).limit(25)).scalars().all()
    mock = token.address.startswith("Mock") or USE_SIMULATED_SOCIAL
    if not mock:
        # REAL: posts from the watched public Telegram call channels naming this contract
        from ..models import TelegramPost
        rows = db.execute(select(TelegramPost).where(TelegramPost.ts >= int(time.time()) - 3 * 86400)
                          .order_by(TelegramPost.ts.desc()).limit(2000)).scalars().all()
        tg = [r for r in rows if token.address in (r.mints_json or [])][:25]
        return {"posts": [{"ts": r.ts, "author": "@" + r.channel, "text": r.text, "views": r.views,
                           "url": f"https://t.me/{r.post_id}", "platform": "telegram"} for r in tg],
                "source": "telegram" if TELEGRAM_CALL_CHANNELS.strip() else "none", "mock": False,
                "note": "Calls are attention, not buy signals — research found buying on calls lost money on average."}
    return {"posts": [{"ts": p.ts, "author": p.author, "text": p.text, "likes": p.likes,
                       "reposts": p.reposts, "quality": p.quality} for p in posts],
            "source": "mock:x-style-sim" if mock else "none", "mock": mock}


@router.get("/tokens/{address}/holders")
def token_holders(address: str, db: Session = Depends(get_db)):
    token = get_token_or_404(db, address)
    from ..services.holders import real_holder_stats
    rh = real_holder_stats(db, [token.id]).get(token.id)
    whales = db.execute(select(WalletActivity).where(WalletActivity.token_id == token.id)
                        .order_by(WalletActivity.ts.desc()).limit(20)).scalars().all()
    return {"current": None if not rh else {
                "holders": rh["count"], "capped": rh["capped"], "growth_pct": rh["growth_pct"],
                "span_h": rh["span_h"],
                "checked_at": rh["checked_at"].isoformat() if rh["checked_at"] else None,
                "source": "helius"},
            "holders_mock": False,
            "whales": [{"ts": w.ts, "wallet": w.wallet, "side": w.side,
                        "amount_usd": w.amount_usd, "kind": w.kind} for w in whales],
            # whale trade list is only simulated (demo universe); real tokens get none
            "source": "mock:chain-sim" if token.address.startswith("Mock") else "helius",
            "mock": token.address.startswith("Mock")}


@router.get("/tokens/{address}/onchain-risk")
def token_onchain_risk(address: str, db: Session = Depends(get_db)):
    """REAL on-chain risk facts from Solana RPC (Helius). Not simulated."""
    from ..models import TokenRiskCheck
    from ..providers import helius as helius_mod
    token = get_token_or_404(db, address)
    last = db.execute(select(TokenRiskCheck).where(TokenRiskCheck.token_id == token.id)
                      .order_by(TokenRiskCheck.checked_at.desc()).limit(1)).scalar_one_or_none()
    if last is None:
        return {"available": False, "mock": False,
                "reason": "not checked yet" if helius_mod.enabled()
                          else "Helius not configured (set HELIUS_API_KEY)"}
    return {"available": bool(last.ok), "mock": False, "source": "helius",
            "checked_at": last.checked_at.isoformat() if last.checked_at else None,
            "error": last.error or None,
            "label": helius_mod.risk_label(last), "flags": helius_mod.risk_flags(last),
            "facts": None if not last.ok else {
                "mint_authority": last.mint_authority, "freeze_authority": last.freeze_authority,
                "mint_renounced": not last.mint_authority,
                "freeze_disabled": not last.freeze_authority,
                "supply": last.supply, "decimals": last.decimals,
                "top1_pct": last.top1_pct, "top10_pct": last.top10_pct,
                "accounts_sampled": last.holders_sampled, "token_2022": bool(last.token_2022),
                "holders": last.holders_count, "holders_capped": bool(last.holders_capped),
                "top1_wallet_pct": last.top1_wallet_pct, "top10_wallet_pct": last.top10_wallet_pct,
                "excluded_pct": last.excluded_pct,
                "token_extensions": helius_mod.ext_names_from_str(last.token_extensions),
                "transfer_fee_bps": last.transfer_fee_bps,
                "transfer_fee_mutable": last.transfer_fee_mutable},
            "exit": None if last.exit_sol_in is None else {
                "source": "jupiter", "sol_in": last.exit_sol_in,
                "buy_route": last.exit_buy_route, "sell_route": last.exit_sell_route,
                "round_trip_loss_pct": last.exit_round_trip_loss_pct,
                "route": last.exit_route, "error": last.exit_error,
                "note": "Quote only (no transaction). Freeze/hook/frozen-account checks above still apply."},
            "caveat": ("Wallet figures exclude pools, bonding curves, lockers and burn addresses "
                       "(program-owned accounts). Account figures include them."
                       if last.top10_wallet_pct is not None else
                       "Largest accounts may include the liquidity-pool vault or burn wallets, "
                       "so concentration is an upper bound.")}


_TM_CACHE: dict[tuple, tuple[float, dict]] = {}
_TM_TTL = 300  # seconds; the pool only changes every 5-min bucket


@router.get("/tokens/{address}/timemachine")
def token_timemachine(address: str, stop_pct: Optional[float] = None,
                      target_pct: Optional[float] = None, hours: Optional[float] = None,
                      kind: Optional[str] = None, replays: int = 10000, seed: int = 19,
                      db: Session = Depends(get_db)):
    """Time Machine: Monte Carlo replays from this token's own REAL 5-min prices,
    plus the same rule re-run after every past signal of the same kind.
    Defaults come from the paper strategy (stop, take-profit, max hold)."""
    from ..engine import timemachine as tm
    from ..models import SignalEvent
    from ..paper.config import BUY_KINDS, DEFAULT_SETTINGS

    token = get_token_or_404(db, address)
    stop_pct = float(stop_pct if stop_pct is not None else DEFAULT_SETTINGS["stop_loss_pct"])
    target_pct = float(target_pct if target_pct is not None
                       else DEFAULT_SETTINGS["take_profit_pct"] or 35.0)
    hours = float(hours if hours is not None else DEFAULT_SETTINGS["max_hold_hours"])
    err = tm.validate_plan(stop_pct, target_pct, hours, replays)
    if err:
        raise HTTPException(400, err)
    if kind is not None and kind not in BUY_KINDS:
        raise HTTPException(400, f"kind must be one of {list(BUY_KINDS)}")

    now = int(time.time())
    if kind is None:  # default: this token's latest bullish signal kind
        kind = db.execute(select(SignalEvent.kind).where(
            SignalEvent.token_id == token.id, SignalEvent.kind.in_(BUY_KINDS),
            SignalEvent.ts >= now - 86400).order_by(SignalEvent.ts.desc()).limit(1)).scalar()
    ckey = (token.id, stop_pct, target_pct, hours, kind, replays, seed, now // _TM_TTL)
    hit = _TM_CACHE.get(ckey)
    if hit and now - hit[0] < _TM_TTL:
        return hit[1]

    plan = {"stop_pct": stop_pct, "target_pct": target_pct, "hours": hours,
            "r_multiple": round(target_pct / stop_pct, 3),
            "breakeven_pct": round(tm.breakeven_rate(target_pct / stop_pct) * 100, 1)}
    pts = db.execute(select(PriceSnapshot.ts, PriceSnapshot.price_usd).where(
        PriceSnapshot.token_id == token.id, PriceSnapshot.ts >= now - 7 * 86400)
        .order_by(PriceSnapshot.ts)).all()
    rets = tm.contiguous_log_returns([(int(t), p) for t, p in pts])
    entry = next((p for _, p in reversed(pts) if p and p > 0), None)
    steps = max(1, int(round(hours * 3600 / tm.BUCKET_SECS)))
    out: dict = {"mock": False, "source": "dexscreener 5-min snapshots", "plan": plan,
                 "entry_price": entry, "pool_returns": int(len(rets)),
                 "pool_hours": round(len(rets) * tm.BUCKET_SECS / 3600, 1), "kind": kind,
                 "caveat": ("Built from 5-minute closes: moves inside a bucket aren't seen, and a "
                            "real stop can fill worse. A replay of past prices, not a prediction.")}
    if len(rets) < tm.MIN_RETURNS or entry is None:
        out["simulation"] = None
        out["reason"] = (f"Needs at least {tm.MIN_RETURNS * 5 // 60}h of unbroken 5-minute price "
                         f"data; this token has {out['pool_hours']}h.")
    else:
        out["simulation"] = tm.simulate(rets, stop_pct, target_pct, steps, replays, seed)

    # history: same rule after every past signal of this kind (all tokens, 14 days)
    horizon = steps * tm.BUCKET_SECS
    kinds = [kind] if kind else list(BUY_KINDS)
    evs = db.execute(select(SignalEvent.token_id, SignalEvent.ts).where(
        SignalEvent.kind.in_(kinds), SignalEvent.ts >= now - 14 * 86400,
        SignalEvent.ts <= now - horizon).order_by(SignalEvent.ts.desc()).limit(200)).all()

    def walks():
        for tok_id, ev_ts in evs:
            rows = db.execute(select(PriceSnapshot.ts, PriceSnapshot.price_usd).where(
                PriceSnapshot.token_id == tok_id, PriceSnapshot.ts >= ev_ts,
                PriceSnapshot.ts <= ev_ts + horizon + tm.BUCKET_SECS)
                .order_by(PriceSnapshot.ts)).all()
            yield int(ev_ts), [(int(t), p) for t, p in rows]

    hist = tm.replay_history(walks(), stop_pct, target_pct, horizon, now)
    hist["signals_used"] = len(evs)
    hist["scope"] = f"'{kind}' signals, all tokens, last 14 days" if kind \
        else "all bullish signals, all tokens, last 14 days"
    out["history"] = hist
    if len(_TM_CACHE) > 256:
        _TM_CACHE.clear()
    _TM_CACHE[ckey] = (now, out)
    return out


@router.get("/tokens/{address}/risk")
def token_risk_events(address: str, db: Session = Depends(get_db)):
    token = get_token_or_404(db, address)
    events = db.execute(select(RiskEvent).where(RiskEvent.token_id == token.id)
                        .order_by(RiskEvent.ts.desc()).limit(30)).scalars().all()
    return {"events": [{"ts": e.ts, "category": e.category, "severity": e.severity,
                        "message": e.message} for e in events]}


def _fresh_payloads(db: Session, token: Token):
    now = int(time.time()) - int(time.time()) % 300
    ctx = TokenContext.build(db, token, now)
    sc = db.execute(select(TrendScore).where(TrendScore.token_id == token.id)
                    .order_by(TrendScore.ts.desc()).limit(1)).scalar_one_or_none()
    names = []
    if sc:
        names = (sc.components_json or {}).get("summary", {}).get("narratives", [])
    narr_active = {n for (n,) in db.execute(
        select(NewsArticle.narrative).where(NewsArticle.ts >= now - 86400).distinct()).all() if n}
    news_active = any(n in narr_active for n in names)
    ctx.available["news"] = news_active
    info = {"best_score": 0.0, "count": len(names),
            "cross_platform": news_active}
    trend_p = scoring_engine.calculate_trend_score(ctx, info, {"sector_momentum": 50, "breadth": 0.5})
    fomo_p = fomo_engine.calculate_fomo(ctx, news_active)
    risk_p = risk_engine.calculate_risk(ctx, contract_facts_for(db, token))
    signals = signals_engine.confirmation(ctx, news_active)
    return ctx, trend_p, fomo_p, risk_p, signals, names, news_active


@router.get("/tokens/{address}/analysis")
def token_analysis(address: str, db: Session = Depends(get_db)):
    """§17 AI analysis — computed from stored, traceable metrics only."""
    token = get_token_or_404(db, address)
    ctx, tp, fp, rp, sig, names, na = _fresh_payloads(db, token)
    return build_analysis(ctx, tp, fp, rp, names, sig, na)


@router.get("/tokens/{address}/why")
def token_why(address: str, db: Session = Depends(get_db)):
    token = get_token_or_404(db, address)
    ctx, tp, fp, rp, _, names, _ = _fresh_payloads(db, token)
    return {"explanation": build_why_trending(ctx, tp, fp, rp, names)}


# ---------------------------------------------------------------- narratives
@router.get("/narratives/trending")
def narratives_trending(db: Session = Depends(get_db)):
    narrs = db.execute(select(Narrative).order_by(Narrative.score.desc())).scalars().all()
    latest = {sc.token_id: sc for sc, _ in latest_score_rows(db)}
    out = []
    for n in narrs:
        links = db.execute(select(TokenNarrative, Token)
                           .join(Token, Token.id == TokenNarrative.token_id)
                           .where(TokenNarrative.narrative_id == n.id)).all()
        members = sorted(
            ({"symbol": t.symbol, "address": t.address,
              "trend": latest[t.id].trend if t.id in latest else 0,
              "fomo": latest[t.id].fomo if t.id in latest else 0} for l, t in links),
            key=lambda m: -m["trend"])
        out.append({
            "id": n.id, "name": n.name, "kind": n.kind, "score": n.score,
            "mentions_growth": n.mentions_growth, "token_count": n.token_count,
            "strong_candidates": n.strong_candidates,
            "abnormal_volume_tokens": n.abnormal_volume_tokens,
            "cross_platform": n.cross_platform,
            "top_tokens": members[:6],
        })
    return {"items": out}


@router.get("/narratives/{narrative_id}")
def narrative_detail(narrative_id: int, db: Session = Depends(get_db)):
    n = db.get(Narrative, narrative_id)
    if not n:
        raise HTTPException(404, "narrative not found")
    latest = {sc.token_id: sc for sc, _ in latest_score_rows(db)}
    links = db.execute(select(TokenNarrative, Token)
                       .join(Token, Token.id == TokenNarrative.token_id)
                       .where(TokenNarrative.narrative_id == n.id)).all()
    members = sorted(
        ({"symbol": t.symbol, "name": t.name, "address": t.address, "chain": t.chain,
          "trend": latest[t.id].trend if t.id in latest else 0,
          "fomo": latest[t.id].fomo if t.id in latest else 0,
          "risk": latest[t.id].risk if t.id in latest else 0,
          "relevance": l.relevance} for l, t in links),
        key=lambda m: -m["trend"])
    articles = db.execute(select(NewsArticle).where(NewsArticle.narrative == n.name)
                          .order_by(NewsArticle.ts.desc()).limit(12)).scalars().all()
    return {"narrative": {"id": n.id, "name": n.name, "kind": n.kind, "score": n.score,
                          "mentions_growth": n.mentions_growth, "token_count": n.token_count,
                          "strong_candidates": n.strong_candidates,
                          "abnormal_volume_tokens": n.abnormal_volume_tokens,
                          "cross_platform": n.cross_platform,
                          "first_seen_at": n.first_seen_at},
            "tokens": members,
            "news": [{"ts": a.ts, "source": a.source, "title": a.title, "url": a.url,
                      "sentiment": a.sentiment} for a in articles],
            "news_source": "rss" if NEWS_RSS_ENABLED else "mock:news-sim"}


@router.get("/news")
def news_feed(limit: int = Query(40, ge=1, le=100), narrative: str | None = None,
              symbol: str | None = None, db: Session = Depends(get_db)):
    """Recent headlines (last 7 days, newest first). narrative= filters by tagged
    narrative; symbol= keeps headlines that mention that token ($CASHTAG or name)."""
    q = select(NewsArticle).where(NewsArticle.ts >= int(time.time()) - 7 * 86400)
    if narrative:
        q = q.where(NewsArticle.narrative == narrative[:120])
    rows = db.execute(q.order_by(NewsArticle.ts.desc()).limit(1000 if symbol else limit)).scalars().all()
    if symbol:
        sym = symbol.strip().upper()[:32]
        rows = [r for r in rows if sym in (r.entities_json or [])]
    rows = rows[:limit]
    return {"source": "rss" if NEWS_RSS_ENABLED else "mock:news-sim",
            "items": [{"ts": r.ts, "source": r.source, "title": r.title, "url": r.url,
                       "narrative": r.narrative, "sentiment": r.sentiment,
                       "symbols": r.entities_json or []} for r in rows]}


# ---------------------------------------------------------------- calibration
@router.get("/calibration/overview")
def calibration_overview(db: Session = Depends(get_db)):
    """Signal reliability over labeled forward outcomes (§21, Tier-4)."""
    from ..engine.backtest import calibration_report
    return calibration_report(db)


# ---------------------------------------------------------------- misc
@router.get("/risk/events")
def risk_events_feed(db: Session = Depends(get_db), limit: int = Query(30, le=100)):
    rows = db.execute(select(RiskEvent, Token)
                      .join(Token, Token.id == RiskEvent.token_id)
                      .order_by(RiskEvent.ts.desc()).limit(limit)).all()
    return {"items": [{"ts": e.ts, "category": e.category, "severity": e.severity,
                       "message": e.message, "symbol": t.symbol, "name": t.name,
                       "address": t.address} for e, t in rows]}


@router.get("/search")
def search(q: str = Query(min_length=1, max_length=128), db: Session = Depends(get_db)):
    q = q.strip()
    result: dict = {"tokens": [], "narratives": [], "address_valid": None}
    if len(q) >= 30:
        result["address_valid"] = valid_address(q)
        if result["address_valid"]:
            tok = db.execute(select(Token).where(Token.address == q)).scalar_one_or_none()
            if tok:
                sc_rows = [r for r in latest_score_rows(db) if r[1].id == tok.id]
                if sc_rows:
                    result["tokens"] = [card(sc_rows[0][1], sc_rows[0][0])]
            return result
    like = f"{q}%"
    toks = db.execute(select(Token).where(
        (Token.symbol.ilike(like)) | (Token.name.ilike(f"%{q}%"))).limit(10)).scalars().all()
    latest = {t.id: sc for sc, t in latest_score_rows(db)}
    result["tokens"] = [card(t, latest[t.id]) for t in toks if t.id in latest]
    narrs = db.execute(select(Narrative).where(Narrative.name.ilike(f"%{q}%"))
                       .limit(5)).scalars().all()
    result["narratives"] = [{"id": n.id, "name": n.name, "score": n.score} for n in narrs]
    return result
