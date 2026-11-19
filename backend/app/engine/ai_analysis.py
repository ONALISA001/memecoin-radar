"""Explainable AI-analysis layer (§17, §18).

Phase 1 uses a DETERMINISTIC composer: it can only rearrange numbers that
already exist in TokenContext + score payloads, so fabrication is impossible
by construction. Output is split into Observed facts / Interpretation /
Uncertainty as required. A real LLM can later consume the same structured
payload via an LLMProvider abstraction with this composer as fallback.
"""
from __future__ import annotations

from .context import TokenContext


def build_analysis(ctx: TokenContext, trend_payload: dict, fomo_payload: dict,
                   risk_payload: dict, narrative_names: list[str],
                   signals: dict, news_active: bool) -> dict:
    d = ctx.d
    sym = ctx.token.symbol or "?"
    comps = trend_payload["components"]

    observed = [
        f"{sym}: price ${d['price_now']:.8g}, market cap ${d['mcap_now']:,.0f}, "
        f"liquidity ${d['liq_now']:,.0f} (source: {ctx.sources['market']}).",
        f"Social mentions (1h): {d['mentions_1h']:.0f} vs {d['mentions_prev_1h']:.0f} prior hour "
        f"({d['mentions_g_1h']:+.0f}%), unique accounts {d['uniques_1h']:.0f} "
        f"(source: {ctx.sources['social']}, simulated).",
        f"Trading volume (1h): ${d['volume_1h']:,.0f} vs ${d['volume_prev_1h']:,.0f} prior hour "
        f"({d['volume_g_1h']:+.0f}%); buy share {d['buy_share_1h']:.0%}.",
        f"Holders: {d['holders_now']:.0f} ({d['holders_g_1h']:+.1f}% 1h, {d['holders_g_24h']:+.1f}% 24h) "
        f"(source: {ctx.sources['onchain']}"
        f"{'' if ctx.sources['onchain'] == 'helius+geckoterminal' else ', simulated'}).",
    ]
    conc = next((c for c in risk_payload.get("components", [])
                 if c.get("label") == "Holder concentration"), None)
    if conc and conc.get("source") == "helius":
        observed.append(f"Holder concentration: {conc['value']} (source: helius, real — "
                        f"pools, bonding curves and burn addresses excluded).")
    elif conc:
        observed.append("Holder concentration: not verified on-chain yet.")
    if news_active:
        real = ctx.sources.get("news") == "rss"
        observed.append("Narrative-related news articles detected in the last 24h "
                        f"(source: {ctx.sources['news']}{'' if real else ', simulated'}).")

    # interpretation: rank trend components by share of their max
    ranked = sorted(((k, v["points"] / v["max"]) for k, v in comps.items()),
                    key=lambda kv: -kv[1])
    prim = ranked[0][0].replace("_", " ")
    interpretation = [f"Primary momentum driver: {prim} "
                      f"({comps[ranked[0][0]]['points']:.1f}/{comps[ranked[0][0]]['max']} pts)."]
    if narrative_names:
        interpretation.append(f"Narrative association: {', '.join(narrative_names[:3])}.")
    if signals["confirmed"] >= 3:
        interpretation.append("Multiple independent signal classes confirm the move "
                              f"({signals['confirmed']}/4). This is corroboration, not certainty.")
    else:
        interpretation.append("Signal confirmation is partial — momentum relies on "
                              f"{signals['confirmed']}/4 signal classes.")
    phase = ("overheated territory — elevated reversal risk"
             if fomo_payload["fomo"] >= 81 else
             "strong acceleration" if fomo_payload["fomo"] >= 61 else
             "building momentum" if fomo_payload["fomo"] >= 41 else
             "early / quiet phase")
    interpretation.append(f"FOMO index {fomo_payload['fomo']:.0f}/100 → {phase}.")
    for flag in risk_payload["flags"][:3]:
        interpretation.append(f"Risk note: {flag}.")

    sim_scope = ("Social data and part of the on-chain data are" if ctx.sources.get("news") == "rss"
                 else "Social, on-chain and news data are")
    uncertainty = [
        f"{sim_scope} currently SIMULATED (mock providers) and must not be treated as ground truth.",
        "Market history beyond live ticks is partially backfilled from provider "
        "aggregates; treat long-range chart detail as approximate.",
        "Scores are algorithmic estimates; they are not predictions of future price.",
    ]
    if not news_active:
        uncertainty.append("No corroborating news coverage detected.")
    return {"observed": observed, "interpretation": interpretation,
            "uncertainty": uncertainty}


def build_why_trending(ctx: TokenContext, trend_payload: dict, fomo_payload: dict,
                       risk_payload: dict, narrative_names: list[str]) -> str:
    """§18 'WHY IS THIS TRENDING?' — metric-grounded explanation."""
    d = ctx.d
    comps = trend_payload["components"]
    drivers = []
    for key in ("social", "market", "onchain", "liquidity"):
        best = max(comps[key]["drivers"], key=lambda x: x["points"] / x["max"])
        if best["points"] / best["max"] >= 0.35:
            drivers.append(best)
    drivers = sorted(drivers, key=lambda x: -x["points"] / x["max"])[:3]
    sym = ctx.token.symbol or "This token"
    if not drivers:
        lead = f"{sym} shows no dominant driver right now; its Trend Score is {trend_payload['trend']:.0f}/100."
    else:
        narr = f" associated with the {narrative_names[0]} narrative" if narrative_names else ""
        bits = "; ".join(f"{b['label'].lower()} at {b['value']}" for b in drivers)
        lead = f"{sym} is trending primarily because of {bits}{narr}."
    tail_bits = []
    if risk_payload["flags"]:
        tail_bits.append("However: " + risk_payload["flags"][0].lower() +
                         (f"; also {risk_payload['flags'][1].lower()}" if len(risk_payload["flags"]) > 1 else "") + ".")
    if fomo_payload.get("warning"):
        tail_bits.append(fomo_payload["warning"])
    if d["liq_now"] < 50_000:
        tail_bits.append(f"Liquidity is shallow (${d['liq_now']:,.0f}).")
    return " ".join([lead] + tail_bits)
