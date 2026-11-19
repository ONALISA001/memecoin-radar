"""Risk engine (§8). Higher score = higher estimated observable risk.
Language is deliberately cautious (§8): 'elevated', 'potential manipulation',
'concentrated ownership' — never 'guaranteed scam'. Missing data never
improves a score (§19)."""
from __future__ import annotations

from ..config import ANOMALY_Z
from .context import TokenContext

# Points charged while mint/freeze authority are still unknown (no Helius check yet).
CONTRACT_UNVERIFIED_POINTS = 5

RISK_BANDS = [(70, "HIGH"), (50, "ELEVATED"), (30, "MODERATE"), (0, "LOW")]


def _onoff(v) -> str:
    return "unknown" if v is None else ("ON" if v else "off")


def classify(score: float) -> str:
    for lo, name in RISK_BANDS:
        if score >= lo:
            return name
    return "LOW"


def calculate_risk(ctx: TokenContext, facts: dict) -> dict:
    d = ctx.d
    flags: list[str] = []
    events: list[dict] = []

    # ---- liquidity risk /30 ----
    liq = d["liq_now"]
    p_liq = 14 if liq < 5_000 else 10 if liq < 20_000 else 6 if liq < 50_000 else \
        3 if liq < 150_000 else 0
    drop = d["liq_chg_3h"]
    p_drop = 12 if drop <= -30 else 7 if drop <= -15 else 3 if drop <= -5 else 0
    p_ratio = 4 if d["vol_liq_ratio"] > 5 else 0
    if drop <= -25:
        flags.append(f"Liquidity declined {drop:.0f}% in 3h — potential withdrawal")
        events.append({"category": "liquidity", "severity": "high",
                       "message": f"Liquidity dropped {drop:.0f}% over 3 hours "
                                  f"(${d['liq_3h_ago']:,.0f} → ${liq:,.0f})."})
    elif drop <= -10:
        flags.append(f"Liquidity down {drop:.0f}% (3h)")
    if p_liq >= 10:
        flags.append(f"Insufficient liquidity (${liq:,.0f})")
    if p_ratio:
        flags.append(f"Volume {d['vol_liq_ratio']:.1f}× liquidity — thin market")
    liq_risk = min(30, p_liq + p_drop + p_ratio)

    # ---- holder concentration /25 ----
    t10 = d["top10_now"]
    dep = d["deployer_pct_now"]
    p_t10 = 18 if t10 >= 60 else 14 if t10 >= 45 else 10 if t10 >= 35 else \
        6 if t10 >= 25 else 3 if t10 >= 15 else 1
    p_dep = 7 if dep >= 15 else 4 if dep >= 8 else 2 if dep >= 3 else 0
    if t10 >= 50:
        flags.append(f"Concentrated ownership: top-10 wallets ≈ {t10:.0f}% of supply")
        events.append({"category": "concentration", "severity": "high",
                       "message": f"Top-10 wallets control ≈{t10:.0f}% of supply."})
    elif t10 >= 35:
        flags.append(f"Top-10 wallets hold ≈{t10:.0f}%")
    if dep >= 8:
        flags.append(f"Deployer-linked share ≈{dep:.0f}%")
    concentration = min(25, p_t10 + p_dep)

    # ---- contract risk /15 ----
    # Real facts come from Helius (services/contract_facts.py). None = not verified
    # yet, which is scored as a partial risk: missing data never improves a score.
    src = facts.get("source", "unverified")
    simulated = src.startswith("mock")
    tag = " (simulated chain data)" if simulated else ""
    mint, freeze = facts.get("mint_authority"), facts.get("freeze_authority")
    p_contract = 0.0
    if mint is None and freeze is None:
        p_contract += CONTRACT_UNVERIFIED_POINTS
        flags.append("Contract permissions not verified yet — treated as a risk until checked")
    if mint:
        p_contract += 8
        flags.append("Mint authority appears ENABLED — supply can be inflated")
        events.append({"category": "contract", "severity": "warn",
                       "message": f"Mint authority reported enabled{tag}."})
    if freeze:
        p_contract += 7
        flags.append("Freeze authority appears ENABLED — transfers can be frozen")
        events.append({"category": "contract", "severity": "warn",
                       "message": f"Freeze authority reported enabled{tag}."})
    if facts.get("token_2022"):
        p_contract += 3
        flags.append("Token-2022 program — may carry transfer fees/extensions")
    lp = facts.get("lp_burned_pct")
    if lp is not None and lp < 30:
        flags.append(f"Only ~{lp:.0f}% of LP reported burned/locked")
    contract = min(15, p_contract)

    # ---- deployer risk /10 (no real source yet → unavailable, scored 0) ----
    p_deployer = 0.0
    if facts.get("deployer_suspicious"):
        p_deployer += 6
        flags.append(f"Deployer shows suspicious launch patterns{tag}")
        events.append({"category": "deployer", "severity": "warn",
                       "message": f"Deployer wallet matches patterns of repeated short-lived launches{tag}."})
    prev = facts.get("deployer_prev_launches")
    if prev is not None:
        p_deployer += 4 if prev >= 5 else 2 if prev >= 3 else 0
        if prev >= 3:
            flags.append(f"Deployer linked to ~{prev} previous launches")
    deployer = min(10, p_deployer)

    # ---- market behavior /10 ----
    p_beh = 0.0
    pc = d["price_chg_1h"]
    p_beh += 5 if abs(pc) > 80 else 3 if abs(pc) > 40 else 0
    if d["buy_share_1h"] > 0.9:
        p_beh += 3
        flags.append(f"Abnormal buy skew ({d['buy_share_1h']:.0%}) — few sellers")
    elif d["buy_share_1h"] > 0.8:
        p_beh += 2
    if d["whale_net_24h"] < -30_000:
        p_beh += 2
        flags.append(f"Net whale outflow ≈ ${abs(d['whale_net_24h']):,.0f} (24h)")
        events.append({"category": "whale", "severity": "warn",
                       "message": f"Whales net-sold ≈ ${abs(d['whale_net_24h']):,.0f} over 24h (simulated)."})
    behavior = min(10, p_beh)

    # ---- social manipulation /10 ----
    p_soc = 0.0
    ratio = d["uniques_1h"] / d["mentions_1h"] if d["mentions_1h"] > 5 else 1.0
    p_soc += 4 if ratio < 0.3 else 2 if ratio < 0.5 else 0
    lowq = d["lowq_24h"]
    p_soc += 3 if lowq > 0.6 else 2 if lowq > 0.4 else 0
    if ratio < 0.3 or lowq > 0.55:
        flags.append("Potential manipulation: social activity concentrated in "
                     "few / low-quality accounts")
        events.append({"category": "social", "severity": "warn",
                       "message": f"Social activity looks bot-heavy (unique/mention "
                                  f"{ratio:.0%}, low-quality share {lowq:.0%}) — simulated flags."})
    social = min(10, p_soc)

    score = min(100.0, liq_risk + concentration + contract + deployer + behavior + social)
    band = classify(score)
    for key, z in (ctx.anomalies or {}).items():
        if abs(z) >= ANOMALY_Z:
            events.append({"category": "anomaly", "severity": "info",
                           "message": f"Anomalous {key} activity (z={z:+.1f} vs 24h baseline)."})

    return {
        "risk": round(score, 1),
        "classification": band,
        "flags": flags[:8],
        "events": events,
        "components": [
            {"label": "Liquidity risk", "points": round(liq_risk, 1), "max": 30,
             "value": f"${liq:,.0f}, {drop:+.1f}% (3h)"},
            {"label": "Holder concentration", "points": round(concentration, 1), "max": 25,
             "value": f"top10 ≈{t10:.0f}%"},
            {"label": "Contract permissions", "points": round(contract, 1), "max": 15,
             "value": "mint " + _onoff(mint) + " · freeze " + _onoff(freeze),
             "source": src},
            {"label": "Deployer history", "points": round(deployer, 1), "max": 10,
             "value": f"{prev} prior launches" if prev is not None else "unavailable",
             "source": src if prev is not None else "unavailable"},
            {"label": "Market behavior", "points": round(behavior, 1), "max": 10,
             "value": f"1h {pc:+.0f}%, buy share {d['buy_share_1h']:.0%}"},
            {"label": "Social manipulation", "points": round(social, 1), "max": 10,
             "value": f"unique/mention {ratio:.0%}, low-quality {lowq:.0%}",
             "source": "mock:x-style-sim"},
        ],
    }
