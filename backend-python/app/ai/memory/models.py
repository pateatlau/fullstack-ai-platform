"""Canonical Memory domain models (public API — stable after Phase 1).

These are in-memory domain models, distinct from the SQLAlchemy ORM models in
``app/db/models.py`` (mirrors the ``app/ai/documents/schemas.py`` convention).
"""

from __future__ import annotations

import datetime
import uuid
from enum import StrEnum

from pydantic import BaseModel, Field, field_validator, model_validator

from app.ai.memory.lifecycle import LifecycleState
from app.ai.memory.preferences import validate_preference_key, validate_preference_value


class MemoryType(StrEnum):
    """Persisted discriminator for ``memory_records.memory_type`` (DB CHECK)."""

    USER = "user"
    PROJECT = "project"


class MemoryScope(StrEnum):
    """Memory domain per Part I § Memory Domains (Conversation, User, Project, System).

    Conversation summaries are not stored as ``MemoryRecord`` (see
    ``SessionSummary``), so only ``USER`` and ``PROJECT`` are reachable in v1.
    ``SYSTEM`` is reserved for a future epic and is never persisted.
    """

    USER = "user"
    PROJECT = "project"
    SYSTEM = "system"  # TODO(future epic): system memory — not implemented in v1.


class MemoryRecord(BaseModel):
    """Canonical durable memory representation (Part I § Canonical Memory Representation).

    ``UserMemory`` and ``ProjectMemory`` are the same shape, distinguished only
    by ``memory_type`` — there are no separate model subclasses.
    """

    id: uuid.UUID
    memory_type: MemoryType
    scope: MemoryScope
    owner_id: uuid.UUID
    project_id: uuid.UUID | None = None
    title: str | None = None
    content: str = Field(min_length=1)
    summary: str | None = None
    embedding: list[float] | None = None
    metadata: dict[str, object] = Field(default_factory=dict)
    importance: float = Field(default=0.5, ge=0.0, le=1.0)
    confidence: float = Field(default=0.5, ge=0.0, le=1.0)
    quality_score: float = Field(default=0.5, ge=0.0, le=1.0)
    created_at: datetime.datetime
    updated_at: datetime.datetime
    last_accessed_at: datetime.datetime | None = None
    expires_at: datetime.datetime | None = None
    lifecycle_state: LifecycleState = LifecycleState.CREATED
    source: str = Field(min_length=1)

    @model_validator(mode="after")
    def _validate_project_scope(self) -> MemoryRecord:
        """Enforce the frozen v1 invariant: ``project_id`` == ``chat_session_id``.

        Project memory is session-scoped until a standalone projects entity
        ships (Part I § Locked Architectural Decisions); user memory has no
        session association. ``scope`` must agree with ``memory_type`` since
        ``SYSTEM`` scope is reserved and not yet a valid ``memory_type``.
        """
        is_project = self.memory_type is MemoryType.PROJECT
        if is_project and self.project_id is None:
            raise ValueError("project_id is required when memory_type is 'project'.")
        if not is_project and self.project_id is not None:
            raise ValueError("project_id must be unset when memory_type is 'user'.")
        if self.scope.value != self.memory_type.value:
            raise ValueError(
                "scope must match memory_type in v1 ('system' scope is reserved)."
            )
        return self


class UserPreferenceUpsert(BaseModel):
    """Request body for upserting a structured user preference (Phase 4 API model)."""

    value: dict[str, object] = Field(default_factory=dict)

    @field_validator("value")
    @classmethod
    def _validate_value(cls, value: object) -> dict[str, object]:
        return validate_preference_value(value)


class UserPreferenceItem(BaseModel):
    """Canonical preference key/value pair for responses and ``MemoryContext``."""

    key: str
    value: dict[str, object] = Field(default_factory=dict)

    @field_validator("key")
    @classmethod
    def _validate_key(cls, key: str) -> str:
        return validate_preference_key(key)

    @field_validator("value")
    @classmethod
    def _validate_value(cls, value: object) -> dict[str, object]:
        return validate_preference_value(value)


class UserPreferenceListResponse(BaseModel):
    """List of caller-owned structured preferences."""

    preferences: list[UserPreferenceItem] = Field(default_factory=list)


class MemoryContext(BaseModel):
    """Normalized retrieval result for prompt assembly (Part I § MemoryContext).

    Downstream prompt assembly (``MemoryPromptInjector`` / RAG instructions)
    depends only on this model — never on storage.
    """

    conversation_summary: str | None = None
    conversation_memories: list[MemoryRecord] = Field(default_factory=list)
    user_memories: list[MemoryRecord] = Field(default_factory=list)
    project_memories: list[MemoryRecord] = Field(default_factory=list)
    preferences: dict[str, object] = Field(default_factory=dict)
    metadata: dict[str, object] = Field(default_factory=dict)
    token_usage: int = Field(default=0, ge=0)
