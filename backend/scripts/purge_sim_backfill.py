"""One-off cleanup: delete the SIMULATED 48h history that older versions wrote
for every real (non-Mock) token when it was first discovered, plus the retro
scores/outcomes computed on top of it.

What is deleted, per real token:
  * price/volume/liquidity/holder/social snapshots with ts < first_seen bucket
    (everything before the token was discovered was invented by the simulator)
  * TrendScore rows with kind='retro' and SignalOutcome rows with kind='retro'
  * ALL WalletActivity (whale trade) rows — the simulator is the only writer, so
    every row on a real token is invented

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

from sqlalchemy import (BigInteger, Column, Integer, MetaData, Table, delete, exists,  # noqa: E402
                        func, insert, select)

from app.config import SNAPSHOT_BUCKET_SECONDS as BUCKET  # noqa: E402
from app.database import SessionLocal  # noqa: E402
from app.services.holders import epoch  # noqa: E402
from app.models import (HolderSnapshot, LiquiditySnapshot, PriceSnapshot,  # noqa: E402
                        SignalOutcome, SocialSnapshot, Token, TrendScore, VolumeSnapshot,
                        WalletActivity)

SNAPSHOTS = (PriceSnapshot, VolumeSnapshot, LiquiditySnapshot, HolderSnapshot, SocialSnapshot)


def purge(apply: bool) -> dict:
    """Set-based: real tokens go into a temp table, then ONE count (and one delete)
    per table. The old per-token loop made thousands of round trips and timed out
    against a remote database."""
    with SessionLocal() as db:
        rows = [(tid, epoch(fs) // BUCKET * BUCKET) for tid, addr, fs in
                db.execute(select(Token.id, Token.address, Token.first_seen_at)).all()
                if fs and not (addr or "").startswith("Mock")]
        tmp = Table("purge_real_tokens", MetaData(), Column("token_id", Integer, primary_key=True),
                    Column("first_b", BigInteger, nullable=False), prefixes=["TEMPORARY"])
        tmp.create(db.connection())
        if rows:
            db.execute(insert(tmp), [{"token_id": i, "first_b": b} for i, b in rows])

        jobs = [(m.__tablename__, m, lambda m=m: (m.ts < tmp.c.first_b,)) for m in SNAPSHOTS]
        jobs += [("trend_scores(retro)", TrendScore, lambda: (TrendScore.kind == "retro",)),
                 ("signal_outcomes(retro)", SignalOutcome, lambda: (SignalOutcome.kind == "retro",)),
                 ("wallet_activity(all)", WalletActivity, lambda: ())]
        totals = {"tokens": len(rows)}
        for key, m, extra in jobs:
            match = exists().where(tmp.c.token_id == m.token_id, *extra())
            n = db.execute(select(func.count()).select_from(m).where(match)).scalar() or 0
            totals[key] = n
            if apply and n:
                db.execute(delete(m).where(match))
        if apply:
            db.commit()
        else:
            db.rollback()
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
