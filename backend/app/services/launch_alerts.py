"""Launch-safety alerts — the early tip-off.

When a token discovered as a FRESH POOL gets its first complete Helius check
(authorities + real-wallet concentration), decide once:

  * passes  → one Telegram message ("fresh launch passed safety checks")
  * fails   → recorded as skipped, never alerted
  * no data → wait for the next check (stays undecided)

Passing means only that the obvious rug levers are absent. It is NOT a buy
signal, and the message says so: LP lock and deployer history are unchecked.
A Jupiter round-trip quote (Exit analyst) failing also skips the alert; the
exit check being unavailable does not.
Decisions are stored on Token.launch_alert so nothing is ever sent twice.
"""
from __future__ import annotations

import html
import logging
import time

from sqlalchemy import select

from .. import config
from ..engine import telegram
from ..models import SystemLog, Token, TrendScore
from ..providers.jupiter import exit_verdict
from .contract_facts import latest_contract_facts

log = logging.getLogger("radar.launch")


def launch_verdict(facts: dict) -> tuple[str | None, list[str]]:
    """('pass' | 'fail' | None, reasons). None = not enough real data yet."""
    if facts.get("source") != "helius":
        return None, []
    w1, w10 = facts.get("top1_wallet_pct"), facts.get("top10_wallet_pct")
    if w1 is None or w10 is None:
        return None, []
    bad = []
    if facts.get("mint_authority"):
        bad.append("mint authority active")
    if facts.get("freeze_authority"):
        bad.append("freeze authority active")
    if facts.get("token_2022"):
        exts = facts.get("token_extensions")
        if exts is None or not config.LAUNCH_ALLOW_CLEAN_TOKEN_2022:
            bad.append("Token-2022 program")
        else:
            bad.extend(f"Token-2022 {n}" for n in exts)
    fee = facts.get("transfer_fee_bps") or 0
    if fee:
        bad.append(f"transfer fee {fee / 100:.2f}%")
    # Exit analyst: a missing check (Jupiter down) never blocks, a failed one always does
    ev = exit_verdict(facts.get("exit_buy_route"), facts.get("exit_sell_route"),
                      facts.get("exit_round_trip_loss_pct"))
    if ev == "fail":
        loss = facts.get("exit_round_trip_loss_pct")
        bad.append("no sell route" if not facts.get("exit_sell_route")
                   else f"round trip loses {loss:.0f}%")
    if w10 > config.LAUNCH_MAX_TOP10_WALLET_PCT:
        bad.append(f"top-10 wallets {w10:.0f}%")
    if w1 > config.LAUNCH_MAX_TOP1_WALLET_PCT:
        bad.append(f"largest wallet {w1:.0f}%")
    return ("fail" if bad else "pass"), bad


def _money(v) -> str:
    v = float(v or 0)
    return f"${v / 1e6:.2f}M" if v >= 1e6 else f"${v / 1e3:.1f}k" if v >= 1e3 else f"${v:.0f}"


def _exit_line(facts: dict) -> str:
    loss, sol = facts.get("exit_round_trip_loss_pct"), facts.get("exit_sol_in") or 0.1
    if facts.get("exit_sell_route") is None:
        return "⚠️ Sell route NOT checked (Jupiter unavailable)"
    if not facts.get("exit_buy_route"):
        return "⚠️ No Jupiter route yet"
    return (f"✓ Sell route found · {sol:g} SOL round trip -{loss:.1f}%" if loss is not None
            else "✓ Sell route found")


def format_launch(token: Token, facts: dict, s: dict) -> str:
    e = lambda v: html.escape(str(v), quote=False)  # noqa: E731
    age_min = None
    if token.token_created_at:
        age_min = max(0, int((time.time() * 1000 - token.token_created_at) / 60000))
    age = (f"{age_min}m" if age_min is not None and age_min < 120
           else f"{age_min / 60:.1f}h" if age_min is not None else "?")
    b, sl = s.get("buys_1h") or 0, s.get("sells_1h") or 0
    lines = [
        "🟢 <b>FRESH LAUNCH · passed safety checks</b>",
        f"<b>${e(token.symbol or '?')}</b> {e(token.name or '')}",
        f"Pool created {age} ago on {e(token.dex or '?')}",
        f"Liquidity {_money(s.get('liquidity'))} · MCap {_money(s.get('mcap'))} · Vol 1h {_money(s.get('volume_1h'))}",
        f"1h trades {b + sl} ({b} buys / {sl} sells)",
        "✓ Mint authority renounced · ✓ No freeze authority · "
        + ("✓ Token-2022, no risky extensions" if facts.get("token_2022") else "✓ Standard SPL token"),
        _exit_line(facts),
        f"✓ Top-10 wallets {facts['top10_wallet_pct']:.0f}% · largest {facts['top1_wallet_pct']:.0f}% "
        f"(pools & burn excluded)",
        "⚠️ Not a buy signal. LP lock and deployer history are NOT checked; fresh launches are "
        "extremely volatile and most go to zero.",
        f"<code>{e(token.address)}</code>",
        f'<a href="https://dexscreener.com/solana/{e(token.address)}">DexScreener</a>',
    ]
    return "\n".join(lines)[:telegram.MAX_LEN]


def evaluate_launches(db) -> dict:
    """Decide pending fresh-pool tokens. Commits decisions; returns the messages
    to send as {"to_send": [(token_id, text)], "passed": n, "skipped": n, "pending": n}."""
    out = {"passed": 0, "skipped": 0, "pending": 0, "to_send": []}
    if not config.LAUNCH_ALERTS_ENABLED:
        return out
    cutoff = time.time() * 1000 - config.NEW_POOL_MAX_AGE_H * 3.6e6
    toks = db.execute(select(Token).where(Token.discovery_source == "new_pool",
                                          Token.launch_alert.is_(None),
                                          Token.token_created_at >= cutoff)).scalars().all()
    if not toks:
        return out
    facts = latest_contract_facts(db, toks)
    for t in toks:
        verdict, reasons = launch_verdict(facts[t.id])
        if verdict is None:
            out["pending"] += 1
            continue
        if verdict == "fail":
            t.launch_alert = "skipped"
            out["skipped"] += 1
            db.add(SystemLog(ts=int(time.time()), level="info", source="launch",
                             message=f"{t.symbol}: no launch alert ({', '.join(reasons)})"[:480]))
            continue
        if len(out["to_send"]) >= config.TELEGRAM_MAX_PER_TICK:
            out["pending"] += 1          # flood guard: the rest go out next tick
            continue
        sc = db.execute(select(TrendScore).where(TrendScore.token_id == t.id)
                        .order_by(TrendScore.ts.desc()).limit(1)).scalar_one_or_none()
        if sc is None:
            out["pending"] += 1          # no market snapshot yet; next tick
            continue
        summary = (sc.components_json or {}).get("summary") or {}
        t.launch_alert = "sent"          # claimed now; downgraded below if delivery fails
        out["passed"] += 1
        out["to_send"].append((t.id, format_launch(t, facts[t.id], summary)))
    db.commit()
    return out


def send_launch_alerts(items: list[tuple[int, str]]) -> int:
    """Deliver (blocking — run in a thread). Failed sends are marked 'failed' and
    not retried, so a Telegram outage can't cause a burst of stale alerts later."""
    from ..database import SessionLocal
    sent = 0
    for tid, text in items:
        res = telegram.send_text(text) if telegram.is_configured() else {"ok": False, "error": "not configured"}
        with SessionLocal() as db:
            t = db.get(Token, tid)
            if res.get("ok"):
                sent += 1
                msg = f"{t.symbol}: launch alert sent to Telegram"
            else:
                if t is not None:
                    t.launch_alert = "failed"
                msg = f"{t.symbol if t else tid}: launch alert not delivered ({res.get('error')})"
            db.add(SystemLog(ts=int(time.time()), level="info", source="launch", message=msg[:480]))
            db.commit()
    return sent
