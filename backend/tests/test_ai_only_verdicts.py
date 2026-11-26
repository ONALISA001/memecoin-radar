"""verdict_ai_only: paper trading on AI-debate verdicts only.

Picks (buy filter, sizing) count only source=llm verdicts; safety checks (block, sell on
downgrade) count llm + the hard-fail gate. Rules verdicts never override the AI's call.
The backtest must apply the same rules as live paper trading."""
import datetime as dt
import time
from types import SimpleNamespace

import pytest

from app.database import SessionLocal
from app.main import ensure_schema_and_demo_user
from app.models import Token, TokenVerdict
from app.paper import backtest as BT
from app.paper import config as PC
from app.paper import engine as PE
from app.paper import filters as F

ensure_schema_and_demo_user()
NOW = int(time.time())


def _at(minutes_ago: float) -> dt.datetime:
    return dt.datetime.fromtimestamp(NOW - minutes_ago * 60, tz=dt.timezone.utc)


@pytest.fixture()
def coin():
    """A coin plus a helper that adds verdicts (oldest first, so ids follow time)."""
    with SessionLocal() as db:
        t = Token(chain="solana", address=f"AiOnlyTest{NOW}{id(object())}pump", symbol="AIO")
        db.add(t); db.commit(); tid = t.id

    def add(rating, source, minutes_ago, confidence="medium"):
        with SessionLocal() as db:
            v = TokenVerdict(token_id=tid, rating=rating, source=source, confidence=confidence,
                             created_at=_at(minutes_ago))
            db.add(v); db.commit()
            return v.id
    yield tid, add
    with SessionLocal() as db:
        db.query(TokenVerdict).filter(TokenVerdict.token_id == tid).delete()
        db.query(Token).filter(Token.id == tid).delete()
        db.commit()


def _checks(tid, settings):
    with SessionLocal() as db:
        tok = db.get(Token, tid)
        return {name: (ok, detail) for name, ok, detail in F.strategy_checks(db, settings, tok, {}, NOW)}


def test_ai_only_buy_filter_uses_the_ai_call_not_a_newer_rules_verdict(coin):
    tid, add = coin
    add("WATCH", "llm", 30)
    add("NEUTRAL", "rules", 10)          # newer, but rules
    base = {"verdict_filter": 1, "verdict_max_age_h": 6.0}
    assert _checks(tid, base)["AI verdict"][0] is False                   # default: latest = NEUTRAL
    ok, detail = _checks(tid, {**base, "verdict_ai_only": 1})["AI verdict"]
    assert ok and "Watch AI" in detail


def test_ai_only_needs_an_ai_verdict(coin):
    tid, add = coin
    add("WATCH", "rules", 5)
    base = {"verdict_filter": 1, "verdict_max_age_h": 6.0}
    assert _checks(tid, base)["AI verdict"][0] is True
    ok, detail = _checks(tid, {**base, "verdict_ai_only": 1})["AI verdict"]
    assert not ok and detail == "no AI-debate verdict yet"


def test_ai_only_block_ignores_rules_but_honours_the_safety_gate(coin):
    tid, add = coin
    s = {"block_bad_verdicts": 1, "verdict_ai_only": 1, "verdict_max_age_h": 6.0}
    add("WATCH", "llm", 30)
    add("AVOID", "rules", 20)
    assert _checks(tid, s)["AI verdict not negative"][0] is True          # rules AVOID ignored
    add("RUG_RISK", "gate", 10)
    assert _checks(tid, s)["AI verdict not negative"][0] is False         # hard fail still blocks


def test_ai_only_exit_on_downgrade(coin):
    tid, add = coin
    entry_id = add("WATCH", "llm", 60)
    pos = SimpleNamespace(token_id=tid, entry_ts=NOW - 50 * 60, metrics_json={"verdict": {"id": entry_id}})
    safety = F.verdict_sources({"verdict_ai_only": 1}, "safety")
    add("AVOID", "rules", 40)
    with SessionLocal() as db:
        assert PE._downgraded(db, pos, NOW, safety) is False             # rules can't sell it
        assert PE._downgraded(db, pos, NOW) is True                       # default: any source
    add("AVOID", "llm", 20)
    with SessionLocal() as db:
        assert PE._downgraded(db, pos, NOW, safety) is True               # the AI's own downgrade


def test_backtest_verdicts_follow_the_same_sources_and_skip_review(coin):
    tid, add = coin
    add("WATCH", "llm", 30)
    add("NEUTRAL", "rules", 20)
    add("REVIEW", "gate", 10)
    with SessionLocal() as db:
        v = BT.Verdicts(db, NOW)
    assert v.at(tid, NOW)[0] == "NEUTRAL"                                 # REVIEW skipped
    assert v.at(tid, NOW, F.AI_PICK_SOURCES)[0] == "WATCH"
    assert v.at(tid, NOW - 35 * 60, F.AI_PICK_SOURCES) is None            # before the AI call
    S = {"verdict_filter": 1, "verdict_ai_only": 1, "verdict_max_age_h": 6.0}
    assert dict((n, ok) for n, ok, _ in BT.verdict_checks(S, v, tid, NOW))["AI verdict"] is True
    assert dict((n, ok) for n, ok, _ in BT.verdict_checks({**S, "verdict_ai_only": 0}, v, tid, NOW))["AI verdict"] is False


def test_ai_only_is_off_by_default_and_the_preset_changes_only_that():
    assert PC.DEFAULT_SETTINGS["verdict_ai_only"] == 0
    assert F.verdict_sources(PC.DEFAULT_SETTINGS, "pick") is None
    base, v3 = PC.PRESETS["ai_verdict"]["settings"], PC.PRESETS["verdict_ai_only"]["settings"]
    assert set(v3.items()) - set(base.items()) == {("verdict_ai_only", 1)}
