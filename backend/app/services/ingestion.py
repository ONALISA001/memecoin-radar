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

from ..config import (DEMO_FALLBACK, HELIUS_MAX_CHECKS_PER_TICK, HELIUS_RECHECK_HOURS, SOLANA_ONLY,
                      MAX_TRACKED_TOKENS, NEW_POOLS_ENABLED, NEW_POOL_MAX_AGE_H,
                      NEWS_REFRESH_MINUTES, NEWS_RSS_ENABLED, USE_SIMULATED_SOCIAL,
                      TELEGRAM_CALL_CHANNELS, TELEGRAM_DISCOVERY_SLOTS, TELEGRAM_REFRESH_MINUTES,
                      NEW_POOL_MIN_LIQUIDITY_USD, NEW_POOL_MIN_TXNS_H1, NEW_POOL_SLOTS,
                      ANOMALY_Z, BACKFILL_HOURS, DEXSCREENER_ENABLED,
                      SNAPSHOT_BUCKET_SECONDS as BUCKET)
from ..database import SessionLocal
from ..engine import alerts as alerts_engine
from ..engine import backtest as backtest_engine
from ..engine import narratives as narratives_engine
from ..engine import risk as risk_engine
from ..engine import scoring as scoring_engine
from ..engine import signals as signals_engine
from ..engine import fomo as fomo_engine
from ..engine import intel as intel_engine
from ..engine import market_intel as market_intel_engine
from ..engine import timeseries as tsx
from ..engine.context import TokenContext
from ..engine.forensics import compute_forensics
from ..models import (Alert, DataSource, HolderSnapshot, LiquiditySnapshot,
                      Narrative, NewsArticle, PriceSnapshot, RiskEvent,
                      SocialPost, SocialSnapshot, SystemLog, Token, TokenPair,
                      TrendScore, VolumeSnapshot, WalletActivity, Watchlist,
                      SignalEvent)
from ..paper import engine as paper_engine
from ..providers.base import HEALTH, RawToken
from ..providers import helius as helius_mod
from ..providers import jupiter as jup_mod
from .holders import real_holder_stats
from .contract_facts import is_mock, latest_contract_facts, unverified_facts
from .launch_alerts import evaluate_launches, send_launch_alerts
from . import creator_profiles as creator_svc
from . import launch_forensics as lf_svc
from . import risk_alerts as risk_alerts_svc
from . import ai_verdict as verdict_svc
from . import outcomes as outcomes_svc
from . import onchain_real
from . import telegram_feed as tg_feed
from ..providers import telegram_public as tg_public
from ..providers.dexscreener import DexscreenerProvider
from ..providers.geckoterminal import GeckoTerminalProvider
from ..providers import news_rss
from . import news as news_svc
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
    if status in ("ok", "mock"):          # keep the last time it actually worked
        src.last_ok_at = func.now()


# --------------------------------------------------------------------------
OUTAGE: dict = {"since": None, "error": None}     # DexScreener outage state (process-local)


def _outage(msg: str):
    """No real market data: demo universe only if DEMO_FALLBACK, else nothing (pause)."""
    if DEMO_FALLBACK:
        return _fallback_universe(), _fallback_ticks(), True
    OUTAGE["since"] = OUTAGE["since"] or int(time.time())
    OUTAGE["error"] = msg[:200]
    return [], {}, False


async def discover_tokens() -> tuple[list[RawToken], dict[str, dict], bool]:
    """Returns (raw tokens, live market ticks, used_fallback?). Empty lists = outage."""
    if not DEXSCREENER_ENABLED:
        return _outage("DexScreener disabled (DEXSCREENER_ENABLED=0)")
    provider = DexscreenerProvider()
    try:
        promo = await provider.discover()                   # boosted / profiled (paid)
        fresh, gt_ticks = [], {}
        if NEW_POOLS_ENABLED:
            pools = await GeckoTerminalProvider().new_pools()
            fresh = [r for r, _ in pools]
            gt_ticks = {r.address: mt for r, mt in pools}
            seen = {r.address for r in fresh}
            fresh += [r for r in _sticky_new_pool_tokens() if r.address not in seen]
        fresh_addrs = {r.address for r in fresh}
        promo = [r for r in promo if r.address not in fresh_addrs]
        ticks_obj = await provider.fetch_market(promo + fresh)
        for addr, mt in gt_ticks.items():                   # not on DexScreener yet
            ticks_obj.setdefault(addr, mt)
        fresh = select_new_pool_tokens(fresh, ticks_obj)
        called = await _telegram_called(provider, {r.address for r in fresh} | {r.address for r in promo}, ticks_obj)
        promo = [r for r in promo if r.address in ticks_obj][:max(0, MAX_TRACKED_TOKENS - len(fresh) - len(called))]
        raws = fresh + called + promo
        ticks = {r.address: ticks_obj[r.address].__dict__ for r in raws}
        if not raws:
            raise RuntimeError("no tokens with data")
        HEALTH.report("dexscreener", "market", True,
                      f"{len(raws)} tracked ({len(fresh)} fresh pools, {len(promo)} promoted)")
        OUTAGE["since"], OUTAGE["error"] = None, None
        return raws, ticks, False
    except Exception as exc:
        HEALTH.report("dexscreener", "market", False, str(exc)[:160])
        log_line_db_only("warn", "ingestion",
                         f"DexScreener unavailable — {'demo universe' if DEMO_FALLBACK else 'pausing on last real data'}: {exc}")
        return _outage(f"{type(exc).__name__}: {exc}")


async def _telegram_called(provider, already: set, ticks_obj: dict) -> list[RawToken]:
    """Coins called in the watched Telegram channels (last 30 min) that aren't tracked yet.
    Fills ticks_obj with their real DexScreener data; coins without data are dropped."""
    if not tg_public.clean_channels(TELEGRAM_CALL_CHANNELS) or TELEGRAM_DISCOVERY_SLOTS <= 0:
        return []
    try:        # a Telegram problem must never take down market data
        with SessionLocal() as db:
            calls = [m for m, _ in tg_feed.recent_calls(db, int(time.time()))]
        # name/symbol are filled in from DexScreener by fetch_market
        cands = [RawToken(address=m, chain="solana", name="", symbol="", extra={"source": "telegram"})
                 for m in calls if m not in already][:TELEGRAM_DISCOVERY_SLOTS * 2]
        if not cands:
            return []
        found = await provider.fetch_market(cands)
        ticks_obj.update(found)
        return [r for r in cands if r.address in found][:TELEGRAM_DISCOVERY_SLOTS]
    except Exception as exc:
        log.warning("telegram discovery skipped: %s", exc)
        return []


def _passes_new_pool_floor(mt) -> bool:
    return (mt.liquidity_usd >= NEW_POOL_MIN_LIQUIDITY_USD
            and mt.txns_h1_buys + mt.txns_h1_sells >= NEW_POOL_MIN_TXNS_H1)


def select_new_pool_tokens(cands: list[RawToken], ticks: dict) -> list[RawToken]:
    """Fresh-pool candidates that still have live data and activity, most traded
    (1h transactions) first, capped at NEW_POOL_SLOTS. Dead launches drop out."""
    keep = [r for r in cands if r.address in ticks and _passes_new_pool_floor(ticks[r.address])]
    keep.sort(key=lambda r: -(ticks[r.address].txns_h1_buys + ticks[r.address].txns_h1_sells))
    for r in keep:
        r.extra["source"] = "new_pool"
    return keep[:NEW_POOL_SLOTS]


def _sticky_new_pool_tokens(now_ms: int | None = None) -> list[RawToken]:
    """Tokens first found as fresh pools stay candidates for NEW_POOL_MAX_AGE_H
    after launch, even once they scroll off GeckoTerminal's new-pools feed."""
    now_ms = now_ms or int(time.time() * 1000)
    cutoff = now_ms - NEW_POOL_MAX_AGE_H * 3.6e6
    with SessionLocal() as db:
        rows = db.execute(select(Token).where(Token.discovery_source == "new_pool",
                                              Token.token_created_at >= cutoff)).scalars().all()
        return [RawToken(address=t.address, chain=t.chain or "solana", name=t.name or "",
                         symbol=t.symbol or "", image_url=t.image_url or "", url=t.url or "",
                         dex=t.dex or "", pair_address=t.pair_address or "",
                         token_created_at=t.token_created_at, extra={"source": "new_pool"})
                for t in rows]


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
        if SOLANA_ONLY and chain != "solana":
            continue
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
        if SOLANA_ONLY and chain != "solana":
            continue
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
    """48h of SIMULATED 5-minute snapshots (§33) — demo/fallback (Mock*) tokens
    only. Real tokens are never backfilled with invented history."""
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


def write_live_bucket(db, token: Token, sim: Sim, tick: dict | None, now_b: int,
                      onchain: HolderSnapshot | None = None):
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
    if onchain is not None:                       # REAL (Helius + GeckoTerminal)
        db.add(onchain)
    else:                                         # demo/fallback universe only
        c = sim.bucket_onchain(now_b)
        db.add(HolderSnapshot(token_id=token.id, ts=now_b, holders=c["holders"],
                              top10_pct=c["top10_pct"], deployer_pct=c["deployer_pct"],
                              new_wallets=c["new_wallets"], active_wallets=c["active_wallets"],
                              whale_net_usd=c["whale_net_usd"]))
    if onchain is not None and not USE_SIMULATED_SOCIAL:
        return              # real coin: no simulated social rows at all
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


def _latest_summaries(db) -> dict[int, dict]:
    """Latest TrendScore summary blob per token — used as `prev_mi` source
    for momentum/sell-pressure deltas in the v3 event engine."""
    sub = select(TrendScore.token_id, func.max(TrendScore.ts).label("mts")).group_by(TrendScore.token_id).subquery()
    rows = db.execute(
        select(TrendScore.token_id, TrendScore.components_json)
        .join(sub, (TrendScore.token_id == sub.c.token_id) & (TrendScore.ts == sub.c.mts))
    ).all()
    out: dict[int, dict] = {}
    for tid, comp in rows:
        summ = (comp or {}).get("summary") or {}
        out[tid] = summ
    return out


def _score_all(db, now_b: int, live: dict | None = None) -> dict:
    """`live` = {address: real market tick}. When given (real-data mode) only tokens
    that have a live tick this round are scored — dropped / filtered-out tokens
    stop receiving scores instead of drifting on simulated data."""
    tokens = db.execute(select(Token)).scalars().all()
    if live is not None:
        tokens = [t for t in tokens if t.address in live]
    prev_trends = _latest_trends(db)
    prev_summaries = _latest_summaries(db)

    # pass 1: contexts + narrative inputs
    ctxs: dict[int, TokenContext] = {}
    narr_metrics: dict[int, dict] = {}
    holder_rates = real_holder_stats(db, [t.id for t in tokens if not is_mock(t)])
    for t in tokens:
        ctx = TokenContext.build(db, t, now_b)
        if not is_mock(t):
            onchain_real.apply_real_holder_growth(ctx, holder_rates.get(t.id))
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

    # maybe emit SIMULATED news for hot narratives — only when real RSS news is off
    hour_slot = now_b // 3600
    sim_narrs = [] if NEWS_RSS_ENABLED else \
        db.execute(select(Narrative).where(Narrative.score >= 50)).scalars().all()
    for narr in sim_narrs:
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

    real_holders = real_holder_stats(db, [t.id for t in tokens])
    contract_facts = latest_contract_facts(db, tokens)
    tg_on = bool(tg_public.clean_channels(TELEGRAM_CALL_CHANNELS))
    tg_stats = tg_feed.coin_stats(db, [t.address for t in tokens], now_b) if tg_on else {}
    watched = {r[0] for r in db.execute(select(Watchlist.token_id)).all()}
    payloads = []
    new_emerging = 0
    chain_candidates: list[int] = []
    for t in tokens:
        ctx = ctxs[t.id]
        ni = per_token_narr.get(t.id, {"best_score": 0.0, "count": 0,
                                       "cross_platform": False, "names": [], "narrative_ids": []})
        news_active = any(news_flags.get(n) for n in ni["names"])
        ctx.available["news"] = news_active
        facts = contract_facts.get(t.id) or unverified_facts()   # REAL Helius facts
        ctx.telegram = tg_stats.get(t.address, {}) if tg_on else None    # None = feed not configured
        lv0 = (live or {}).get(t.address) or {}
        if lv0.get("volume_h24") is not None and ctx.d.get("liq_now"):
            # live DexScreener 24h volume (recorded history only covers the time we've tracked)
            ctx.d["vol24_liq_live"] = lv0["volume_h24"] / ctx.d["liq_now"]

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

        # v3: enriched intelligence events (categories, importance tiers,
        # confidence, checklists, concerns) with cooldown + escalation dedupe (§43)
        prev_sum = prev_summaries.get(t.id) or {}
        prev_mi = {"momentum": prev_sum.get("momentum"), "sell_pressure": prev_sum.get("sellp")}
        try:
            evs = intel_engine.detect_events(ctx, mi, t, age_hours, prev_mi,
                                             risk_payload["risk"], risk_payload["flags"])
            if evs:
                written = intel_engine.record_events(db, t.id, now_b, evs)
                if any(e["kind"] == "new_token" for e in evs):
                    t.new_token_evented = True
                if written:
                    chain_candidates.append(t.id)
        except Exception as exc:
            log.warning("intel detect failed for %s: %s", t.symbol, exc)

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
            "volume_g_1h": d.get("volume_g_1h"),     # paper momentum strategies (live + backtest)
            "whale_class": mi["whales"]["classification"],
            "confluence": mi["confluence"]["signal"],
            "reversals": len(mi["reversals"]),
            "price_chg_5m": d["price_chg_5m"],
            "price_chg_15m": d["price_chg_15m"], "price_chg_4h": d.get("price_chg_4h", 0.0),
            "sells_1h": d["sells_1h"], "liq_chg_24h": d["liq_chg_24h"],
            "holders_g_24h": d["holders_g_24h"], "txns_24h": txns_24h_total,
        }
        # REAL holders (Helius) replace the simulated numbers: unknown stays None, never faked
        rh = real_holders.get(t.id)
        summary.update({
            "holders": rh["count"] if rh else None,
            "holders_capped": bool(rh and rh["capped"]),
            "holder_growth": rh["growth_pct"] if rh else None,
            "holders_g_24h": rh["growth_pct"] if rh else None,
        })
        lv = (live or {}).get(t.address)
        if lv:
            # REAL DexScreener aggregates override the bucket sums (which are built on a
            # simulated 48h backfill and therefore give placeholder-looking trade counts).
            b1, s1 = lv.get("txns_h1_buys", 0), lv.get("txns_h1_sells", 0)
            b24, s24 = lv.get("txns_h24_buys", 0), lv.get("txns_h24_sells", 0)
            summary.update({
                "txns_1h": b1 + s1, "buys_1h": b1, "sells_1h": s1,
                "txns_24h": b24 + s24,
                "buy_share": (b1 / (b1 + s1)) if (b1 + s1) else None,
                "volume_1h": lv.get("volume_h1", 0.0), "volume_24h": lv.get("volume_h24", 0.0),
                "price_change_h1": lv.get("pc_h1", 0.0), "price_change_h24": lv.get("pc_h24", 0.0),
                "price_chg_5m": lv.get("pc_m5", 0.0), "real_market": True,
            })
        components = {
            "trend": trend_payload["components"],
            "fomo": fomo_payload["components"],
            "risk": risk_payload["components"],
            "fomo_warning": fomo_payload.get("warning"),
            "risk_flags": risk_payload["flags"],
            "risk_hard_fails": risk_payload.get("hard_fails", []),
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
            "live": lv or {},          # real DexScreener aggregates (paper strategy rules)
        })

    # content generators for active tokens
    for t in tokens:
        sim = Sim.from_state(t.address, t.state_json or {})
        post = live_post(sim, t.symbol or "", (t.description or "").split()[:2] or [], now_b) \
            if (is_mock(t) or USE_SIMULATED_SOCIAL) else None      # no fake posts for real coins
        if post:
            db.add(SocialPost(token_id=t.id, **post))
        if is_mock(t):     # simulated whale trades only for the demo universe
            for ev in generate_whale_events(sim, now_b):
                db.add(WalletActivity(token_id=t.id, **ev))

    # v3 §16: signal-chain detection over the last window for tokens that
    # produced fresh events this tick
    payloads_map = {p["token_id"]: p["mi"] for p in payloads}
    if chain_candidates:
        db.flush()
        for tid in chain_candidates:
            try:
                chain_evs = intel_engine.detect_chains(db, tid, now_b,
                                                       payloads_map.get(tid) or {})
                if chain_evs:
                    intel_engine.record_events(db, tid, now_b, chain_evs)
            except Exception as exc:
                log.warning("chain detect failed for token %s: %s", tid, exc)
        db.flush()

    created_alerts = alerts_engine.evaluate_alerts(db, payloads, now_b, new_narratives=[])
    labels = backtest_engine.label_due(db, now_b)
    # v3 §49: forward-return labeling of past intelligence events
    ev_labeled = 0
    try:
        ev_labeled = intel_engine.label_event_outcomes(db, now_b)
    except Exception as exc:
        log.warning("event outcome labeling failed: %s", exc)
    # v4: paper-trading engine — consumes this tick's intel events through the
    # risk layer; idempotent per account cursor (§31)
    paper_stats: dict = {}
    try:
        market: dict[int, dict] = {}
        for p in payloads:
            d, mi, lv = p["d"], p["mi"], p.get("live") or {}
            b1, s1 = lv.get("txns_h1_buys", 0) or 0, lv.get("txns_h1_sells", 0) or 0
            liq = d.get("liq_now") or 0
            conf_sig = (mi.get("confluence") or {}).get("signal")
            market[p["token_id"]] = {
                "price": d.get("price_now"), "liq": d.get("liq_now"), "mcap": d.get("mcap_now"),
                "volume_24h": lv.get("volume_h24", d.get("volume_24h")),
                "momentum": (mi.get("momentum") or {}).get("score"),
                # live DexScreener figure when available; recorded history needs time to build
                "price_chg_1h": lv.get("pc_h1", d.get("price_chg_1h")),
                "sellp": (mi.get("sell_pressure") or {}).get("score"),
                "liq_chg_3h": d.get("liq_chg_3h"), "risk": p.get("risk"),
                # strategy-lab filters / pullback entries
                # units: percent; 24h volume ÷ liquidity
                "buy_share_pct": (100.0 * b1 / (b1 + s1)) if (b1 + s1) else
                                 100.0 * (d.get("buy_share_1h") or 0.0),
                "vol_liq_ratio": ((lv.get("volume_h24") or d.get("volume_24h") or 0.0) / liq) if liq > 0 else None,
                "price_chg_15m": d.get("price_chg_15m"),
                "volume_g_1h": d.get("volume_g_1h"),
                "price_chg_24h": lv.get("pc_h24", d.get("price_chg_24h")),
                "regime": {"emerging_momentum": "BULLISH",
                           "potential_distribution": "DISTRIBUTION"}.get(conf_sig, "NEUTRAL"),
            }
        paper_stats = paper_engine.run_tick(db, now_b, market)
    except Exception as exc:
        log.warning("paper engine tick failed (non-fatal): %s", exc)
    db.flush()
    return {"tokens": len(tokens), "emerging": new_emerging, "alerts_fired": created_alerts,
            "labels": labels, "event_labels": ev_labeled, "breadth": round(breadth, 3),
            "paper": paper_stats}


# --------------------------------------------------------------------------
async def onchain_checks() -> dict:
    """Quota-guarded REAL on-chain risk checks via Helius (no-op without a key).
    Checks never-checked tokens first, then the stalest; max N per tick."""
    import datetime as _dt
    from ..models import TokenRiskCheck
    if not helius_mod.enabled():
        with SessionLocal() as db:
            upsert_source(db, "helius", "onchain", "down",
                          {"note": "not configured — set HELIUS_API_KEY for real on-chain risk checks"})
            db.commit()
        return {"helius": "disabled"}
    now = _dt.datetime.utcnow()
    with SessionLocal() as db:
        toks = db.execute(select(Token).where(Token.chain == "solana")).scalars().all()
        todo = []
        for t in toks:
            if t.address.startswith("Mock"):
                continue
            last = db.execute(select(TokenRiskCheck).where(TokenRiskCheck.token_id == t.id)
                              .order_by(TokenRiskCheck.checked_at.desc()).limit(1)).scalar_one_or_none()
            if last is None:            # never checked => always first (fresh launches)
                todo.append((-1e9, t.id, t.address)); continue
            age_h = (now - last.checked_at.replace(tzinfo=None)).total_seconds() / 3600
            # failed checks, and old checks that predate real holder counts or real-wallet
            # concentration, retry hourly
            complete = (last.ok and last.holders_count is not None
                        and last.top10_wallet_pct is not None
                        # checks made before the extension / exit analysts existed
                        and (not last.token_2022 or last.token_extensions is not None)
                        and (last.exit_sol_in is not None or not jup_mod.enabled()))
            wait = HELIUS_RECHECK_HOURS if complete else 1.0
            if age_h >= wait:
                todo.append((-age_h, t.id, t.address))
        todo.sort()
        batch = todo[:HELIUS_MAX_CHECKS_PER_TICK]
    prov = helius_mod.HeliusProvider()
    jup = jup_mod.JupiterProvider() if jup_mod.enabled() else None
    done = failed = 0
    exit_ok = exit_failed = 0
    for _, tid, addr in batch:
        r = await prov.check_token(addr)
        ex = None
        if jup is not None:
            try:            # the exit check must never break the Helius check
                ex = await jup.check_exit(addr)
            except Exception as exc:     # pragma: no cover - check_exit already catches
                ex = jup_mod.ExitCheck(ok=False, error=f"{type(exc).__name__}"[:200])
            exit_ok += 1 if ex.ok else 0
            exit_failed += 0 if ex.ok else 1
        with SessionLocal() as db:
            db.add(TokenRiskCheck(
                token_id=tid, ok=r.ok, error=r.error[:200], mint_authority=r.mint_authority,
                freeze_authority=r.freeze_authority, supply=r.supply, decimals=r.decimals,
                top1_pct=r.top1_pct, top10_pct=r.top10_pct, holders_sampled=r.holders_sampled,
                token_2022=r.token_2022, holders_count=r.holders_count,
                holders_capped=r.holders_capped, top1_wallet_pct=r.top1_wallet_pct,
                top10_wallet_pct=r.top10_wallet_pct, excluded_pct=r.excluded_pct,
                token_extensions=r.token_extensions if r.ok else None,
                transfer_fee_bps=r.transfer_fee_bps, transfer_fee_mutable=r.transfer_fee_mutable,
                exit_sol_in=ex.sol_in if ex else None,
                exit_buy_route=ex.buy_route if ex and ex.ok else None,
                exit_sell_route=ex.sell_route if ex and ex.ok else None,
                exit_round_trip_loss_pct=ex.round_trip_loss_pct if ex and ex.ok else None,
                exit_route=(ex.route or None) if ex else None,
                exit_error=(ex.error or None) if ex else None))
            db.commit()
        done += 1 if r.ok else 0
        failed += 0 if r.ok else 1
    # Launch forensics (bundles/snipers/funders) BEFORE launch alerts are decided,
    # so a fresh launch is judged with its wallet-level evidence.
    try:
        lfx = await lf_svc.run_launch_forensics(SessionLocal)
    except Exception as exc:          # never let forensics break the on-chain tick
        lfx = {"error": type(exc).__name__}
    if lfx.get("analysed"):
        with SessionLocal() as db:
            upsert_source(db, "launch-forensics", "onchain", "ok" if lfx.get("ok") else "degraded",
                          {"note": "wallet-level launch analysis: bundles, snipers, shared funders",
                           "last_batch": lfx})
            db.commit()
    # Creator track record (earlier launches of the creators forensics found)
    try:
        crx = await creator_svc.run_creator_profiles(SessionLocal)
    except Exception as exc:          # never let it break the on-chain tick
        crx = {"error": type(exc).__name__}
    if crx.get("scored"):
        with SessionLocal() as db:
            upsert_source(db, "creator-track-record", "onchain", "ok" if crx.get("ok") else "degraded",
                          {"note": "creator's earlier launches: dumps at open, graduations, peaks "
                                   "(GMGN dev-score model)", "last_batch": crx})
            db.commit()
    with SessionLocal() as db:
        db.execute(delete(TokenRiskCheck).where(
            TokenRiskCheck.checked_at < now - _dt.timedelta(days=7)))   # retention
        launch = evaluate_launches(db)
        status = "ok" if (done or not failed) else "degraded"
        upsert_source(db, "helius", "onchain", status,
                      {"note": "real on-chain risk checks (mint/freeze authority, holder concentration)",
                       "last_batch": {"ok": done, "failed": failed, "pending": max(0, len(todo) - len(batch))}})
        if jup is not None:
            upsert_source(db, "jupiter", "onchain", "ok" if (exit_ok or not exit_failed) else "degraded",
                          {"note": "exit check: round-trip buy/sell quote (no wallet, no transaction)",
                           "last_batch": {"ok": exit_ok, "failed": exit_failed}})
        db.commit()
    if launch.get("to_send"):
        launch["sent"] = await asyncio.to_thread(send_launch_alerts, launch.pop("to_send"))
    return {"helius": {"ok": done, "failed": failed},
            "jupiter": {"ok": exit_ok, "failed": exit_failed} if jup is not None else "disabled",
            "launch_forensics": lfx, "creator_profiles": crx, "launch_alerts": launch}


async def tick() -> dict:
    t0 = time.time()
    tg_channels = tg_public.clean_channels(TELEGRAM_CALL_CHANNELS)
    if tg_channels and tg_feed.SCHEDULE.due(TELEGRAM_REFRESH_MINUTES):
        tg_feed.SCHEDULE.mark()
        try:    # never let Telegram break the market tick
            posts, tg_status = await tg_public.fetch_channels(tg_channels[:25])
            with SessionLocal() as db:
                res = tg_feed.store_posts(db, posts, int(time.time()))
                ok = sum(1 for v in tg_status.values() if not isinstance(v, str))
                upsert_source(db, "telegram-public", "social", "ok" if ok else "down",
                              {"note": "public call channels via t.me/s preview (real)",
                               "channels": tg_status, "last_batch": res})
                db.commit()
        except Exception as exc:
            log.warning("telegram feed failed (non-fatal): %s", exc)
    raws, ticks, fallback = await discover_tokens()
    now_b = bucket_of(time.time())
    stats: dict = {"tokens": 0}
    if not raws and not fallback:
        # OUTAGE: nothing is invented. Last real data stays on screen (marked stale);
        # no new scores, alerts or paper trades until real market data is back.
        with SessionLocal() as db:
            upsert_source(db, "dexscreener", "market", "down", {
                "mode": "paused", "since": OUTAGE["since"], "error": OUTAGE["error"],
                "note": "DexScreener unreachable — showing the last real data; scoring, alerts "
                        "and paper trading are paused until it is back"})
            db.commit()
        return {"outage": True, "since": OUTAGE["since"], "error": OUTAGE["error"],
                "elapsed_s": round(time.time() - t0, 2)}
    if not fallback and ticks:                    # REAL unique buyers/sellers per pool
        pair_of = {r.address: r.pair_address for r in raws if r.pair_address}
        if pair_of:
            act = await GeckoTerminalProvider().pool_activity(list(pair_of.values()))
            for addr, pair in pair_of.items():
                if pair in act and addr in ticks:
                    ticks[addr].update(act[pair])
    news_articles, news_status = None, None
    if NEWS_RSS_ENABLED and news_svc.SCHEDULE.due(NEWS_REFRESH_MINUTES):
        news_articles, news_status = await news_rss.fetch_news()
        news_svc.SCHEDULE.mark()
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
                    token = Token(address=raw.address, chain=raw.chain,
                                  discovery_source=(raw.extra or {}).get("source"))
                    db.add(token)
                    db.flush()
                    sim = Sim.fresh(
                        raw.address, price_now=mt["price_usd"], mcap_now=mt["mcap"],
                        liq_now=mt["liquidity_usd"], vol_h1=mt["volume_h1"],
                        vol_h24=mt["volume_h24"], pc_h1=mt["pc_h1"], pc_h6=mt["pc_h6"],
                        pc_h24=mt["pc_h24"], ref_bucket=now_b)
                    token.state_json = sim.to_state()
                    token.archetype = sim.archetype
                    # No invented history for real tokens: their windows fill from live
                    # ticks. Only the demo/fallback (Mock*) universe gets a simulated backfill.
                    if is_mock(token):
                        backfill_token(db, token, sim, now_b)
                    new_count += 1
                # refresh mutable metadata
                for fld in ("chain",):
                    setattr(token, fld, getattr(raw, fld) or getattr(token, fld))
                token.name = (raw.name or token.name or raw.symbol or "?")[:160]
                token.symbol = (raw.symbol or token.symbol or "?")[:32]
                token.image_url = (raw.image_url or token.image_url or "")[:512] or None
                token.description = raw.description or token.description
                token.dex = (raw.dex or token.dex or "")[:64]
                token.pair_address = (raw.pair_address or token.pair_address or "")[:128]
                token.url = (raw.url or token.url or "")[:512]
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
            q = select(Token).order_by(Token.id)
            if SOLANA_ONLY:
                q = q.where(Token.chain == "solana")
            tokens = db.execute(q).scalars().all()
            real_ids = [t.id for t in tokens if not is_mock(t) and t.address in ticks]
            w_checks = onchain_real.wallet_checks(db, real_ids)
            h_stats = real_holder_stats(db, real_ids)
            for token in tokens:
                tk = ticks.get(token.address)
                if tk is None and not fallback:
                    continue        # no live data this round -> never fabricate a bucket
                sim = Sim.from_state(token.address, token.state_json or {})
                real_row = None if is_mock(token) or tk is None else onchain_real.real_holder_row(
                    token, tk, w_checks.get(token.id, []), h_stats.get(token.id), now_b)
                write_live_bucket(db, token, sim, tk, now_b, real_row)
            db.flush()

            news_stats = None
            if news_articles is not None:            # REAL news, before scoring uses it
                news_stats = news_svc.store_articles(db, news_articles, int(time.time()))
                ok = sum(1 for v in news_status.values() if not isinstance(v, str))
                upsert_source(db, "news-rss", "news", "ok" if ok else "down",
                              {"note": "real headlines from publisher RSS feeds",
                               "publishers": news_status, "last_batch": news_stats})
                db.execute(delete(DataSource).where(DataSource.name == "news-sim"))
            stats = _score_all(db, now_b, None if fallback else ticks)
            if news_stats:
                stats["news"] = news_stats
            stats["new_tokens"] = new_count
            prune_old(db, now_b)

            upsert_source(db, "dexscreener", "market", "mock" if fallback else "ok",
                          {"mode": "fallback-sim" if fallback else "live",
                           "note": "real market data" if not fallback else
                                   "unreachable — simulated market universe"})
            if fallback or USE_SIMULATED_SOCIAL:
                upsert_source(db, "x-social-sim", "social", "mock",
                              {"note": "MOCK — simulated social (demo universe / USE_SIMULATED_SOCIAL)"})
            else:
                upsert_source(db, "attention-real", "social", "ok",
                              {"note": "no free X/Twitter source: attention = unique buyers "
                                       "(GeckoTerminal), bot/wash check = trades per unique trader; "
                                       "influencers/engagement unavailable"})
                db.execute(delete(DataSource).where(DataSource.name == "x-social-sim"))
            if fallback:
                upsert_source(db, "solana-chain-sim", "onchain", "mock",
                              {"note": "demo universe only (DexScreener unreachable)"})
            else:
                upsert_source(db, "onchain-real", "onchain", "ok",
                              {"note": "unique traders (GeckoTerminal), holders + top-10 wallets "
                                       "+ whale flow (Helius); deployer history not available"})
                db.execute(delete(DataSource).where(DataSource.name == "solana-chain-sim"))
            if not NEWS_RSS_ENABLED:
                upsert_source(db, "news-sim", "news", "mock",
                              {"note": "MOCK — set NEWS_RSS_ENABLED=1 for real publisher news"})
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
    try:
        stats.update(await onchain_checks())
    except Exception as exc:                      # on-chain checks must never break a tick
        log.error("onchain_checks failed: %s", exc)
    try:
        stats["verdicts"] = await verdict_svc.run_verdicts(SessionLocal)
    except Exception as exc:                      # the AI verdict must never break a tick
        log.error("verdicts failed: %s", exc)
    try:
        stats["risk_alerts"] = await risk_alerts_svc.run_risk_alerts(SessionLocal)
    except Exception as exc:                      # risk alerts must never break a tick
        log.error("risk alerts failed: %s", exc)
    try:
        stats["outcomes"] = await outcomes_svc.update_outcomes(SessionLocal)
        stats["scorecard"] = await asyncio.to_thread(outcomes_svc.maybe_send_daily, SessionLocal)
    except Exception as exc:                      # the outcome tracker must never break a tick
        log.error("outcomes failed: %s", exc)
    stats["seconds"] = round(time.time() - t0, 2)
    return stats


def latest_market_map(db) -> dict:
    """Latest per-token market facts for the paper engine's exit checks and
    the paper API's mark-to-market (v4). Built from stored TrendScore
    summaries — no fabricated values (§34)."""
    sub = (select(TrendScore.token_id, func.max(TrendScore.ts).label("mts"))
           .group_by(TrendScore.token_id).subquery())
    rows = db.execute(
        select(TrendScore)
        .join(sub, (TrendScore.token_id == sub.c.token_id) & (TrendScore.ts == sub.c.mts))
    ).scalars().all()
    out: dict[int, dict] = {}
    for sc in rows:
        c = sc.components_json or {}
        s = c.get("summary") or {}
        regime = ((c.get("forensics") or {}).get("components") or {}).get("regime", {}).get("regime")
        out[sc.token_id] = {
            "price": s.get("price"), "liq": s.get("liquidity"), "mcap": s.get("mcap"),
            "volume_24h": s.get("volume_24h"), "price_chg_1h": s.get("price_change_h1"),
            "momentum": s.get("momentum"), "sellp": s.get("sellp"),
            "liq_chg_3h": None,
            "risk": sc.risk, "regime": regime or "NEUTRAL",
        }
    return out
