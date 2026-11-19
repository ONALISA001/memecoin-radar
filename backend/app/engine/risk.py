"""Risk engine (§8). Higher score = higher estimated observable risk.
Language is deliberately cautious (§8): 'elevated', 'potential manipulation',
'concentrated ownership' — never 'guaranteed scam'. Missing data never
improves a score (§19)."""
from __future__ import annotations

from ..config import ANOMALY_Z
from ..providers.helius import RISKY_EXTENSIONS, TRANSFER_FEE_HIGH_BPS
from ..providers.jupiter import exit_verdict
from .context import TokenContext

# Risk floor when a single fact can make a token unsellable / rug-able on its own.
HARD_FAIL_FLOOR = 85.0
HARD_FAIL_EXTENSIONS = {"permanentDelegate", "nonTransferable", "defaultAccountState",
                        "pausableConfig", "transferHook"}

# Points charged while mint/freeze authority are still unknown (no Helius check yet).
CONTRACT_UNVERIFIED_POINTS = 5
# Points charged while real-wallet concentration is unknown (out of 25).
CONCENTRATION_UNVERIFIED_POINTS = 8

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

    hard_fails: list[str] = []   # facts that alone make a token un-sellable or rug-able

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
    # Exit analyst (Jupiter round trip): a real "can you get out?" measurement.
    buy_r, sell_r = facts.get("exit_buy_route"), facts.get("exit_sell_route")
    loss = facts.get("exit_round_trip_loss_pct")
    verdict = exit_verdict(buy_r, sell_r, loss)
    p_exit = 0
    if verdict == "fail" and buy_r and not sell_r:
        p_exit = 12
        flags.insert(0, "Can buy but NO sell route on Jupiter (honeypot-like)")
        hard_fails.append("No sell route")
        events.append({"category": "liquidity", "severity": "high",
                       "message": "Jupiter routes a buy but finds no way to sell back (honeypot-like)."})
    elif verdict == "fail":
        p_exit = 10
        flags.insert(0, f"Round trip loses ~{loss:.0f}% — thin pool or hidden sell tax")
        hard_fails.append(f"Round-trip loss {loss:.0f}%")
    elif verdict == "warn" and not buy_r:
        p_exit = 4
        flags.append("No Jupiter trade route yet")
    elif verdict == "warn":
        p_exit = 5
        flags.append(f"Round trip loses ~{loss:.0f}% on a small trade")
    liq_risk = min(30, p_liq + p_drop + p_ratio + p_exit)

    # ---- holder concentration /25 ----
    # Real: Helius top-10 / largest REAL wallets (pools, curves, lockers, burn excluded).
    # Demo tokens use the simulator; unchecked real tokens are "unverified".
    src_c = facts.get("source", "unverified")
    t10, top1 = facts.get("top10_wallet_pct"), facts.get("top1_wallet_pct")
    if src_c.startswith("mock"):
        t10, top1, conc_src = d["top10_now"], d["deployer_pct_now"], src_c
    elif t10 is None or top1 is None:
        conc_src = "unverified"
    else:
        conc_src = "helius"
    if conc_src == "unverified":
        concentration = CONCENTRATION_UNVERIFIED_POINTS
        flags.append("Holder concentration not verified yet — treated as a risk until checked")
        conc_value = "unknown"
    else:
        p_t10 = 18 if t10 >= 60 else 14 if t10 >= 45 else 10 if t10 >= 35 else \
            6 if t10 >= 25 else 3 if t10 >= 15 else 1
        p_top1 = 7 if top1 >= 15 else 4 if top1 >= 8 else 2 if top1 >= 3 else 0
        if t10 >= 50:
            flags.append(f"Concentrated ownership: top-10 wallets ≈ {t10:.0f}% of supply")
            events.append({"category": "concentration", "severity": "high",
                           "message": f"Top-10 wallets control ≈{t10:.0f}% of supply"
                                      + (" (pools and burn excluded)." if conc_src == "helius" else ".")})
        elif t10 >= 35:
            flags.append(f"Top-10 wallets hold ≈{t10:.0f}%")
        if top1 >= 8:
            flags.append(f"Largest single wallet ≈{top1:.0f}% of supply")
        concentration = min(25, p_t10 + p_top1)
        conc_value = f"top10 wallets ≈{t10:.0f}% · largest ≈{top1:.0f}%"

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
    exts, fee = facts.get("token_extensions"), facts.get("transfer_fee_bps") or 0
    if facts.get("token_2022") and exts is None:      # not analysed yet
        p_contract += 3
        flags.append("Token-2022 program — may carry transfer fees/extensions")
    for name in exts or []:
        sev, msg = RISKY_EXTENSIONS.get(name, ("medium", f"Token-2022 extension {name} active"))
        p_contract += 8 if sev == "high" else 3
        flags.append(msg.rstrip("."))
        if name in HARD_FAIL_EXTENSIONS:
            hard_fails.append(msg.rstrip("."))
        events.append({"category": "contract", "severity": "high" if sev == "high" else "warn",
                       "message": msg})
    if fee:
        p_contract += 8 if fee >= TRANSFER_FEE_HIGH_BPS else 3
        flags.append(f"Transfer fee {fee / 100:.2f}% on every transfer (a sell tax)")
        if fee >= TRANSFER_FEE_HIGH_BPS:
            hard_fails.append(f"Transfer fee {fee / 100:.0f}%")
    lp = facts.get("lp_burned_pct")
    if lp is not None and lp < 30:
        flags.append(f"Only ~{lp:.0f}% of LP reported burned/locked")
    contract = min(15, p_contract)

    # ---- launch & deployer risk /10 (launch forensics; unknown → scored 0) ----
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
    # Launch forensics (wallet-level, Helius): bundles, snipers, shared/creator funders.
    lf = facts.get("launch") or {}
    lf_ok = lf.get("status") == "ok"
    if lf_ok:
        g = lambda k: lf.get(k) or 0  # noqa: E731
        p_deployer += 6 if g("funded_by_creator") >= 1 else 0
        p_deployer += 4 if g("bundle_pct") >= 15 else 2 if g("bundle_pct") >= 5 else 0
        p_deployer += 3 if g("sniper_pct") >= 25 else 1 if g("sniper_pct") >= 10 else 0
        p_deployer += 3 if g("shared_funder_wallets") >= 3 else 1 if g("shared_funder_wallets") == 2 else 0
        p_deployer += 1 if g("snipers_hold_pct") >= 10 else 0
        for f in lf.get("flags") or []:
            if f["severity"] == "high":
                flags.append(f["message"].rstrip("."))
                events.append({"category": "deployer", "severity": "warn", "message": f["message"]})
    deployer = min(10, p_deployer)
    if lf_ok:
        dep_value = (f"bundle {lf.get('bundle_pct') or 0:.0f}% · snipers {lf.get('sniper_pct') or 0:.0f}%"
                     f" · dev bought {lf.get('dev_buy_pct') or 0:.1f}%"
                     + (f" · {lf['funded_by_creator']} buyers funded by creator" if lf.get("funded_by_creator") else "")
                     + (f" · {lf['shared_funder_wallets']} share a funder" if (lf.get("shared_funder_wallets") or 0) >= 2 else ""))
        dep_src = "helius:launch-forensics"
    elif prev is not None:
        dep_value, dep_src = f"{prev} prior launches", src
    else:
        dep_value = {"too_active": "launch out of reach (too many transactions)",
                     "error": "launch analysis failed, will retry"}.get(lf.get("status"), "unavailable")
        dep_src = "unavailable"

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
                       "message": f"Whales net-sold ≈ ${abs(d['whale_net_24h']):,.0f} over 24h"
                                  + (" (top-10 real wallets, Helius)." if ctx.social_proxy else " (simulated).")})
    behavior = min(10, p_beh)

    # ---- social manipulation /10 ----
    # Real coins: "trading authenticity" — trades per unique trader (bots/wash trading
    # make many trades from few wallets) + 24h volume far above liquidity.
    p_soc = 0.0
    if ctx.social_proxy:
        tpt = d.get("trades_per_trader")
        vlr = d.get("vol24_liq_live")
        if vlr is None:
            vlr = (d["volume_24h"] / d["liq_now"]) if d["liq_now"] > 0 else 0.0
        p_soc += (6 if tpt >= 7 else 4 if tpt >= 4.5 else 2 if tpt >= 3 else 0) if tpt is not None else 2
        p_soc += 4 if vlr > 30 else 2 if vlr > 15 else 0
        p_wash = p_soc
        tg = ctx.telegram or {}
        if tg.get("tg_coordinated"):
            p_soc += 4
            flags.append(f"Coordinated shilling: {len(tg['tg_coord_channels'])} Telegram channels "
                         f"posted it within 10 minutes")
            events.append({"category": "social", "severity": "warn",
                           "message": "Coordinated Telegram push: " + ", ".join(
                               "@" + c for c in tg["tg_coord_channels"][:5]) + " posted it within 10 min."})
        social = min(10, p_soc)
        soc_value = (f"{tpt:.1f} trades per unique trader (1h)" if tpt is not None else "no trader data yet") \
            + f", 24h volume {vlr:.1f}x liquidity" + (", coordinated Telegram push" if tg.get("tg_coordinated") else "")
        if p_wash >= 6:
            flags.append("Possible wash/bot trading: many trades from few wallets or volume far above liquidity")
            events.append({"category": "social", "severity": "warn",
                           "message": f"Trading looks bot-heavy ({soc_value})."})
        soc_label, soc_src = "Trading authenticity", "geckoterminal+dexscreener"
    ratio = d["uniques_1h"] / d["mentions_1h"] if d["mentions_1h"] > 5 else 1.0
    lowq = d["lowq_24h"]
    if not ctx.social_proxy:
        p_soc += 4 if ratio < 0.3 else 2 if ratio < 0.5 else 0
        p_soc += 3 if lowq > 0.6 else 2 if lowq > 0.4 else 0
        soc_label, soc_src = "Social manipulation", "mock:x-style-sim"
        soc_value = f"unique/mention {ratio:.0%}, low-quality {lowq:.0%}"
    if not ctx.social_proxy and (ratio < 0.3 or lowq > 0.55):
        flags.append("Potential manipulation: social activity concentrated in "
                     "few / low-quality accounts")
        events.append({"category": "social", "severity": "warn",
                       "message": f"Social activity looks bot-heavy (unique/mention "
                                  f"{ratio:.0%}, low-quality share {lowq:.0%}) — simulated flags."})
    social = min(10, p_soc)

    score = min(100.0, liq_risk + concentration + contract + deployer + behavior + social)
    if hard_fails:               # one of these alone is enough to lose everything
        score = max(score, HARD_FAIL_FLOOR)
    band = classify(score)
    for key, z in (ctx.anomalies or {}).items():
        if abs(z) >= ANOMALY_Z:
            events.append({"category": "anomaly", "severity": "info",
                           "message": f"Anomalous {key} activity (z={z:+.1f} vs 24h baseline)."})

    return {
        "risk": round(score, 1),
        "classification": band,
        "flags": flags[:8],
        "hard_fails": hard_fails,
        "events": events,
        "components": [
            {"label": "Liquidity risk", "points": round(liq_risk, 1), "max": 30,
             "value": f"${liq:,.0f}, {drop:+.1f}% (3h)" + (
                 "" if verdict is None else
                 " · NO SELL ROUTE" if (buy_r and not sell_r) else
                 " · no route" if not buy_r else f" · round trip -{loss:.1f}%")},
            {"label": "Holder concentration", "points": round(concentration, 1), "max": 25,
             "value": conc_value, "source": conc_src},
            {"label": "Contract permissions", "points": round(contract, 1), "max": 15,
             "value": "mint " + _onoff(mint) + " · freeze " + _onoff(freeze),
             "source": src},
            {"label": "Launch & deployer", "points": round(deployer, 1), "max": 10,
             "value": dep_value, "source": dep_src},
            {"label": "Market behavior", "points": round(behavior, 1), "max": 10,
             "value": f"1h {pc:+.0f}%, buy share {d['buy_share_1h']:.0%}"},
            {"label": soc_label, "points": round(social, 1), "max": 10,
             "value": soc_value, "source": soc_src},
        ],
    }
