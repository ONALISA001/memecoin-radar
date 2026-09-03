"""Provider abstraction (§3). The engine ONLY consumes the normalised
structures below, so any provider (real or mock) can be swapped without
touching analytics code (§39.10)."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol


@dataclass
class RawToken:
    address: str
    chain: str
    name: str
    symbol: str
    image_url: str = ""
    description: str = ""
    url: str = ""
    dex: str = ""
    pair_address: str = ""
    deployer: str = ""
    token_created_at: int | None = None     # epoch ms
    extra: dict[str, Any] = field(default_factory=dict)


@dataclass
class MarketTick:
    """Current (point-in-time) market values supplied by a market provider."""
    price_usd: float = 0.0
    mcap: float = 0.0
    fdv: float = 0.0
    liquidity_usd: float = 0.0
    volume_h24: float = 0.0
    volume_h6: float = 0.0
    volume_h1: float = 0.0
    volume_m5: float = 0.0
    pc_m5: float = 0.0                        # price change %
    pc_h1: float = 0.0
    pc_h6: float = 0.0
    pc_h24: float = 0.0
    txns_h1_buys: int = 0
    txns_h1_sells: int = 0
    txns_h24_buys: int = 0
    txns_h24_sells: int = 0
    txns_m5_buys: int = 0
    txns_m5_sells: int = 0
    pair_liquidity_usd: float = 0.0


class MarketProvider(Protocol):
    name: str
    kind: str  # "market"

    async def discover(self) -> list[RawToken]: ...
    async def fetch_market(self, tokens: list[RawToken]) -> dict[str, MarketTick]: ...


# ---- registry / health (surfaced at GET /sources for §30 transparency) ----
class ProviderHealth:
    def __init__(self) -> None:
        self.status: dict[str, dict[str, Any]] = {}

    def report(self, name: str, kind: str, ok: bool, note: str = "") -> None:
        import datetime as _dt
        prev = self.status.get(name, {})
        self.status[name] = {
            "name": name,
            "kind": kind,
            "ok": ok,
            "note": note,
            "consecutive_failures": 0 if ok else prev.get("consecutive_failures", 0) + 1,
            "last_ok_at": prev.get("last_ok_at"),
        }
        if ok:
            self.status[name]["last_ok_at"] = _dt.datetime.now(_dt.timezone.utc).isoformat()


HEALTH = ProviderHealth()
