"""Emerging-narrative engine (§9).

Two discovery modes:
1. Seeded taxonomy — keyword matching against token names/symbols/descriptions.
2. Automatic discovery — frequent-token clustering over tokens that match no
   seeded narrative (basis for real NLP clustering in Phase 4).

Narrative Score /100 = mention growth (30) + member-token count (20) +
member volume growth (20) + strong candidates (15) + news activity (10) +
cross-platform confirmation (5).
"""
from __future__ import annotations

import re
from collections import Counter, defaultdict

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import Narrative, Token, TokenNarrative
from .timeseries import growth_pct, sat_log as sat

SEED_NARRATIVES: dict[str, list[str]] = {
    "AI Agents": ["agent", "ai16z", "claude", "gpt", "llm", "aixbt", "botai", "auton"],
    "AI & Compute": ["ai", "gpu", "compute", "neural", "nvidia", "nvda"],
    "Animals": ["dog", "cat", "frog", "pepe", "wif", "shib", "inu", "pengu", "moodeng",
                "bonk", "popcat", "goat", "chillguy", "monkey", "hippo", "snek"],
    "Politics & Figures": ["trump", "biden", "maga", "kamala", "elon", "musk", "president",
                           "politifi"],
    "Gaming": ["game", "play", "quest", "pixel", "arcade", "gg"],
    "Internet Culture": ["chill", "sigma", "rizz", "giga", "viral", "tiktok", "meme", "mog",
                         "aura", "skibidi", "retard"],
    "DePIN & Infra": ["depin", "node", "network", "miner", "iot"],
    "Celebrity": ["ye", "swift", "drake", "celebrity", "fame", "kardash"],
}
_STOP = set("the a an of in on for and or with coin token new official v2 ai".split())
_WORD = re.compile(r"[a-z]{3,}")


def token_text(t: Token) -> str:
    return f"{t.name or ''} {t.symbol or ''} {t.description or ''}".lower()


def match_seeded(text: str) -> dict[str, float]:
    out = {}
    for name, kws in SEED_NARRATIVES.items():
        hits = sum(text.count(k) for k in kws)
        if hits:
            out[name] = min(1.0, 0.4 + 0.2 * hits)
    return out


def discover_auto(tokens: list[Token], covered_ids: set[int]) -> dict[str, list[int]]:
    """Cluster uncovered tokens by shared rare words (>=3 tokens, cap 2 clusters)."""
    words: dict[int, list[str]] = {}
    counter: Counter[str] = Counter()
    for t in tokens:
        if t.id in covered_ids:
            continue
        ws = [w for w in _WORD.findall(token_text(t)) if w not in _STOP and len(w) >= 4]
        ws = list(dict.fromkeys(ws))
        words[t.id] = ws
        counter.update(ws)
    clusters: dict[str, list[int]] = defaultdict(list)
    for w, c in counter.most_common(8):
        if c >= 3 and len(clusters) < 2:
            for tid, ws in words.items():
                if w in ws:
                    clusters[w].append(tid)
    return {f"AUTO: {w}": ids for w, ids in clusters.items()}


def recompute_narratives(db: Session, tokens: list[Token],
                         metrics: dict[int, dict], now: int,
                         news_flags: dict[str, bool] | None = None) -> dict[int, dict]:
    """Upsert narratives + token links; return per-token narrative info:
    token_id → {names, best_score, count, cross_platform}"""
    per_token: dict[int, dict] = defaultdict(lambda: {"names": [], "best_score": 0.0,
                                                      "count": 0, "cross_platform": False,
                                                      "narrative_ids": []})
    covered: set[int] = set()
    assignments: dict[str, list[tuple[int, float]]] = defaultdict(list)
    for t in tokens:
        for name, rel in match_seeded(token_text(t)).items():
            assignments[name].append((t.id, rel))
            covered.add(t.id)
    for name, ids in discover_auto(tokens, covered).items():
        for tid in ids:
            assignments[name].append((tid, 0.45))

    db.execute(TokenNarrative.__table__.delete())
    for name, members in assignments.items():
        narr = db.execute(select(Narrative).where(Narrative.name == name)).scalar_one_or_none()
        if narr is None:
            narr = Narrative(name=name, kind="seeded" if name in SEED_NARRATIVES else "auto",
                             keywords_json=SEED_NARRATIVES.get(name, []))
            db.add(narr)
            db.flush()
        # aggregate member metrics
        agg_g, agg_vol_g, strong, abnormal_vol = [], [], 0, 0
        for tid, rel in members:
            m = metrics.get(tid) or {}
            agg_g.append(m.get("mentions_g_1h", 0.0))
            agg_vol_g.append(m.get("volume_g_1h", 0.0))
            if m.get("trend", 0) >= 60:
                strong += 1
            if abs(m.get("volume_z", 0.0)) >= 3:
                abnormal_vol += 1
            db.add(TokenNarrative(token_id=tid, narrative_id=narr.id, relevance=rel))
        mean_g = sum(agg_g) / len(agg_g) if agg_g else 0.0
        mean_vg = sum(agg_vol_g) / len(agg_vol_g) if agg_vol_g else 0.0
        news_hit = bool((news_flags or {}).get(name))
        cross = "HIGH" if (news_hit and mean_g > 40) else ("MEDIUM" if mean_g > 40 or news_hit else "LOW")
        score = min(100.0, 30 * sat(max(mean_g, 0), 300) + 20 * sat(len(members), 8)
                    + 20 * sat(max(mean_vg, 0), 300) + 15 * sat(strong, 3)
                    + (10 if news_hit else 0) + (5 if cross == "HIGH" else 2 if cross == "MEDIUM" else 0))
        narr.score = round(score, 1)
        narr.mentions_growth = round(mean_g, 1)
        narr.token_count = len(members)
        narr.strong_candidates = strong
        narr.abnormal_volume_tokens = abnormal_vol
        narr.cross_platform = cross
        for tid, rel in members:
            info = per_token[tid]
            info["names"].append(name)
            info["narrative_ids"].append(narr.id)
            info["count"] += 1
            info["best_score"] = max(info["best_score"], narr.score)
            info["cross_platform"] = info["cross_platform"] or cross in ("HIGH", "MEDIUM")
    return per_token


def narratives_growth_word(narr: Narrative) -> str:
    return f"{narr.mentions_growth:+.0f}%"
