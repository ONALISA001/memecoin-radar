"""Outcome tracker tests: returns, rugs, right/wrong, fallback, scorecard, memory, Telegram."""
import asyncio, datetime as dt, json, os, sys, tempfile, time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
fd, dbpath = tempfile.mkstemp(suffix=".db"); os.close(fd)
os.environ["DATABASE_URL"] = f"sqlite:///{dbpath}"
os.environ["LLM_API_KEY"] = "K"

import httpx  # noqa: E402
from app.database import Base, engine, SessionLocal  # noqa: E402
from app.models import (LiquiditySnapshot, PriceSnapshot, Token, TokenVerdict,  # noqa: E402
                        VerdictOutcome)
from app.services import outcomes as O  # noqa: E402
from app.engine import verdict as V  # noqa: E402
from app.providers import llm as L  # noqa: E402

Base.metadata.create_all(engine)
PASS, FAIL = [], []
def check(name, cond, extra=""):
    (PASS if cond else FAIL).append(name); print(("  ✅" if cond else "  ❌"), name, extra)

def matrix():
    j = O.judge_call
    check("WATCH +20% at the judging point => right", j("WATCH", 20, 25, False) == "right")
    check("WATCH spiked +80% then faded => right (upside showed up)", j("WATCH", -5, 80, False) == "right")
    check("WATCH rugged => wrong", j("STRONG_WATCH", 5, 60, True) == "wrong")
    check("WATCH -40% => wrong", j("WATCH", -40, 5, False) == "wrong")
    check("AVOID rugged => right", j("AVOID", -95, 0, True) == "right")
    check("AVOID +45% => wrong (missed runner)", j("RUG_RISK", 45, 60, False) == "wrong")
    check("AVOID -10% => mixed", j("AVOID", -10, 3, False) == "mixed")
    check("NEUTRAL flat => right, big move => mixed", j("NEUTRAL", 8, 12, False) == "right" and j("NEUTRAL", 60, 70, False) == "mixed")
    check("no 24h price => no judgement", j("WATCH", None, 50, False) is None)

def bucket(ts): return ts - ts % 300

def seed(now):
    t0 = now - 26 * 3600
    t0b = bucket(t0)
    with SessionLocal() as db:
        toks = {s: Token(chain="solana", address=f"{s}Mint", symbol=s) for s in
                ("PUMP", "RUG", "LIQRUG", "GONE", "NODATA", "FRESH", "REV", "DUP", "LOST")}
        db.add_all(toks.values()); db.flush()
        def verdict(sym, rating, created, price=1.0, liq=100_000, source="llm", ev=None):
            v = TokenVerdict(token_id=toks[sym].id, rating=rating, source=source, price_at=price,
                             liquidity_at=liq, created_at=created, evidence_json=ev or {})
            db.add(v); return v
        created = dt.datetime.utcfromtimestamp(t0)
        verdict("PUMP", "WATCH", created)
        verdict("RUG", "AVOID", created)
        verdict("LIQRUG", "WATCH", created, ev={"launch": {"sniper_pct": 31, "bundle_pct": 12},
                                                 "holders": {"top10_wallet_pct": 44},
                                                 "market": {"liquidity_usd": 15000, "price_change_1h_pct": 40}})
        verdict("GONE", "STRONG_WATCH", dt.datetime.utcfromtimestamp(now - 12 * 3600 - 600))
        verdict("LOST", "NEUTRAL", dt.datetime.utcfromtimestamp(now - 12 * 3600 - 1800))
        verdict("NODATA", "WATCH", dt.datetime.utcfromtimestamp(now - 31 * 3600))
        verdict("FRESH", "WATCH", dt.datetime.utcfromtimestamp(now - 2 * 3600))
        verdict("REV", "REVIEW", created)
        verdict("DUP", "AVOID", created, source="gate")
        verdict("DUP", "AVOID", created + dt.timedelta(hours=1), source="gate")   # same call again
        def path(sym, pts, liq_end=None):
            for off_h, p in pts:
                db.add(PriceSnapshot(token_id=toks[sym].id, ts=bucket(t0 + int(off_h * 3600)), price_usd=p))
            if liq_end is not None:
                for h in (12, 24):
                    db.add(LiquiditySnapshot(token_id=toks[sym].id, ts=bucket(t0 + h * 3600), liquidity_usd=liq_end))
        path("PUMP", [(0, 1.0), (1, 1.2), (3, 2.1), (6, 1.6), (12, 1.4), (24, 1.3)], liq_end=140_000)
        path("RUG", [(0, 1.0), (1, 0.9), (6, 0.3), (12, 0.1), (24, 0.04)], liq_end=900)
        path("LIQRUG", [(0, 1.0), (1, 1.1), (6, 0.8), (12, 0.6), (24, 0.5)], liq_end=10_000)
        path("DUP", [(0, 1.0), (1, 1.0), (6, 0.7), (12, 0.65), (24, 0.6)], liq_end=50_000)
        fresh_t = now - 2 * 3600
        db.add(PriceSnapshot(token_id=toks["FRESH"].id, ts=bucket(fresh_t + 3600), price_usd=1.05))
        db.commit()
        return {s: t.id for s, t in toks.items()}

async def service(now, ids):
    hits = []
    def dex(req):
        hits.append(str(req.url))
        return httpx.Response(200, json=[{"baseToken": {"address": "GONEMint"}, "priceUsd": "2.5",
                                          "liquidity": {"usd": 150_000}}])
    res = await O.update_outcomes(SessionLocal, now=now, transport=httpx.MockTransport(dex))
    with SessionLocal() as db:
        o = {db.get(Token, x.token_id).symbol: x for x in db.query(VerdictOutcome).all()}
        check("REVIEW verdicts are not scored", "REV" not in o)
        p = o["PUMP"]
        check("PUMP returns 1h/6h/12h/24h from snapshots",
              (p.ret_1h, p.ret_6h, p.ret_12h, p.ret_24h) == (20.0, 60.0, 40.0, 30.0),
              f"{p.ret_1h} {p.ret_6h} {p.ret_12h} {p.ret_24h}")
        check("PUMP judged at 12h: +40%, best +110%, liquidity +40%", p.judge_h == 12 and p.ret_judge == 40.0
              and p.max_up_judge == 110.0 and p.liq_change_judge == 40.0)
        check("PUMP WATCH => right; 24h numbers still to finish", p.result == "right" and p.rugged is False
              and p.status == "judged")
        r = o["RUG"]
        check("RUG: -90% at 12h and liquidity gone => rugged; AVOID right", r.rugged and r.result == "right")
        lr = o["LIQRUG"]
        check("LIQRUG: liquidity -90% => rugged even at -40% price; WATCH wrong", lr.rugged and lr.result == "wrong")
        g = o["GONE"]
        check("GONE: untracked token judged at 12h via one DexScreener call", g.ret_judge == 150.0
              and g.data_source == "dexscreener" and g.result == "right" and len(hits) == 1
              and "LOSTMint" in hits[0], str(hits))
        check("NODATA: no price at 12h (+6h grace) => no_data (not guessed)", o["NODATA"].status == "no_data"
              and o["NODATA"].ret_judge is None and o["NODATA"].result is None)
        check("LOST: no data yet, still inside the fallback window", o["LOST"].status == "pending")
        f = o["FRESH"]
        check("FRESH: 1h scored, still pending", f.ret_1h == 5.0 and f.status == "pending" and f.result is None)
    res2 = await O.update_outcomes(SessionLocal, now=now, transport=httpx.MockTransport(dex))
    check("idempotent: nothing re-judged, no repeat DexScreener call within 15 min",
          res2["judged"] == 0 and res2["fallback"] == 0 and len(hits) == 1, str(res2))
    res3 = await O.update_outcomes(SessionLocal, now=now + 16 * 60, transport=httpx.MockTransport(dex))
    check("unresolved fallback retried after 15 min, only for the unresolved token",
          len(hits) == 2 and "GONEMint" not in hits[1] and "LOSTMint" in hits[1], str(hits[1:]))
    with SessionLocal() as db:
        p = db.query(VerdictOutcome).filter_by(token_id=ids["PUMP"]).one()
        check("after 24h a judged call moves to done; result unchanged", p.status == "done"
              and p.result == "right" and p.liq_change_24h == 40.0 and p.max_up_24h == 110.0, str(res3))

def card(now):
    with SessionLocal() as db:
        sc = O.scorecard(db, 7, now=now)
    w = sc["by_rating"]["WATCH"]
    check("WATCH: 1 right, 1 wrong, 1 pending (no_data excluded from scored)",
          (w["right"], w["wrong"], w["scored"], w["pending"]) == (1, 1, 2, 1), str(w))
    check("repeated identical call counted once", sc["by_rating"]["AVOID"]["calls"] == 2, str(sc["by_rating"]["AVOID"]))
    check("worst miss listed", sc["worst_misses"] and sc["worst_misses"][0]["symbol"] == "LIQRUG")
    check("AI vs gate split", sc["by_source"]["llm"]["scored"] >= 3 and sc["by_source"]["gate"]["scored"] == 1)
    msg = O.format_scorecard(sc)
    check("Telegram scorecard text", "FOMO scorecard" in msg and "$LIQRUG" in msg and "Not financial advice" in msg)
    return sc

async def memory(now):
    with SessionLocal() as db:
        txt = O.track_record_text(db)
    check("track record built (>=5 scored)", txt is not None and "MISS: rated WATCH, rugged" in txt
          and "snipers 31%" in txt, str(txt)[:200])
    seen = []
    def h(req):
        b = json.loads(req.content); seen.append(b)
        out = '{"rating":"NEUTRAL","confidence":"low","reason":"r"}' if "RISK JUDGE" in b["system"] else "arg"
        return httpx.Response(200, json={"content": [{"type": "text", "text": out}], "usage": {}})
    ev = {"risk_engine": {"band": "LOW", "hard_fails": [], "flags": [], "score": 10},
          "launch": {"status": "ok", "early_buyers_funded_by_creator": 0, "bundle_pct": 0, "sniper_pct": 0,
                     "largest_shared_funder_group": 0},
          "exit": {"round_trip_loss_pct": 1, "buy_route": True, "sell_route": True},
          "market": {"liquidity_usd": 90_000}}
    await V.debate(ev, L.LLMClient(transport=httpx.MockTransport(h)), "q", "j", track_record=txt)
    judge_msg = seen[2]["messages"][0]["content"]
    check("judge prompt carries the track record; bull/bear do not",
          "TRACK RECORD" in judge_msg and "TRACK RECORD" not in seen[0]["messages"][0]["content"])

def daily(now):
    from app.engine import telegram
    sent = []
    telegram.is_configured = lambda: True
    telegram.send_text = lambda text, **k: (sent.append(text), {"ok": True})[1]
    early = dt.datetime(2026, 11, 20, 4, 0)
    check("not before the configured hour", O.maybe_send_daily(SessionLocal, early)["sent"] is False and not sent)
    at = dt.datetime(2026, 11, 20, 7, 0)
    check("sent once at/after the hour", O.maybe_send_daily(SessionLocal, at)["sent"] and len(sent) == 1)
    check("not twice the same day", not O.maybe_send_daily(SessionLocal, at + dt.timedelta(hours=3))["sent"] and len(sent) == 1)
    check("again the next day", O.maybe_send_daily(SessionLocal, at + dt.timedelta(days=1))["sent"] and len(sent) == 2)

def api():
    from fastapi.testclient import TestClient
    from app.main import app
    c = TestClient(app)
    base = "/api" if c.get("/api/verdicts/scorecard").status_code == 200 else ""
    j = c.get(f"{base}/verdicts/scorecard").json()
    check("API scorecard", "by_rating" in j and j["overall"]["scored"] >= 5)
    j = c.get(f"{base}/tokens/PUMPMint/verdict").json()
    check("API verdict carries its outcome", j["latest"]["outcome"]["result"] == "right")

now = int(time.time())
matrix()
ids = seed(now)
asyncio.run(service(now, ids))
card(now)
asyncio.run(memory(now))
daily(now)
api()
print(f"\n{len(PASS)} passed, {len(FAIL)} failed")
os.remove(dbpath)
sys.exit(1 if FAIL else 0)
