"""Intelligence event engine (v3 §4–§19, §35–§37, §43–§44).

Consumes the per-token TokenContext + market_intel composite each tick and
produces ENRICHED events: category, importance (LOW/MEDIUM/HIGH/CRITICAL),
confidence 0–100 (supporting-indicator share, age-capped), a signal statement
in plain language, a "why detected" checklist, metrics snapshot, and a
potential-concern line. Also detects multi-event signal CHAINS (§16).

All thresholds centralized in intel_config (§35). Escalation semantics (§43):
an event family re-fires inside its cooldown only when a strictly higher
magnitude bucket is crossed.
"""
from __future__ import annotations

from sqlalchemy import select

from ..models import PriceSnapshot, SignalEvent, SignalEventOutcome
from . import timeseries as tsx
from . import intel_config as C


# ------------------------------------------------------------------ helpers
def _imp(score: float) -> str:
    return "LOW" if score < 35 else "MEDIUM" if score < 60 else "HIGH" if score < 85 else "CRITICAL"


def _is_distribution(whales: dict) -> bool:
    return whales["classification"] in ("distributing", "heavy_distribution")


def _conf_cap(age: float) -> int:
    for lim, cap in C.CONF_CAPS:
        if age < lim:
            return cap
    return 100


def _confidence(checks: list[tuple[str, bool, str]], age: float) -> tuple[int, list[dict]]:
    passed = sum(1 for _, ok, _ in checks if ok)
    conf = round(30 + 70 * passed / max(len(checks), 1))
    cap = _conf_cap(age)
    return min(conf, cap), [{"label": l, "passed": bool(ok), "value": v} for l, ok, v in checks]


def _ev(kind, category, severity, imp_score, title, signal_text, conf, checklist,
        why, concern, metrics, cooldown_key="") -> dict:
    return {"kind": kind, "category": category, "severity": severity,
            "importance": _imp(imp_score), "importance_score": round(imp_score),
            "title": title, "signal_text": signal_text[:290],
            "confidence": conf, "checklist": checklist, "why": why[:6],
            "concern": concern, "metrics": metrics, "cooldown_key": cooldown_key,
            "cooldown_secs": C.COOLDOWNS.get(kind, 3600)}


def _base_metrics(ctx, mi) -> dict:
    d = ctx.d
    return {
        "price": d["price_now"], "mcap": d["mcap_now"], "liquidity": d["liq_now"],
        "momentum": mi["momentum"]["score"], "sell_pressure": mi["sell_pressure"]["score"],
        "liq_health": mi["liquidity_health"]["score"],
        "buy_share": round(d["buy_share_1h"] * 100, 1),
        "holders_g_1h": round(d["holders_g_1h"], 1), "volume_g_1h": round(d["volume_g_1h"], 1),
    }


def _baseline_15m(ctx, series_key: str, win: int = 900) -> tuple[float, float]:
    """(current window sum, mean of same-length windows over trailing 24h)."""
    now = ctx.now
    series = getattr(ctx, series_key)
    cur = tsx.ssum(series, now - win, now)
    base = [tsx.ssum(series, now - (i + 1) * win, now - i * win) for i in range(1, 95)]
    avg = sum(base) / len(base) if base else 0.0
    return cur, avg


def _sell_series(ctx) -> dict[int, float]:
    sells = getattr(ctx, "sells", None)
    if sells:
        return sells
    return {t: max(0.0, tx - ctx.buys.get(t, 0.0)) for t, tx in ctx.txns.items()}


# ------------------------------------------------------------------ detectors
def detect_events(ctx, mi, token, age_hours: float, prev_mi: dict,
                  risk_score: float, risk_flags: list) -> list[dict]:
    d, now = ctx.d, ctx.now
    mom, sp, lh, ath, whales = (mi["momentum"], mi["sell_pressure"], mi["liquidity_health"],
                                mi["ath"], mi["whales"])
    out: list[dict] = []
    B = _base_metrics(ctx, mi)

    # ── NEW TOKEN (§5) — once ever per token
    if age_hours < 24 and not token.new_token_evented:
        checks = [("liquidity present", d["liq_now"] > 1000, f"${d['liq_now']:,.0f}"),
                  ("volume present", d["volume_1h"] > 0, f"${d['volume_1h']:,.0f} 1h"),
                  ("holders visible", d["holders_now"] > 5, f"{d['holders_now']:.0f} holders"),
                  ("early momentum", mom["score"] >= 55, f"{mom['score']:.0f}/100")]
        conf, cl = _confidence(checks, age_hours)
        imp = 45 + (12 if mom["score"] >= 65 else 0) + (8 if d["liq_now"] >= 50000 else 0) \
            + (10 if d["volume_1h"] >= 10000 else 0)
        out.append(_ev("new_token", "new", "info", imp,
                       f"🆕 New token detected: ${token.symbol}",
                       f"Age {age_str(age_hours)} · liquidity ${d['liq_now']:,.0f} · momentum {mom['score']:.0f} · risk {risk_score:.0f}",
                       conf, cl, [f"age {age_str(age_hours)}", f"momentum {mom['score']:.0f}/100",
                                  f"risk {risk_score:.0f} ({'elevated' if risk_score >= 60 else 'moderate'})"],
                       "Young token — baseline window is short, confidence is capped.",
                       {**B, "age_hours": round(age_hours, 2), "risk": risk_score}))

    # ── MOMENTUM ACCELERATION (§6)
    prev_mom = (prev_mi or {}).get("momentum")
    jump = (mom["score"] - prev_mom) if prev_mom is not None else None
    crossed = next((b for b in C.MOMENTUM_BANDS
                    if prev_mom is not None and prev_mom < b <= mom["score"]), None)
    if (jump is not None and jump >= C.MOMENTUM_JUMP_MIN and mom["score"] >= 55) or crossed:
        band = crossed or 0
        fhire = [f for f in mom["factors"] if f["score"] >= 65]
        checks = [(f["label"], f["score"] >= 60, f["detail"]) for f in mom["factors"]]
        conf, cl = _confidence(checks, age_hours)
        imp = 55 + (15 if band >= 80 else 8 if band >= 65 else 0) + min(12, 4 * len(fhire)) \
            + (6 if sp["score"] < 35 else 0)
        why = [f"{f['label']} — {f['detail']}" for f in sorted(mom["factors"], key=lambda f: -f["score"])[:4]]
        concern = None
        if sp["score"] >= 45:
            concern = f"sell pressure already {sp['score']:.0f}/100 — watch for distribution into strength"
        elif _is_distribution(whales):
            concern = "whale flow is distribution-biased (inferred)"
        speed = f"{jump:+.0f} pts" if jump is not None else f"crossed {band}"
        out.append(_ev("momentum_acceleration", "momentum", "positive", imp,
                       f"🚀 Momentum acceleration — ${token.symbol}",
                       f"Momentum {f'{prev_mom:.0f} → ' if prev_mom is not None else ''}{mom['score']:.0f} "
                       f"({speed}). {mom['band_label']}."
                       + (f" Volume {d['volume_g_15m']:+.0f}% 15m, holders {d['holders_g_1h']:+.1f}%, buy share {d['buy_share_1h'] * 100:.0f}%."
                          if d["volume_g_15m"] else ""),
                       conf, cl, why, concern,
                       {**B, "momentum_prev": prev_mom, "momentum_delta": round(jump or 0, 1),
                        "band_crossed": band or None, "vol_accel_15m": round(d["volume_g_15m"], 1)},
                       f"b{band or 'j'}"))

    # ── UNUSUAL VOLUME (§7) — vs token's own baseline, escalation buckets (§43)
    v15, vavg = _baseline_15m(ctx, "volume")
    ratio = v15 / vavg if vavg > 50 else 0.0
    zv = ctx.anomalies.get("volume", 0.0)
    if v15 >= 500 and (ratio >= C.UNUSUAL_VOL_RATIO_MIN or zv >= C.UNUSUAL_VOL_Z_MIN):
        bucket = max([f"{r:.0f}x" for r in C.VOL_ESCALATION_BUCKETS if ratio >= r] or ["1x"])
        mag_i = {"1x": 0, "2x": 8, "3x": 16, "5x": 24}[bucket]
        sustained = d["volume_g_1h"] > 30
        reversal = d["volume_g_1h"] < -25
        checks = [("baseline ratio", ratio >= 2, f"15m {ratio:.1f}× recent avg"),
                  ("activity floor", v15 >= 500, f"${v15:,.0f} this 15m"),
                  ("volume anomaly", zv >= 2.5, f"z = {zv:.1f}σ"),
                  ("sustained into 1h", sustained, f"1h {d['volume_g_1h']:+.0f}%")]
        conf, cl = _confidence(checks, age_hours)
        imp = 48 + mag_i + (8 if sustained and d["buy_share_1h"] > 0.52 else 0)
        out.append(_ev("unusual_volume", "volume", "warning", imp,
                       f"📈 Unusual volume — ${token.symbol}",
                       f"15m volume ${v15:,.0f} is {f'{ratio:.1f}×' if ratio >= 2 else f'{zv:.0f}σ above'} "
                       f"the token's recent baseline"
                       + (" and still rising." if sustained else " — now fading." if reversal else "."),
                       conf, cl,
                       [f"current 15m ${v15:,.0f}", f"recent avg 15m ${vavg:,.0f}",
                        f"1h change {d['volume_g_1h']:+.0f}%", f"vol/liquidity {d['vol_liq_ratio']:.2f}"],
                       "volume without price confirmation can be churn/wash flow." if d["price_chg_15m"] < 2 else None,
                       {**B, "vol_15m_usd": round(v15), "vol_15m_avg_usd": round(vavg),
                        "vol_ratio": round(ratio, 2), "vol_z": round(zv, 1), "bucket": bucket},
                       bucket))

    # ── BUY / SELL PRESSURE (§8–9) — activity floor gates (§35)
    bs, bs_previous_share = d["buy_share_1h"], _prev_buy_share(ctx)
    active_enough = d["txns_1h"] >= C.MIN_TXNS_1H_FOR_FLOW or d["volume_1h"] >= C.MIN_VOLUME_1H_FOR_FLOW_USD
    if bs >= C.BUY_PRESSURE_MIN_SHARE and (bs - bs_previous_share) >= C.BUY_PRESSURE_DELTA_PP:
        checks = [("buy share high", bs >= 0.6, f"{bs * 100:.0f}% 1h"),
                  ("share improving", (bs - bs_previous_share) >= 0.06, f"+{(bs - bs_previous_share) * 100:.0f}pp vs 4h ago"),
                  ("broad activity", active_enough, f"{d['txns_1h']:.0f} txns/h"),
                  ("volume supports", d["volume_g_1h"] > 10, f"volume {d['volume_g_1h']:+.0f}%")]
        conf, cl = _confidence(checks, age_hours)
        if not active_enough:
            conf = min(conf, 45)
        imp = 46 + (10 if bs >= 0.7 else 0) + (8 if d["volume_g_1h"] > 30 else 0) \
            + (6 if d["holders_g_1h"] > 5 else 0) - (8 if not active_enough else 0)
        out.append(_ev("buy_pressure_increasing", "flow", "positive", imp,
                       f"🟢 Buying pressure rising — ${token.symbol}",
                       f"Buy-side now {bs * 100:.0f}% of 1h transactions (+{(bs - bs_previous_share) * 100:.0f}pp vs 4h ago)"
                       + ("" if active_enough else " — thin activity, treat with caution."),
                       conf, cl,
                       [f"buy share {bs * 100:.0f}% (was {bs_previous_share * 100:.0f}%)",
                        f"{d['buys_1h']:.0f} buys vs {d['sells_1h']:.0f} sells 1h",
                        f"volume {d['volume_g_1h']:+.0f}% 1h"],
                       "single large wallets can skew small-samples — check whale panel." if d["txns_1h"] < 60 else None,
                       {**B, "buy_share_prev": round(bs_previous_share * 100, 1)}))

    sells = _sell_series(ctx)
    s_cur = tsx.ssum(sells, now - 900, now)
    s_prev = tsx.ssum(sells, now - 1800, now - 900)
    s_surge = s_cur / s_prev - 1 if s_prev >= 2 and s_cur >= 4 else 0.0
    if bs <= C.SELL_PRESSURE_MAX_SHARE and s_surge >= C.SELL_SURGE_MIN and active_enough:
        checks = [("sell surge", s_surge >= 1, f"sell-side txns +{s_surge * 100:.0f}% vs prior 15m"),
                  ("buy share weak", bs <= 0.46, f"{bs * 100:.0f}% 1h"),
                  ("broad activity", active_enough, f"{d['txns_1h']:.0f} txns/h"),
                  ("liquidity weakening", d["liq_chg_3h"] < 0, f"{d['liq_chg_3h']:+.1f}% 3h")]
        conf, cl = _confidence(checks, age_hours)
        imp = 55 + min(15, s_surge * 4) + (10 if d["liq_chg_3h"] < -5 else 0) \
            + (8 if _is_distribution(whales) else 0)
        out.append(_ev("sell_pressure_increasing", "distribution", "danger", imp,
                       f"🔴 Sell pressure increasing — ${token.symbol}",
                       f"Sell-side transactions +{s_surge * 100:.0f}% vs prior 15m while buy share fell to {bs * 100:.0f}%."
                       + (f" Liquidity {d['liq_chg_3h']:+.1f}% 3h." if d["liq_chg_3h"] < -3 else ""),
                       conf, cl,
                       [f"sell-side 15m surge +{s_surge * 100:.0f}%", f"buy share {bs * 100:.0f}%",
                        f"whale flow ${whales['net_flow_24h_usd']:,.0f}/24h (inferred)",
                        f"liquidity {d['liq_chg_3h']:+.1f}% 3h"],
                       "may be profit-taking rather than distribution — watch next windows.",
                       {**B, "sell_surge_pct": round(s_surge * 100, 1)}))

    # ── WHALE FLOW (§10) — requires sustained direction (§35)
    wnet, wnet6 = whales["net_flow_24h_usd"], whales["net_flow_6h_usd"]
    w_ratio = abs(wnet) / max(d["volume_24h"], 1.0)
    w_sustained = (wnet > 0) == (wnet6 > 0)
    if w_ratio >= C.WHALE_NET_MIN_OF_VOL and abs(wnet) >= 2500:
        acc = wnet > 0
        kind = "whale_accumulation" if acc else "whale_distribution"
        heavy = w_ratio >= C.WHALE_HEAVY_OF_VOL
        checks = [("net flow magnitude", True, f"${wnet:+,.0f} 24h ({w_ratio * 100:.0f}% of volume)"),
                  ("sustained 6h+24h", w_sustained, f"6h ${wnet6:+,.0f}"),
                  ("concentration shift", abs(whales["top10_delta_24h"]) > 0.3,
                   f"top-10 {whales['top10_delta_24h']:+.1f}pp"),
                  ("direction consistent", whales["classification"] != "neutral", whales["classification"])]
        conf, cl = _confidence(checks, age_hours)
        if not w_sustained:
            conf = min(conf, 50)
        imp = 58 + (12 if heavy else 0) + (8 if w_sustained else -8) \
            + (10 if (not acc and sp["score"] >= 60) else 0)
        out.append(_ev(kind, "whales", "positive" if acc else "danger", imp,
                       f"🐋 {'Whale accumulation' if acc else 'Whale distribution'} (inferred) — ${token.symbol}",
                       f"Net large-wallet flow ${wnet:+,.0f} over 24h ({w_ratio * 100:.0f}% of volume)"
                       + ("" if w_sustained else " — direction not sustained at 6h horizon."),
                       conf, cl,
                       [f"net 24h ${wnet:+,.0f} · net 6h ${wnet6:+,.0f}",
                        f"top-10 holders {whales['top10_pct']}% ({whales['top10_delta_24h']:+.1f}pp)",
                        whales["note"]],
                       "aggregate flow inference — not a claim about any specific wallet (§23).",
                       {**B, "whale_net_24h": round(wnet), "whale_net_6h": round(wnet6),
                        "whale_ratio_vol": round(w_ratio, 3), "sustained": w_sustained}))

    # ── HOLDER GROWTH / WEAKENING (§11)
    if d["holders_g_1h"] >= C.HOLDER_GROWTH_MIN and d["holders_now"] >= 10:
        checks = [("growth rate", True, f"+{d['holders_g_1h']:.1f}% 1h"),
                  ("24h trend", d["holders_g_24h"] > 0, f"{d['holders_g_24h']:+.1f}% 24h"),
                  ("new wallets accelerating", d["new_wallets_g_1h"] > 20, f"{d['new_wallets_g_1h']:+.0f}%"),
                  ("with activity", d["txns_g_1h"] > 0, f"txns {d['txns_g_1h']:+.0f}%")]
        conf, cl = _confidence(checks, age_hours)
        imp = 44 + min(14, d["holders_g_1h"] / 3) + (6 if d["buy_share_1h"] > 0.53 else 0)
        out.append(_ev("holder_growth", "holders", "positive", imp,
                       f"👥 Holder growth — ${token.symbol}",
                       f"Holders +{d['holders_g_1h']:.1f}% in 1h to {d['holders_now']:,.0f}"
                       + (f" ({d['holders_g_24h']:+.0f}% over 24h)." if d["holders_g_24h"] else "."),
                       conf, cl,
                       [f"holders {d['holders_1h_ago']:,.0f} → {d['holders_now']:,.0f} (1h)",
                        f"new wallet growth {d['new_wallets_g_1h']:+.0f}%",
                        f"top-10 concentration {d['top10_now']:.1f}%"],
                       "growth concentrated in top-10 wallets is not organic breadth." if d["top10_now"] > 35 else None,
                       {**B, "holders": round(d["holders_now"])}))
    hgw = (d["holders_g_1h"] <= C.HOLDER_WEAKENING_MAX and d["price_chg_1h"] >= 5
           and (prev_mi or {}).get("momentum") is not None)
    if hgw:
        conf, cl = _confidence([("holders flat", True, f"{d['holders_g_1h']:+.1f}% 1h"),
                                ("price rising", True, f"{d['price_chg_1h']:+.1f}% 1h"),
                                ("attention fading", d["mentions_g_1h"] < 0, f"{d['mentions_g_1h']:+.0f}%")],
                               age_hours)
        out.append(_ev("holder_weakening", "holders", "warning", 52,
                       f"⚠️ Holder growth weakening — ${token.symbol}",
                       f"Price +{d['price_chg_1h']:.1f}% 1h but holder growth only {d['holders_g_1h']:+.1f}% — rally losing breadth.",
                       conf, cl, [f"holders {d['holders_g_1h']:+.1f}% vs price {d['price_chg_1h']:+.1f}% 1h"],
                       "price up / holders flat is a classic fade setup — not a certainty.",
                       {**B}))

    # ── LIQUIDITY EVENTS (§12)
    if d["liq_chg_3h"] >= C.LIQ_INCREASE_MIN:
        checks = [("growth magnitude", True, f"{d['liq_chg_3h']:+.1f}% 3h"),
                  ("pool material", d["liq_now"] > 5000, f"${d['liq_now']:,.0f}"),
                  ("volume vs pool", d["vol_liq_ratio"] < 1.5, f"{d['vol_liq_ratio']:.2f}")]
        conf, cl = _confidence(checks, age_hours)
        imp = 52 + min(12, d["liq_chg_3h"] / 3) + (8 if d["mcap_now"] > 100000 else 0)
        out.append(_ev("liquidity_increase", "liquidity", "positive", imp,
                       f"💧 Liquidity increase — ${token.symbol}",
                       f"Pool up {d['liq_chg_3h']:+.1f}% in 3h to ${d['liq_now']:,.0f} ({lh['band_label'].lower()} health {lh['score']:.0f}).",
                       conf, cl, [f"pool ${d['liq_3h_ago']:,.0f} → ${d['liq_now']:,.0f}",
                                  f"liq/mcap {(d['liq_now'] / d['mcap_now'] * 100) if d['mcap_now'] else 0:.1f}%"],
                       "new liquidity can still be pulled — LP lock status worth checking.",
                       {**B, "liq_chg_3h": round(d["liq_chg_3h"], 1)}))
    if d["liq_chg_3h"] <= C.LIQ_DROP_MIN:
        critical = d["liq_chg_3h"] <= C.LIQ_DROP_CRITICAL or lh["band"] == "critical"
        checks = [("drop magnitude", True, f"{d['liq_chg_3h']:+.1f}% 3h"),
                  ("pool now thin", lh["band"] in ("thin", "critical"), lh["band_label"]),
                  ("sell pressure rising", sp["score"] >= 55, f"{sp['score']:.0f}/100"),
                  ("whale flow negative", whales["net_flow_24h_usd"] < 0, f"${whales['net_flow_24h_usd']:,.0f}")]
        conf, cl = _confidence(checks, age_hours)
        imp = 68 + (17 if critical else 0) + min(10, -d["liq_chg_3h"] / 4)
        out.append(_ev("liquidity_drop", "liquidity", "danger", imp,
                       f"🚨 Liquidity drop — ${token.symbol}",
                       f"Pool down {d['liq_chg_3h']:+.1f}% in 3h to ${d['liq_now']:,.0f} — exit depth is shrinking.",
                       conf, cl, [f"pool ${d['liq_3h_ago']:,.0f} → ${d['liq_now']:,.0f}",
                                  f"volume/pool {d['vol_liq_ratio']:.2f}", lh["detail"]],
                       "exit liquidity is a structural risk — this is a market observation, not a rug claim.",
                       {**B, "liq_chg_3h": round(d["liq_chg_3h"], 1)}))

    # ── NEW HIGH / BREAKOUT (§13)
    if ath.get("new_ath"):
        breakout = ath["status"] == "breaking_out"
        checks = [("new tracked high", True, f"{ath['distance_pct']:.1f}% from high"),
                  ("volume confirms", d["volume_g_1h"] > 40, f"{d['volume_g_1h']:+.0f}% 1h"),
                  ("buy share supports", d["buy_share_1h"] > 0.52, f"{d['buy_share_1h'] * 100:.0f}%"),
                  ("not overheated", ath["status"] != "overheated", ath["status_label"])]
        conf, cl = _confidence(checks, age_hours)
        imp = 58 + (12 if breakout else 0) + (8 if d["volume_g_1h"] > 100 else 0)
        out.append(_ev("new_high", "ath", "positive", imp,
                       f"🏆 {'Breakout — new high' if breakout else 'New tracked high'} — ${token.symbol}",
                       f"Price at tracked high after {d['price_chg_24h']:+.0f}% 24h"
                       + (" with volume confirmation." if breakout else " — volume not confirming yet."),
                       conf, cl, [f"24h change {d['price_chg_24h']:+.0f}%",
                                  f"volume {d['volume_g_1h']:+.0f}% 1h",
                                  ath.get("note", "")],
                       "ATHs are neutral information — watch the follow-through, not the label.",
                       {**B, "ath_distance": ath.get("distance_pct")}))

    # ── REVERSAL / DIVERGENCE / DISTRIBUTION (§14–15) — from mi.reversals
    kind_map = {"momentum_divergence": ("momentum_divergence", "reversal", 58),
                "momentum_weakening": ("momentum_reversal", "reversal", 64),
                "distribution_warning": ("distribution_warning", "distribution", 70),
                "abnormal_volume": ("unusual_volume", "volume", 50)}
    for r in mi["reversals"]:
        kk, cat, ibase = kind_map.get(r["kind"], ("reversal", "reversal", 55))
        heavy = (r["kind"] == "distribution_warning"
                 and (sp["score"] >= 70 or whales["classification"] == "heavy_distribution"))
        checks = [(it["label"], it["score"] >= 60 if it["direction"] == -1 else it["score"] <= 40, it["detail"])
                  for it in (sp["factors"][:3] + mom["factors"][:2])]
        conf, cl = _confidence(checks if checks else [("composite reversal", True, r["title"])], age_hours)
        imp = ibase + (17 if heavy else 0) + (6 if len(mi["reversals"]) >= 2 else 0)
        out.append(_ev(kk, cat, "danger" if cat != "volume" else "warning", imp,
                       f"{'🔴' if cat != 'volume' else '⚠️'} {r['title']} — ${token.symbol}",
                       r["detail"], conf, cl,
                       [f"sell pressure {sp['score']:.0f}/100 ({sp['band_label']})",
                        f"momentum {mom['score']:.0f}/100"] + [r["title"]],
                       whales["note"] if not heavy else "multiple distribution signals aligned — investigate immediately.",
                       {**B}, r["kind"]))

    # ── ELEVATED STRUCTURAL RISK (§34)
    if risk_score >= C.ELEVATED_RISK_MIN:
        conf, cl = _confidence([("risk threshold", True, f"{risk_score:.0f}/100"),
                                ("flags present", bool(risk_flags), f"{len(risk_flags)} flags")],
                               age_hours)
        out.append(_ev("elevated_risk", "risk", "danger", 66,
                       f"⚠️ Elevated structural risk — ${token.symbol}",
                       f"Risk {risk_score:.0f}/100" + ((": " + risk_flags[0]) if risk_flags else "") + ".",
                       conf, cl, risk_flags[:4],
                       "structural risk ≠ certain loss — it marks what requires investigation first.",
                       {**B, "risk": risk_score}))
    return out


def _prev_buy_share(ctx) -> float:
    now = ctx.now
    tx = tsx.ssum(ctx.txns, now - 14400, now - 10800)
    b = tsx.ssum(ctx.buys, now - 14400, now - 10800)
    return b / tx if tx > 0 else 0.5


def age_str(h: float) -> str:
    if h < 1: return f"{max(1, round(h * 60))}m"
    if h < 48: return f"{h:.1f}h"
    return f"{h / 24:.1f}d"


# ------------------------------------------------------------------ chains §16
CHAIN_PATTERNS = {
    "pattern_emerging": {
        "title": "🔥 Emerging momentum pattern",
        "cats": ["volume", "flow", "holders", "momentum"],
        "importance": 78, "severity": "positive",
        "text": "Volume → buy pressure → holders → momentum signals are aligning in sequence.",
    },
    "pattern_distribution": {
        "title": "🔴 Distribution pattern",
        "cats": ["ath", "volume", "whales", "reversal"],
        "alt": {"whales": ["whales", "distribution"], "reversal": ["reversal", "distribution"]},
        "importance": 85, "severity": "danger",
        "text": "New high → abnormal volume → distribution flow → weakening signals in sequence.",
    },
}


def detect_chains(db, token_id: int, now: int, mi: dict) -> list[dict]:
    """Ordered-subsequence detection over the token's last N minutes of events."""
    rows = db.execute(
        select(SignalEvent).where(SignalEvent.token_id == token_id,
                                  SignalEvent.ts >= now - C.CHAIN_WINDOW_SECS)
        .order_by(SignalEvent.ts.asc())).scalars().all()
    seq = [(r.category, r.ts) for r in rows]
    out = []
    for kind, pat in CHAIN_PATTERNS.items():
        groups = [pat.get("alt", {}).get(c, [c]) for c in pat["cats"]]
        hits, pos = [], 0
        for group in groups:
            hit = next((j for j in range(pos, len(seq)) if seq[j][0] in group), None)
            if hit is None:
                hits = []
                break
            hits.append(seq[hit][1])
            pos = hit + 1
        if hits:
            has_liq_event = any(cat == "liquidity" for cat, _ in seq)
            imp = pat["importance"] + (12 if (has_liq_event and kind == "pattern_distribution") else 0)
            chain_txt = " → ".join(c.replace("_", " ") for c in pat["cats"])
            out.append({"kind": kind, "category": "pattern",
                        "severity": pat["severity"], "importance": _imp(imp),
                        "importance_score": imp,
                        "title": f"{pat['title']} — chain detected",
                        "signal_text": pat["text"] + f" ({chain_txt})",
                        "confidence": 88 if len(hits) >= len(pat["cats"]) else 72,
                        "checklist": [{"label": c.replace("_", " "), "passed": True,
                                       "value": "observed in window"} for c in pat["cats"]],
                        "why": [f"chain over last {C.CHAIN_WINDOW_SECS // 60}m: {chain_txt}"],
                        "concern": None, "metrics": _chain_metrics(mi, hits),
                        "cooldown_key": "", "cooldown_secs": C.COOLDOWNS[kind],
                        "chain_json": {"cats": pat["cats"], "ts": hits}})
            break  # one pattern per token per tick is enough
    return out


def _chain_metrics(mi, chain_ts: list[int]) -> dict:
    return {"momentum": mi["momentum"]["score"], "sell_pressure": mi["sell_pressure"]["score"],
            "liq_health": mi["liquidity_health"]["score"],
            "chain_window_secs": C.CHAIN_WINDOW_SECS, "chain_len": len(chain_ts)}


# ------------------------------------------------------------------ persistence
def record_events(db, token_id: int, ts: int, events: list[dict]) -> int:
    """Cooldown-deduped persistence with escalation keys (§43)."""
    written = 0
    for e in events:
        key = e.get("cooldown_key", "")
        recent = db.execute(
            select(SignalEvent.key).where(
                SignalEvent.token_id == token_id, SignalEvent.kind == e["kind"],
                SignalEvent.ts >= ts - e["cooldown_secs"])
            .order_by(SignalEvent.ts.desc()).limit(1)).first()
        if recent is not None:
            # escalation: allow if new magnitude key strictly exceeds stored key
            if not key or key <= (recent[0] or ""):
                continue
        db.add(SignalEvent(
            token_id=token_id, ts=ts, kind=e["kind"], key=key,
            title=e["title"], detail=e["detail"] if "detail" in e else e["signal_text"],
            severity=e["severity"], metrics_json=e["metrics"],
            category=e["category"], importance=e["importance"],
            importance_score=e["importance_score"], confidence=e["confidence"],
            signal_text=e["signal_text"], why_json=e["why"], concern=e.get("concern"),
            chain_json=e.get("chain_json"), checklist_json=e["checklist"]))
        written += 1
    return written


# ------------------------------------------------------------------ outcomes §49
HORIZONS = (900, 3600, 14400, 86400)


def label_event_outcomes(db, now: int, batch: int = 300) -> int:
    """Forward-return labeling of intelligence events (v3 §49)."""
    created = 0
    for h in HORIZONS:
        already = select(SignalEventOutcome.id).where(
            SignalEventOutcome.event_id == SignalEvent.id,
            SignalEventOutcome.horizon_secs == h).exists()
        rows = db.execute(
            select(SignalEvent).where(SignalEvent.ts <= now - h, ~already)
            .order_by(SignalEvent.ts.desc()).limit(batch)).scalars().all()
        for ev in rows:
            prices = {r.ts: r.price_usd for r in db.execute(
                select(PriceSnapshot).where(
                    PriceSnapshot.token_id == ev.token_id,
                    PriceSnapshot.ts >= ev.ts - 300,
                    PriceSnapshot.ts <= ev.ts + h + 300)).scalars().all()}
            if len(prices) < 4:
                continue
            series = [(t, v) for t, v in tsx.series_list(prices, ev.ts) if t <= ev.ts + h]
            if len(series) < 2:
                continue
            entry = tsx.value_at_or_before(prices, ev.ts)
            exit_p = tsx.value_at_or_before(prices, ev.ts + h)
            if entry <= 0 or exit_p <= 0:
                continue
            ret = (exit_p / entry - 1) * 100
            dd = min((v / entry - 1) * 100 for _, v in series)
            up = max((v / entry - 1) * 100 for _, v in series)
            db.add(SignalEventOutcome(event_id=ev.id, token_id=ev.token_id, ts=ev.ts,
                                      horizon_secs=h, kind=ev.kind, category=ev.category,
                                      importance=ev.importance, fwd_ret_pct=round(ret, 2),
                                      max_dd_pct=round(dd, 2), max_up_pct=round(up, 2)))
            created += 1
            if created % 100 == 0:
                db.flush()
    return created
