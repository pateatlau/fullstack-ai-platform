"""SQLAlchemy-backed usage persistence (plan Sections 2.7, 8.2).

Append-only recording of provider token usage for lightweight observability —
not billing. One row per assistant generation (and optionally per summary).
"""

from __future__ import annotations

import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import UsageEvent


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
        )
        self._session.add(event)
        await self._session.flush()
        return event
