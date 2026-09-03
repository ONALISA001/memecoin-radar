"""Market intelligence engine (v2 spec §4–13, §17–19).

Composite, fully explainable market-intelligence scores derived from the stored
5-minute time-series grid — no extra provider calls, every factor traceable
to source rows (§30 explainability).

Produces, per token per tick:
  momentum        0–100  §4–§5  multi-factor momentum with bands + factor list
  sell_pressure   0–100  §6     accumulation ↔ heavy-distribution scale
  liquidity_health 0–100 §10    pool depth & stability
  ath             §7             price position vs tracked high
  volume_accel    §12            5m/15m/1h/4h window-over-window acceleration
  pressure        §11            buy vs sell counts/shares/trend
  whales          §8             whale accumulation/distribution (INFERRED)
  reversals       §18            momentum divergence / weakening / distribution
  confluence      §13            multi-signal composite + reasons

Data-quality contract (§23): every number here is a DERIVED metric from stored
snapshots; whale behavior is explicitly INFERRED, never asserted as fact.
"""
from __future__ import annotations

from . import timeseries as tsx


def _clamp(x: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, x))


def _map(x: float, span: float) -> float:
    """Map a signed change onto -1..+1 with a saturation span."""
    return _clamp(x / span, -1.0, 1.0) if span else 0.0


def _strength(score: float) -> str:
    return ("very strong" if score >= 80 else "strong" if score >= 65
            else "moderate" if score >= 50 else "weak" if score >= 35 else "very weak")


def _factor(key: str, label: str, score: float, detail: str,
            invert: bool = False) -> dict:
    return {"key": key, "label": label, "score": round(score, 1),
            "strength": _strength(score),
            # direction: +1 supports momentum, -1 works against it
            "direction": 1 if (score >= 60 if not invert else score <= 40)
            else -1 if (score <= 40 if not invert else score >= 60) else 0,
            "detail": detail}


# ---------------------------------------------------------------- momentum §4–5
def _momentum(ctx) -> dict:
    d = ctx.d
    now = ctx.now
    price = tsx.value_at_or_before(ctx.price, now)
    high24 = max((v for t, v in tsx.series_list(ctx.price, now - 86400)), default=price)
    chg4h = tsx.level_growth(ctx.price, now, 14400)[2]
    dist_high = ((high24 - price) / high24 * 100) if high24 > 0 else 0.0
    # 4h price momentum fills d lazily to avoid touching context for other engines
    d["price_chg_4h"] = chg4h

    vol4h = tsx.window_growth(ctx.volume, now, 14400)
    txns4h = tsx.window_growth(ctx.txns, now, 14400)
    buys4h = tsx.ssum(ctx.buys, now - 14400, now)
    sells_series = {t: v for t, v in ctx.txns.items()}
    d["volume_g_4h"] = vol4h[2]
    d["txns_g_4h"] = txns4h[2]

    # --- sub-scores 0–100
    price_sub = _clamp(50
        + 50 * (0.35 * _map(d["price_chg_1h"], 40) + 0.20 * _map(d["price_chg_15m"], 15)
                + 0.25 * _map(chg4h, 80) + 0.20 * _map(d["price_chg_24h"], 150))
        + (6 if dist_high < 2 else -min(12, max(0, dist_high - 25) * 0.5)), 0, 100)

    vmr = d["volume_1h"] / d["mcap_now"] if d["mcap_now"] > 0 else 0.0
    volume_sub = _clamp(50
        + 25 * _map(d["volume_g_1h"], 300) + 12 * _map(d["volume_g_15m"], 200)
        + 10 * _map(vol4h[2], 150)
        + 13 * _clamp(vmr / 0.08, 0, 1)
        + (8 if ctx.anomalies.get("volume", 0) >= 2.5 else 0), 0, 100)
    if d["volume_1h"] <= 0:
        volume_sub = min(volume_sub, 25.0)

    buys_g_1h = tsx.window_growth(ctx.buys, now, 3600)[2]
    txn_sub = _clamp(50
        + 20 * _map(d["txns_g_1h"], 300) + 10 * _map(d["txns_g_15m"], 200)
        + 10 * _map(txns4h[2], 150)
        + 18 * _map((d["buy_share_1h"] - 0.5) * 100, 25)
        + 7 * _map(buys_g_1h, 300), 0, 100)

    holder_sub = _clamp(50
        + 26 * _map(d["holders_g_1h"], 25) + 16 * _map(d["holders_g_15m"], 10)
        + 18 * _map(d["holders_g_24h"], 100) + 15 * _map(d["new_wallets_g_1h"], 300), 0, 100)

    liq_mcap = d["liq_now"] / d["mcap_now"] if d["mcap_now"] > 0 else 0.0
    liq_sub = _clamp(50
        + 28 * _map(d["liq_chg_3h"], 30) + 12 * _map(d["liq_chg_24h"], 60)
        + 20 * _map(liq_mcap * 100, 25), 0, 100)

    social_sub = _clamp(50
        + 26 * _map(d["mentions_g_1h"], 400) + 14 * _map(d["mentions_g_24h"], 200)
        + 10 * _clamp(d["influencers_1h"] / 6, 0, 1), 0, 100)

    # §4: social must not dominate — its deviation from neutral is halved
    social_adj = 50 + (social_sub - 50) * 0.5
    total = (0.22 * price_sub + 0.20 * volume_sub + 0.16 * txn_sub
             + 0.16 * holder_sub + 0.12 * liq_sub + 0.14 * social_adj)
    total = round(_clamp(total, 0, 100), 1)

    band = ("extreme" if total >= 80 else "strong" if total >= 65
            else "developing" if total >= 50 else "weakening" if total >= 35 else "low")
    band_label = {"extreme": "Extreme Momentum", "strong": "Strong Momentum",
                  "developing": "Developing", "weakening": "Weakening",
                  "low": "Low Momentum"}[band]

    factors = [
        _factor("price", "Price momentum", price_sub,
                f"1h {d['price_chg_1h']:+.1f}% · 4h {chg4h:+.1f}% · 24h {d['price_chg_24h']:+.1f}% · {dist_high:.1f}% off 24h high"),
        _factor("volume", "Volume momentum", volume_sub,
                f"accel 15m {d['volume_g_15m']:+.0f}% · 1h {d['volume_g_1h']:+.0f}% · 4h {vol4h[2]:+.0f}% · 1h vol = {vmr * 100:.1f}% of mcap"),
        _factor("transactions", "Transaction momentum", txn_sub,
                f"txns 1h {d['txns_g_1h']:+.0f}% · buy share {d['buy_share_1h'] * 100:.0f}% · {d['txns_1h']:.0f} txns/h"),
        _factor("holders", "Holder momentum", holder_sub,
                f"holders 15m {d['holders_g_15m']:+.1f}% · 1h {d['holders_g_1h']:+.1f}% · 24h {d['holders_g_24h']:+.1f}%"),
        _factor("liquidity", "Liquidity trend", liq_sub,
                f"3h {d['liq_chg_3h']:+.1f}% · 24h {d['liq_chg_24h']:+.1f}% · liq/mcap {liq_mcap * 100:.1f}%"),
        _factor("social", "Attention (capped)", social_adj,
                f"mentions 1h {d['mentions_g_1h']:+.0f}% · 24h {d['mentions_g_24h']:+.0f}%"),
    ]
    positive = [f for f in factors if f["score"] >= 60]
    negative = [f for f in factors if f["score"] <= 40]
    return {"score": total, "band": band, "band_label": band_label,
            "factors": factors, "distance_from_high_pct": round(dist_high, 1),
            "positive": [f["label"] + " — " + f["detail"] for f in positive],
            "negative": [f["label"] + " — " + f["detail"] for f in negative]}


# ------------------------------------------------------- sell pressure §6
def _sell_pressure(ctx, momentum_band_strength: float) -> dict:
    d = ctx.d
    now = ctx.now
    parts: list[tuple[str, float, str]] = []

    bs = d["buy_share_1h"]
    bs_prev = (tsx.ssum(ctx.buys, now - 14400, now - 10800)
               / max(tsx.ssum(ctx.txns, now - 14400, now - 10800), 1))
    parts.append(("Buy/sell balance", _clamp(50 - (bs - 0.5) * 100 * 1.6, 0, 100),
                  f"buy share 1h {bs * 100:.0f}% (4h ago {bs_prev * 100:.0f}%)"))

    sells_g = tsx.window_growth(ctx.txns, now, 3600)[2] - 0  # activity growth
    buys_g = tsx.window_growth(ctx.buys, now, 3600)[2]
    sells_series_g = tsx.window_growth({t: b for t, b in ctx.txns.items()}, now, 3600)[2]
    sell_rel = _clamp(50 + (sells_series_g - buys_g) / 6, 0, 100)
    parts.append(("Sell-side growth", sell_rel,
                  f"sell-side activity {sells_series_g:+.0f}% vs buy-side {buys_g:+.0f}% 1h"))

    wn = d["whale_net_24h"]
    vol24 = max(d["volume_24h"], 1.0)
    whale_sev = _clamp(50 + (-wn / vol24 * 100) * 80, 0, 100)
    parts.append(("Whale flow (inferred)", whale_sev,
                  f"net whale flow 24h ${wn:,.0f} ({wn / vol24 * 100:+.1f}% of volume)"))

    hh = _clamp(50 + (-d["holders_g_1h"] / 8) * 50 + (-max(d["holders_g_24h"], -30) / 30) * 20, 0, 100)
    parts.append(("Holder behavior", hh,
                  f"holders 1h {d['holders_g_1h']:+.1f}% · 24h {d['holders_g_24h']:+.1f}%"))

    liq_sev = _clamp(50 + (-d["liq_chg_3h"] / 10) * 30 + (-d["liq_chg_24h"] / 25) * 20, 0, 100)
    parts.append(("Liquidity behavior", liq_sev,
                  f"liq 3h {d['liq_chg_3h']:+.1f}% · 24h {d['liq_chg_24h']:+.1f}%"))

    divergence = 50.0
    if d["price_chg_1h"] < -2 and d["volume_g_1h"] > 30:
        divergence = 82.0
    elif d["price_chg_1h"] < 0 and d["volume_g_1h"] > 0:
        divergence = 65.0
    elif d["price_chg_1h"] > 3 and d["volume_g_1h"] < -20:
        divergence = 60.0
    parts.append(("Volume/price structure", divergence,
                  "selling into elevated volume" if divergence == 82
                  else "price down, volume up" if divergence == 65
                  else "price up, volume fading" if divergence == 60
                  else "no divergence"))

    weights = (0.24, 0.16, 0.20, 0.14, 0.14, 0.12)
    score = round(_clamp(sum(w * p[1] for w, p in zip(weights, parts)), 0, 100), 1)
    band = ("healthy_accumulation" if score < 25 else "neutral" if score < 45
            else "distribution" if score < 65 else "heavy_distribution")
    band_label = {"healthy_accumulation": "Healthy accumulation", "neutral": "Neutral",
                  "distribution": "Distribution", "heavy_distribution": "Heavy distribution"}[band]
    factors = [_factor(k, k, s, txt, invert=True) for k, s, txt in parts]
    return {"score": score, "band": band, "band_label": band_label, "factors": factors,
            "note": "Sell pressure is inferred from aggregate flow & structure — it cannot identify individual wallets' intent (§6, §23)."}


# ------------------------------------------------------- liquidity health §10
def _liquidity_health(ctx) -> dict:
    d = ctx.d
    liq = d["liq_now"]
    base = 10 if liq < 1000 else 40 if liq < 5000 else 60 if liq < 20000 else 78 if liq < 100000 else 88
    liq_mcap = liq / d["mcap_now"] if d["mcap_now"] > 0 else 0.0
    score = base + (8 if liq_mcap >= 0.25 else -12 if liq_mcap < 0.05 else 0) \
        + (-15 if d["vol_liq_ratio"] > 1.5 else -8 if d["vol_liq_ratio"] > 1.0 else 0) \
        + (-10 if d["liq_chg_3h"] < -10 else 8 if d["liq_chg_3h"] > 10 else 0) \
        + (6 if d["liq_chg_24h"] > 15 else -8 if d["liq_chg_24h"] < -15 else 0)
    score = round(_clamp(score, 1, 100), 1)
    band = "healthy" if score >= 70 else "moderate" if score >= 45 else "thin" if score >= 25 else "critical"
    return {"score": score, "band": band, "band_label": band.capitalize(),
            "detail": (f"pool ${liq:,.0f} · {liq_mcap * 100:.1f}% of mcap · "
                       f"1h vol/pool {d['vol_liq_ratio']:.2f} · 3h {d['liq_chg_3h']:+.1f}%")}


# ------------------------------------------------------- ATH / position §7
def _ath(ctx, sellp_score: float) -> dict:
    now = ctx.now
    price = tsx.value_at_or_before(ctx.price, now)
    series = tsx.series_list(ctx.price, 0)
    if not series:
        return {"status": "neutral", "note": "data unavailable"}
    ath_t, ath = max(series, key=lambda kv: kv[1])
    dist = (ath - price) / ath * 100 if ath > 0 else 0.0
    new_ath = ath_t >= now - 600
    high24 = max((v for t, v in series if t >= now - 86400), default=ath)
    low24 = min((v for t, v in series if t >= now - 86400), default=price)
    d = ctx.d
    chg24, chg1h = d["price_chg_24h"], d["price_chg_1h"]
    if chg1h < -10 and sellp_score >= 60:
        status = "reversal_risk"
    elif new_ath and d["volume_g_1h"] > 40:
        status = "breaking_out"
    elif chg24 > 150 and (d["volume_g_1h"] < 0 or d["mentions_g_1h"] < 0):
        status = "overheated"
    elif chg24 > 80 and dist <= 25:
        status = "extended"
    elif dist <= 8:
        status = "near_ath"
    else:
        status = "neutral"
    labels = {"breaking_out": "🟢 Breaking out", "near_ath": "🟢 Near high",
              "extended": "🟡 Extended", "overheated": "🟠 Overheated",
              "reversal_risk": "🔴 Reversal risk", "neutral": "Neutral"}
    return {"price": price, "ath": ath, "ath_ts": ath_t,
            "distance_pct": round(dist, 1), "new_ath": new_ath,
            "high_24h": high24, "low_24h": low24,
            "time_since_ath_h": round((now - ath_t) / 3600, 1),
            "status": status, "status_label": labels[status],
            "note": "Highs measured over tracked history (since first listed), not all-time exchange history."}


# ------------------------------------------------------- volume accel §12
def _volume_accel(ctx) -> dict:
    now = ctx.now
    out = {}
    for key, win in (("m5", 300), ("m15", 900), ("h1", 3600), ("h4", 14400)):
        cur, prev, g = tsx.window_growth(ctx.volume, now, win)
        out[key] = {"usd": round(cur, 2), "prev_usd": round(prev, 2), "growth_pct": round(g, 1)}
    abnormal = out["h1"]["growth_pct"] > 200 or ctx.anomalies.get("volume", 0) >= 3
    bear_div = (ctx.d["price_chg_1h"] > 3 and out["h1"]["growth_pct"] < -15)
    return {**out, "abnormal": abnormal,
            "price_up_volume_down": bear_div,
            "detail": (f"1h volume ${out['h1']['usd']:,.0f} vs ${out['h1']['prev_usd']:,.0f} prior "
                       f"({out['h1']['growth_pct']:+.0f}%)")}


# ------------------------------------------------------- whales §8 (inferred)
def _whales(ctx) -> dict:
    d = ctx.d
    now = ctx.now
    net6 = tsx.ssum(ctx.whale_net, now - 21600, now)
    net24 = d["whale_net_24h"]
    vol = max(d["volume_24h"], 1.0)
    ratio = -net24 / vol
    top10 = d["top10_now"]
    top10_prev = tsx.value_at_or_before(ctx.top10, now - 86400)
    top10_delta = top10 - top10_prev
    if ratio > 0.15 and top10_delta < 0:
        cls = "heavy_distribution"
    elif ratio > 0.08 or (net6 < 0 and abs(net6) > 0.08 * vol):
        cls = "distributing"
    elif net24 > 0.08 * vol or (net6 > 0 and top10_delta > 0.5):
        cls = "accumulating"
    else:
        cls = "neutral"
    labels = {"accumulating": "🟢 Whale accumulation", "neutral": "🟡 Neutral",
              "distributing": "🟠 Whale distribution", "heavy_distribution": "🔴 Heavy whale distribution"}
    return {"classification": cls, "label": labels[cls], "inferred": True,
            "net_flow_24h_usd": round(net24, 0), "net_flow_6h_usd": round(net6, 0),
            "top10_pct": round(top10, 1), "top10_delta_24h": round(top10_delta, 2),
            "detail": (f"Net large-wallet flow ${net24:,.0f}/24h "
                       f"({-net24 / vol * 100:+.1f}% of volume) · top-10 {top10:.1f}% "
                       f"({top10_delta:+.1f} pp/24h)"),
            "note": "Whale behavior is INFERRED from aggregate large-wallet flow — not a claim about any specific wallet (§8, §23)."}


# ------------------------------------------------------- reversals §18
def _reversals(ctx, mom: dict, sellp: dict, ath: dict, vacc: dict) -> list[dict]:
    d = ctx.d
    found = []
    if d["price_chg_1h"] > 3 and (d["volume_g_1h"] < -15 or d["mentions_g_1h"] < -15):
        found.append({"kind": "momentum_divergence", "severity": "warning",
                      "title": "Momentum divergence detected",
                      "detail": (f"Price {d['price_chg_1h']:+.1f}% 1h while volume "
                                 f"{d['volume_g_1h']:+.0f}% and attention {d['mentions_g_1h']:+.0f}% — "
                                 "price alone is not confirming.")})
    if d["price_chg_1h"] < -3 and d["volume_g_1h"] > 30 and d["buy_share_1h"] < 0.45:
        found.append({"kind": "momentum_weakening", "severity": "danger",
                      "title": "Momentum reversal / distribution underway",
                      "detail": (f"Price falling ({d['price_chg_1h']:+.1f}% 1h) on rising volume "
                                 f"({d['volume_g_1h']:+.0f}%) with buy share only {d['buy_share_1h'] * 100:.0f}%.")})
    if sellp["score"] >= 65 and (ath.get("distance_pct", 100) <= 25 or d["price_chg_24h"] > 50):
        found.append({"kind": "distribution_warning", "severity": "danger",
                      "title": "Potential distribution near highs",
                      "detail": (f"Sell pressure {sellp['score']:.0f}/100 ({sellp['band_label']}) "
                                 f"while price is {ath.get('distance_pct', 0):.0f}% from tracked high "
                                 f"(+{d['price_chg_24h']:.0f}% 24h).")})
    if vacc["abnormal"] and mom["score"] < 45:
        found.append({"kind": "abnormal_volume", "severity": "warning",
                      "title": "Abnormal volume without momentum",
                      "detail": (f"Volume accelerating ({vacc['h1']['growth_pct']:+.0f}%) but momentum "
                                 f"is {mom['score']:.0f}/100 — investigate wash trading / churn.")})
    return found


# ------------------------------------------------------- confluence §13
def _confluence(ctx, mom: dict, sellp: dict, whales: dict, ath: dict) -> dict:
    d = ctx.d
    reasons_up, reasons_dn = [], []
    checks_up = [
        (mom["score"] >= 65, f"momentum {mom['score']:.0f}/100"),
        (d["holders_g_1h"] > 5, f"holders {d['holders_g_1h']:+.1f}% 1h"),
        (d["volume_g_1h"] > 20, f"volume {d['volume_g_1h']:+.0f}% 1h"),
        (d["buy_share_1h"] > 0.52, f"buy share {d['buy_share_1h'] * 100:.0f}%"),
        (d["liq_chg_3h"] >= -5, f"liquidity stable ({d['liq_chg_3h']:+.1f}% 3h)"),
        (whales["classification"] in ("accumulating",), "whale accumulation (inferred)"),
        (d["mentions_g_1h"] > 20, f"attention {d['mentions_g_1h']:+.0f}% 1h"),
    ]
    checks_dn = [
        (sellp["score"] >= 60, f"sell pressure {sellp['score']:.0f}/100"),
        (ath.get("distance_pct", 100) <= 25 or d["price_chg_24h"] > 50,
         f"price {ath.get('distance_pct', 0):.0f}% from high after {d['price_chg_24h']:+.0f}% 24h"),
        (d["volume_g_1h"] > 30, f"elevated volume ({d['volume_g_1h']:+.0f}%)"),
        (d["holders_g_1h"] < 0, f"holders declining ({d['holders_g_1h']:+.1f}%)"),
        (d["liq_chg_3h"] < -5, f"liquidity declining ({d['liq_chg_3h']:+.1f}%)"),
        (whales["classification"] in ("distributing", "heavy_distribution"),
         "whale distribution (inferred)"),
    ]
    reasons_up = [r for ok, r in checks_up if ok]
    reasons_dn = [r for ok, r in checks_dn if ok]
    if mom["score"] >= 65 and len(reasons_up) >= 5:
        strength = "high" if len(reasons_up) >= 6 else "medium"
        return {"signal": "emerging_momentum", "strength": strength,
                "label": "🟢 Emerging momentum", "reasons": reasons_up}
    if sellp["score"] >= 60 and len(reasons_dn) >= 4:
        strength = "high" if len(reasons_dn) >= 5 else "medium"
        return {"signal": "potential_distribution", "strength": strength,
                "label": "🔴 Potential distribution", "reasons": reasons_dn}
    return {"signal": "none", "strength": "low", "label": "No composite signal",
            "reasons": []}


# ------------------------------------------------------------------ entry point
def compute_market_intel(ctx, trend_score: float = 0.0, age_hours: float = 999.0) -> dict:
    mom = _momentum(ctx)
    sellp = _sell_pressure(ctx, mom["score"])
    liqh = _liquidity_health(ctx)
    ath = _ath(ctx, sellp["score"])
    vacc = _volume_accel(ctx)
    whales = _whales(ctx)
    pressure = {
        "buys_1h": round(ctx.d["buys_1h"], 1), "sells_1h": round(ctx.d["sells_1h"], 1),
        "buy_share_1h": round(ctx.d["buy_share_1h"], 3),
        "buy_share_24h": round(tsx.ssum(ctx.buys, ctx.now - 86400, ctx.now)
                               / max(tsx.ssum(ctx.txns, ctx.now - 86400, ctx.now), 1), 3),
    }
    reversals = _reversals(ctx, mom, sellp, ath, vacc)
    confl = _confluence(ctx, mom, sellp, whales, ath)
    return {"momentum": mom, "sell_pressure": sellp, "liquidity_health": liqh,
            "ath": ath, "volume_accel": vacc, "pressure": pressure,
            "whales": whales, "reversals": reversals, "confluence": confl,
            "trend_score_ref": trend_score, "age_hours": round(age_hours, 2),
            "quality": {"basis": "derived from stored 5-min snapshots",
                        "whale_note": "inferred, not wallet-verified"}}
