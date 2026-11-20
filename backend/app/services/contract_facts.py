"""Contract facts for the risk engine — REAL where we have them, never invented.

Source of truth is the latest successful Helius check (TokenRiskCheck). Tokens
that have not been checked yet get *unverified* facts (values None): the risk
engine treats "unknown" as a risk, so missing data can never make a token look
safer (§19). Simulated facts are only used for the demo/fallback universe
(addresses starting with "Mock"), and are labeled as such.
"""
from __future__ import annotations

from sqlalchemy import select

from ..models import Token, TokenRiskCheck
from ..providers.helius import ext_names_from_str
from ..providers.mock_providers import Sim
from .creator_profiles import creator_facts, profiles_for
from .launch_forensics import latest_launch_rows, launch_facts


def is_mock(token: Token) -> bool:
    return (token.address or "").startswith("Mock")


def unverified_facts() -> dict:
    return {"verified": False, "mint_authority": None, "freeze_authority": None,
            "token_2022": None, "top1_wallet_pct": None, "top10_wallet_pct": None,
            "excluded_pct": None, "lp_burned_pct": None, "deployer_prev_launches": None,
            "deployer_suspicious": None, "token_extensions": None, "transfer_fee_bps": None,
            "transfer_fee_mutable": None, "exit_buy_route": None, "exit_sell_route": None,
            "exit_round_trip_loss_pct": None, "exit_sol_in": None,
            "launch": None, "creator": None, "checked_at": None, "source": "unverified"}


def facts_from_check(chk: TokenRiskCheck) -> dict:
    return {"verified": True,
            "mint_authority": bool(chk.mint_authority),
            "freeze_authority": bool(chk.freeze_authority),
            "token_2022": bool(chk.token_2022),
            # REAL-wallet concentration (None for checks made before it existed)
            "top1_wallet_pct": chk.top1_wallet_pct, "top10_wallet_pct": chk.top10_wallet_pct,
            "excluded_pct": chk.excluded_pct,
            # Token-2022: list of ACTIVE risky extensions (None = not analysed yet)
            "token_extensions": ext_names_from_str(chk.token_extensions),
            "transfer_fee_bps": chk.transfer_fee_bps,
            "transfer_fee_mutable": chk.transfer_fee_mutable,
            # Exit analyst (Jupiter round trip). sell_route None = not checked
            "exit_buy_route": chk.exit_buy_route, "exit_sell_route": chk.exit_sell_route,
            "exit_round_trip_loss_pct": chk.exit_round_trip_loss_pct, "exit_sol_in": chk.exit_sol_in,
            # Not available from our Helius checks yet: unknown, not "fine".
            "lp_burned_pct": None, "deployer_prev_launches": None, "deployer_suspicious": None,
            "checked_at": chk.checked_at.isoformat() if chk.checked_at else None,
            "source": "helius"}


def _sim_facts(token: Token) -> dict:
    try:
        f = Sim.from_state(token.address, token.state_json or {}).contract_facts()
    except (KeyError, TypeError, ValueError):     # no usable simulator state
        return unverified_facts()
    f.setdefault("verified", False)
    f.setdefault("token_2022", False)
    return f


def latest_contract_facts(db, tokens: list[Token]) -> dict[int, dict]:
    """{token_id: facts} for a batch of tokens in one query."""
    real = [t.id for t in tokens if not is_mock(t)]
    latest: dict[int, TokenRiskCheck] = {}
    if real:
        rows = db.execute(select(TokenRiskCheck)
                          .where(TokenRiskCheck.ok.is_(True), TokenRiskCheck.token_id.in_(real))
                          .order_by(TokenRiskCheck.token_id, TokenRiskCheck.checked_at,
                                    TokenRiskCheck.id)).scalars().all()
        for r in rows:            # ordered ascending → last one wins
            latest[r.token_id] = r
    launch_rows = latest_launch_rows(db, real)
    profiles = profiles_for(db, [r.creator for r in launch_rows.values() if r.status == "ok"])
    out: dict[int, dict] = {}
    for t in tokens:
        if is_mock(t):
            out[t.id] = _sim_facts(t)
        elif t.id in latest:
            out[t.id] = facts_from_check(latest[t.id])
        else:
            out[t.id] = unverified_facts()
        if not is_mock(t):
            lrow = launch_rows.get(t.id)
            out[t.id]["launch"] = launch_facts(lrow)
            cf = creator_facts(profiles.get(lrow.creator), t.address) if lrow and lrow.creator else None
            out[t.id]["creator"] = cf
            if cf and cf.get("status") == "ok":
                # real values for the risk engine's deployer inputs (were always unknown)
                out[t.id]["deployer_prev_launches"] = cf["prior_launches"]
                out[t.id]["deployer_suspicious"] = cf["label"] in ("SERIAL_RUGGER", "RISKY")
                out[t.id]["deployer_label"] = cf["label"]
    return out


def contract_facts_for(db, token: Token) -> dict:
    return latest_contract_facts(db, [token])[token.id]
