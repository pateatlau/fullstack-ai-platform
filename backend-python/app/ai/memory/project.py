"""Project memory validation and normalization (Epic 05 Phase 5).

In v1, ``project_id`` in domain/API models maps to ``chat_session_id`` in storage
(``memory_records.session_id``). Cross-session project memory is forbidden until
a standalone projects entity ships (Part I § Locked Architectural Decisions).
"""

from __future__ import annotations

import uuid
from typing import Protocol

from app.ai.memory.exceptions import MemoryAccessDeniedError
from app.ai.memory.models import MemoryRecord, MemoryType


class SessionOwnershipChecker(Protocol):
    """Verify that a user owns a chat session before project memory access."""

    async def user_owns_session(
        self, *, user_id: uuid.UUID, session_id: uuid.UUID
    ) -> bool: ...


class ChatStoreSessionOwnershipChecker:
    """``SessionOwnershipChecker`` backed by ``SqlChatStore.get_owned_session``."""

    def __init__(self, chat_store: object) -> None:
        self._chat_store = chat_store

    async def user_owns_session(
        self, *, user_id: uuid.UUID, session_id: uuid.UUID
    ) -> bool:
        owned = await self._chat_store.get_owned_session(  # type: ignore[attr-defined]
            session_id, user_id=user_id
        )
        return owned is not None


def map_project_id_to_session_id(project_id: uuid.UUID) -> uuid.UUID:
    """Map API ``project_id`` to persisted ``session_id`` (identity in v1)."""
    return project_id


def validate_project_id(project_id: uuid.UUID) -> uuid.UUID:
    """Return ``project_id`` when it is a non-nil session identifier."""
    if project_id.int == 0:
        raise ValueError("project_id must be a valid session identifier.")
    return project_id


def normalize_project_memories(records: list[MemoryRecord]) -> list[MemoryRecord]:
    """Return project memories in deterministic order for ``MemoryContext``."""
    project_only = [r for r in records if r.memory_type is MemoryType.PROJECT]
    return sorted(
        project_only,
        key=lambda record: (-record.created_at.timestamp(), str(record.id)),
    )


def assert_project_record_scope(record: MemoryRecord, *, project_id: uuid.UUID) -> None:
    """Raise when a record is not project-scoped to the requested session."""
    session_id = map_project_id_to_session_id(project_id)
    if record.memory_type is not MemoryType.PROJECT:
        raise MemoryAccessDeniedError("Record is not project-scoped memory.")
    if record.project_id != session_id:
        raise MemoryAccessDeniedError(
            "Project memory does not belong to the requested session."
        )
