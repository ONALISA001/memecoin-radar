"""AI verdict tests (gate, caps, rules, LLM debate, scheduling, Telegram). No network."""
import asyncio, datetime as dt, json, os, sys, tempfile, time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
fd, dbpath = tempfile.mkstemp(suffix=".db"); os.close(fd)
os.environ["DATABASE_URL"] = f"sqlite:///{dbpath}"
os.environ["LLM_API_KEY"] = "LLMSECRET789"
os.environ["LLM_PROVIDER"] = "anthropic"
os.environ["VERDICT_DAILY_LLM_MAX"] = "2"

import httpx  # noqa: E402
from app import config  # noqa: E402
from app.database import Base, engine, SessionLocal  # noqa: E402
from app.engine import verdict as V  # noqa: E402
from app.models import Token, TokenRiskCheck, TokenLaunchForensics, TokenVerdict, TrendScore  # noqa: E402
from app.providers import llm as L  # noqa: E402
from app.services import ai_verdict as SVC  # noqa: E402

Base.metadata.create_all(engine)
PASS, FAIL = [], []
def check(name, cond, extra=""):
    (PASS if cond else FAIL).append(name); print(("  ✅" if cond else "  ❌"), name, extra)

def ev(**over):
    e = {"token": {"symbol": "T", "name": "T", "dex": "raydium", "age_hours": 3},
         "market": {"price_usd": 0.001, "mcap_usd": 900_000, "liquidity_usd": 120_000, "volume_1h_usd": 80_000,
                    "volume_24h_usd": 400_000, "price_change_1h_pct": 12, "price_change_24h_pct": 40,
                    "buy_share_1h": 0.62, "txns_1h": 900, "holders": 2500, "holder_growth_pct": 4,
                    "momentum": "strong", "sell_pressure": "low", "liquidity_health": "healthy",
                    "organic_score": 72, "source": "dexscreener+geckoterminal"},
         "contract": {"verified": True, "mint_authority_active": False, "freeze_authority_active": False,
                      "token_2022": False, "risky_extensions": [], "transfer_fee_pct": None, "source": "helius"},
         "holders": {"top1_wallet_pct": 3, "top10_wallet_pct": 18, "note": "", "source": "helius"},
         "exit": {"buy_route": True, "sell_route": True, "round_trip_loss_pct": 2.1, "test_size_sol": 0.1, "source": "j"},
         "launch": {"status": "ok", "bundle_pct": 2, "sniper_pct": 6, "dev_buy_pct": 1, "dev_holds_pct_at_check": 1,
                    "snipers_hold_pct_at_check": 2, "early_buyers_funded_by_creator": 0,
                    "largest_shared_funder_group": 1, "fresh_wallets_among_checked": 1, "wallets_checked": 5,
                    "source": "h"},
         "risk_engine": {"score": 18, "band": "LOW", "flags": [], "hard_fails": []}}
    for path, val in over.items():
        sec, key = path.split("__")
        e[sec][key] = val
    return e

def unit():
    g = V.gate(ev(risk_engine__hard_fails=["No sell route"]), False)
    check("hard fail => RUG_RISK without LLM", g["rating"] == "RUG_RISK" and g["source"] == "gate")
    g = V.gate(ev(exit__sell_route=None), False)
    check("missing sell-route check => REVIEW", g["rating"] == "REVIEW" and "Jupiter" in g["reason"])
    check("recent launch without forensics => REVIEW", V.gate(ev(launch__status=None), True)["rating"] == "REVIEW")
    check("old token without forensics is not blocked", V.gate(ev(launch__status=None), False) is None)
    check("complete evidence passes the gate", V.gate(ev(), True) is None)

    check("HIGH risk band caps at AVOID", V.clamp("STRONG_WATCH", ev(risk_engine__band="HIGH"))[0] == "AVOID")
    check("creator-funded buyers cap at AVOID", V.clamp("WATCH", ev(launch__early_buyers_funded_by_creator=1))[0] == "AVOID")
    check("bundled launch caps at NEUTRAL", V.clamp("STRONG_WATCH", ev(launch__bundle_pct=20))[0] == "NEUTRAL")
    check("thin liquidity caps at NEUTRAL", V.clamp("WATCH", ev(market__liquidity_usd=6000))[0] == "NEUTRAL")
    check("a cautious rating is never raised", V.clamp("AVOID", ev())[0] == "AVOID")

    r = V.rules_verdict(ev())
    check("rules: clean, liquid, organic, rising => STRONG_WATCH", r["rating"] == "STRONG_WATCH", r["reason"])
    r = V.rules_verdict(ev(risk_engine__score=75, risk_engine__band="HIGH", risk_engine__flags=["x"]))
    check("rules: high risk => AVOID", r["rating"] == "AVOID")
    check("evidence hash ignores small moves", V.evidence_hash(ev()) == V.evidence_hash(ev(market__liquidity_usd=125_000)))
    check("evidence hash changes on material change", V.evidence_hash(ev()) != V.evidence_hash(ev(risk_engine__band="HIGH")))

def anthropic_mock(judge_reply, seen):
    def handler(req):
        body = json.loads(req.content); seen.append((req, body))
        sys_p = body["system"]
        text = judge_reply if "RISK JUDGE" in sys_p else ("Bull case: launch.sniper_pct=6" if "BULL" in sys_p else "Bear case")
        return httpx.Response(200, json={"content": [{"type": "text", "text": text}],
                                         "usage": {"input_tokens": 900, "output_tokens": 80}})
    return httpx.MockTransport(handler)

async def debate_tests():
    seen = []
    llm = L.LLMClient(transport=anthropic_mock('Here: ```json\n{"rating":"STRONG_WATCH","confidence":"high",'
                                               '"reason":"clean","positives":["a","b","c","d"],"risks":[]}\n```', seen))
    out = await V.debate(ev(risk_engine__band="ELEVATED"), llm, "q-model", "j-model")
    check("3 calls: bull, bear, judge", llm.calls == 3 and [b["model"] for _, b in seen] == ["q-model", "q-model", "j-model"])
    check("judge sees both arguments", "BULL:\nBull case" in seen[2][1]["messages"][0]["content"])
    check("key sent as header only", seen[0][0].headers["x-api-key"] == "LLMSECRET789" and "LLMSECRET789" not in str(seen[0][0].url))
    check("ELEVATED band: judge's STRONG_WATCH capped to NEUTRAL", out["rating"] == "NEUTRAL"
          and out["llm_rating"] == "STRONG_WATCH" and "Capped" in out["reason"], out["reason"])
    check("high confidence lowered after a cap", out["confidence"] == "low")
    check("lists trimmed to 3", len(out["positives"]) == 3)
    out = await V.debate(ev(), L.LLMClient(transport=anthropic_mock('{"rating":"MOON","confidence":"high"}', [])), "q", "j")
    check("invalid rating => REVIEW", out["rating"] == "REVIEW")
    try:
        await V.debate(ev(), L.LLMClient(transport=anthropic_mock("I think it's great", [])), "q", "j")
        check("no JSON => LLMError", False)
    except L.LLMError:
        check("no JSON => LLMError", True)
    def boom(req): return httpx.Response(401, json={"error": "bad key LLMSECRET789"})
    try:
        await L.LLMClient(transport=httpx.MockTransport(boom)).complete("s", "u", "m")
    except L.LLMError as e:
        check("HTTP error => LLMError, key scrubbed", "LLMSECRET789" not in str(e), str(e))

async def service_tests():
    now = int(time.time())
    with SessionLocal() as db:
        good = Token(chain="solana", address="GoodMint", symbol="GOOD", launch_alert="sent",
                     token_created_at=time.time() * 1000 - 2 * 3.6e6)
        rug = Token(chain="solana", address="RugMint", symbol="RUG")
        mock = Token(chain="solana", address="MockX", symbol="MOCK")
        db.add_all([good, rug, mock]); db.flush()
        summary = {"price": 0.001, "mcap": 900_000, "liquidity": 120_000, "volume_1h": 80_000, "buy_share": 0.62,
                   "price_change_h1": 12, "holder_growth": 4, "organic": 72, "age_hours": 2, "real_market": True}
        for t, hard in ((good, []), (rug, ["No sell route"]), (mock, [])):
            db.add(TrendScore(token_id=t.id, ts=now - 60, trend=50, risk=85 if hard else 18,
                              risk_class="HIGH" if hard else "LOW", kind="live",
                              components_json={"summary": summary, "risk_flags": [], "risk_hard_fails": hard}))
            db.add(TokenRiskCheck(token_id=t.id, ok=True, supply=1e9, top1_wallet_pct=3, top10_wallet_pct=18,
                                  holders_count=2500, exit_sol_in=0.1, exit_buy_route=True,
                                  exit_sell_route=not hard, exit_round_trip_loss_pct=2.0))
        db.add(TokenLaunchForensics(token_id=good.id, status="ok", bundle_pct=2, sniper_pct=6, dev_buy_pct=1,
                                    funded_by_creator=0, shared_funder_wallets=1))
        db.commit()
        gid, rid = good.id, rug.id

    seen = []
    llm = L.LLMClient(transport=anthropic_mock('{"rating":"WATCH","confidence":"medium","reason":"ok",'
                                               '"positives":["liquid"],"risks":["meme"]}', seen))
    sent = []
    SVC.telegram.is_configured = lambda: True
    SVC.telegram.send_text = lambda text, **k: (sent.append(text), {"ok": True})[1]
    res = await SVC.run_verdicts(SessionLocal, llm)
    with SessionLocal() as db:
        v = {r.token_id: r for r in db.query(TokenVerdict).all()}
        check("mock token never judged", len(v) == 2, str(res))
        check("rug: safety gate, no LLM", v[rid].rating == "RUG_RISK" and v[rid].source == "gate")
        check("good: AI debate verdict stored", v[gid].rating == "WATCH" and v[gid].source == "llm"
              and v[gid].bull and v[gid].bear and v[gid].model == config.LLM_JUDGE_MODEL)
        check("price/mcap/liquidity at verdict kept for outcome tracking",
              v[gid].price_at == 0.001 and v[gid].liquidity_at == 120_000)
        check("only 3 LLM calls this tick", llm.calls == 3)
        check("Telegram follow-up for the launch-alerted coin only, once",
              len(sent) == 1 and "FOMO verdict · $GOOD: Watch" in sent[0] and "not a buy or sell" in sent[0])

    res = await SVC.run_verdicts(SessionLocal, llm)
    check("unchanged evidence => not re-judged", res["due"] == 0 and llm.calls == 3, str(res))
    check("follow-up not repeated", len(sent) == 1)

    with SessionLocal() as db:       # material change (risk band) after the minimum gap
        for r in db.query(TokenVerdict).all():
            r.created_at = dt.datetime.utcnow() - dt.timedelta(minutes=45)
        ts = db.query(TrendScore).filter_by(token_id=gid).one()
        ts.risk_class, ts.risk = "ELEVATED", 55
        db.commit()
    res = await SVC.run_verdicts(SessionLocal, llm)
    with SessionLocal() as db:
        latest = SVC.latest_verdicts(db, [gid])[gid]
        check("material change => re-judged by AI", res["llm"] == 1 and latest.source == "llm")
        check("still capped by the risk band", latest.rating in ("NEUTRAL", "AVOID", "RUG_RISK"), latest.rating)
        for r in db.query(TokenVerdict).all():
            r.created_at = dt.datetime.utcnow() - dt.timedelta(minutes=45)
        ts = db.query(TrendScore).filter_by(token_id=gid).one()
        ts.risk_class, ts.risk = "LOW", 20
        db.commit()
    res = await SVC.run_verdicts(SessionLocal, llm)
    with SessionLocal() as db:
        latest = SVC.latest_verdicts(db, [gid])[gid]
        check("daily AI cap reached => rule-based verdict, labelled", latest.source == "rules"
              and "daily AI budget" in latest.reason and res["llm"] == 0, str(res))

    def down(req): return httpx.Response(503, json={})
    with SessionLocal() as db:
        db.query(TokenVerdict).delete(); db.commit()
    res = await SVC.run_verdicts(SessionLocal, L.LLMClient(transport=httpx.MockTransport(down)))
    with SessionLocal() as db:
        latest = SVC.latest_verdicts(db, [gid])[gid]
        check("AI down => rules verdict with a note", latest.source == "rules" and "AI unavailable" in latest.reason
              and res["llm_errors"] == 1, latest.reason)

    from fastapi.testclient import TestClient
    from app.main import app
    c = TestClient(app)
    j = c.get("/api/tokens/GoodMint/verdict").json() if c.get("/api/tokens/GoodMint/verdict").status_code == 200 \
        else c.get("/tokens/GoodMint/verdict").json()
    check("API: token verdict with evidence", j.get("available") and "evidence" in j["latest"], str(j)[:120])

unit()
asyncio.run(debate_tests())
asyncio.run(service_tests())
print(f"\n{len(PASS)} passed, {len(FAIL)} failed")
os.remove(dbpath)
sys.exit(1 if FAIL else 0)
