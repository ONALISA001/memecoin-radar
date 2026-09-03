"""MOCK providers for social, on-chain and news data (Phase 1 — §32).

⚠️  Everything produced here is SIMULATED. Sources are explicitly labelled
"mock:*" and surfaced through GET /sources and the data-confidence UI (§30).

Design: on first sight each token receives a deterministic parameter set
(*state*) seeded by sha256(address) — archetype
(emerging / overheated / rug_risk / steady / dormant, biased by real market
momentum), ramp anchors, baselines. The state is persisted on the token row,
and all value functions depend ONLY on (params, bucket_ts), so backfills,
live ticks and restarts produce one continuous, reproducible history.

Swap-in path for real providers (Phase 2/3): implement the same surface
(bucket_social / bucket_onchain / bucket_market / posts / whales / articles)
against X API / Solana RPC / news APIs and remove these.
"""
from __future__ import annotations

import hashlib
import math
import random
import time
from dataclasses import dataclass

BUCKET = 300  # 5-minute grid (§33)


def sha_seed(s: str) -> int:
    return int(hashlib.sha256(s.encode()).hexdigest()[:12], 16)


def n01(seed: int, salt: str, t: float) -> float:
    h = hashlib.md5(f"{seed}:{salt}:{t}".encode()).hexdigest()
    return int(h[:8], 16) / 0xFFFFFFFF


def nz(seed: int, salt: str, t: float, lo: float, hi: float) -> float:
    return lo + (hi - lo) * n01(seed, salt, t)


def bucket_of(ts: float) -> int:
    return int(ts) - int(ts) % BUCKET


def _logistic(x: float, center: float, tau: float) -> float:
    tau = max(tau, 1.0)
    z = (x - center) / tau
    if z > 40:
        return 1.0
    if z < -40:
        return 0.0
    return 1.0 / (1.0 + math.exp(-z))


def _pick_archetype(r: random.Random, pc_h24: float, pc_h1: float,
                    liq: float, vol_h1: float) -> str:
    mom_up = (pc_h24 > 250) or (pc_h1 > 40)
    mom_down = pc_h24 < -35
    thin = liq < 15_000 and vol_h1 > 3 * max(liq, 1)
    roll = r.random()
    if thin and roll < 0.5:
        return "rug_risk"
    if mom_up:
        return "emerging" if roll < 0.5 else "overheated"
    if mom_down and roll < 0.6:
        return "dormant"
    return ("emerging" if roll < 0.24 else "overheated" if roll < 0.44
            else "rug_risk" if roll < 0.54 else "steady" if roll < 0.8 else "dormant")


@dataclass
class Sim:
    """A token's deterministic simulated world, driven by a persisted state."""
    address: str
    p: dict

    # ------------------------------------------------ construction ----
    @classmethod
    def fresh(cls, address: str, *, price_now: float, mcap_now: float, liq_now: float,
              vol_h1: float, vol_h24: float, pc_h1: float, pc_h6: float, pc_h24: float,
              ref_bucket: int) -> "Sim":
        seed = sha_seed(address)
        r = random.Random(seed)
        arch = _pick_archetype(r, pc_h24, pc_h1, liq_now, vol_h1)
        nb = ref_bucket
        p: dict = {
            "seed": seed, "archetype": arch, "ref": nb,
            "price_now": max(price_now, 1e-12), "mcap_now": max(mcap_now, 0.0),
            "liq_now": max(liq_now, 0.0),
        }
        if arch == "emerging":
            p.update(ramp_start=nb - r.randint(2, 14) * BUCKET, peak_mult=r.uniform(8, 30),
                     diversity=r.uniform(0.55, 0.85), lowq=r.uniform(0.12, 0.3),
                     top10=r.uniform(12, 32), hold_k=r.uniform(0.5, 1.1), decay_start=0,
                     liq_drop=0.0, liq_drop_start=0)
        elif arch == "overheated":
            p.update(ramp_start=nb - r.randint(72, 240) * BUCKET, peak_mult=r.uniform(14, 34),
                     diversity=r.uniform(0.4, 0.7), lowq=r.uniform(0.3, 0.5),
                     top10=r.uniform(18, 40), hold_k=r.uniform(1.0, 1.8),
                     decay_start=nb - r.randint(6, 24) * BUCKET, liq_drop=0.0, liq_drop_start=0)
        elif arch == "rug_risk":
            p.update(ramp_start=nb - r.randint(3, 18) * BUCKET, peak_mult=r.uniform(12, 40),
                     diversity=r.uniform(0.12, 0.3), lowq=r.uniform(0.5, 0.8),
                     top10=r.uniform(52, 78), hold_k=r.uniform(0.8, 1.6), decay_start=0,
                     liq_drop=r.uniform(0.3, 0.6), liq_drop_start=nb - r.randint(12, 60) * BUCKET)
        elif arch == "steady":
            p.update(ramp_start=nb - r.randint(200, 560) * BUCKET, peak_mult=r.uniform(1.2, 3.0),
                     diversity=r.uniform(0.6, 0.9), lowq=r.uniform(0.08, 0.22),
                     top10=r.uniform(8, 26), hold_k=r.uniform(0.1, 0.35), decay_start=0,
                     liq_drop=0.0, liq_drop_start=0)
        else:  # dormant
            p.update(ramp_start=nb - r.randint(400, 700) * BUCKET, peak_mult=r.uniform(0.8, 1.4),
                     diversity=r.uniform(0.5, 0.8), lowq=r.uniform(0.1, 0.3),
                     top10=r.uniform(10, 30), hold_k=r.uniform(0.02, 0.08), decay_start=0,
                     liq_drop=0.0, liq_drop_start=0)
        p["tau"] = r.uniform(30, 70) * 60
        p["base_mentions"] = max(0.6, math.sqrt(max(mcap_now, 1e4)) / 420.0)
        p["base_vol"] = max(vol_h1, vol_h24 / 24.0) / 12.0 + 1.0
        p["avg_trade"] = r.uniform(25, 400) * (1 + math.log10(max(mcap_now, 1e5)) / 8)
        p["holders_base"] = int(max(60, mcap_now / r.uniform(1800, 4200)))
        sim = cls(address=address, p=p)
        p["vol_norm"] = (max(vol_h1, 0.0) / max(1e-9, sum(
            sim._raw_vol(t) for t in range(nb - 11 * BUCKET, nb + BUCKET, BUCKET))))
        p["holders_norm"] = sim._adoption(nb)
        if p["liq_drop"] > 0:
            p["liq_before"] = liq_now / max(0.05, 1 - p["liq_drop"])
        else:
            p["liq_before"] = liq_now
        # backfill price anchors (fixed at first sighting)
        anchors = []
        for secs, pc in ((3600, pc_h1), (21600, pc_h6), (86400, pc_h24)):
            back = p["price_now"] / (1 + pc / 100.0) if pc > -99.9 else p["price_now"] * 100
            anchors.append((nb - secs, max(back, 1e-12)))
        p["price_anchors"] = sorted(anchors)
        return sim

    @classmethod
    def from_state(cls, address: str, state: dict) -> "Sim":
        return cls(address=address, p=dict(state))

    def to_state(self) -> dict:
        return dict(self.p)

    @property
    def archetype(self) -> str:
        return self.p.get("archetype", "steady")

    # ------------------------------------------------ curves ----------
    def _ramp(self, t: float) -> float:
        return _logistic(t, self.p["ramp_start"], self.p["tau"])

    def _attention(self, t: float) -> float:
        v = 0.12 + self.p["peak_mult"] * self._ramp(t)
        ds = self.p.get("decay_start") or 0
        if ds:
            v *= 1 - 0.55 * _logistic(t, ds, self.p["tau"])
        return v

    def _adoption(self, t: float) -> float:
        return 1 + self.p["hold_k"] * max(self._ramp(t), 0.05)

    def _raw_vol(self, t: float) -> float:
        lead = _logistic(t, self.p["ramp_start"] - self.p["tau"] * 0.6, self.p["tau"])
        v = 0.22 + (0.35 + self.p["peak_mult"] * 0.6) * lead
        ds = self.p.get("decay_start") or 0
        if ds:
            v *= 1 - 0.5 * _logistic(t, ds, self.p["tau"] * 0.8)
        return v * nz(self.p["seed"], "vol", t, 0.65, 1.45)

    # ------------------------------------------------ series ----------
    def bucket_social(self, t: int) -> dict:
        s = self.p["seed"]
        att = self._attention(t)
        mentions = max(0, round(self.p["base_mentions"] * att * nz(s, "m", t, 0.55, 1.5)))
        if self.archetype == "rug_risk":
            uniq_frac = self.p["diversity"] * (1 - 0.4 * _logistic(
                t, self.p["ramp_start"] + self.p["tau"], self.p["tau"]))
        else:
            uniq_frac = self.p["diversity"]
        uniques = min(mentions, round(mentions * max(0.05, uniq_frac) * nz(s, "u", t, 0.9, 1.1)))
        eng = round(mentions * nz(s, "e", t, 3.0, 11.0))
        delta = self._attention(t + BUCKET) - att
        sent = 0.5 + 0.35 * min(1.0, att / max(self.p["peak_mult"], 1))
        if self.p.get("decay_start") and t > self.p["decay_start"]:
            sent -= 0.25
        lowq = self.p["lowq"] + (0.15 if self.archetype == "rug_risk" else 0.05) * _logistic(
            t, self.p["ramp_start"] + self.p["tau"], self.p["tau"])
        return {"mentions": mentions, "unique_accounts": int(uniques), "engagement": eng,
                "likes": round(eng * 0.55), "reposts": round(eng * 0.18),
                "replies": round(eng * 0.27),
                "sentiment": round(max(-1.0, min(1.0, sent + 3 * delta + nz(s, "s", t, -0.12, 0.12))), 3),
                "influencer_posts": (1 + int(att / 9) if att > 2.2 and n01(s, "inf", t) < 0.28 else 0),
                "low_quality_share": round(min(0.95, lowq), 3)}

    def bucket_onchain(self, t: int) -> dict:
        s = self.p["seed"]
        adop = self._adoption(t) / max(1e-9, self.p.get("holders_norm") or 1.0)
        adop_prev = self._adoption(t - BUCKET) / max(1e-9, self.p.get("holders_norm") or 1.0)
        holders = max(20, int(self.p["holders_base"] * adop * nz(s, "h", t, 0.97, 1.03)))
        prev = max(20, int(self.p["holders_base"] * adop_prev * nz(s, "h", t - BUCKET, 0.97, 1.03)))
        att = self._attention(t)
        whale_net = nz(s, "wn", t, -0.02, 0.02) * max(self.p["mcap_now"], 1e5)
        if self.archetype == "rug_risk" and t > self.p["ramp_start"] and n01(s, "ws", t) < 0.3:
            whale_net -= nz(s, "wx", t, 4_000, 90_000)
        return {"holders": holders, "new_wallets": max(0, holders - prev),
                "active_wallets": max(1, int(holders * min(0.4, 0.02 + 0.06 * att)
                                             * nz(s, "aw", t, 0.7, 1.3))),
                "top10_pct": round(min(95.0, self.p["top10"] * nz(s, "t10", t, 0.94, 1.06)), 2),
                "deployer_pct": round(min(40.0, (self.p["top10"] * (0.35 if self.archetype == "rug_risk" else 0.12))
                                        * nz(s, "dep", t, 0.9, 1.1)), 2),
                "whale_net_usd": round(whale_net, 2)}

    def bucket_market(self, t: int) -> dict:
        """Backfill market series; the CURRENT bucket is overridden by real
        provider values at ingest time wherever available."""
        s = self.p["seed"]
        p_now = self.p["price_now"]
        anchors = self.p.get("price_anchors") or []
        ref = self.p["ref"]
        if t >= ref:
            price = p_now
        elif anchors and t >= anchors[0][0]:
            pts = anchors + [(ref, p_now)]
            price = pts[0][1]
            for (t0, p0), (t1, p1) in zip(pts, pts[1:]):
                if t0 <= t <= t1:
                    f = (t - t0) / max(t1 - t0, 1)
                    price = math.exp(math.log(p0) + f * (math.log(p1) - math.log(p0)))
                    break
        elif anchors:
            decay = min(0.9, (anchors[0][0] - t) / 86400.0 * nz(s, "od", t // 86400, 0.1, 0.5))
            price = anchors[0][1] * (1 - decay)
        else:
            price = p_now * 0.6
        if t != ref:
            price *= nz(s, "p", t, 0.97, 1.03)
        price = max(price, 1e-12)
        vol = self._raw_vol(t) * self.p.get("vol_norm", 0.0)
        txns = max(1, round(vol / max(self.p["avg_trade"], 1)))
        a = self.archetype
        if a == "rug_risk":
            share = nz(s, "bs", t, 0.82, 0.95)
        elif a == "overheated" and self.p.get("decay_start") and t > self.p["decay_start"]:
            share = nz(s, "bs", t, 0.4, 0.52)
        elif a == "emerging":
            share = nz(s, "bs", t, 0.58, 0.78)
        else:
            share = nz(s, "bs", t, 0.46, 0.6)
        if a == "rug_risk":
            liq = self.p["liq_before"] * (1 - self.p["liq_drop"] * _logistic(
                t, self.p["liq_drop_start"], 45 * 60)) * nz(s, "l", t, 0.98, 1.02)
        else:
            ratio = price / p_now
            growth = 0.15 * self._ramp(t) if a == "emerging" else 0.0
            liq = self.p["liq_now"] * (0.55 + 0.45 * ratio + growth) * nz(s, "l", t, 0.97, 1.03)
        return {"price_usd": price, "mcap": max(0.0, self.p["mcap_now"] * price / p_now),
                "fdv": max(0.0, self.p["mcap_now"] * 1.02 * price / p_now),
                "liquidity_usd": max(0.0, liq), "volume": round(vol, 2), "txns": txns,
                "buys": min(txns, round(txns * share)),
                "sells": txns - min(txns, round(txns * share))}

    def contract_facts(self) -> dict:
        r = random.Random(self.p["seed"] + 7)
        if self.archetype == "rug_risk":
            return {"mint_authority": r.random() < 0.8, "freeze_authority": r.random() < 0.6,
                    "lp_burned_pct": round(r.uniform(0, 25), 1),
                    "deployer_prev_launches": r.randint(3, 9), "deployer_suspicious": True,
                    "source": "mock:chain-sim"}
        return {"mint_authority": r.random() < 0.12, "freeze_authority": r.random() < 0.05,
                "lp_burned_pct": round(r.uniform(72, 100), 1),
                "deployer_prev_launches": r.randint(0, 2), "deployer_suspicious": False,
                "source": "mock:chain-sim"}


# --------------------------- generated content ---------------------------
_POST_TEMPLATES = [
    "${sym} is just getting started 🚀 #{kw}", "aped ${sym}, this {kw} meta is real",
    "${sym} chart looking strong af 👀", "why is nobody talking about ${sym} yet? {kw}",
    "${sym} {kw} narrative loading…", "gm to ${sym} holders only ☀️",
    "whales accumulating ${sym} 👀📈", "${sym} community is actually different fr",
    "loading more ${sym} here, {kw} szn", "${sym} volume picking up, iykyk",
    "can't fade the {kw} wave, ${sym} leads it", "${sym} dip = gift 🎁",
    "who else early to ${sym}? #{kw}", "${sym} trending on every feed rn",
    "serious {kw} rotation into ${sym}", "not financial advice but ${sym} 👀",
]


def generate_posts(sim: Sim, symbol: str, keywords: list[str], now: int, count: int = 14) -> list[dict]:
    posts = []
    kw = (keywords[0] if keywords else "memecoin").replace(" ", "").lower()
    span = 6 * 3600
    base_q = {"rug_risk": 0.35, "overheated": 0.5}.get(sim.archetype, 0.7)
    for i in range(count):
        t = now - int((i + 0.5) / count * span)
        tpl = _POST_TEMPLATES[int(n01(sim.p["seed"], "tpl", i) * len(_POST_TEMPLATES)) % len(_POST_TEMPLATES)]
        q = min(1.0, base_q * nz(sim.p["seed"], "pq", i, 0.4, 1.6))
        posts.append({"ts": t - t % 60,
                      "author": f"{'sig' if q > 0.65 else 'anon'}_{sha_seed(sim.address + str(i)) % 9999}",
                      "text": tpl.format(sym=symbol, kw=kw),
                      "likes": int(q * nz(sim.p["seed"], "pl", i, 2, 900)),
                      "reposts": int(q * nz(sim.p["seed"], "pr", i, 0, 300)),
                      "quality": round(q, 2)})
    return posts


def live_post(sim: Sim, symbol: str, keywords: list[str], bucket: int) -> dict | None:
    """Occasionally emit a fresh post for socially-active tokens."""
    if sim._attention(bucket) < 2.0 and n01(sim.p["seed"], "lp", bucket) > 0.06:
        return None
    if n01(sim.p["seed"], "lp2", bucket) > 0.5:
        return None
    kw = (keywords[0] if keywords else "memecoin").replace(" ", "").lower()
    i = bucket // BUCKET
    tpl = _POST_TEMPLATES[int(n01(sim.p["seed"], "lpl", i) * len(_POST_TEMPLATES)) % len(_POST_TEMPLATES)]
    q = min(1.0, 0.5 * nz(sim.p["seed"], "lpq", bucket, 0.4, 1.8))
    return {"ts": bucket, "author": f"live_{sha_seed(sim.address + str(bucket)) % 9999}",
            "text": tpl.format(sym=symbol, kw=kw),
            "likes": int(q * nz(sim.p["seed"], "lpl2", bucket, 2, 400)),
            "reposts": int(q * nz(sim.p["seed"], "lpr", bucket, 0, 120)), "quality": round(q, 2)}


def generate_whale_events(sim: Sim, bucket: int) -> list[dict]:
    events = []
    p_event = {"rug_risk": 0.5, "overheated": 0.4, "emerging": 0.35,
               "steady": 0.12, "dormant": 0.03}[sim.archetype]
    if n01(sim.p["seed"], "we", bucket) > p_event:
        return events
    n = 2 if n01(sim.p["seed"], "we2", bucket) < 0.25 else 1
    for i in range(n):
        sell = (sim.archetype == "rug_risk" and n01(sim.p["seed"], "side", bucket + i) < 0.7) or \
               n01(sim.p["seed"], "side", bucket + i) < 0.4
        events.append({"ts": bucket + 60 * i,
                       "wallet": f"{sim.address[:6]}wh{int(n01(sim.p['seed'], 'w', bucket + i) * 999):03d}",
                       "side": "sell" if sell else "buy",
                       "amount_usd": round(nz(sim.p["seed"], "amt", bucket + i, 5_000,
                                              120_000 if sim.archetype != "dormant" else 20_000), 0),
                       "kind": "whale"})
    return events


_NEWS_TEMPLATES = [
    "Memecoin traders pile into {narr} tokens as mentions surge",
    "{narr}: the memecoin narrative gaining steam this week",
    "New wave of {narr}-themed tokens launches amid social spike",
    "Analysts debate sustainability of the {narr} memecoin trend",
    "{narr} tokens dominate DEX trending lists",
    "Social volume for {narr} coins hits multi-week high",
]


def generate_articles(narrative: str, symbols: list[str], now: int, count: int = 2) -> list[dict]:
    arts = []
    h = sha_seed(narrative)
    for i in range(count):
        tpl = _NEWS_TEMPLATES[int(n01(h, "nt", i) * len(_NEWS_TEMPLATES)) % len(_NEWS_TEMPLATES)]
        arts.append({"ts": now - int(n01(h, "nts", i) * 6 * 3600),
                     "source": ["CoinPulse", "BlockBeat", "TheMemeTimes", "ChainWire"][int(n01(h, "ns", i) * 4) % 4],
                     "title": tpl.format(narr=narrative.lower()),
                     "url": f"https://mocknews.example/{narrative.lower().replace(' ', '-')}-{i}",
                     "narrative": narrative,
                     "sentiment": round(nz(h, "nse", i, -0.1, 0.7), 2),
                     "relevance": round(nz(h, "nr", i, 0.5, 0.95), 2),
                     "entities": symbols[:6]})
    return arts


# Fallback universe used ONLY if DexScreener is unreachable (§39.10).
FALLBACK_TOKENS: list[tuple[str, str, str]] = [
    ("AIxDoge", "AIDOGE", "solana"), ("AgentPepe", "AGPEPE", "solana"),
    ("RoboCat", "ROBOCAT", "solana"), ("GptFrog", "GPTFROG", "solana"),
    ("MoonShiba", "MSHIB", "solana"), ("TurboWif", "TWIF", "solana"),
    ("ChillGup", "CHILLG", "solana"), ("SigmaPig", "SIGMAP", "solana"),
    ("DePinNode", "DPNODE", "solana"), ("PixelQuest", "PXQ", "base"),
    ("MagaMouse", "MAGAM", "solana"), ("ElonFalcon", "EFALC", "ethereum"),
    ("ViralToad", "VTOAD", "solana"), ("WordleCat", "WDCAT", "bnb"),
    ("NvidiaInu", "NVINU", "solana"), ("ClaudiusBot", "CLAUDIUS", "solana"),
    ("BonkSister", "BSIS", "solana"), ("PopcatSon", "PSON", "solana"),
    ("GigaChad", "GCHAD", "base"), ("MoodengJr", "MDJR", "solana"),
]
