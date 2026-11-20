"""AI verdict — a Solana-native take on the TradingAgents pattern.

    evidence (analysts) ─► gate ─► bull + bear (quick model) ─► judge (JSON) ─► caps
                            │
                            └─► RUG_RISK / REVIEW without any LLM call

* Analysts are the code checks that already exist (Helius safety + holders, Jupiter
  exit, launch forensics, DexScreener/GeckoTerminal market, the risk engine). The
  evidence pack holds ONLY their real outputs; unknown values are null.
* Gate: a hard fail => RUG_RISK; missing core data => REVIEW. No LLM spend.
* The LLM never sees anything but the evidence pack, and the code clamps its rating:
  it can never rate a token above what the risk engine allows (caps()).
* No LLM key => rules_verdict(): the same evidence, transparent thresholds.
* Creator analyst: the creator's earlier launches, scored with GMGN's dev-score model
  (engine/dev_score.py). A dumping creator caps the rating however clean the coin looks.

Ratings are research labels for what to WATCH, never buy/sell instructions.
"""
from __future__ import annotations

import hashlib
import json
import math

RATINGS = ["RUG_RISK", "AVOID", "NEUTRAL", "WATCH", "STRONG_WATCH"]
LABEL = {"RUG_RISK": "Rug risk", "AVOID": "Avoid", "NEUTRAL": "Neutral", "WATCH": "Watch",
         "STRONG_WATCH": "Strong watch", "REVIEW": "Review (missing data)"}
LAUNCH_RECENT_H = 72.0


def _r(v, nd=2):
    return None if v is None else round(float(v), nd)


def build_evidence(token, facts: dict, score) -> dict:
    """token: Token row; facts: latest_contract_facts entry; score: latest TrendScore (or None)."""
    comp = (score.components_json or {}) if score is not None else {}
    s = comp.get("summary") or {}
    lf = facts.get("launch") or {}
    cr = facts.get("creator") or {}
    fee = facts.get("transfer_fee_bps")
    return {
        "token": {"symbol": token.symbol, "name": token.name, "dex": token.dex,
                  "age_hours": _r(s.get("age_hours"), 1)},
        "market": {"price_usd": s.get("price"), "mcap_usd": _r(s.get("mcap"), 0),
                   "liquidity_usd": _r(s.get("liquidity"), 0), "volume_1h_usd": _r(s.get("volume_1h"), 0),
                   "volume_24h_usd": _r(s.get("volume_24h"), 0), "price_change_1h_pct": _r(s.get("price_change_h1"), 1),
                   "price_change_24h_pct": _r(s.get("price_change_h24"), 1),
                   "buy_share_1h": _r(s.get("buy_share"), 2), "txns_1h": s.get("txns_1h"),
                   "holders": s.get("holders"), "holder_growth_pct": _r(s.get("holder_growth"), 1),
                   "momentum": s.get("momentum_band"), "sell_pressure": s.get("sellp_band"),
                   "liquidity_health": s.get("liq_band"), "organic_score": _r(s.get("organic"), 0),
                   "source": "dexscreener+geckoterminal" if s.get("real_market") else (comp.get("sources") or {}).get("market")},
        "contract": {"verified": facts.get("source") == "helius",
                     "mint_authority_active": facts.get("mint_authority"),
                     "freeze_authority_active": facts.get("freeze_authority"),
                     "token_2022": facts.get("token_2022"),
                     "risky_extensions": facts.get("token_extensions"),
                     "transfer_fee_pct": None if fee is None else fee / 100, "source": "helius"},
        "holders": {"top1_wallet_pct": facts.get("top1_wallet_pct"),
                    "top10_wallet_pct": facts.get("top10_wallet_pct"),
                    "note": "pools, bonding curves and burn excluded", "source": "helius"},
        "exit": {"buy_route": facts.get("exit_buy_route"), "sell_route": facts.get("exit_sell_route"),
                 "round_trip_loss_pct": facts.get("exit_round_trip_loss_pct"),
                 "test_size_sol": facts.get("exit_sol_in"), "source": "jupiter quote"},
        "launch": {"status": lf.get("status"), "bundle_pct": lf.get("bundle_pct"),
                   "sniper_pct": lf.get("sniper_pct"), "dev_buy_pct": lf.get("dev_buy_pct"),
                   "dev_holds_pct_at_check": lf.get("dev_holds_pct"),
                   "snipers_hold_pct_at_check": lf.get("snipers_hold_pct"),
                   "early_buyers_funded_by_creator": lf.get("funded_by_creator"),
                   "largest_shared_funder_group": lf.get("shared_funder_wallets"),
                   "fresh_wallets_among_checked": lf.get("fresh_early_wallets"),
                   "wallets_checked": lf.get("funder_checked"), "source": "helius launch forensics"},
        "creator": {"status": cr.get("status"), "band": cr.get("band"), "score": cr.get("total"),
                    "conduct": cr.get("conduct"), "power": cr.get("power"), "dump_tier": cr.get("dump_tier"),
                    "earlier_launches": cr.get("launches"), "launches_he_traded": cr.get("traded"),
                    "dumps_at_open": cr.get("dumps"), "fastest_first_sell_s": cr.get("fastest_first_sell_s"),
                    "graduated": cr.get("graduated"), "still_alive": cr.get("alive"),
                    "best_peak_mcap_usd": cr.get("peak_mcap_usd"), "summary": cr.get("summary"),
                    "source": "creator track record (GMGN dev-score model, helius+dexscreener)"},
        "risk_engine": {"score": _r(score.risk if score is not None else None, 0),
                        "band": score.risk_class if score is not None else None,
                        "flags": (comp.get("risk_flags") or [])[:6],
                        "hard_fails": comp.get("risk_hard_fails") or []},
    }


def evidence_hash(ev: dict) -> str:
    """Coarse fingerprint: changes only when something material changes."""
    m, x, la, rk = ev["market"], ev["exit"], ev["launch"], ev["risk_engine"]
    bucket = lambda v: None if not v else int(math.log2(max(float(v), 1)))  # noqa: E731
    sign = lambda v: None if v is None else (2 if v > 25 else 1 if v > 0 else -1 if v > -25 else -2)  # noqa: E731
    key = [rk["band"], sorted(rk["hard_fails"]), x["sell_route"], la["status"], la["early_buyers_funded_by_creator"],
           (ev.get("creator") or {}).get("band"),
           bucket(m["liquidity_usd"]), bucket(m["mcap_usd"]), sign(m["price_change_1h_pct"]),
           ev["contract"]["mint_authority_active"], ev["contract"]["freeze_authority_active"],
           None if ev["holders"]["top10_wallet_pct"] is None else int(ev["holders"]["top10_wallet_pct"] // 10)]
    return hashlib.sha1(json.dumps(key, default=str).encode()).hexdigest()[:16]


def gate(ev: dict, launch_expected: bool) -> dict | None:
    """Deterministic verdict that needs no LLM, or None to continue to the debate."""
    hard = ev["risk_engine"]["hard_fails"]
    if hard:
        return {"rating": "RUG_RISK", "confidence": "high", "source": "gate",
                "reason": "Hard fail: " + "; ".join(hard) + ".", "positives": [], "risks": hard}
    missing = []
    if not ev["contract"]["verified"]:
        missing.append("contract permissions (Helius)")
    if ev["holders"]["top10_wallet_pct"] is None:
        missing.append("holder concentration")
    if ev["exit"]["sell_route"] is None:
        missing.append("sell-route check (Jupiter)")
    if ev["risk_engine"]["band"] is None or ev["market"]["liquidity_usd"] is None:
        missing.append("market data")
    if launch_expected and ev["launch"]["status"] is None:
        missing.append("launch forensics")
    if missing:
        return {"rating": "REVIEW", "confidence": "low", "source": "gate",
                "reason": "Waiting for real data: " + ", ".join(missing) + ".", "positives": [], "risks": []}
    return None


def caps(ev: dict) -> tuple[str, list[str]]:
    """Highest rating the evidence allows, and why. The LLM can never exceed it."""
    top, why = "STRONG_WATCH", []
    def cap(r, msg):
        nonlocal top
        if RATINGS.index(r) < RATINGS.index(top):
            top = r
        why.append(msg)
    band = ev["risk_engine"]["band"]
    if band == "HIGH":
        cap("AVOID", "risk engine HIGH")
    elif band == "ELEVATED":
        cap("NEUTRAL", "risk engine ELEVATED")
    la = ev["launch"]
    if (la["early_buyers_funded_by_creator"] or 0) >= 1:
        cap("AVOID", "creator funded early buyers")
    if (la["bundle_pct"] or 0) >= 15 or (la["sniper_pct"] or 0) >= 25 or (la["largest_shared_funder_group"] or 0) >= 3:
        cap("NEUTRAL", "manipulated launch")
    if (ev["exit"]["round_trip_loss_pct"] or 0) >= 10 or ev["exit"]["buy_route"] is False:
        cap("NEUTRAL", "costly or missing exit route")
    if (ev["market"]["liquidity_usd"] or 0) < 10_000:
        cap("NEUTRAL", "liquidity under $10k")
    if la["status"] != "ok":
        cap("WATCH", "launch wallets not analysed")
    cr = ev.get("creator") or {}
    cb = cr.get("band")
    # The score is GMGN's; this stricter rule is ours: frequent dumping at open is disqualifying
    # for a coin rating even when power lifts the creator's total into "mixed".
    if cr.get("dump_tier") == "systematic":
        cap("AVOID", "creator dumps at open in most launches")
    elif cr.get("dump_tier") == "often":
        cap("NEUTRAL", "creator often dumps at open")
    if cb == "stay_away":
        cap("AVOID", "creator dumps on holders")
    elif cb == "avoid":
        cap("NEUTRAL", "creator track record poor")
    elif cb == "mixed":
        cap("WATCH", "creator track record mixed")
    elif cb == "new":
        cap("WATCH", "creator has no earlier launches to judge")
    elif cb is None:
        cap("WATCH", "creator track record not checked")
    return top, why


def clamp(rating: str, ev: dict) -> tuple[str, list[str]]:
    top, why = caps(ev)
    if rating in RATINGS and RATINGS.index(rating) > RATINGS.index(top):
        return top, why
    return rating, []


def rules_verdict(ev: dict) -> dict:
    """No-LLM verdict from the same evidence (transparent thresholds)."""
    m, rk = ev["market"], ev["risk_engine"]
    pos, neg = [], list(rk["flags"][:3])
    liq, risk = m["liquidity_usd"] or 0, rk["score"] if rk["score"] is not None else 100
    if risk < 30:
        pos.append(f"low risk score ({risk:.0f})")
    if liq >= 50_000:
        pos.append(f"liquidity ${liq:,.0f}")
    if (m["organic_score"] or 0) >= 55:
        pos.append(f"organic score {m['organic_score']:.0f}")
    bs = m["buy_share_1h"]
    if bs is not None and 0.5 <= bs <= 0.85:
        pos.append(f"healthy buy share {bs:.0%}")
    if (m["price_change_1h_pct"] or 0) > 0 and (m["holder_growth_pct"] or 0) > 0:
        pos.append("price and holders rising")
    cr = ev.get("creator") or {}
    if cr.get("band") == "clean":
        pos.append(f"creator record clean ({cr['score']:.0f}/100)")
    elif cr.get("band") in ("avoid", "stay_away"):
        neg.insert(0, f"creator record {cr['score']:.0f}/100: {cr.get('summary') or cr['band']}")
    score = len(pos) - (2 if risk >= 50 else 0)
    rating = "STRONG_WATCH" if score >= 5 else "WATCH" if score >= 3 else "AVOID" if risk >= 70 else "NEUTRAL"
    rating, capped = clamp(rating, ev)
    reason = (("Strengths: " + ", ".join(pos) + ". ") if pos else "") + \
             (("Concerns: " + "; ".join(neg) + ".") if neg else "No major flags.")
    if capped:
        reason += " Capped by: " + ", ".join(capped) + "."
    return {"rating": rating, "confidence": "medium" if len(pos) >= 3 or risk >= 70 else "low",
            "source": "rules", "reason": reason, "positives": pos, "risks": neg}


# ---------------------------------------------------------------- LLM debate
SYSTEM_COMMON = (
    "You analyse Solana meme coins for a research dashboard. Use ONLY the JSON evidence given. "
    "null means unknown: treat unknown as a risk, never as safe, and never invent numbers. "
    "Cite evidence by field name, e.g. launch.sniper_pct=12. Meme coins are extremely risky and most go to zero. "
    "You never tell anyone to buy or sell.")
BULL = SYSTEM_COMMON + (" You are the BULL researcher: make the strongest honest case that this coin deserves "
                        "close attention. At most 110 words, plain text, no headings.")
BEAR = SYSTEM_COMMON + (" You are the BEAR researcher: make the strongest honest case that this coin is a trap, "
                        "a rug or not worth attention. At most 110 words, plain text, no headings.")
JUDGE = SYSTEM_COMMON + (
    " You are the RISK JUDGE. Weigh the evidence and both arguments, then reply with ONE JSON object only:\n"
    '{"rating": "STRONG_WATCH|WATCH|NEUTRAL|AVOID|RUG_RISK", "confidence": "low|medium|high", '
    '"reason": "max 45 words citing fields", "positives": ["max 3 short items"], "risks": ["max 3 short items"]}\n'
    "STRONG_WATCH needs clean contract, real sell route, clean launch, a clean creator record (creator.band=clean) "
    "and healthy liquidity and flow. A creator who dumped his earlier coins (creator.dumps_at_open) is a major risk. "
    "When evidence conflicts or is thin, choose the more cautious rating.")


def _ev_text(ev: dict) -> str:
    return "EVIDENCE:\n" + json.dumps(ev, separators=(",", ":"), default=str)


async def debate(ev: dict, llm, quick_model: str, judge_model: str,
                 track_record: str | None = None) -> dict:
    """Bull + bear (quick model), then judge (JSON). Raises LLMError on failure.
    track_record: the judge's own recent scorecard and worst misses (memory)."""
    from ..providers.llm import extract_json
    text = _ev_text(ev)
    bull = (await llm.complete(BULL, text, quick_model, 260))[:900]
    bear = (await llm.complete(BEAR, text, quick_model, 260))[:900]
    memory = f"\n\nTRACK RECORD:\n{track_record[:1200]}" if track_record else ""
    raw = await llm.complete(JUDGE, f"{text}\n\nBULL:\n{bull}\n\nBEAR:\n{bear}{memory}", judge_model, 350)
    out = extract_json(raw)
    rating = str(out.get("rating", "")).upper().strip()
    if rating not in RATINGS:
        return {"rating": "REVIEW", "confidence": "low", "source": "llm",
                "reason": "AI judge returned no valid rating.", "positives": [], "risks": [],
                "bull": bull, "bear": bear}
    final, capped = clamp(rating, ev)
    conf = str(out.get("confidence", "low")).lower()
    reason = str(out.get("reason", ""))[:400]
    if capped:
        reason += f" [Capped from {LABEL[rating]} to {LABEL[final]}: {', '.join(capped)}.]"
        conf = "low" if conf == "high" else conf
    as_list = lambda v: [str(i)[:120] for i in v][:3] if isinstance(v, list) else []  # noqa: E731
    return {"rating": final, "confidence": conf if conf in ("low", "medium", "high") else "low",
            "source": "llm", "reason": reason, "positives": as_list(out.get("positives")),
            "risks": as_list(out.get("risks")), "bull": bull, "bear": bear,
            "llm_rating": rating}
