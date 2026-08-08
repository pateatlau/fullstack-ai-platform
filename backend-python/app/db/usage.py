"""SQLAlchemy-backed usage persistence (plan Sections 2.7, 8.2).

Append-only recording of provider token usage for lightweight observability —
not billing. One row per assistant generation (and optionally per summary).
"""

from __future__ import annotations

import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from app.ai.observability.cost.calculator import CostRegistry
from app.ai.observability.metrics.instruments import record_llm_cost_metric
from app.core.logging import get_logger
from app.db.models import UsageEvent
from app.providers.base import ProviderUsage

logger = get_logger(__name__)


class SqlUsageStore:
    """Insert ``usage_events`` rows against an ``AsyncSession``."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def record(
        self,
        *,
        session_id: uuid.UUID,
        provider: str,
        model: str,
        token_source: str,
        kind: str = "chat",
        user_id: uuid.UUID | None = None,
        guest_id: uuid.UUID | None = None,
        message_id: uuid.UUID | None = None,
        prompt_tokens: int | None = None,
        completion_tokens: int | None = None,
        total_tokens: int | None = None,
        latency_ms: int | None = None,
        request_id: str | None = None,
    ) -> UsageEvent:
        cost_usd: float | None = None
        pricing_version: str | None = None

        calculator = CostRegistry.get_calculator()
        if calculator is not None:
            try:
                usage = ProviderUsage(
                    prompt_tokens=prompt_tokens,
                    completion_tokens=completion_tokens,
                    total_tokens=total_tokens,
                )
                cost_usd, pricing_version = calculator.price(provider, model, usage)
                if cost_usd is not None:
                    record_llm_cost_metric(
                        provider=provider,
                        model=model,
                        cost_usd=cost_usd,
                    )
            except Exception as exc:
                logger.warning(
                    "Observability usage cost calculation failed",
                    provider=provider,
                    model=model,
                    error=str(exc),
                    exc_info=True,
                )

        event = UsageEvent(
            session_id=session_id,
            user_id=user_id,
            guest_id=guest_id,
            message_id=message_id,
            kind=kind,
            provider=provider,
            model=model,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            total_tokens=total_tokens,
            token_source=token_source,
            latency_ms=latency_ms,
            request_id=request_id,
            cost_usd=cost_usd,
            pricing_version=pricing_version,
        )
        self._session.add(event)
        await self._session.flush()
        return event
