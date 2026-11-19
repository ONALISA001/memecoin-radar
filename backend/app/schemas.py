"""Pydantic input contracts (§22 input validation, §28)."""
from __future__ import annotations

from typing import Literal, Optional

from pydantic import BaseModel, Field, field_validator

from .engine.alerts import VALID_METRICS


class WatchlistAdd(BaseModel):
    address: str = Field(min_length=6, max_length=128)


class AlertCreate(BaseModel):
    name: str = Field(min_length=1, max_length=160)
    metric: str
    op: Literal["gt", "gte", "lt", "lte"] = "gt"
    value: float
    scope: Literal["all", "watchlist", "address"] = "all"
    address: Optional[str] = None
    channel_type: Literal["browser", "webhook", "telegram"] = "browser"
    webhook_url: Optional[str] = Field(default=None, max_length=512)

    @field_validator("metric")
    @classmethod
    def _metric_ok(cls, v: str) -> str:
        if v not in VALID_METRICS:
            raise ValueError(f"metric must be one of {sorted(VALID_METRICS)}")
        return v

    @field_validator("webhook_url")
    @classmethod
    def _url_ok(cls, v: Optional[str]) -> Optional[str]:
        if v and not v.startswith(("https://", "http://")):
            raise ValueError("webhook_url must be http(s)")
        return v


class AlertPatch(BaseModel):
    enabled: Optional[bool] = None
    name: Optional[str] = Field(default=None, max_length=160)
