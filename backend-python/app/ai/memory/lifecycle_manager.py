"""Lifecycle transition execution (Epic 05 Phase 7).

``LifecycleManager`` applies policy decisions via ``MemoryProvider`` while
keeping storage details provider-independent.
"""

from __future__ import annotations

import datetime
import uuid
from typing import TYPE_CHECKING

from app.ai.memory.events import (
    MemoryEvent,
    MemoryEventMetadata,
    MemoryEventPublisher,
    MemoryEventType,
)
from app.ai.memory.exceptions import MemoryError, MemoryNotFoundError
from app.ai.memory.interfaces.memory_provider import MemoryProvider
from app.ai.memory.lifecycle import LifecycleState, validate_transition
from app.ai.memory.models import MemoryRecord, MemoryType
from app.ai.memory.policy_engine import MemoryPolicyEngine
from app.core.logging import get_logger

if TYPE_CHECKING:
    from app.core.config import Settings

logger = get_logger(__name__)

_LIFECYCLE_EVENT_BY_STATE: dict[LifecycleState, MemoryEventType] = {
    LifecycleState.ACTIVE: MemoryEventType.ACTIVATED,
    LifecycleState.CONSOLIDATED: MemoryEventType.CONSOLIDATED,
    LifecycleState.ARCHIVED: MemoryEventType.ARCHIVED,
    LifecycleState.DELETED: MemoryEventType.DELETED,
}


class LifecycleManager:
    """Execute lifecycle transitions and retention policies."""

    def __init__(
        self,
        provider: MemoryProvider,
        *,
        settings: Settings,
        policy_engine: MemoryPolicyEngine | None = None,
        event_publisher: MemoryEventPublisher | None = None,
    ) -> None:
        self._provider = provider
        self._settings = settings
        self._policy_engine = policy_engine or MemoryPolicyEngine(settings)
        from app.ai.memory.events import LoggingMemoryEventPublisher

        self._event_publisher = event_publisher or LoggingMemoryEventPublisher()

    async def transition_record(
        self,
        record: MemoryRecord,
        target: LifecycleState,
        *,
        reason: str | None = None,
    ) -> MemoryRecord:
        """Apply a validated lifecycle transition and publish an event."""
        if record.lifecycle_state is target:
            return record

        try:
            validate_transition(record.lifecycle_state, target)
        except MemoryError:
            raise

        metadata = dict(record.metadata)
        now = datetime.datetime.now(datetime.timezone.utc)
        if target is LifecycleState.ARCHIVED:
            metadata.setdefault("archived_at", now.isoformat())
        if target is LifecycleState.DELETED:
            metadata.setdefault("deleted_at", now.isoformat())
        if reason:
            metadata["lifecycle_reason"] = reason

        updated = await self._provider.update_lifecycle_state(
            record.id,
            owner_id=record.owner_id,
            state=target,
            metadata=metadata,
        )
        await self._publish_transition(updated, previous=record.lifecycle_state)
        return updated

    async def activate_record(self, record: MemoryRecord) -> MemoryRecord:
        """Transition ``created`` memories to ``active`` after persistence."""
        if not self._policy_engine.should_activate(record):
            return record
        try:
            return await self.transition_record(
                record,
                LifecycleState.ACTIVE,
                reason="post_persist_activation",
            )
        except MemoryError:
            logger.warning(
                "Memory activation skipped — invalid transition",
                record_id=str(record.id),
                lifecycle_state=record.lifecycle_state.value,
            )
            return record
        except Exception:  # noqa: BLE001 - lifecycle must not block callers
            logger.warning(
                "Memory activation failed",
                record_id=str(record.id),
                exc_info=True,
            )
            return record

    async def delete_record(
        self, record_id: uuid.UUID, *, owner_id: uuid.UUID
    ) -> MemoryRecord:
        """Soft-delete a memory via the lifecycle ``deleted`` state."""
        record = await self._provider.get_record(record_id, owner_id=owner_id)
        if record is None:
            raise MemoryNotFoundError(
                f"Memory record {record_id} not found for owner {owner_id}."
            )
        if record.lifecycle_state is LifecycleState.DELETED:
            return record
        return await self.transition_record(
            record,
            LifecycleState.DELETED,
            reason="explicit_delete",
        )

    async def process_owner_memories(
        self,
        *,
        owner_id: uuid.UUID,
        session_id: uuid.UUID | None = None,
    ) -> None:
        """Run consolidation, archival, and retention for one owner scope."""
        try:
            records = await self._load_manageable_records(
                owner_id=owner_id,
                session_id=session_id,
            )
            await self._run_consolidation(records)
            refreshed = await self._load_manageable_records(
                owner_id=owner_id,
                session_id=session_id,
            )
            await self._run_archival(refreshed)
            archived = await self._load_archived_records(
                owner_id=owner_id,
                session_id=session_id,
            )
            await self._run_retention_deletion(archived)
        except Exception:  # noqa: BLE001 - lifecycle processing is best-effort
            logger.warning(
                "Memory lifecycle processing failed",
                owner_id=str(owner_id),
                session_id=str(session_id) if session_id is not None else None,
                exc_info=True,
            )

    async def _run_consolidation(self, records: list[MemoryRecord]) -> None:
        for group in self._policy_engine.find_consolidation_groups(records):
            for outcome in self._policy_engine.consolidation_targets(group):
                record = _find_record(records, outcome.record_id)
                if record is None or outcome.target_state is None:
                    continue
                try:
                    await self.transition_record(
                        record,
                        outcome.target_state,
                        reason="duplicate_consolidation",
                    )
                except MemoryError:
                    continue

    async def _run_archival(self, records: list[MemoryRecord]) -> None:
        for record in records:
            if not self._policy_engine.should_archive(record):
                continue
            try:
                await self.transition_record(
                    record,
                    LifecycleState.ARCHIVED,
                    reason="consolidated_archival",
                )
            except MemoryError:
                continue

    async def _run_retention_deletion(self, records: list[MemoryRecord]) -> None:
        now = datetime.datetime.now(datetime.timezone.utc)
        for record in records:
            if not self._policy_engine.should_delete_archived(record, now=now):
                continue
            try:
                await self.transition_record(
                    record,
                    LifecycleState.DELETED,
                    reason="retention_expired",
                )
            except MemoryError:
                continue

    async def _load_manageable_records(
        self,
        *,
        owner_id: uuid.UUID,
        session_id: uuid.UUID | None,
    ) -> list[MemoryRecord]:
        list_records = getattr(self._provider, "list_records", None)
        if list_records is None:
            return []
        user_records = await list_records(  # type: ignore[misc]
            owner_id=owner_id,
            memory_type=MemoryType.USER,
            include_deleted=False,
        )
        if session_id is None:
            return user_records
        project_records = await list_records(  # type: ignore[misc]
            owner_id=owner_id,
            memory_type=MemoryType.PROJECT,
            session_id=session_id,
            include_deleted=False,
        )
        return user_records + project_records

    async def _load_archived_records(
        self,
        *,
        owner_id: uuid.UUID,
        session_id: uuid.UUID | None,
    ) -> list[MemoryRecord]:
        list_records = getattr(self._provider, "list_records", None)
        if list_records is None:
            return []
        user_records = await list_records(  # type: ignore[misc]
            owner_id=owner_id,
            memory_type=MemoryType.USER,
            lifecycle_state=LifecycleState.ARCHIVED,
        )
        if session_id is None:
            return user_records
        project_records = await list_records(  # type: ignore[misc]
            owner_id=owner_id,
            memory_type=MemoryType.PROJECT,
            session_id=session_id,
            lifecycle_state=LifecycleState.ARCHIVED,
        )
        return user_records + project_records

    async def _publish_transition(
        self,
        record: MemoryRecord,
        *,
        previous: LifecycleState,
    ) -> None:
        event_type = _LIFECYCLE_EVENT_BY_STATE.get(
            record.lifecycle_state,
            MemoryEventType.TRANSITIONED,
        )
        await self._event_publisher.publish(
            MemoryEvent(
                event_type=event_type,
                record_id=record.id,
                owner_id=record.owner_id,
                memory_type=record.memory_type.value,
                lifecycle_state=record.lifecycle_state,
                metadata=MemoryEventMetadata(
                    source=record.source,
                    previous_state=previous.value,
                ),
            )
        )


def _find_record(records: list[MemoryRecord], record_id: str) -> MemoryRecord | None:
    for record in records:
        if str(record.id) == record_id:
            return record
    return None
