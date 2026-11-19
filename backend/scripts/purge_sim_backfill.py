"""One-off cleanup: delete the SIMULATED 48h history that older versions wrote
for every real (non-Mock) token when it was first discovered, plus the retro
scores/outcomes computed on top of it.

What is deleted, per real token:
  * price/volume/liquidity/holder/social snapshots with ts < first_seen bucket
    (everything before the token was discovered was invented by the simulator)
  * TrendScore rows with kind='retro' and SignalOutcome rows with kind='retro'

Live data recorded after discovery is kept.

Dry run (default) only prints counts:
    python scripts/purge_sim_backfill.py
Apply:
    python scripts/purge_sim_backfill.py --apply
On Railway: run it in the API service shell (DATABASE_URL is already set).
"""
from __future__ import annotations

import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sqlalchemy import delete, func, select  # noqa: E402

from app.config import SNAPSHOT_BUCKET_SECONDS as BUCKET  # noqa: E402
from app.database import SessionLocal  # noqa: E402
from app.services.holders import epoch  # noqa: E402
from app.models import (HolderSnapshot, LiquiditySnapshot, PriceSnapshot,  # noqa: E402
                        SignalOutcome, SocialSnapshot, Token, TrendScore, VolumeSnapshot)

SNAPSHOTS = (PriceSnapshot, VolumeSnapshot, LiquiditySnapshot, HolderSnapshot, SocialSnapshot)


def purge(apply: bool) -> dict:
    totals = {m.__tablename__: 0 for m in SNAPSHOTS}
    totals.update({"trend_scores(retro)": 0, "signal_outcomes(retro)": 0, "tokens": 0})
    with SessionLocal() as db:
        tokens = db.execute(select(Token)).scalars().all()
        for t in tokens:
            if (t.address or "").startswith("Mock") or not t.first_seen_at:
                continue
            first_b = epoch(t.first_seen_at) // BUCKET * BUCKET
            totals["tokens"] += 1
            for m in SNAPSHOTS:
                cond = (m.token_id == t.id, m.ts < first_b)
                n = db.execute(select(func.count()).select_from(m).where(*cond)).scalar() or 0
                totals[m.__tablename__] += n
                if apply and n:
                    db.execute(delete(m).where(*cond))
            for m, key in ((TrendScore, "trend_scores(retro)"), (SignalOutcome, "signal_outcomes(retro)")):
                cond = (m.token_id == t.id, m.kind == "retro")
                n = db.execute(select(func.count()).select_from(m).where(*cond)).scalar() or 0
                totals[key] += n
                if apply and n:
                    db.execute(delete(m).where(*cond))
        if apply:
            db.commit()
    return totals


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--apply", action="store_true", help="actually delete (default: dry run)")
    args = ap.parse_args()
    res = purge(args.apply)
    print(("DELETED" if args.apply else "DRY RUN — would delete"), "for", res.pop("tokens"), "real tokens:")
    for k, v in res.items():
        print(f"  {k:28s} {v}")
    if not args.apply:
        print("Re-run with --apply to delete.")
