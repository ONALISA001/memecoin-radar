"""Ingestion pipeline (§4, §33). Runs once per tick:

    discover → market fetch → upsert tokens → backfill new tokens (48h) →
    append current-bucket snapshots → rescore (trend/fomo/risk) → narratives →
    news generation → risk events → alerts → source-health logging.

The same `tick()` body is designed to be lifted into a Celery beat task
production-side without modification.
"""
from __future__ import annotations

import asyncio
import logging
import math
import time
import traceback

from sqlalchemy import delete, func, select

from ..config import (ANOMALY_Z, BACKFILL_HOURS, DEXSCREENER_ENABLED,
                      SNAPSHOT_BUCKET_SECONDS as BUCKET)
from ..database import SessionLocal
from ..engine import alerts as alerts_engine
from ..engine import backtest as backtest_engine
from ..engine import narratives as narratives_engine
from ..engine import risk as risk_engine
from ..engine import scoring as scoring_engine
from ..engine import signals as signals_engine
from ..engine import fomo as fomo_engine
from ..engine import market_intel as market_intel_engine
from ..engine import timeseries as tsx
from ..engine.context import TokenContext
from ..engine.forensics import compute_forensics
from ..models import (Alert, DataSource, HolderSnapshot, LiquiditySnapshot,
                      Narrative, NewsArticle, PriceSnapshot, RiskEvent,
                      SocialPost, SocialSnapshot, SystemLog, Token, TokenPair,
                      TrendScore, VolumeSnapshot, WalletActivity, Watchlist,
                      SignalEvent)
from ..providers.base import HEALTH, RawToken
from ..providers.dexscreener import DexscreenerProvider
from ..providers.mock_providers import (FALLBACK_TOKENS, Sim, bucket_of,
                                        generate_articles, generate_posts,
                                        generate_whale_events, live_post, sha_seed)

log = logging.getLogger("ingestion")
KST = 60  # max tokens fully rescored per tick


def log_line(db, level: str, source: str, message: str):
    db.add(SystemLog(ts=int(time.time()), level=level, source=source, message=message[:480]))


def upsert_source(db, name: str, kind: str, status: str, meta: dict):
    src = db.execute(select(DataSource).where(DataSource.name == name)).scalar_one_or_none()
    if src is None:
        src = DataSource(name=name, kind=kind)
        db.add(src)
    src.status = status
    src.meta_json = meta
    src.last_ok_at = func.now()


# --------------------------------------------------------------------------
async def discover_tokens() -> tuple[list[RawToken], dict[str, dict], bool]:
    """Returns (raw tokens, live market ticks, used_fallback?)."""
    if not DEXSCREENER_ENABLED:
        return _fallback_universe(), _fallback_ticks(), True
    provider = DexscreenerProvider()
    try:
        raws = await provider.discover()
        ticks_obj = await provider.fetch_market(raws)
        ticks = {a: mt.__dict__ for a, mt in ticks_obj.items()}
        raws = [r for r in raws if r.address in ticks]      # only tokens with live data
        if not raws:
            raise RuntimeError("no tokens with data")
        HEALTH.report("dexscreener", "market", True, f"{len(raws)} tracked")
        return raws, ticks, False
    except Exception as exc:
        HEALTH.report("dexscreener", "market", False, str(exc)[:160])
        log_line_db_only("warn", "ingestion", f"DexScreener unavailable, using fallback universe: {exc}")
        return _fallback_universe(), _fallback_ticks(), True


def log_line_db_only(level, source, message):
    with SessionLocal() as db:
        log_line(db, level, source, message)
        db.commit()


_B58_FIX = str.maketrans({"0": "2", "O": "P", "I": "J", "l": "k"})


def _mock_addr(sym: str) -> str:
    return f"Mock{sym}{sha_seed(sym):x}".translate(_B58_FIX)[:44]


def _fallback_universe() -> list[RawToken]:
    out = []
    for i, (name, sym, chain) in enumerate(FALLBACK_TOKENS):
        out.append(RawToken(
            address=_mock_addr(sym), chain=chain, name=name, symbol=sym,
            url="", description=f"{name} — simulated discovery token (DexScreener unreachable).",
            extra={"boosts": 100 - i},
        ))
    return out


def _fallback_ticks() -> dict[str, dict]:
    import random as _r
    ticks = {}
    for i, (name, sym, chain) in enumerate(FALLBACK_TOKENS):
        r = _r.Random(sha_seed(sym))
        addr = _mock_addr(sym)
        mcap = 10 ** r.uniform(4.2, 8.0)
        liq = mcap / r.uniform(4, 60)
        vol_h1 = mcap / r.uniform(6, 80)
        price = 10 ** r.uniform(-9, -2)
        ticks[addr] = {
            "price_usd": price, "mcap": mcap, "fdv": mcap * 1.02, "liquidity_usd": liq,
            "volume_h24": vol_h1 * r.uniform(14, 24), "volume_h6": vol_h1 * r.uniform(3, 6),
            "volume_h1": vol_h1, "volume_m5": vol_h1 / 12,
            "pc_m5": r.uniform(-4, 6), "pc_h1": r.uniform(-30, 120), "pc_h6": r.uniform(-40, 400),
            "pc_h24": r.uniform(-60, 900),
            "txns_h1_buys": int(vol_h1 / r.uniform(60, 300)), "txns_h1_sells": int(vol_h1 / r.uniform(70, 350)),
            "txns_h24_buys": 0, "txns_h24_sells": 0,
            "txns_m5_buys": int(vol_h1 / 12 / r.uniform(60, 300)),
            "txns_m5_sells": int(vol_h1 / 12 / r.uniform(70, 350)),
            "pair_liquidity_usd": liq,
        }
    return ticks


# --------------------------------------------------------------------------
def backfill_token(db, token: Token, sim: Sim, now_b: int):
    """48h of 5-minute snapshots for a newly discovered token (§33). The
    current bucket is excluded — it is written right after from LIVE data."""
    start = now_b - BACKFILL_HOURS * 3600
    price_rows, vol_rows, liq_rows, hold_rows, soc_rows = [], [], [], [], []
    for t in range(start, now_b, BUCKET):
        m = sim.bucket_market(t)
        c = sim.bucket_onchain(t)
        s = sim.bucket_social(t)
        price_rows.append(PriceSnapshot(token_id=token.id, ts=t, price_usd=m["price_usd"],
                                        mcap=m["mcap"], fdv=m["fdv"]))
        vol_rows.append(VolumeSnapshot(token_id=token.id, ts=t, volume=m["volume"],
                                       txns=m["txns"], buys=m["buys"], sells=m["sells"]))
        liq_rows.append(LiquiditySnapshot(token_id=token.id, ts=t, liquidity_usd=m["liquidity_usd"]))
        hold_rows.append(HolderSnapshot(token_id=token.id, ts=t, holders=c["holders"],
                                        top10_pct=c["top10_pct"], deployer_pct=c["deployer_pct"],
                                        new_wallets=c["new_wallets"], active_wallets=c["active_wallets"],
                                        whale_net_usd=c["whale_net_usd"]))
        soc_rows.append(SocialSnapshot(token_id=token.id, ts=t, mentions=s["mentions"],
                                       unique_accounts=s["unique_accounts"], engagement=s["engagement"],
                                       likes=s["likes"], reposts=s["reposts"], replies=s["replies"],
                                       sentiment=s["sentiment"], influencer_posts=s["influencer_posts"],
                                       low_quality_share=s["low_quality_share"]))
    db.add_all(price_rows); db.add_all(vol_rows); db.add_all(liq_rows)
    db.add_all(hold_rows); db.add_all(soc_rows)
    kws = [p for p in (token.description or "").split() if len(p) > 3][:3]
    for post in generate_posts(sim, token.symbol or "", kws, now_b):
        db.add(SocialPost(token_id=token.id, **post))
    for b in range(now_b - 72 * BUCKET, now_b, BUCKET):
        for ev in generate_whale_events(sim, b):
            db.add(WalletActivity(token_id=token.id, **ev))
    db.flush()


def write_live_bucket(db, token: Token, sim: Sim, tick: dict | None, now_b: int):
    """Current-bucket snapshot. Market metrics come from the REAL provider
    when available; social/on-chain from the (mock) sim providers."""
    for model in (PriceSnapshot, VolumeSnapshot, LiquiditySnapshot, HolderSnapshot, SocialSnapshot):
        db.execute(delete(model).where(model.token_id == token.id, model.ts == now_b))
    if tick:
        db.add(PriceSnapshot(token_id=token.id, ts=now_b, price_usd=tick["price_usd"],
                             mcap=tick["mcap"], fdv=tick["fdv"]))
        buys, sells = tick.get("txns_m5_buys", 0), tick.get("txns_m5_sells", 0)
        db.add(VolumeSnapshot(token_id=token.id, ts=now_b, volume=tick.get("volume_m5", 0.0),
                              txns=buys + sells, buys=buys, sells=sells))
        db.add(LiquiditySnapshot(token_id=token.id, ts=now_b,
                                 liquidity_usd=tick["liquidity_usd"]))
    else:
        m = sim.bucket_market(now_b)
        db.add(PriceSnapshot(token_id=token.id, ts=now_b, price_usd=m["price_usd"],
                             mcap=m["mcap"], fdv=m["fdv"]))
        db.add(VolumeSnapshot(token_id=token.id, ts=now_b, volume=m["volume"],
                              txns=m["txns"], buys=m["buys"], sells=m["sells"]))
        db.add(LiquiditySnapshot(token_id=token.id, ts=now_b, liquidity_usd=m["liquidity_usd"]))
    c = sim.bucket_onchain(now_b)
    db.add(HolderSnapshot(token_id=token.id, ts=now_b, holders=c["holders"],
                          top10_pct=c["top10_pct"], deployer_pct=c["deployer_pct"],
                          new_wallets=c["new_wallets"], active_wallets=c["active_wallets"],
                          whale_net_usd=c["whale_net_usd"]))
    s = sim.bucket_social(now_b)
    db.add(SocialSnapshot(token_id=token.id, ts=now_b, mentions=s["mentions"],
                          unique_accounts=s["unique_accounts"], engagement=s["engagement"],
                          likes=s["likes"], reposts=s["reposts"], replies=s["replies"],
                          sentiment=s["sentiment"], influencer_posts=s["influencer_posts"],
                          low_quality_share=s["low_quality_share"]))


def prune_old(db, now: int):
    horizon = now - 72 * 3600
    for model in (PriceSnapshot, VolumeSnapshot, LiquiditySnapshot, HolderSnapshot,
                  SocialSnapshot, TrendScore):
        db.execute(delete(model).where(model.ts < horizon))
    db.execute(delete(SocialPost).where(SocialPost.ts < now - 30 * 3600))


# --------------------------------------------------------------------------
def _latest_trends(db) -> dict[int, float]:
    sub = select(TrendScore.token_id, func.max(TrendScore.ts).label("mts")).group_by(TrendScore.token_id).subquery()
    rows = db.execute(
        select(TrendScore.token_id, TrendScore.trend)
        .join(sub, (TrendScore.token_id == sub.c.token_id) & (TrendScore.ts == sub.c.mts))
    ).all()
    return {tid: t for tid, t in rows}


EVENT_COOLDOWN_SECONDS = 1800  # per (token, kind, key) — keeps the feed signal-dense


def _record_signal_events(db, token_id: int, ts: int, mi: dict) -> None:
    """Persist notable market-intelligence transitions (v2 §15 signal history,
    §20 event alerts). Cooldown-deduped; only observed/derived events, never
    fabricated ones (§23)."""
    now = ts
    mom, sellp, ath, vacc, conf = (mi["momentum"], mi["sell_pressure"], mi["ath"],
                                   mi["volume_accel"], mi["confluence"])
    events = []
    if ath.get("new_ath"):
        events.append(("new_ath", "", f"📈 New tracked high {'(breakout)' if ath['status'] == 'breaking_out' else ''}".strip(),
                       f"Price touched tracked high at tick {ath['ath_ts']}",
                       "positive", {"price": ath.get("price"), "ath": ath.get("ath")}))
    if conf["signal"] == "emerging_momentum":
        events.append(("confluence_emerging", "", "🟢 Emerging momentum confluence",
                       "; ".join(conf["reasons"]), "positive", conf))
    if conf["signal"] == "potential_distribution":
        events.append(("confluence_distribution", "", "🔴 Potential distribution confluence",
                       "; ".join(conf["reasons"]), "danger", conf))
    for r in mi["reversals"]:
        events.append(("reversal", r["kind"], "⚠️ " + r["title"], r["detail"],
                       r["severity"], r))
    if vacc["abnormal"]:
        events.append(("volume_accel", "", "🚀 Abnormal volume acceleration",
                       vacc["detail"], "warning", vacc["h1"]))
    if mom["score"] >= 80:
        events.append(("momentum_cross", "80", f"⚡ Momentum crossed 80 ({mom['score']:.0f})",
                       mom["band_label"] + " — " + (mom["positive"][0] if mom["positive"] else ""),
                       "positive", {"momentum": mom["score"]}))
    for kind, key, title, detail, severity, metrics in events:
        recent = db.execute(
            select(SignalEvent.id).where(
                SignalEvent.token_id == token_id, SignalEvent.kind == kind,
                SignalEvent.key == key,
                SignalEvent.ts >= now - EVENT_COOLDOWN_SECONDS)).first()
        if recent:
            continue
        db.add(SignalEvent(token_id=token_id, ts=now, kind=kind, key=key,
                           title=title, detail=detail[:590], severity=severity,
                           metrics_json=metrics if isinstance(metrics, dict) else {}))


def _score_all(db, now_b: int) -> dict:
    tokens = db.execute(select(Token)).scalars().all()
    prev_trends = _latest_trends(db)

    # pass 1: contexts + narrative inputs
    ctxs: dict[int, TokenContext] = {}
    narr_metrics: dict[int, dict] = {}
    for t in tokens:
        ctx = TokenContext.build(db, t, now_b)
        ctxs[t.id] = ctx
        narr_metrics[t.id] = {
            "mentions_g_1h": ctx.d["mentions_g_1h"],
            "volume_g_1h": ctx.d["volume_g_1h"],
            "volume_z": ctx.anomalies.get("volume", 0.0),
            "trend": prev_trends.get(t.id, 0.0),
            "symbol": t.symbol,
        }

    news_cutoff = now_b - 86400
    news_flags: dict[str, bool] = {
        n: True for (n,) in db.execute(
            select(NewsArticle.narrative).where(NewsArticle.ts >= news_cutoff).distinct()
        ).all() if n
    }
    tokens_sorted = tokens  # stable
    per_token_narr = narratives_engine.recompute_narratives(db, tokens_sorted, narr_metrics, now_b, news_flags)
    db.flush()

    # maybe emit news for hot narratives (deterministic cadence per hour)
    hour_slot = now_b // 3600
    for narr in db.execute(select(Narrative).where(Narrative.score >= 50)).scalars().all():
        if sha_seed(f"{narr.name}:{hour_slot}") % 100 < 45:
            recent = db.execute(
                select(NewsArticle.id).where(NewsArticle.narrative == narr.name,
                                             NewsArticle.ts >= now_b - 3 * 3600).limit(1)
            ).first()
            if not recent:
                syms = narr_metrics and [m["symbol"] for m in narr_metrics.values()][:8] or []
                for art in generate_articles(narr.name, syms, now_b):
                    art = dict(art)
                    art["entities_json"] = art.pop("entities", [])
                    db.add(NewsArticle(**art))
                news_flags[narr.name] = True
    db.flush()
    # narratives recompute again with fresh news flags
    per_token_narr = narratives_engine.recompute_narratives(db, tokens_sorted, narr_metrics, now_b, news_flags)

    # market context from the tracked universe (explainable, no oracle needed)
    chgs = [ctxs[t.id].d["price_chg_24h"] for t in tokens]
    breadth = sum(1 for c in chgs if c > 0) / max(len(chgs), 1)
    top10_trends = sorted(prev_trends.values(), reverse=True)[:10]
    avg_top = sum(top10_trends) / len(top10_trends) if top10_trends else 50.0
    sector_momentum = max(0.0, min(100.0, 45 + (breadth - 0.5) * 60 + (avg_top - 45) * 0.4))
    mkt = {"breadth": breadth, "sector_momentum": sector_momentum}

    watched = {r[0] for r in db.execute(select(Watchlist.token_id)).all()}
    payloads = []
    new_emerging = 0
    for t in tokens:
        ctx = ctxs[t.id]
        ni = per_token_narr.get(t.id, {"best_score": 0.0, "count": 0,
                                       "cross_platform": False, "names": [], "narrative_ids": []})
        news_active = any(news_flags.get(n) for n in ni["names"])
        ctx.available["news"] = news_active
        sim = Sim.from_state(t.address, t.state_json or {})
        facts = sim.contract_facts()

        trend_payload = scoring_engine.calculate_trend_score(ctx, ni, mkt)
        fomo_payload = fomo_engine.calculate_fomo(ctx, news_active)
        risk_payload = risk_engine.calculate_risk(ctx, facts)
        signals = signals_engine.confirmation(ctx, news_active)
        conflicts = signals_engine.detect_conflicts(ctx, fomo_payload["fomo"])
        forensics = compute_forensics(db, ctx)
        age_hours = max(0.0, (time.time() * 1000 - (t.token_created_at or 0)) / 3.6e6) \
            if t.token_created_at else 999.0
        if t.token_created_at is None and t.first_seen_at:
            age_hours = min(999.0, (time.time() - t.first_seen_at.timestamp()) / 3600) if t.first_seen_at else 999.0
        mi = market_intel_engine.compute_market_intel(ctx, trend_payload["trend"], age_hours)

        prev = prev_trends.get(t.id)
        trend_delta_30m = (trend_payload["trend"] - prev) if prev is not None else 0.0
        emerging = signals_engine.is_emerging(ctx, trend_payload["trend"],
                                              fomo_payload["fomo"], age_hours, trend_delta_30m)
        new_emerging += int(emerging)

        # v2: persist notable market-intelligence events with per-key cooldowns
        _record_signal_events(db, t.id, now_b, mi)

        d = ctx.d
        d["price_chg_5m"] = tsx.level_growth(ctx.price, now_b, 300)[2]
        txns_24h_total = tsx.ssum(ctx.txns, now_b - 86400, now_b)
        summary = {
            "price": d["price_now"], "mcap": d["mcap_now"], "fdv": d["fdv_now"],
            "liquidity": d["liq_now"], "volume_24h": d["volume_24h"], "volume_1h": d["volume_1h"],
            "price_change_h1": d["price_chg_1h"], "price_change_h24": d["price_chg_24h"],
            "social_growth": d["mentions_g_1h"], "holder_growth": d["holders_g_1h"],
            "holders": d["holders_now"], "txns_1h": d["txns_1h"],
            "mentions_1h": d["mentions_1h"], "buy_share": d["buy_share_1h"],
            "narratives": ni["names"], "emerging": emerging,
            "anomalies": trend_payload["anomalies"],
            "confidence": signals["confidence"], "confirmed": signals["confirmed"],
            "fomo_class": fomo_payload["classification"], "risk_class": risk_payload["classification"],
            "conflicts": len(conflicts),
            "age_hours": age_hours,
            "organic": forensics["organic_score"],
            "regime": forensics["components"]["regime"]["regime"],
            # v2 market-intelligence headline fields (cards/screener)
            "momentum": mi["momentum"]["score"], "momentum_band": mi["momentum"]["band"],
            "sellp": mi["sell_pressure"]["score"], "sellp_band": mi["sell_pressure"]["band"],
            "liq_health": mi["liquidity_health"]["score"],
            "liq_band": mi["liquidity_health"]["band"],
            "ath_status": mi["ath"]["status"], "ath_distance": mi["ath"].get("distance_pct"),
            "vol_accel_h1": mi["volume_accel"]["h1"]["growth_pct"],
            "whale_class": mi["whales"]["classification"],
            "confluence": mi["confluence"]["signal"],
            "reversals": len(mi["reversals"]),
            "price_chg_5m": d["price_chg_5m"],
            "price_chg_15m": d["price_chg_15m"], "price_chg_4h": d.get("price_chg_4h", 0.0),
            "sells_1h": d["sells_1h"], "liq_chg_24h": d["liq_chg_24h"],
            "holders_g_24h": d["holders_g_24h"], "txns_24h": txns_24h_total,
        }
        components = {
            "trend": trend_payload["components"],
            "fomo": fomo_payload["components"],
            "risk": risk_payload["components"],
            "fomo_warning": fomo_payload.get("warning"),
            "risk_flags": risk_payload["flags"],
            "signals": signals, "conflicts": conflicts,
            "forensics": forensics,
            "market_intel": mi,
            "summary": summary,
            "sources": ctx.sources,
        }
        db.execute(delete(TrendScore).where(TrendScore.token_id == t.id, TrendScore.ts == now_b))
        db.add(TrendScore(token_id=t.id, ts=now_b, trend=trend_payload["trend"],
                          fomo=fomo_payload["fomo"], fomo_class=fomo_payload["classification"],
                          risk=risk_payload["risk"], risk_class=risk_payload["classification"],
                          confidence=signals["confidence"], emerging=emerging,
                          components_json=components))

        # risk events with 6h dedupe per (token, category)
        for ev in risk_payload["events"]:
            if ev["severity"] == "info" and not trend_payload["anomalies"]:
                continue
            dup = db.execute(
                select(RiskEvent.id).where(RiskEvent.token_id == t.id,
                                           RiskEvent.category == ev["category"],
                                           RiskEvent.ts >= now_b - 21600).limit(1)
            ).first()
            if not dup:
                db.add(RiskEvent(token_id=t.id, ts=now_b, **ev))

        payloads.append({
            "token_id": t.id, "address": t.address, "symbol": t.symbol or "?",
            "trend": trend_payload["trend"], "fomo": fomo_payload["fomo"],
            "risk": risk_payload["risk"], "risk_flags": risk_payload["flags"],
            "d": d, "mi": mi, "watched": t.id in watched,
        })

    # content generators for active tokens
    for t in tokens:
        sim = Sim.from_state(t.address, t.state_json or {})
        post = live_post(sim, t.symbol or "", (t.description or "").split()[:2] or [], now_b)
        if post:
            db.add(SocialPost(token_id=t.id, **post))
        for ev in generate_whale_events(sim, now_b):
            db.add(WalletActivity(token_id=t.id, **ev))

    created_alerts = alerts_engine.evaluate_alerts(db, payloads, now_b, new_narratives=[])
    labels = backtest_engine.label_due(db, now_b)
    db.flush()
    return {"tokens": len(tokens), "emerging": new_emerging, "alerts_fired": created_alerts,
            "labels": labels, "breadth": round(breadth, 3)}


# --------------------------------------------------------------------------
async def tick() -> dict:
    t0 = time.time()
    raws, ticks, fallback = await discover_tokens()
    now_b = bucket_of(time.time())
    stats: dict = {"tokens": 0}
    with SessionLocal() as db:
        try:
            # upsert tokens + build sims
            new_count = 0
            for raw in raws:
                mt = ticks.get(raw.address)
                if not mt:
                    continue
                token = db.execute(select(Token).where(Token.address == raw.address)).scalar_one_or_none()
                if token is None:
                    token = Token(address=raw.address, chain=raw.chain)
                    db.add(token)
                    db.flush()
                    sim = Sim.fresh(
                        raw.address, price_now=mt["price_usd"], mcap_now=mt["mcap"],
                        liq_now=mt["liquidity_usd"], vol_h1=mt["volume_h1"],
                        vol_h24=mt["volume_h24"], pc_h1=mt["pc_h1"], pc_h6=mt["pc_h6"],
                        pc_h24=mt["pc_h24"], ref_bucket=now_b)
                    token.state_json = sim.to_state()
                    token.archetype = sim.archetype
                    backfill_token(db, token, sim, now_b)
                    new_count += 1
                # refresh mutable metadata
                for fld in ("chain",):
                    setattr(token, fld, getattr(raw, fld) or getattr(token, fld))
                token.name = (raw.name or token.name or raw.symbol or "?")[:160]
                token.symbol = (raw.symbol or token.symbol or "?")[:32]
                token.image_url = raw.image_url or token.image_url
                token.description = raw.description or token.description
                token.dex = raw.dex or token.dex or ""
                token.pair_address = raw.pair_address or token.pair_address or ""
                token.url = raw.url or token.url or ""
                token.token_created_at = raw.token_created_at or token.token_created_at
                if raw.pair_address:
                    pair = db.execute(select(TokenPair).where(
                        TokenPair.pair_address == raw.pair_address)).scalar_one_or_none()
                    if pair is None:
                        db.add(TokenPair(token_id=token.id, dex=raw.dex or "",
                                         pair_address=raw.pair_address,
                                         liquidity_usd=mt.get("pair_liquidity_usd", 0.0)))
                    else:
                        pair.token_id = token.id
                        pair.liquidity_usd = mt.get("pair_liquidity_usd", 0.0)
            db.flush()

            # live bucket for every tracked token
            tokens = db.execute(select(Token).order_by(Token.id)).scalars().all()
            for token in tokens:
                sim = Sim.from_state(token.address, token.state_json or {})
                write_live_bucket(db, token, sim, ticks.get(token.address), now_b)
            db.flush()

            stats = _score_all(db, now_b)
            stats["new_tokens"] = new_count
            prune_old(db, now_b)

            upsert_source(db, "dexscreener", "market", "mock" if fallback else "ok",
                          {"mode": "fallback-sim" if fallback else "live",
                           "note": "real market data" if not fallback else
                                   "unreachable — simulated market universe"})
            upsert_source(db, "x-social-sim", "social", "mock",
                          {"note": "MOCK — replace with X API provider in Phase 2"})
            upsert_source(db, "solana-chain-sim", "onchain", "mock",
                          {"note": "MOCK — replace with RPC/indexer in Phase 3"})
            upsert_source(db, "news-sim", "news", "mock",
                          {"note": "MOCK — replace with news API in Phase 2"})
            log_line(db, "info", "ingestion",
                     f"tick ok: {stats.get('tokens')} tokens, {new_count} new, "
                     f"{stats.get('emerging')} emerging, {stats.get('alerts_fired')} alerts, "
                     f"{time.time() - t0:.1f}s")
            db.commit()
        except Exception as exc:  # pipeline must survive a bad tick
            db.rollback()
            log.error("tick failed: %s\n%s", exc, traceback.format_exc())
            with SessionLocal() as db2:
                log_line(db2, "error", "ingestion", f"tick failed: {exc}")
                db2.commit()
            stats = {"error": str(exc)}
    stats["seconds"] = round(time.time() - t0, 2)
    return stats
