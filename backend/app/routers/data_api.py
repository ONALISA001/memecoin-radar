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
from ..providers.mock_providers import Sim
from ..security import ALLOWED_SORTS, rate_limit, valid_address

router = APIRouter(dependencies=[Depends(rate_limit)])

TF_SECONDS = {"5m": 300, "15m": 900, "30m": 1800, "1h": 3600, "4h": 14400,
              "12h": 43200, "24h": 86400, "7d": 604800}


# ---------------------------------------------------------------- helpers
def latest_score_rows(db: Session):
    sub = (select(TrendScore.token_id, func.max(TrendScore.ts).label("mts"))
           .group_by(TrendScore.token_id).subquery())
    return db.execute(
        select(TrendScore, Token)
        .join(Token, Token.id == TrendScore.token_id)
        .join(sub, and_(TrendScore.token_id == sub.c.token_id,
                        TrendScore.ts == sub.c.mts))
    ).all()


def card(token: Token, sc: TrendScore) -> dict:
    c = sc.components_json or {}
    s = c.get("summary", {})
    return {
        "address": token.address, "chain": token.chain, "name": token.name,
        "symbol": token.symbol, "image_url": token.image_url, "dex": token.dex,
        "url": token.url,
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
        "holders": s.get("holders"), "mentions_1h": s.get("mentions_1h"),
        "buy_share": s.get("buy_share"),
        "confirmed": s.get("confirmed", 0), "conflicts": s.get("conflicts", 0),
        "age_hours": s.get("age_hours"), "ts": sc.ts,
        "organic": s.get("organic"), "regime": s.get("regime"),
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


@router.get("/tokens/{address}")
def token_detail(address: str, db: Session = Depends(get_db)):
    token = get_token_or_404(db, address)
    sc = db.execute(select(TrendScore).where(TrendScore.token_id == token.id)
                    .order_by(TrendScore.ts.desc()).limit(1)).scalar_one_or_none()
    if not sc:
        raise HTTPException(404, "no scores yet")
    sim = Sim.from_state(token.address, token.state_json or {})
    out = card(token, sc)
    out.update({
        "description": token.description, "pair_address": token.pair_address,
        "contract_facts": sim.contract_facts(),
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
        rows = db.execute(select(HolderSnapshot).where(
            HolderSnapshot.token_id == token.id, HolderSnapshot.ts >= since)
            .order_by(HolderSnapshot.ts)).scalars().all()
        return {"metric": metric, "tf": tf,
                "points": [{"t": r.ts, "holders": r.holders, "top10": r.top10_pct,
                            "new_wallets": r.new_wallets} for r in rows]}
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
    return {"posts": [{"ts": p.ts, "author": p.author, "text": p.text, "likes": p.likes,
                       "reposts": p.reposts, "quality": p.quality} for p in posts],
            "source": "mock:x-style-sim", "mock": True}


@router.get("/tokens/{address}/holders")
def token_holders(address: str, db: Session = Depends(get_db)):
    token = get_token_or_404(db, address)
    last = db.execute(select(HolderSnapshot).where(HolderSnapshot.token_id == token.id)
                      .order_by(HolderSnapshot.ts.desc()).limit(1)).scalar_one_or_none()
    whales = db.execute(select(WalletActivity).where(WalletActivity.token_id == token.id)
                        .order_by(WalletActivity.ts.desc()).limit(20)).scalars().all()
    return {"current": None if not last else {
                "holders": last.holders, "top10_pct": last.top10_pct,
                "deployer_pct": last.deployer_pct, "active_wallets": last.active_wallets},
            "whales": [{"ts": w.ts, "wallet": w.wallet, "side": w.side,
                        "amount_usd": w.amount_usd, "kind": w.kind} for w in whales],
            "source": "mock:chain-sim", "mock": True}


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
    sim = Sim.from_state(token.address, token.state_json or {})
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
    risk_p = risk_engine.calculate_risk(ctx, sim.contract_facts())
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
            "news_source": "mock:news-sim"}


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
