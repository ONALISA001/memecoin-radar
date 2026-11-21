"""Seed script: schema + demo user + default alerts + first ingestion tick
(which discovers tokens and backfills 48h of time-series data — §33).

Usage:  python seed.py
"""
import asyncio
import os
import sys

from sqlalchemy import func, select

from app.config import INGEST_INTERVAL_SECONDS  # noqa: F401  (shows config is wired)
from app.database import SessionLocal
from app.main import ensure_schema_and_demo_user
from app.models import Alert, Token, TrendScore
from app.services import ingestion

DEFAULT_ALERTS = [
    ("Strong trend (any token)", {"metric": "trend_score", "op": "gt", "value": 75, "scope": "all"}),
    ("Extreme FOMO warning", {"metric": "fomo_score", "op": "gt", "value": 80, "scope": "all"}),
    ("Liquidity withdrawal > 30%", {"metric": "liquidity_drop_pct", "op": "gt", "value": 30, "scope": "all"}),
    ("HIGH risk detected", {"metric": "risk_score", "op": "gt", "value": 70, "scope": "all"}),
]


def main() -> int:
    print("▸ creating schema + demo user …")
    ensure_schema_and_demo_user()
    with SessionLocal() as db:
        from app.models import User
        user = db.execute(select(User).where(User.email == "demo@memecoin.radar")).scalar_one()
        if not db.execute(select(Alert).where(Alert.user_id == user.id)).first():
            for name, cond in DEFAULT_ALERTS:
                db.add(Alert(user_id=user.id, name=name, condition_json=cond,
                             channel_json={"type": "browser"}, enabled=True))
            db.commit()
            print(f"▸ seeded {len(DEFAULT_ALERTS)} default alerts")

    if os.getenv("SEED_TICK", "1") == "0":
        # Hosts like Render wait for the web port to open; the API's own scheduler runs the
        # first tick right after start-up, so skip the slow one here.
        print("▸ SEED_TICK=0: schema ready; the API's scheduler will run the first tick")
        return 0
    print("▸ running first ingestion tick (discovery + 48h backfill) …")
    stats = asyncio.run(ingestion.tick())
    print(f"▸ tick stats: {stats}")
    with SessionLocal() as db:
        n_tokens = db.execute(select(func.count(Token.id))).scalar()
        n_scores = db.execute(select(func.count(TrendScore.id))).scalar()
        top = db.execute(select(TrendScore).order_by(TrendScore.trend.desc()).limit(3)).scalars().all()
    print(f"▸ tokens tracked: {n_tokens} · score snapshots: {n_scores}")
    for s in top:
        print(f"   top trend {s.trend}/100 (token #{s.token_id}, fomo {s.fomo}, risk {s.risk})")
    print("✔ seed complete — start the API with:  uvicorn app.main:app --host 0.0.0.0 --port 8000")
    return 0


if __name__ == "__main__":
    sys.exit(main())
