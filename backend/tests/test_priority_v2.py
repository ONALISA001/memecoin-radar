"""Priority coins v2: momentum trend, red flags, early candidates, and the pick track record."""
import time
import uuid

from app.database import SessionLocal
from app.main import ensure_schema_and_demo_user
from app.models import PriceSnapshot, PriorityPick, Token
from app.services import market_views as MV
from app.services import priority_track as PT

ensure_schema_and_demo_user()


def card(addr, **kw):
    base = {"address": addr, "symbol": addr.upper(), "name": addr, "risk": 20, "liquidity": 80_000,
            "volume_24h": 300_000, "sellp": 30, "sellp_band": "neutral", "momentum": 70,
            "momentum_band": "strong", "whale_class": "neutral", "ath_status": None, "price": 1.0,
            "price_change_h1": 8.0, "buy_share": 0.62, "vol_accel_h1": 50, "holder_growth": 4,
            "organic": 70, "liq_band": "healthy", "age_hours": 5}
    base.update(kw)
    return base


def test_rising_momentum_outranks_falling():
    up = MV.priority_score(card("a"), None, None, hist=[60, 64, 68, 70])
    down = MV.priority_score(card("a"), None, None, hist=[80, 76, 72, 70])
    flat = MV.priority_score(card("a"), None, None, hist=None)
    assert up > flat > down


def test_red_flags_lower_the_score_and_severe_ones_exclude():
    clean = MV.priority_score(card("a"), None, None)
    chased = MV.priority_score(card("a", price_change_h1=80), None, None)
    assert chased < clean
    flags = {f["id"]: f for f in MV.red_flags(card("a", price_change_h1=200, volume_24h=80_000 * 40))}
    assert flags["chase"]["severe"] and flags["wash"]["severe"]
    out = MV.priority_coins([card("ok"), card("pumped", price_change_h1=200), card("washed", volume_24h=80_000 * 40),
                             card("whaledump", whale_class="heavy_distribution")], {}, {}, 10)
    assert [r["address"] for r in out["items"]] == ["ok"]
    assert MV.priority_checks(card("pumped", price_change_h1=200), None, None)[-1]["passed"] is False


def test_score_factors_explain_the_score():
    f = MV.score_factors(card("a", ath_status="overheated"), "WATCH", None, hist=[60, 70])
    ids = {x["id"] for x in f}
    assert {"momentum", "trend", "buyers", "volume", "risk", "verdict", "overheated"} <= ids
    assert round(sum(x["points"] for x in f), 1) == MV.priority_score(card("a", ath_status="overheated"), "WATCH",
                                                                       None, hist=[60, 70])


def test_early_candidates_are_found_before_the_full_bar():
    # momentum still "developing"-low and liquidity under $15K: fails the Priority gates, but climbing fast
    young = card("young", momentum=45, momentum_band="weakening", liquidity=10_000, volume_24h=40_000, age_hours=3)
    hist = {"young": [30, 36, 41, 45], "old": [30, 36, 41, 45], "slow": [44, 45, 45, 45]}
    cards = [young, card("old", momentum=45, momentum_band="weakening", age_hours=60),
             card("slow", momentum=45, momentum_band="weakening", age_hours=3),
             card("dumping", momentum=45, momentum_band="weakening", sellp_band="distribution", age_hours=2)]
    out = MV.priority_coins(cards, {}, {}, 10, hist={**hist, "dumping": [30, 45]})
    assert [r["address"] for r in out["early"]] == ["young"]
    reasons = out["early"][0]["early_reasons"]
    assert reasons[0].startswith("momentum +15") and any("buys" in r for r in reasons)
    assert out["early_count"] == 1 and "young" not in [r["address"] for r in out["items"]]


def test_more_than_eight_coins_can_be_listed():
    cards = [card(f"c{i}") for i in range(30)]
    assert len(MV.priority_coins(cards, {}, {}, limit=30)["items"]) == 30


def test_pick_track_record_records_labels_and_scores():
    now = int(time.time()) - 5 * 3600            # picks made 5 h ago, so both horizons have passed
    with SessionLocal() as db:
        try:
            win, loss = (Token(address=f"Pk{uuid.uuid4().hex}", chain="solana", symbol=s) for s in ("WIN", "LOSS"))
            db.add_all([win, loss]); db.flush()
            for t, path in ((win, [1.0, 1.15, 1.30, 1.20]), (loss, [1.0, 0.95, 0.80, 0.70])):
                for i, px in enumerate(path):          # prices at +0, +1h, +2h, +4h
                    db.add(PriceSnapshot(token_id=t.id, ts=now + [0, 3600, 7200, 14400][i], price_usd=px))
            prio = {"items": [{"token_id": win.id, "price": 1.0, "score": 70}],
                    "early": [{"token_id": loss.id, "price": 1.0, "score": 50}]}
            assert PT.record(db, now, prio) == 2
            assert PT.record(db, now + 60, prio) == 0           # not re-picked within 6 h
            assert PT.label(db, now + 5 * 3600) == []          # both labelled from snapshots
            w = db.query(PriorityPick).filter_by(token_id=win.id).one()
            assert (w.ret_1h, w.ret_4h, w.max_up_4h, w.status) == (15.0, 20.0, 30.0, "done")
            s = PT.stats(db, now=now + 5 * 3600)
            assert s["passing"]["up_after_4h_pct"] == 100.0 and s["passing"]["reached_10pct_pct"] == 100.0
            assert s["early"]["avg_ret_4h"] == -30.0
        finally:
            db.rollback()


def _pick(db, sym, path, now, kind="passing"):
    """A token with price snapshots at the given (offset_s, price) points and a pick at `now`."""
    t = Token(address=f"Hn{uuid.uuid4().hex}", chain="solana", symbol=sym)
    db.add(t); db.flush()
    for off, px in path:
        db.add(PriceSnapshot(token_id=t.id, ts=now + off, price_usd=px))
    p = PriorityPick(token_id=t.id, list_kind=kind, ts=now, price=1.0, status="open")
    db.add(p); db.flush()
    return t, p


def _cleanup(db):
    from app.models import LiquiditySnapshot, SystemLog
    db.rollback()
    ids = [t.id for t in db.query(Token).filter(Token.address.like("Hn%")).all()]
    if ids:
        db.query(LiquiditySnapshot).filter(LiquiditySnapshot.token_id.in_(ids)).delete(synchronize_session=False)
        db.query(PriorityPick).filter(PriorityPick.token_id.in_(ids)).delete(synchronize_session=False)
        db.query(PriceSnapshot).filter(PriceSnapshot.token_id.in_(ids)).delete(synchronize_session=False)
        db.query(Token).filter(Token.id.in_(ids)).delete(synchronize_session=False)
    db.query(SystemLog).filter_by(source="priority_track").delete()
    db.commit()


def test_track_record_counts_vanished_coins_instead_of_dropping_them():
    import asyncio
    import httpx
    from app.models import SystemLog
    H = 3600
    now = int(time.time()) - 5 * H                      # 4h mark was 1h ago: fallback window open
    with SessionLocal() as db:
        try:
            db.add(SystemLog(ts=now, level="info", source="priority_track", message=PT.RELABEL_MARK))
            _, ok = _pick(db, "OK", [(H, 1.1), (4 * H, 1.2)], now)
            dead_t, dead = _pick(db, "DEAD", [(H, 1.3)], now)            # tracking stopped after 1h
            alive_t, alive = _pick(db, "ALIVE", [(H, 0.9)], now)
            _, crash = _pick(db, "CRASH", [(H, 0.1)], now)               # -90%, then fell off
            # old pick, no 4h price, fallback window long gone => no_data (not a stale +30%)
            _, old = _pick(db, "OLD", [(H, 1.3)], now - 2 * 24 * H)
            db.commit()

            def dex(req):
                assert dead_t.address in str(req.url) and alive_t.address in str(req.url)
                return httpx.Response(200, json=[{"baseToken": {"address": alive_t.address},
                                                  "priceUsd": "0.5", "liquidity": {"usd": 20000}}])
            res = asyncio.run(PT.run(SessionLocal, transport=httpx.MockTransport(dex)))
            assert res["rugged"] == 1 and res["fallback"] == 1
            db.expire_all()
            assert (ok.status, ok.ret_4h) == ("done", 20.0)
            assert (dead.status, dead.ret_4h) == ("rugged", -100.0)       # no pool left
            assert (alive.status, alive.ret_4h) == ("done", -50.0)        # DexScreener price now
            assert (crash.status, crash.ret_4h) == ("rugged", -100.0)
            assert old.status == "no_data" and old.ret_4h is None
            s = PT.stats(db, days=7, now=int(time.time()))["passing"]
            assert s["rugged"] == 2 and s["no_data"] == 1
            assert s["coverage_pct"] == 80.0                              # 4 of 5 due picks judged
            assert s["median_ret_4h"] == -75.0                            # [20, -100, -50, -100]
            assert s["net_avg_ret_4h"] == round((20 - 100 - 50 - 100) / 4 - PT.ROUND_TRIP_COST_PCT, 2)
        finally:
            _cleanup(db)


def test_failed_lookup_concludes_nothing():
    import asyncio
    import httpx
    from app.models import SystemLog
    now = int(time.time()) - 5 * 3600
    with SessionLocal() as db:
        try:
            db.add(SystemLog(ts=now, level="info", source="priority_track", message=PT.RELABEL_MARK))
            _, p = _pick(db, "GONE", [(3600, 1.3)], now)
            db.commit()
            asyncio.run(PT.run(SessionLocal, transport=httpx.MockTransport(lambda r: httpx.Response(500))))
            db.expire_all()
            assert p.status == "open" and p.ret_4h is None               # retried later, not a rug
        finally:
            _cleanup(db)


def test_relabel_once_replaces_stale_old_labels():
    from app.models import SystemLog
    H = 3600
    now = int(time.time()) - 3 * 24 * H
    with SessionLocal() as db:
        try:
            # labelled the old way: last price before tracking stopped (+30%) stood in for 4h
            _, p = _pick(db, "STALE", [(H, 1.3)], now)
            p.ret_4h, p.status = 30.0, "done"
            db.commit()
            assert PT._relabel_once(db) >= 1
            assert (p.status, p.ret_4h) == ("no_data", None)
            assert PT._relabel_once(db) == 0                              # only once
        finally:
            _cleanup(db)


def test_open_picks_are_followed_until_their_4h_mark():
    import asyncio
    import httpx
    from app.models import LiquiditySnapshot, SystemLog
    from app.config import SNAPSHOT_BUCKET_SECONDS as B
    now = int(time.time())
    with SessionLocal() as db:
        try:
            db.add(SystemLog(ts=now, level="info", source="priority_track", message=PT.RELABEL_MARK))
            live_t, _ = _pick(db, "ROTATED", [(60, 1.0)], now - 2 * 3600)        # off the radar now
            thin_t, _ = _pick(db, "THIN", [(60, 1.0)], now - 2 * 3600)           # pool nearly empty
            done_t, _ = _pick(db, "OLDER", [(60, 1.0)], now - 6 * 3600)          # past its window
            db.commit()
            seen = []

            def dex(req):
                seen.append(str(req.url))
                return httpx.Response(200, json=[
                    {"baseToken": {"address": live_t.address}, "priceUsd": "1.4", "liquidity": {"usd": 30000}},
                    {"baseToken": {"address": thin_t.address}, "priceUsd": "0.2", "liquidity": {"usd": 300}}])
            res = asyncio.run(PT.run(SessionLocal, transport=httpx.MockTransport(dex), now=now))
            assert res["followed"] == 1
            # the older pick is past its follow window: looked up once for its 4h result
            # (no pool in the reply => rugged), but no new price is stored for it
            b = now - now % B
            db.expire_all()
            snap = db.query(PriceSnapshot).filter_by(token_id=live_t.id, ts=b).one()
            assert snap.price_usd == 1.4
            assert db.query(LiquiditySnapshot).filter_by(token_id=live_t.id, ts=b).one().liquidity_usd == 30000
            assert db.query(PriceSnapshot).filter_by(token_id=thin_t.id, ts=b).count() == 0
            assert db.query(PriceSnapshot).filter_by(token_id=done_t.id, ts=b).count() == 0
            assert db.query(PriorityPick).filter_by(token_id=done_t.id).one().status == "rugged"
            # same bucket again: no duplicate row, no second lookup for it
            asyncio.run(PT.run(SessionLocal, transport=httpx.MockTransport(dex), now=now + 30))
            assert db.query(PriceSnapshot).filter_by(token_id=live_t.id, ts=b).count() == 1
        finally:
            _cleanup(db)
