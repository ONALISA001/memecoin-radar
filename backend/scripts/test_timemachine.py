"""Time Machine tests: return pool, Monte Carlo odds, history replay, verdict
and the API endpoint. Runs against a scratch SQLite DB — no live data."""
import math, os, sys, tempfile, time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
fd, dbpath = tempfile.mkstemp(suffix=".db")
os.close(fd)
os.environ["DATABASE_URL"] = f"sqlite:///{dbpath}"

import numpy as np  # noqa: E402
from fastapi import HTTPException  # noqa: E402
from app.database import Base, engine, SessionLocal  # noqa: E402
from app.engine import timemachine as tm  # noqa: E402
from app.models import PriceSnapshot, SignalEvent, Token  # noqa: E402
from app.routers import data_api  # noqa: E402

Base.metadata.create_all(engine)
db = SessionLocal()
NOW = (int(time.time()) // 300) * 300
PASS, FAIL = [], []


def check(name: str, cond: bool, extra: str = ""):
    (PASS if cond else FAIL).append(name)
    print(("  ✅" if cond else "  ❌"), name, extra)


# ---------- return pool -------------------------------------------------
pts = [(0, 1.0), (300, 1.1), (600, 1.21), (1500, 2.0), (1800, 2.2), (2100, None), (2400, 3.0)]
r = tm.contiguous_log_returns(pts)
check("only 5-min-apart pairs become returns (gaps and Nones skipped)", len(r) == 3, str(len(r)))
check("returns are log ratios", abs(r[0] - math.log(1.1)) < 1e-12)

# ---------- simulation --------------------------------------------------
flat = np.zeros(100)
s = tm.simulate(flat, 10, 20, steps=48, replays=500)
check("flat pool → 100% neither", s["neither_pct"] == 100.0, str(s["neither_pct"]))
check("flat pool → avg R 0", abs(s["avg_r"]) < 1e-9)

up = np.full(100, math.log(1.01))          # +1% every 5 min
s = tm.simulate(up, 10, 20, steps=48, replays=500)
check("steady uptrend → target first 100%", s["target_first_pct"] == 100.0)
check("target after 19 buckets ≈ 95 min", s["median_minutes_to_exit"] == 95, str(s["median_minutes_to_exit"]))
check("avg R equals target R", abs(s["avg_r"] - 2.0) < 1e-9)

down = np.full(100, math.log(0.99))
s = tm.simulate(down, 10, 20, steps=48, replays=500)
check("steady downtrend → stop first 100%", s["stop_first_pct"] == 100.0)

rng = np.random.default_rng(1)
noisy = rng.normal(0, 0.03, 600)
a = tm.simulate(noisy, 12, 35, steps=288, replays=3000, seed=19)
b = tm.simulate(noisy, 12, 35, steps=288, replays=3000, seed=19)
check("same seed → identical result", a == b)
tot = a["target_first_pct"] + a["stop_first_pct"] + a["neither_pct"]
check("odds sum to 100%", abs(tot - 100) < 0.2, f"{tot}")
check("fan has ≤48 ordered points", 0 < len(a["fan"]) <= 48 and
      all(f["p10"] <= f["p50"] <= f["p90"] for f in a["fan"]))
big = tm.simulate(noisy, 12, 35, steps=288, replays=20000, seed=3)   # chunked path
check("20k replays × 24h runs (chunked)", big["replays"] == 20000)

# ---------- verdict -----------------------------------------------------
check("1R 50/50 → coin flip", tm.verdict(50, 50, 1.0)["label"] == "Close to a coin flip")
check("2R 33% → break-even", tm.verdict(34, 66, 2.0)["label"] == "Close to break-even")
check("2R 45% → leans target", tm.verdict(45, 55, 2.0)["label"] == "Leans toward the target")
check("1R 20% → strongly stop", tm.verdict(20, 80, 1.0)["label"] == "Strongly toward the stop")
check("nothing decided → Undecided", tm.verdict(0, 0, 2.0)["label"] == "Undecided")

# ---------- history replay ----------------------------------------------
H = 3600
def path(t0, prices, step=300):
    return [(t0 + i * step, p) for i, p in enumerate(prices)]
walks = [
    (0, path(0, [1.0, 1.05, 1.25])),                 # target (+20%)
    (10000, path(10000, [1.0, 0.95, 0.85])),         # stop (-10%)
    (20000, path(20000, [1.0] + [1.02] * 12)),       # neither, ends +2%
    (30000, [(30000, 1.0), (31500, 1.3)]),           # 25-min hole → data gap
    (NOW - 600, path(NOW - 600, [1.0, 1.0])),        # hold not over → too recent
    (40000, [(41000, 1.0)]),                          # no snapshot near the signal
]
h = tm.replay_history(walks, 10, 20, H, NOW)
check("history counts target/stop/neither", (h["target"], h["stop"], h["neither"]) == (1, 1, 1), str(h))
check("gaps counted, not filled", h["data_gap"] == 1)
check("3 decided signals → too few to judge", h["verdict"]["label"] == "Too few to judge")
check("unfinished holds excluded", h["too_recent"] == 1 and h["no_entry"] == 1)
check("history avg R = (2 - 1 + 0.2)/3", abs(h["avg_r"] - 0.4) < 1e-3, str(h["avg_r"]))

# ---------- endpoint ----------------------------------------------------
tok = Token(address="tmUNITtest", chain="solana", symbol="TM", name="Time Machine Coin")
young = Token(address="tmYOUNGtest", chain="solana", symbol="NEW", name="Young Coin")
db.add_all([tok, young]); db.flush()
rng = np.random.default_rng(7)
px = 0.001
for i in range(2 * 288, 0, -1):                      # 2 days of real-shaped data
    px *= math.exp(rng.normal(0, 0.02))
    db.add(PriceSnapshot(token_id=tok.id, ts=NOW - i * 300, price_usd=px))
for i in range(10, 0, -1):
    db.add(PriceSnapshot(token_id=young.id, ts=NOW - i * 300, price_usd=0.5))
db.add(SignalEvent(token_id=tok.id, ts=NOW - 30 * H, kind="momentum_acceleration",
                   key="", title="t", detail="", severity="positive", category="momentum"))
db.add(SignalEvent(token_id=tok.id, ts=NOW - 600, kind="momentum_acceleration",
                   key="", title="t", detail="", severity="positive", category="momentum"))
db.commit()

out = data_api.token_timemachine("tmUNITtest", stop_pct=12, target_pct=35, hours=6,
                                 kind=None, replays=2000, seed=19, db=db)
check("endpoint returns a simulation", out["simulation"] is not None)
check("default kind = latest bullish signal", out["kind"] == "momentum_acceleration")
check("history used the past signal", out["history"]["counted"] + out["history"]["data_gap"] >= 1,
      str(out["history"]))
check("labelled real, not mock", out["mock"] is False)
y = data_api.token_timemachine("tmYOUNGtest", stop_pct=12, target_pct=35, hours=6,
                               kind=None, replays=2000, seed=19, db=db)
check("too little data → honest reason, no simulation", y["simulation"] is None and "Needs" in y["reason"])
for bad in ({"stop_pct": 0}, {"target_pct": -5}, {"hours": 500}, {"replays": 10},
            {"kind": "sell_pressure_increasing"}):
    args = dict(stop_pct=12, target_pct=35, hours=6, kind=None, replays=2000, seed=19)
    args.update(bad)
    try:
        data_api.token_timemachine("tmUNITtest", db=db, **args)
        check(f"rejects {bad}", False)
    except HTTPException as e:
        check(f"rejects {bad}", e.status_code == 400)
try:
    data_api.token_timemachine("nope", stop_pct=12, target_pct=35, hours=6, kind=None,
                               replays=2000, seed=19, db=db)
    check("unknown token → 404", False)
except HTTPException as e:
    check("unknown token → 404", e.status_code == 404)

db.close()
os.unlink(dbpath)
print(f"\n{len(PASS)} passed, {len(FAIL)} failed")
sys.exit(1 if FAIL else 0)
