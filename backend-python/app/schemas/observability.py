"""Observability REST API schemas (Epic 07 Phase 6).

Responses expose aggregated counts, token totals, and cost figures only —
never trace/span IDs, session identifiers, or cross-owner data.
"""

from __future__ import annotations

import datetime
from typing import Literal

from pydantic import BaseModel, Field

UsageGroupBy = Literal["day", "provider", "model"]

__all__ = [
    "UsageGroupBy",
    "UsageSummaryResponse",
    "UsageSummaryRow",
]


class UsageSummaryRow(BaseModel):
    """One aggregated usage/cost bucket for the requested ``group_by`` mode."""

    day: datetime.date | None = None
    provider: str | None = None
    model: str | None = None
    request_count: int = Field(ge=0)
    prompt_tokens: int = Field(ge=0)
    completion_tokens: int = Field(ge=0)
    total_tokens: int = Field(ge=0)
    cost_usd: float | None = Field(default=None, ge=0)


class UsageSummaryResponse(BaseModel):
    """Caller-scoped usage/cost summary for a date range."""

    since: datetime.date
    until: datetime.date
    group_by: UsageGroupBy
    rows: list[UsageSummaryRow]
