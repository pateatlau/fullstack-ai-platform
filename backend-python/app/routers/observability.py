"""Observability REST API — usage/cost summaries and Prometheus metrics (Epic 07 Phase 6)."""

from __future__ import annotations

import datetime

from fastapi import APIRouter, Depends, Query
from fastapi.responses import Response
from prometheus_client import CONTENT_TYPE_LATEST

from app.ai.deps import get_usage_aggregator
from app.ai.observability.aggregation.usage_aggregator import (
    UsageAggregator,
    UsageSummaryRowData,
)
from app.ai.observability.metrics.meter import MeterRegistry
from app.core.caller import CallerContext, get_current_caller
from app.core.config import Settings, get_settings
from app.core.errors import AppError
from app.core.logging import bind_context
from app.schemas.observability import (
    UsageGroupBy,
    UsageSummaryResponse,
    UsageSummaryRow,
)

router = APIRouter()


def _require_observability_enabled(settings: Settings) -> None:
    if not settings.observability_enabled:
        raise AppError(
            code="feature_disabled",
            message="Observability is not enabled on this server.",
            status_code=503,
        )


def _to_summary_row(row: UsageSummaryRowData) -> UsageSummaryRow:
    return UsageSummaryRow(
        day=row.day,
        provider=row.provider,
        model=row.model,
        request_count=row.request_count,
        prompt_tokens=row.prompt_tokens,
        completion_tokens=row.completion_tokens,
        total_tokens=row.total_tokens,
        cost_usd=row.cost_usd,
    )


@router.get("/api/observability/usage", response_model=UsageSummaryResponse)
async def get_usage_summary(
    since: datetime.date | None = Query(default=None),
    until: datetime.date | None = Query(default=None),
    group_by: UsageGroupBy = Query(default="day"),
    caller: CallerContext = Depends(get_current_caller),
    settings: Settings = Depends(get_settings),
    aggregator: UsageAggregator = Depends(get_usage_aggregator),
) -> UsageSummaryResponse:
    _require_observability_enabled(settings)
    if caller.user_id is not None:
        bind_context(user_id=str(caller.user_id))
    elif caller.guest_id is not None:
        bind_context(guest_id=str(caller.guest_id))

    try:
        resolved_since, resolved_until = UsageAggregator.resolve_date_range(
            since, until
        )
    except ValueError as exc:
        raise AppError(
            code="validation_error",
            message=str(exc),
            status_code=422,
        ) from exc

    rows = await aggregator.aggregate(
        caller=caller,
        since=resolved_since,
        until=resolved_until,
        group_by=group_by,
    )
    return UsageSummaryResponse(
        since=resolved_since,
        until=resolved_until,
        group_by=group_by,
        rows=[_to_summary_row(row) for row in rows],
    )


@router.get("/metrics")
async def prometheus_metrics(
    settings: Settings = Depends(get_settings),
) -> Response:
    if not settings.observability_enabled:
        return Response(status_code=404)

    payload = MeterRegistry.render_prometheus_metrics()
    if payload is None:
        return Response(status_code=404)

    return Response(content=payload, media_type=CONTENT_TYPE_LATEST)
