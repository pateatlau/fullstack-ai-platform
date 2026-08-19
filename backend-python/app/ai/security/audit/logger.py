"""Durable, non-blocking-to-caller audit event recording (Epic 11 Phase 3)."""

from __future__ import annotations

import datetime
import uuid
from typing import Any

from fastapi import Request

from app.ai.observability.metrics.instruments import record_audit_write_failure
from app.ai.observability.tracing.spans import capture_current_span_context
from app.ai.security.audit.actions import AuditAction
from app.ai.security.audit.models import ActorKind, AuditEvent, AuditOutcome
from app.ai.security.audit.store import AuditStore
from app.core.caller import CallerContext
from app.core.config import Settings
from app.core.logging import get_logger
from app.core.security import hash_ip
from app.middleware.correlation_id import get_request_id

_logger = get_logger(__name__)


def _resolve_actor(actor: CallerContext | None) -> tuple[uuid.UUID | None, ActorKind]:
    if actor is None:
        return None, "system"
    if actor.kind == "user":
        return actor.user_id, "user"
    return None, "guest"


def _resolve_trace_id() -> str | None:
    snapshot = capture_current_span_context()
    if snapshot is None:
        return None
    return format(snapshot.trace_id, "032x")


def _resolve_source_ip_hash(request: Request | None, settings: Settings) -> str | None:
    if request is None:
        return None
    from app.ai.hitl.request_metadata import resolve_client_source_ip

    ip = resolve_client_source_ip(
        request, trust_forwarded=settings.hitl_trust_forwarded_client_ip
    )
    return hash_ip(ip) if ip else None


class AuditLogger:
    """Insert one ``audit_events`` row per :meth:`record` call, in its own
    short transaction. Never raises: a DB error or unknown ``action`` is
    logged at ``ERROR`` and increments ``audit_write_failures_total``; the
    guarded action's own outcome is never affected."""

    def __init__(self, store: AuditStore | None, *, settings: Settings) -> None:
        self._store = store
        self._settings = settings

    def _enabled(self) -> bool:
        return bool(
            self._settings.security_governance_enabled
            and self._settings.security_audit_log_enabled
            and self._store is not None
        )

    async def get_by_id(self, event_id: uuid.UUID) -> AuditEvent | None:
        if self._store is None:
            return None
        return await self._store.get_by_id(event_id)

    async def query(
        self,
        *,
        actor_user_id: uuid.UUID | None = None,
        action: str | None = None,
        resource_type: str | None = None,
        outcome: AuditOutcome | None = None,
        since: datetime.datetime | None = None,
        until: datetime.datetime | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> list[AuditEvent]:
        if self._store is None:
            return []
        return await self._store.query(
            actor_user_id=actor_user_id,
            action=action,
            resource_type=resource_type,
            outcome=outcome,
            since=since,
            until=until,
            limit=limit,
            offset=offset,
        )

    async def record(
        self,
        *,
        actor: CallerContext | None,
        action: str,
        outcome: AuditOutcome,
        resource_type: str | None = None,
        resource_id: str | None = None,
        metadata: dict[str, Any] | None = None,
        request: Request | None = None,
    ) -> None:
        if not self._enabled():
            return

        try:
            AuditAction(action)
        except ValueError:
            _logger.error("Unknown audit action — taxonomy drift", action=action)
            record_audit_write_failure()
            return

        actor_user_id, actor_kind = _resolve_actor(actor)
        event = AuditEvent(
            id=uuid.uuid4(),
            occurred_at=datetime.datetime.now(datetime.UTC),
            actor_user_id=actor_user_id,
            actor_kind=actor_kind,
            action=action,
            resource_type=resource_type,
            resource_id=resource_id,
            outcome=outcome,
            metadata=metadata or {},
            request_id=get_request_id(),
            trace_id=_resolve_trace_id(),
            source_ip_hash=_resolve_source_ip_hash(request, self._settings),
        )

        try:
            assert self._store is not None
            await self._store.insert(event)
        except Exception as exc:
            _logger.error(
                "Audit event write failed",
                action=action,
                error=str(exc),
                exc_info=True,
            )
            record_audit_write_failure()
