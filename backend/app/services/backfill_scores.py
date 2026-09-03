"""Retroactive score backfill (Tier-4 bootstrap).

Scores are only produced live from the moment the server boots; backtesting
needs history NOW. This service replays the scoring engines over the stored
48h of snapshots at 30-minute steps, writing trend_scores rows with
kind='retro' (UI marks these as training-window estimates).

Retro rows are approximations: narrative assignments and market context use
current values; social/on-chain data in the backfill window is simulated.
They exist to bootstrap calibration curves until enough live history exists.
"""
from __future__ import annotations

import logging

from sqlalchemy import delete, select

from ..database import SessionLocal
from ..engine import fomo as fomo_engine
from ..engine import risk as risk_engine
from ..engine import scoring as scoring_engine
from ..engine.backtest import label_due
from ..engine.context import TokenContext
from ..models import Token, TrendScore
from ..providers.mock_providers import Sim

log = logging.getLogger("backfill_scores")


def run_retro_backfill(hours: int = 30, step: int = 1800) -> dict:
    from ..providers.mock_providers import bucket_of
    now = bucket_of(__import__("time").time())
    created = 0
    with SessionLocal() as db:
        has_retro = db.execute(select(TrendScore.id).where(TrendScore.kind == "retro").limit(1)).first()
        if has_retro:
            labels = label_due(db, now)
            db.commit()
            return {"skipped": True, "reason": "retro rows already exist", "labels": labels}
        tokens = db.execute(select(Token)).scalars().all()
        mkt = {"sector_momentum": 50.0, "breadth": 0.5}
        for token in tokens:
            ctx = TokenContext.build(db, token, now, hours=48)
            sim = Sim.from_state(token.address, token.state_json or {})
            facts = sim.contract_facts()
            narr_names = []
            for t in range(now - hours * 3600, now - 5 * 3600, step):
                ctx.now = t
                ctx._derive()
                info = {"best_score": 40.0 if narr_names else 0.0,
                        "count": len(narr_names), "cross_platform": False}
                tp = scoring_engine.calculate_trend_score(ctx, info, mkt)
                fp = fomo_engine.calculate_fomo(ctx, False)
                rp = risk_engine.calculate_risk(ctx, facts)
                db.execute(delete(TrendScore).where(TrendScore.token_id == token.id,
                                                    TrendScore.ts == t))
                db.add(TrendScore(token_id=token.id, ts=t, trend=tp["trend"],
                                  fomo=fp["fomo"], fomo_class=fp["classification"],
                                  risk=rp["risk"], risk_class=rp["classification"],
                                  confidence="LOW", emerging=False, kind="retro",
                                  components_json={"retro": True,
                                                   "note": "retro-scored over stored snapshots",
                                                   "summary": {}}))
                created += 1
            narr_names = []  # (kept neutral: narrative input approximated)
        labels = label_due(db, now)
        db.commit()
    log.info("retro backfill: %s score rows, %s labels", created, labels)
    return {"skipped": False, "retro_scores": created, "labels": labels}
