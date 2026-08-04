"""Memory REST API schemas (Epic 05 Phase 7).

Public responses never expose embeddings, internal scores, or lifecycle state.
"""

from __future__ import annotations

import datetime
import uuid

from pydantic import BaseModel, Field

from app.ai.memory.models import (
    MemoryType,
    UserPreferenceItem,
    UserPreferenceListResponse,
    UserPreferenceUpsert,
)

__all__ = [
    "MemoryRecordListResponse",
    "MemoryRecordResponse",
    "UserPreferenceItem",
    "UserPreferenceListResponse",
    "UserPreferenceUpsert",
]


class MemoryRecordResponse(BaseModel):
    """Public memory record shape for management APIs."""

    id: uuid.UUID
    title: str | None = None
    content: str
    memory_type: MemoryType
    session_id: uuid.UUID | None = None
    created_at: datetime.datetime
    updated_at: datetime.datetime


class MemoryRecordListResponse(BaseModel):
    """List of caller-owned memory records."""

    records: list[MemoryRecordResponse] = Field(default_factory=list)
