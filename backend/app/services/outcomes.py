"""Outcome tracker: did FOMO's verdicts age well?

For every verdict (except REVIEW) it records, from REAL data only:
  * price change at +1h, +6h, +12h, +24h        (stored price snapshots, 5-min buckets)
  * at the JUDGING point (OUTCOME_JUDGE_HOURS, default 12h): best and worst point
    since the call, liquidity change and a rug flag, then right / wrong / mixed
  * the 24h numbers keep filling in afterwards, for reference
  * if the token dropped off tracking: a DexScreener lookup near the judging point
Nothing is estimated: a horizon with no data stays empty; no price at the judging
point (or within 6h after it) => no_data.

Was the call right? (judged at {J}h)
  WATCH / STRONG_WATCH  right: no rug and (+10% at {J}h or +50% at some point)
                        wrong: rugged, or -30% or worse at {J}h
  AVOID / RUG_RISK      right: rugged, or -30% or worse at {J}h
                        wrong: +30% or better at {J}h without a rug (missed a runner)
  NEUTRAL               right: within +/-30% and no rug; otherwise mixed (never wrong)
  anything else         mixed
Rug = price -80% or worse, liquidity down 80%+, or a -90% crash at any point.
"""
from __future__ import annotations

import datetime as dt
import html
import statistics
import time

import httpx
from sqlalchemy import func, or_, select

from .. import config
from ..models import (DataSource, LiquiditySnapshot, PriceSnapshot, Token, TokenVerdict,
                      VerdictOutcome)

HORIZONS = {"ret_1h": 3600, "ret_6h": 6 * 3600, "ret_12h": 12 * 3600, "ret_24h": 24 * 3600}
TOLERANCE = 20 * 60          # a snapshot within 20 min of the target counts
FALLBACK_WINDOW = 3 * 3600   # DexScreener fallback accepted up to 3h after a target
JUDGE_GRACE = 6 * 3600       # no price at the judging point after this long => no_data
H24_GIVE_UP = 30 * 3600      # stop waiting for 24h reference numbers
BULLISH, BEARISH = ("WATCH", "STRONG_WATCH"), ("AVOID", "RUG_RISK")
FALLBACK_RETRY = 15 * 60     # one DexScreener attempt per outcome per 15 min
_FALLBACK_TRIED: dict[int, int] = {}


def judge_hours() -> int:
    return max(1, int(config.OUTCOME_JUDGE_HOURS))


def rules_text() -> str:
    return __doc__.split("Was the call right?")[1].strip().replace("{J}", str(judge_hours()))


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


def judge_call(rating: str, ret: float | None, max_up: float | None, rugged: bool | None) -> str | None:
    """ret = price change at the judging point."""
    if ret is None:
        return None
    if rating in BULLISH:
        if rugged or ret <= -30:
            return "wrong"
        if ret >= 10 or (max_up or 0) >= 50:
            return "right"
        return "mixed"
    if rating in BEARISH:
        if rugged or ret <= -30:
            return "right"
        if ret >= 30:
            return "wrong"
        return "mixed"
    if rating == "NEUTRAL":
        return "right" if (abs(ret) < 30 and not rugged) else "mixed"
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


def _ensure_rows(db) -> None:
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


def _liq_at(db, token_id: int, target: int) -> float | None:
    liq = [(r.ts, r.liquidity_usd) for r in db.execute(
        select(LiquiditySnapshot).where(LiquiditySnapshot.token_id == token_id,
                                        LiquiditySnapshot.ts >= target - TOLERANCE,
                                        LiquiditySnapshot.ts <= target + TOLERANCE)).scalars().all()]
    return _snap_at(liq, target)


def _judge(o: VerdictOutcome, J: int) -> None:
    o.rugged = bool((o.ret_judge <= -80)
                    or (o.liq_change_judge is not None and o.liq_change_judge <= -80)
                    or (o.max_dd_judge is not None and o.max_dd_judge <= -90))
    o.result = judge_call(o.rating, o.ret_judge, o.max_up_judge, o.rugged)
    o.judge_h = J


async def update_outcomes(session_factory, now: int | None = None, transport=None) -> dict:
    if not config.OUTCOMES_ENABLED:
        return {"status": "disabled"}
    now = now or int(time.time())
    J = judge_hours()
    jsec = J * 3600
    res = {"updated": 0, "judged": 0, "done": 0, "no_data": 0, "fallback": 0, "judge_h": J}
    with session_factory() as db:
        _ensure_rows(db)
        rows = db.execute(select(VerdictOutcome).where(
            or_(VerdictOutcome.status == "pending", VerdictOutcome.status == "judged"),
            VerdictOutcome.verdict_ts <= now - HORIZONS["ret_1h"])).scalars().all()
        need: list[tuple[VerdictOutcome, str]] = []          # (row, "judge" | "24h")
        for o in rows:
            start = o.verdict_ts
            end = start + max(jsec, HORIZONS["ret_24h"])
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
            if o.price_at:
                def window(until):
                    return [v for ts, v in prices if start <= ts <= min(until, now) and v and v > 0]
                w24 = window(start + HORIZONS["ret_24h"])
                if w24:
                    o.max_up_24h, o.max_dd_24h = _pct(max(w24), o.price_at), _pct(min(w24), o.price_at)
            # ---- judging point
            if o.status == "pending" and now >= start + jsec:
                if o.ret_judge is None:
                    v = _snap_at(prices, start + jsec)
                    if v is not None:
                        o.ret_judge = _pct(v, o.price_at)
                        o.liq_change_judge = _pct(_liq_at(db, o.token_id, start + jsec), o.liquidity_at)
                if o.ret_judge is None:
                    if now <= start + jsec + FALLBACK_WINDOW:
                        if now - _FALLBACK_TRIED.get(o.id, 0) >= FALLBACK_RETRY:
                            _FALLBACK_TRIED[o.id] = now
                            need.append((o, "judge"))
                    elif now >= start + jsec + JUDGE_GRACE:
                        _FALLBACK_TRIED.pop(o.id, None)
                        o.status = "no_data"
                        res["no_data"] += 1
                        changed = True
            # ---- 24h reference numbers after judging
            if o.status == "judged" and now >= start + HORIZONS["ret_24h"]:
                if o.ret_24h is not None and o.liq_change_24h is None:
                    o.liq_change_24h = _pct(_liq_at(db, o.token_id, start + HORIZONS["ret_24h"]), o.liquidity_at)
                if o.ret_24h is None and now <= start + HORIZONS["ret_24h"] + FALLBACK_WINDOW \
                        and now - _FALLBACK_TRIED.get(o.id, 0) >= FALLBACK_RETRY:
                    _FALLBACK_TRIED[o.id] = now
                    need.append((o, "24h"))
                if o.ret_24h is not None or now >= start + H24_GIVE_UP:
                    _FALLBACK_TRIED.pop(o.id, None)
                    o.status = "done"
                    res["done"] += 1
                    changed = True
            res["updated"] += 1 if changed else 0
        # ---- DexScreener fallback for tokens no longer tracked
        if need:
            addr = {t.id: t.address for t in db.execute(select(Token).where(
                Token.id.in_({o.token_id for o, _ in need}))).scalars().all()}
            live = await _dexscreener_now(sorted(set(addr.values())), transport=transport)
            for o, what in need:
                m = live.get(addr.get(o.token_id))
                if not m or m["price"] <= 0:
                    continue
                if what == "judge":
                    o.ret_judge = _pct(m["price"], o.price_at)
                    o.liq_change_judge = _pct(m["liquidity"], o.liquidity_at)
                else:
                    o.ret_24h = _pct(m["price"], o.price_at)
                    o.liq_change_24h = _pct(m["liquidity"], o.liquidity_at)
                o.data_source = "dexscreener" if not o.data_source else (
                    o.data_source if "dex" in o.data_source else o.data_source + "+dex")
                res["fallback"] += 1
        # ---- judge
        for o in rows:
            if o.status == "pending" and o.ret_judge is not None and now >= o.verdict_ts + jsec:
                prices = [v for ts, v in db.execute(
                    select(PriceSnapshot.ts, PriceSnapshot.price_usd).where(
                        PriceSnapshot.token_id == o.token_id, PriceSnapshot.ts >= o.verdict_ts,
                        PriceSnapshot.ts <= o.verdict_ts + jsec)).all() if v and v > 0]
                if prices and o.price_at:
                    o.max_up_judge, o.max_dd_judge = _pct(max(prices), o.price_at), _pct(min(prices), o.price_at)
                _judge(o, J)
                o.status = "done" if jsec >= HORIZONS["ret_24h"] else "judged"
                res["judged"] += 1
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
        done = [o for o, _ in items if o.result is not None]
        rj = [_ret_j(o) for o in done if _ret_j(o) is not None]
        n = len(done)
        right = sum(1 for o in done if o.result == "right")
        wrong = sum(1 for o in done if o.result == "wrong")
        return {"calls": len(items), "scored": n,
                "pending": sum(1 for o, _ in items if o.result is None and o.status == "pending"),
                "right": right, "wrong": wrong, "mixed": n - right - wrong,
                "hit_rate": round(right / n, 3) if n else None,
                "avg_ret_1h": _avg([o.ret_1h for o, _ in items]), "avg_ret_6h": _avg([o.ret_6h for o, _ in items]),
                "avg_ret_12h": _avg([o.ret_12h for o, _ in items]), "avg_ret_24h": _avg([o.ret_24h for o, _ in items]),
                "avg_ret_judged": _avg(rj), "median_ret_judged": round(statistics.median(rj), 1) if rj else None,
                "rug_rate": round(sum(1 for o in done if o.rugged) / n, 3) if n else None}

    by_rating = {r: stats([c for c in calls if c[0].rating == r])
                 for r in ("STRONG_WATCH", "WATCH", "NEUTRAL", "AVOID", "RUG_RISK")}
    by_source = {s: stats([c for c in calls if c[0].source == s]) for s in ("llm", "rules", "gate")}
    misses = [c for c in calls if c[0].result == "wrong"]
    misses.sort(key=lambda c: -abs(_ret_j(c[0]) or 0))
    return {"days": days, "judge_h": judge_hours(), "overall": stats(calls), "by_rating": by_rating,
            "by_source": by_source,
            "worst_misses": [{"symbol": t.symbol, "address": t.address, "rating": o.rating, "source": o.source,
                              "ret_judged": _ret_j(o), "judge_h": o.judge_h or 24, "ret_24h": o.ret_24h,
                              "rugged": o.rugged, "verdict_id": o.verdict_id} for o, t in misses[:5]],
            "rules": rules_text()}


def _ret_j(o) -> float | None:
    """Return at the point the call was judged (older rows were judged at 24h)."""
    return o.ret_judge if o.ret_judge is not None else o.ret_24h


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
                         f"rug rate {(s['rug_rate'] or 0):.0%}, avg {sc['judge_h']}h {s['avg_ret_judged'] or 0:+.0f}%")
    if sc["worst_misses"]:
        ids = [m["verdict_id"] for m in sc["worst_misses"][:3]]
        evs = {v.id: v.evidence_json or {} for v in db.execute(
            select(TokenVerdict).where(TokenVerdict.id.in_(ids))).scalars().all()}
        for m in sc["worst_misses"][:3]:
            ev = evs.get(m["verdict_id"], {})
            la, ho, mk = ev.get("launch") or {}, ev.get("holders") or {}, ev.get("market") or {}
            what = "rugged" if m["rugged"] else f"went {m['ret_judged']:+.0f}% in {m['judge_h']}h"
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
            lines.append(f"{names[r]}: {s['right']}/{s['scored']} right · avg {sc['judge_h']}h {s['avg_ret_judged'] or 0:+.0f}%"
                         f" · rugs {(s['rug_rate'] or 0):.0%}")
    src = sc["by_source"]
    if src["llm"]["scored"] and src["rules"]["scored"]:
        lines.append(f"AI debate {src['llm']['hit_rate'] or 0:.0%} vs rules {src['rules']['hit_rate'] or 0:.0%} hit rate")
    for m in sc["worst_misses"][:2]:
        what = "rugged" if m["rugged"] else f"{m['ret_judged']:+.0f}% in {m['judge_h']}h"
        lines.append(f"❌ ${html.escape(m['symbol'] or '?')}: rated {m['rating'].replace('_', ' ').title()}, {what}")
    lines.append(f"<i>Right/wrong judged at {sc['judge_h']}h from real prices. Not financial advice.</i>")
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
