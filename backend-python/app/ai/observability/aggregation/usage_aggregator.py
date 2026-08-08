"""Owner-scoped usage/cost aggregation over ``usage_events``."""

from __future__ import annotations

import datetime
from dataclasses import dataclass
from decimal import Decimal
from typing import Any, Literal

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.caller import CallerContext
from app.db.models import UsageEvent

UsageGroupBy = Literal["day", "provider", "model"]

DEFAULT_RANGE_DAYS = 30

__all__ = [
    "DEFAULT_RANGE_DAYS",
    "UsageAggregator",
    "UsageGroupBy",
    "UsageSummaryRowData",
]


@dataclass(frozen=True)
class UsageSummaryRowData:
    """Internal aggregation row before API schema mapping."""

    day: datetime.date | None
    provider: str | None
    model: str | None
    request_count: int
    prompt_tokens: int
    completion_tokens: int
    total_tokens: int
    cost_usd: float | None


class UsageAggregator:
    """Owner-scoped aggregation queries over ``usage_events``."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    @staticmethod
    def resolve_date_range(
        since: datetime.date | None,
        until: datetime.date | None,
    ) -> tuple[datetime.date, datetime.date]:
        """Resolve defaults (trailing 30 days) and validate the inclusive range."""
        resolved_until = until or datetime.datetime.now(datetime.UTC).date()
        resolved_since = since or (
            resolved_until - datetime.timedelta(days=DEFAULT_RANGE_DAYS)
        )
        if resolved_since > resolved_until:
            raise ValueError("since must be on or before until.")
        return resolved_since, resolved_until

    async def aggregate(
        self,
        *,
        caller: CallerContext,
        since: datetime.date,
        until: datetime.date,
        group_by: UsageGroupBy,
    ) -> list[UsageSummaryRowData]:
        owner_filter = _owner_filter(caller)
        if owner_filter is None:
            return []

        range_start = datetime.datetime.combine(
            since, datetime.time.min, tzinfo=datetime.UTC
        )
        range_end = datetime.datetime.combine(
            until + datetime.timedelta(days=1),
            datetime.time.min,
            tzinfo=datetime.UTC,
        )

        count_expr = func.count().label("request_count")
        prompt_expr = func.coalesce(func.sum(UsageEvent.prompt_tokens), 0).label(
            "prompt_tokens"
        )
        completion_expr = func.coalesce(
            func.sum(UsageEvent.completion_tokens), 0
        ).label("completion_tokens")
        total_expr = func.coalesce(func.sum(UsageEvent.total_tokens), 0).label(
            "total_tokens"
        )
        cost_expr = func.sum(UsageEvent.cost_usd).label("cost_usd")

        select_columns: list[Any] = [
            count_expr,
            prompt_expr,
            completion_expr,
            total_expr,
            cost_expr,
        ]
        group_columns: list[Any] = []

        if group_by == "day":
            # Truncate in UTC so day buckets align with UTC range_start/range_end.
            created_at_utc = func.timezone("UTC", UsageEvent.created_at)
            day_col = func.date_trunc("day", created_at_utc).label("day")
            select_columns.insert(0, day_col)
            group_columns.append(day_col)
        elif group_by == "provider":
            provider_col = UsageEvent.provider.label("provider")
            select_columns.insert(0, provider_col)
            group_columns.append(provider_col)
        else:
            provider_col = UsageEvent.provider.label("provider")
            model_col = UsageEvent.model.label("model")
            select_columns.insert(0, provider_col)
            select_columns.insert(1, model_col)
            group_columns.extend([provider_col, model_col])

        stmt = (
            select(*select_columns)
            .where(owner_filter)
            .where(UsageEvent.created_at >= range_start)
            .where(UsageEvent.created_at < range_end)
            .group_by(*group_columns)
            .order_by(*group_columns)
        )

        result = await self._session.execute(stmt)
        return [_row_to_data(row, group_by) for row in result.all()]


def _owner_filter(caller: CallerContext) -> Any:
    if caller.kind == "user" and caller.user_id is not None:
        return UsageEvent.user_id == caller.user_id
    if caller.kind == "guest" and caller.guest_id is not None:
        return UsageEvent.guest_id == caller.guest_id
    return None


def _row_to_data(row: object, group_by: UsageGroupBy) -> UsageSummaryRowData:
    mapping = row._mapping  # type: ignore[attr-defined]
    day: datetime.date | None = None
    provider: str | None = None
    model: str | None = None

    if group_by == "day":
        day_value = mapping["day"]
        if day_value is not None:
            day = day_value.date() if hasattr(day_value, "date") else day_value
    elif group_by == "provider":
        provider = mapping["provider"]
    else:
        provider = mapping["provider"]
        model = mapping["model"]

    cost_raw = mapping["cost_usd"]
    cost_usd: float | None = None
    if cost_raw is not None:
        cost_usd = float(cost_raw) if isinstance(cost_raw, Decimal) else float(cost_raw)

    return UsageSummaryRowData(
        day=day,
        provider=provider,
        model=model,
        request_count=int(mapping["request_count"]),
        prompt_tokens=int(mapping["prompt_tokens"]),
        completion_tokens=int(mapping["completion_tokens"]),
        total_tokens=int(mapping["total_tokens"]),
        cost_usd=cost_usd,
    )
