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
            assert PT.label(db, now + 5 * 3600) == 2
            w = db.query(PriorityPick).filter_by(token_id=win.id).one()
            assert (w.ret_1h, w.ret_4h, w.max_up_4h, w.status) == (15.0, 20.0, 30.0, "done")
            s = PT.stats(db, now=now + 5 * 3600)
            assert s["passing"]["up_after_4h_pct"] == 100.0 and s["passing"]["reached_10pct_pct"] == 100.0
            assert s["early"]["avg_ret_4h"] == -30.0
        finally:
            db.rollback()
