"""Outcome tracker: did FOMO's verdicts age well?

For every verdict (except REVIEW) it records, from REAL data only:
  * price change at +1h, +6h, +24h            (stored price snapshots, 5-min buckets)
  * best and worst point within 24h, liquidity change, rug flag
  * if the token dropped off tracking: one DexScreener lookup near +24h
Nothing is estimated: a horizon with no data stays empty; no 24h price => no_data.

Was the call right? (judged at 24h)
  WATCH / STRONG_WATCH  right: no rug and (+10% at 24h or +50% at some point)
                        wrong: rugged, or -30% or worse at 24h
  AVOID / RUG_RISK      right: rugged, or -30% or worse at 24h
                        wrong: +30% or better at 24h without a rug (missed a runner)
  NEUTRAL               right: within +/-30% and no rug; otherwise mixed (never wrong)
  anything else         mixed
Rug = price -80% or worse, or liquidity down 80%+ from the verdict.
"""
from __future__ import annotations

import datetime as dt
import html
import statistics
import time

import httpx
from sqlalchemy import func, select

from .. import config
from ..models import (DataSource, LiquiditySnapshot, PriceSnapshot, Token, TokenVerdict,
                      VerdictOutcome)

HORIZONS = {"ret_1h": 3600, "ret_6h": 6 * 3600, "ret_24h": 24 * 3600}
TOLERANCE = 20 * 60          # a snapshot within 20 min of the target counts
FALLBACK_WINDOW = 3 * 3600   # DexScreener fallback accepted up to 3h after +24h
GIVE_UP = 30 * 3600          # no 24h data after this => no_data
BULLISH, BEARISH = ("WATCH", "STRONG_WATCH"), ("AVOID", "RUG_RISK")
FALLBACK_RETRY = 15 * 60     # one DexScreener attempt per outcome per 15 min
_FALLBACK_TRIED: dict[int, int] = {}


def _epoch(d) -> int:
    if d is None:
        return 0
    if d.tzinfo is None:
        d = d.replace(tzinfo=dt.timezone.utc)
    return int(d.timestamp())


def _pct(now: float | None, then: float | None) -> float | None:
    if not now or not then or then <= 0:
        return None
    return round((now / then - 1) * 100, 2)


def judge_call(rating: str, ret_24h: float | None, max_up: float | None, rugged: bool | None) -> str | None:
    if ret_24h is None:
        return None
    if rating in BULLISH:
        if rugged or ret_24h <= -30:
            return "wrong"
        if ret_24h >= 10 or (max_up or 0) >= 50:
            return "right"
        return "mixed"
    if rating in BEARISH:
        if rugged or ret_24h <= -30:
            return "right"
        if ret_24h >= 30:
            return "wrong"
        return "mixed"
    if rating == "NEUTRAL":
        return "right" if (abs(ret_24h) < 30 and not rugged) else "mixed"
    return "mixed"


def _snap_at(series: list[tuple[int, float]], target: int) -> float | None:
    """Value of the snapshot nearest to target, if within TOLERANCE."""
    best, gap = None, TOLERANCE + 1
    for ts, v in series:
        g = abs(ts - target)
        if g < gap and v and v > 0:
            best, gap = v, g
    return best


async def _dexscreener_now(addresses: list[str], transport=None) -> dict[str, dict]:
    """{address: {"price":, "liquidity":}} for the deepest pool; missing = no pairs."""
    out: dict[str, dict] = {}
    async with httpx.AsyncClient(timeout=10.0, transport=transport) as c:
        for i in range(0, len(addresses), 30):
            chunk = addresses[i:i + 30]
            try:
                r = await c.get(f"{config.DEXSCREENER_BASE}/tokens/v1/solana/{','.join(chunk)}")
                r.raise_for_status()
                pairs = r.json()
            except Exception:
                continue
            for p in pairs if isinstance(pairs, list) else (pairs or {}).get("pairs") or []:
                addr = (p.get("baseToken") or {}).get("address")
                liq = float((p.get("liquidity") or {}).get("usd") or 0)
                if addr and (addr not in out or liq > out[addr]["liquidity"]):
                    out[addr] = {"price": float(p.get("priceUsd") or 0), "liquidity": liq}
    return out


def _ensure_rows(db, now: int) -> None:
    """Create outcome rows for new verdicts (last 3 days, not REVIEW)."""
    since = dt.datetime.utcnow() - dt.timedelta(days=3)
    have = select(VerdictOutcome.verdict_id)
    rows = db.execute(select(TokenVerdict).where(
        TokenVerdict.created_at >= since, TokenVerdict.rating != "REVIEW",
        TokenVerdict.id.not_in(have))).scalars().all()
    for v in rows:
        db.add(VerdictOutcome(verdict_id=v.id, token_id=v.token_id, rating=v.rating, source=v.source,
                              verdict_ts=_epoch(v.created_at), price_at=v.price_at,
                              liquidity_at=v.liquidity_at, status="pending" if v.price_at else "no_data"))
    db.flush()


async def update_outcomes(session_factory, now: int | None = None, transport=None) -> dict:
    if not config.OUTCOMES_ENABLED:
        return {"status": "disabled"}
    now = now or int(time.time())
    res = {"updated": 0, "done": 0, "no_data": 0, "fallback": 0}
    with session_factory() as db:
        _ensure_rows(db, now)
        pend = db.execute(select(VerdictOutcome).where(
            VerdictOutcome.status == "pending",
            VerdictOutcome.verdict_ts <= now - HORIZONS["ret_1h"])).scalars().all()
        need_fallback: list[VerdictOutcome] = []
        for o in pend:
            start, end = o.verdict_ts, o.verdict_ts + HORIZONS["ret_24h"]
            prices = [(r.ts, r.price_usd) for r in db.execute(
                select(PriceSnapshot).where(PriceSnapshot.token_id == o.token_id,
                                            PriceSnapshot.ts >= start - TOLERANCE,
                                            PriceSnapshot.ts <= end + TOLERANCE)
                .order_by(PriceSnapshot.ts)).scalars().all()]
            changed = False
            for col, h in HORIZONS.items():
                if getattr(o, col) is None and now >= start + h:
                    v = _snap_at(prices, start + h)
                    if v is not None:
                        setattr(o, col, _pct(v, o.price_at))
                        o.data_source = o.data_source or "snapshots"
                        changed = True
            window = [v for ts, v in prices if start <= ts <= min(end, now) and v and v > 0]
            if window and o.price_at:
                o.max_up_24h = _pct(max(window), o.price_at)
                o.max_dd_24h = _pct(min(window), o.price_at)
            if now >= end:
                if o.ret_24h is not None and o.liq_change_24h is None:
                    liq = [(r.ts, r.liquidity_usd) for r in db.execute(
                        select(LiquiditySnapshot).where(LiquiditySnapshot.token_id == o.token_id,
                                                        LiquiditySnapshot.ts >= end - TOLERANCE,
                                                        LiquiditySnapshot.ts <= end + TOLERANCE)).scalars().all()]
                    o.liq_change_24h = _pct(_snap_at(liq, end), o.liquidity_at)
                if o.ret_24h is None:
                    if now <= end + FALLBACK_WINDOW:
                        if now - _FALLBACK_TRIED.get(o.id, 0) >= FALLBACK_RETRY:
                            _FALLBACK_TRIED[o.id] = now
                            need_fallback.append(o)
                    elif now >= start + GIVE_UP:
                        _FALLBACK_TRIED.pop(o.id, None)
                        o.status = "no_data"
                        res["no_data"] += 1
                        changed = True
            res["updated"] += 1 if changed else 0
        # DexScreener fallback for tokens no longer tracked
        if need_fallback:
            addr = {t.id: t.address for t in db.execute(select(Token).where(
                Token.id.in_({o.token_id for o in need_fallback}))).scalars().all()}
            live = await _dexscreener_now(sorted(set(addr.values())), transport=transport)
            for o in need_fallback:
                m = live.get(addr.get(o.token_id))
                if m and m["price"] > 0:
                    o.ret_24h = _pct(m["price"], o.price_at)
                    o.liq_change_24h = _pct(m["liquidity"], o.liquidity_at)
                    o.data_source = "dexscreener" if not o.data_source else o.data_source + "+dex"
                    res["fallback"] += 1
        # finalise
        for o in pend:
            if o.status == "pending" and o.ret_24h is not None and now >= o.verdict_ts + HORIZONS["ret_24h"]:
                o.rugged = bool((o.ret_24h <= -80) or (o.liq_change_24h is not None and o.liq_change_24h <= -80)
                                or (o.max_dd_24h is not None and o.max_dd_24h <= -90))
                o.result = judge_call(o.rating, o.ret_24h, o.max_up_24h, o.rugged)
                o.status = "done"
                res["done"] += 1
        db.commit()
    return res


# ---------------------------------------------------------------- scorecard
def scorecard(db, days: int = 7, now: int | None = None) -> dict:
    """Accuracy per rating and source. One call per (token, rating): the first one."""
    now = now or int(time.time())
    rows = db.execute(select(VerdictOutcome, Token).join(Token, Token.id == VerdictOutcome.token_id)
                      .where(VerdictOutcome.verdict_ts >= now - days * 86400)
                      .order_by(VerdictOutcome.verdict_ts)).all()
    first: dict[tuple, tuple] = {}
    for o, t in rows:
        first.setdefault((o.token_id, o.rating), (o, t))
    calls = list(first.values())

    def stats(items):
        done = [o for o, _ in items if o.status == "done"]
        r24 = [o.ret_24h for o in done if o.ret_24h is not None]
        n = len(done)
        right = sum(1 for o in done if o.result == "right")
        wrong = sum(1 for o in done if o.result == "wrong")
        return {"calls": len(items), "scored": n, "pending": sum(1 for o, _ in items if o.status == "pending"),
                "right": right, "wrong": wrong, "mixed": n - right - wrong,
                "hit_rate": round(right / n, 3) if n else None,
                "avg_ret_1h": _avg([o.ret_1h for o, _ in items]), "avg_ret_6h": _avg([o.ret_6h for o, _ in items]),
                "avg_ret_24h": _avg(r24), "median_ret_24h": round(statistics.median(r24), 1) if r24 else None,
                "rug_rate": round(sum(1 for o in done if o.rugged) / n, 3) if n else None}

    by_rating = {r: stats([c for c in calls if c[0].rating == r])
                 for r in ("STRONG_WATCH", "WATCH", "NEUTRAL", "AVOID", "RUG_RISK")}
    by_source = {s: stats([c for c in calls if c[0].source == s]) for s in ("llm", "rules", "gate")}
    misses = [c for c in calls if c[0].result == "wrong"]
    misses.sort(key=lambda c: -abs(c[0].ret_24h or 0))
    return {"days": days, "overall": stats(calls), "by_rating": by_rating, "by_source": by_source,
            "worst_misses": [{"symbol": t.symbol, "address": t.address, "rating": o.rating, "source": o.source,
                              "ret_24h": o.ret_24h, "max_up_24h": o.max_up_24h, "rugged": o.rugged,
                              "verdict_id": o.verdict_id} for o, t in misses[:5]],
            "rules": __doc__.split("Was the call right?")[1].strip()}


def _avg(xs):
    xs = [x for x in xs if x is not None]
    return round(sum(xs) / len(xs), 1) if xs else None


def track_record_text(db, days: int = 7) -> str | None:
    """Short lesson block for the AI judge (TradingAgents-style memory). None if too few."""
    sc = scorecard(db, days)
    if sc["overall"]["scored"] < 5:
        return None
    lines = []
    for r in ("STRONG_WATCH", "WATCH", "AVOID", "RUG_RISK"):
        s = sc["by_rating"][r]
        if s["scored"]:
            lines.append(f"{r}: {s['scored']} scored, {s['right']} right, {s['wrong']} wrong, "
                         f"rug rate {(s['rug_rate'] or 0):.0%}, avg 24h {s['avg_ret_24h'] or 0:+.0f}%")
    if sc["worst_misses"]:
        ids = [m["verdict_id"] for m in sc["worst_misses"][:3]]
        evs = {v.id: v.evidence_json or {} for v in db.execute(
            select(TokenVerdict).where(TokenVerdict.id.in_(ids))).scalars().all()}
        for m in sc["worst_misses"][:3]:
            ev = evs.get(m["verdict_id"], {})
            la, ho, mk = ev.get("launch") or {}, ev.get("holders") or {}, ev.get("market") or {}
            what = "rugged" if m["rugged"] else f"went {m['ret_24h']:+.0f}% in 24h"
            lines.append(f"MISS: rated {m['rating']}, {what}. Evidence then: snipers {la.get('sniper_pct')}%, "
                         f"bundle {la.get('bundle_pct')}%, top10 {ho.get('top10_wallet_pct')}%, "
                         f"liquidity ${mk.get('liquidity_usd')}, 1h {mk.get('price_change_1h_pct')}%")
    return ("Your last %d days of calls (learn from the misses, stay evidence-based):\n" % days) + "\n".join(lines)


# ---------------------------------------------------------------- daily Telegram summary
def format_scorecard(sc: dict) -> str:
    o = sc["overall"]
    lines = [f"📊 <b>FOMO scorecard · last {sc['days']} days</b>",
             f"{o['scored']} calls scored · {o['right']} right · {o['wrong']} wrong · {o['mixed']} mixed"
             + (f" · hit rate {o['hit_rate']:.0%}" if o["hit_rate"] is not None else "")]
    names = {"STRONG_WATCH": "🟢 Strong watch", "WATCH": "🟡 Watch", "NEUTRAL": "⚪ Neutral",
             "AVOID": "🟠 Avoid", "RUG_RISK": "🔴 Rug risk"}
    for r, s in sc["by_rating"].items():
        if s["scored"]:
            lines.append(f"{names[r]}: {s['right']}/{s['scored']} right · avg 24h {s['avg_ret_24h'] or 0:+.0f}%"
                         f" · rugs {(s['rug_rate'] or 0):.0%}")
    src = sc["by_source"]
    if src["llm"]["scored"] and src["rules"]["scored"]:
        lines.append(f"AI debate {src['llm']['hit_rate'] or 0:.0%} vs rules {src['rules']['hit_rate'] or 0:.0%} hit rate")
    for m in sc["worst_misses"][:2]:
        what = "rugged" if m["rugged"] else f"{m['ret_24h']:+.0f}% in 24h"
        lines.append(f"❌ ${html.escape(m['symbol'] or '?')}: rated {m['rating'].replace('_', ' ').title()}, {what}")
    lines.append("<i>Right/wrong judged at 24h from real prices. Not financial advice.</i>")
    return "\n".join(lines)


def maybe_send_daily(session_factory, now: dt.datetime | None = None) -> dict:
    """Send the scorecard once a day at SCORECARD_HOUR_UTC (if anything was scored)."""
    from ..engine import telegram
    now = now or dt.datetime.utcnow()
    if not (config.SCORECARD_TELEGRAM and telegram.is_configured()) or now.hour < config.SCORECARD_HOUR_UTC:
        return {"sent": False}
    today = now.date().isoformat()
    with session_factory() as db:
        src = db.execute(select(DataSource).where(DataSource.name == "scorecard")).scalar_one_or_none()
        if src is not None and (src.meta_json or {}).get("last_sent") == today:
            return {"sent": False}
        sc = scorecard(db, 7)
        if not sc["overall"]["scored"]:
            return {"sent": False, "reason": "nothing scored yet"}
        r = telegram.send_text(format_scorecard(sc))
        if r.get("ok"):
            if src is None:
                src = DataSource(name="scorecard", kind="onchain")
                db.add(src)
            src.status, src.meta_json = "ok", {"last_sent": today, "note": "daily verdict scorecard"}
            src.last_ok_at = func.now()
            db.commit()
        return {"sent": bool(r.get("ok"))}
