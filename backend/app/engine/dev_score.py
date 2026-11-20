"""Creator track record — will this coin's creator dump on you, and has he ever built anything?

Scoring model ported from GMGN's `gmgn-dev-score` skill (gmgn-skills, MIT License,
Copyright (c) 2025 GMGN — https://github.com/GMGNAI/gmgn-skills). The constants, the
two-axis design and the gate tiers are theirs; the inputs are FOMO's own real data
(Helius transactions + DexScreener + GeckoTerminal), so a few of their terms that need
GMGN-only fields are left out and reported as such in `limits`.

Two axes that must never cancel out:
  * CONDUCT (0-100) — only things the creator did with his own hands in his own coins:
    how much he pulled out vs put in, and how soon after launch he started selling.
  * POWER (0-100) — what the market did with his coins: peak market cap, repeat $1M+
    coins, flagship still alive, how much of his book survived / graduated.
TOTAL = CONDUCT + a capped bonus from POWER. A dump gate caps TOTAL, so a big past hit can
never rescue a serial dumper. A thin record is pulled toward 60 (never up): "we did not see
him dump" is not "he does not dump".

Pure functions only — no I/O. Inputs are CoinRecord rows (one per earlier launch).
"""
from __future__ import annotations

import math
import time
from dataclasses import asdict, dataclass

# -- definitions (GMGN constants) ---------------------------------------------------------
CUT_MULT, CUT_SEC = 1.5, 30.0        # a "dump": pulled out >= 1.5x AND first sell within 30 s
SEV_MULT_FULL = 4.0                  # >= 4x out/in is full severity on the amount axis
SEV_SEC_ZERO = 120.0                 # a first sell at/after 120 s is not racing the open
ALIVE_LIQ_USD = 4000.0               # GMGN: open market and pool liquidity >= $4k
BIG_MCAP = 1e6                       # a "success": peak market cap >= $1M
FAC_LO, FAC_HI = 20.0, 500.0         # coins stuck on the curve: 0 penalty at 20, full 45 at 500
PEN_OFTEN = 20.0
CAP_UNPROVEN = 65.0
CAP_OFTEN_TOT = 74.0
CAP_SYS, CAP_SYS_TOT = 45.0, 49.0
BANDS = ((75, "clean"), (50, "mixed"), (30, "avoid"), (0, "stay_away"))
BAND_LABEL = {"clean": "Clean record", "mixed": "Mixed record", "avoid": "Avoid",
              "stay_away": "Stay away", "new": "No earlier launches seen"}


@dataclass
class CoinRecord:
    """One earlier launch by the creator, built from real data."""
    mint: str
    launch_ts: float                     # unix seconds of the creation transaction
    symbol: str = ""
    sol_in: float = 0.0                  # SOL the creator spent buying it
    sol_out: float = 0.0                 # SOL the creator received selling it
    first_buy_s: float | None = None     # seconds after launch (0 = bought in the launch tx)
    first_sell_s: float | None = None
    moved_out: bool = False              # supply left his wallet with no SOL coming back
    graduated: bool | None = None        # left the bonding curve for an open DEX pool
    liquidity_usd: float | None = None   # now
    mcap_usd: float | None = None        # now
    peak_mcap_usd: float | None = None   # from price history; None = unknown
    has_market: bool = False             # DexScreener knows the coin at all

    @property
    def alive(self) -> bool:
        return bool(self.graduated) and (self.liquidity_usd or 0) >= ALIVE_LIQ_USD

    @property
    def peak(self) -> float:
        """The peak we are willing to score: known peak, else today's cap as a floor."""
        return max(self.peak_mcap_usd or 0.0, self.mcap_usd or 0.0)


def _clamp(x: float, lo: float = 0.0, hi: float = 1.0) -> float:
    return max(lo, min(hi, x))


def _med(xs: list[float]) -> float | None:
    xs = sorted(xs)
    return xs[len(xs) // 2] if xs else None


def f_mult(m: float) -> float:
    return _clamp((m - 1.0) / (SEV_MULT_FULL - 1.0))


def f_delay(s: float | None) -> float:
    return _clamp((SEV_SEC_ZERO - s) / SEV_SEC_ZERO) if s is not None else 0.0


def band_of(total: float) -> str:
    for lo, name in BANDS:
        if total >= lo:
            return name
    return "stay_away"


R = lambda x: round(x, 1)  # noqa: E731  (terms rounded at computation so the rows add up)


def score(coins: list[CoinRecord], *, career_start_ts: float | None = None,
          history_complete: bool = False, now: float | None = None) -> dict:
    """Score a creator from his earlier launches.

    career_start_ts: earliest launch seen. history_complete: the scan reached the wallet's
    first transaction, so the career length is a measurement, not a floor.
    """
    now = now or time.time()
    n = len(coins)
    if n == 0:
        return {"status": "new", "band": "new", "label": BAND_LABEL["new"], "total": None,
                "conduct": None, "power": None, "launches": 0, "history_complete": history_complete,
                "summary": ("No earlier launches found in this creator's history."
                            if history_complete else
                            "No earlier launches found in the part of this creator's history we scanned."),
                "coins": [], "limits": _limits()}

    # ---- 1. his conduct in each coin he traded ------------------------------------------
    traded, cut, mults, delays, sev = [], [], [], [], {}
    snipes, no_buy, moved = 0, 0, 0
    for c in coins:
        if c.moved_out:
            moved += 1
        if c.sol_in <= 0 and c.sol_out <= 0:
            continue
        traded.append(c.mint)
        mult = (c.sol_out / c.sol_in) if c.sol_in > 0 else None
        mult_sev = mult if mult is not None else (99.0 if c.sol_out > 0 else 0.0)
        if mult is not None:
            mults.append(mult)
        elif c.sol_out > 0:
            no_buy += 1
        if c.first_buy_s is not None and c.first_buy_s <= 60:
            snipes += 1
        if c.first_sell_s is not None:
            delays.append(c.first_sell_s)
        sev[c.mint] = f_mult(mult_sev) * f_delay(c.first_sell_s)
        if mult_sev >= CUT_MULT and c.first_sell_s is not None and c.first_sell_s <= CUT_SEC:
            cut.append(c.mint)
    n_tr = len(traded)
    cut_rate = len(cut) / n_tr if n_tr else None
    mean_sev = sum(sev.values()) / n_tr if n_tr else None

    # ---- 2. his book ---------------------------------------------------------------------
    alive = sum(1 for c in coins if c.alive)
    grad_known = [c for c in coins if c.graduated is not None]
    opened = sum(1 for c in grad_known if c.graduated)
    inner = sum(1 for c in grad_known if not c.graduated)
    surv = alive / n
    grad = opened / len(grad_known) if grad_known else 0.0
    big = [c for c in coins if c.peak >= BIG_MCAP]
    k1m = len(big)
    dd_big = _med([1.0 - (c.mcap_usd or 0.0) / c.peak for c in big if c.peak > 0]) if big else None
    ranked = sorted(coins, key=lambda c: c.peak, reverse=True)
    top1 = ranked[0]
    top1_cut = top1.mint in cut
    ath = top1.peak
    career_days = max(0.0, (now - career_start_ts) / 86400) if career_start_ts else 0.0
    career_floor = not history_complete

    # ---- 3. CONDUCT ------------------------------------------------------------------------
    dump_pen = R(55.0 * mean_sev if mean_sev is not None else 0.0)
    raw = R(max(0.0, 100.0 - dump_pen))          # walk-away (CTO) term: no data source, left out
    w = min(1.0, n_tr / 5.0) * (1.0 if career_floor else min(1.0, career_days / 30.0))
    cred = R(60.0 + (raw - 60.0) * w) if raw > 60.0 else raw
    shrink = R(raw - cred)
    factory_pen = R(45.0 * _clamp((math.log10(max(FAC_LO, inner)) - math.log10(FAC_LO))
                                  / (math.log10(FAC_HI) - math.log10(FAC_LO))))
    cred = R(max(0.0, cred - factory_pen))

    # ---- 4. POWER --------------------------------------------------------------------------
    b1 = R(_clamp((math.log10(max(1.0, min(ath, 5e10))) - 5.0) / 4.0) * 60.0)
    b2 = R(0.0 if k1m < 1 else min(25.0, 11.0 + 5.0 * math.log2(k1m)))
    b3 = 10.0 if top1.alive else 0.0
    dd_term = (1.0 - _clamp(dd_big)) if dd_big is not None else 0.5
    b4 = R(10.0 * (0.50 * _clamp(surv) + 0.30 * _clamp(grad) + 0.20 * dd_term) * min(1.0, n / 5.0))
    power = R(min(100.0, b1 + b2 + b3 + b4))
    bonus = R(max(0.0, min(1.0, k1m / 3.0) * (power - 50.0) / 50.0 * 15.0))

    # ---- 5. dump gate ----------------------------------------------------------------------
    if n_tr < 5:
        tier = "systematic" if (len(cut) >= 2 or top1_cut) else "unproven"
    elif cut_rate > 0.75 or top1_cut:
        tier = "systematic"
    elif not cut:
        tier = "none"
    elif cut_rate <= 0.30:
        tier = "rare"
    else:
        tier = "often"
    conduct = cred
    if tier == "often":
        conduct = max(0.0, conduct - PEN_OFTEN)
    elif tier == "unproven":
        conduct = min(conduct, CAP_UNPROVEN)
    elif tier == "systematic":
        conduct = min(conduct, CAP_SYS)
    total = R(_clamp(conduct + bonus, 0.0, 100.0))
    if tier in ("often", "unproven"):
        total = min(total, CAP_OFTEN_TOT)
    elif tier == "systematic":
        total = min(total, CAP_SYS_TOT)
    band = band_of(total)

    out = {
        "status": "ok", "band": band, "label": BAND_LABEL[band], "total": total,
        "conduct": R(conduct), "power": power, "bonus": bonus, "dump_tier": tier,
        "launches": n, "traded": n_tr, "dumps": len(cut),
        "dump_rate": None if cut_rate is None else round(cut_rate, 3),
        "median_pull_multiple": None if not mults else round(_med(mults), 2),
        "fastest_first_sell_s": None if not delays else round(min(delays), 1),
        "sniped_own_launch": snipes, "sold_without_buying": no_buy, "moved_supply_out": moved,
        "alive": alive, "graduated": opened, "stuck_on_curve": inner,
        "survival_rate": round(surv, 3), "graduation_rate": round(grad, 3),
        "successes_1m": k1m, "peak_mcap_usd": round(ath) if ath else None,
        "career_days": round(career_days, 1), "history_complete": history_complete,
        "terms": {"dump_pen": dump_pen, "raw": raw, "shrink": shrink, "shrink_w": round(w, 2),
                  "factory_pen": factory_pen, "b1_peak": b1, "b2_repeat": b2, "b3_flagship_alive": b3,
                  "b4_book": b4},
        "coins": [_coin_json(c, c.mint in cut, sev.get(c.mint)) for c in ranked[:12]],
        "limits": _limits(),
    }
    out["summary"] = summary(out)
    return out


def _coin_json(c: CoinRecord, dumped: bool, severity: float | None) -> dict:
    d = asdict(c)
    d.update(alive=c.alive, peak_used_usd=round(c.peak) if c.peak else None, dumped=dumped,
             severity=None if severity is None else round(severity, 2),
             pull_multiple=round(c.sol_out / c.sol_in, 2) if c.sol_in > 0 else None)
    return d


def _limits() -> list[str]:
    return ["Only launches found in the creator's recent transactions are scored; older ones may exist.",
            "Sales through other wallets the creator controls are not traced (they would look cleaner).",
            "Peaks come from daily price history for the biggest coins; for others today's cap is a floor.",
            "Liquidity pulls and community takeovers are not tracked (GMGN-only data)."]


def summary(s: dict) -> str:
    """One plain sentence for the dashboard and the AI evidence."""
    if s["status"] != "ok":
        return s.get("summary", "")
    parts = [f"{s['launches']} earlier launch{'es' if s['launches'] != 1 else ''}"]
    if s["traded"]:
        parts.append(f"dumped at open in {s['dumps']} of {s['traded']} he traded")
        if s["fastest_first_sell_s"] is not None:
            parts.append(f"fastest first sell {_dur(s['fastest_first_sell_s'])} after launch")
    else:
        parts.append("no trades of his own in them")
    parts.append(f"{s['graduated']} graduated, {s['alive']} still alive")
    if s["peak_mcap_usd"]:
        parts.append(f"best peak ~${_money(s['peak_mcap_usd'])}")
    return "; ".join(parts) + "."


def _dur(sec: float) -> str:
    if sec < 90:
        return f"{sec:.0f}s"
    if sec < 5400:
        return f"{sec / 60:.0f} min"
    if sec < 172800:
        return f"{sec / 3600:.1f} h"
    return f"{sec / 86400:.1f} days"


def _money(v: float) -> str:
    for div, suf in ((1e9, "B"), (1e6, "M"), (1e3, "k")):
        if v >= div:
            return f"{v / div:.1f}{suf}"
    return f"{v:.0f}"


def creator_concerns(cr: dict | None) -> list[str]:
    """Why this creator's record is disqualifying, in plain words ([] = not disqualifying,
    or not scored). Same line as the AI verdict caps that rate a coin Neutral or worse:
    dumping at open often / systematically, or an overall band of avoid / stay away."""
    if not cr:
        return []
    out = []
    tier = cr.get("dump_tier")
    if tier in ("systematic", "often"):
        d, n = cr.get("dumps"), cr.get("traded")
        out.append(f"creator dumped at open in {d} of {n} earlier launches" if d is not None and n
                   else f"creator dumps at open ({tier})")
    if cr.get("band") in ("stay_away", "avoid"):
        tot = cr.get("total")
        out.append(f"creator record {tot:.0f}/100 ({cr['band'].replace('_', ' ')})" if tot is not None
                   else f"creator record: {cr['band'].replace('_', ' ')}")
    return out
