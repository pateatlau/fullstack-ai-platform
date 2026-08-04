"""Tests for project memory validation and normalization (Phase 5)."""

from __future__ import annotations

import datetime
import uuid

import pytest

from app.ai.memory.exceptions import MemoryAccessDeniedError
from app.ai.memory.models import MemoryRecord, MemoryScope, MemoryType
from app.ai.memory.project import (
    assert_project_record_scope,
    map_project_id_to_session_id,
    normalize_project_memories,
    validate_project_id,
)

_NOW = datetime.datetime.now(datetime.timezone.utc)


def _project_record(
    *,
    project_id: uuid.UUID | None = None,
    created_at: datetime.datetime | None = None,
    content: str = "Project fact.",
) -> MemoryRecord:
    return MemoryRecord(
        id=uuid.uuid4(),
        memory_type=MemoryType.PROJECT,
        scope=MemoryScope.PROJECT,
        owner_id=uuid.uuid4(),
        project_id=project_id or uuid.uuid4(),
        content=content,
        created_at=created_at or _NOW,
        updated_at=created_at or _NOW,
        source="api",
    )


def _user_record(*, created_at: datetime.datetime | None = None) -> MemoryRecord:
    return MemoryRecord(
        id=uuid.uuid4(),
        memory_type=MemoryType.USER,
        scope=MemoryScope.USER,
        owner_id=uuid.uuid4(),
        content="User fact.",
        created_at=created_at or _NOW,
        updated_at=created_at or _NOW,
        source="api",
    )


class TestProjectIdMapping:
    def test_map_project_id_to_session_id_is_identity_in_v1(self) -> None:
        project_id = uuid.uuid4()

        assert map_project_id_to_session_id(project_id) == project_id

    def test_validate_project_id_rejects_nil_uuid(self) -> None:
        with pytest.raises(ValueError, match="valid session identifier"):
            validate_project_id(uuid.UUID(int=0))


class TestNormalizeProjectMemories:
    def test_filters_non_project_records(self) -> None:
        project = _project_record(content="Project only.")
        user = _user_record()

        normalized = normalize_project_memories([user, project])

        assert normalized == [project]

    def test_orders_by_created_at_desc_then_id(self) -> None:
        older = _project_record(
            created_at=_NOW - datetime.timedelta(hours=2),
            content="Older.",
        )
        newer = _project_record(
            created_at=_NOW - datetime.timedelta(hours=1),
            content="Newer.",
        )

        normalized = normalize_project_memories([older, newer])

        assert [record.content for record in normalized] == ["Newer.", "Older."]


class TestAssertProjectRecordScope:
    def test_accepts_matching_project_scope(self) -> None:
        project_id = uuid.uuid4()
        record = _project_record(project_id=project_id)

        assert_project_record_scope(record, project_id=project_id)

    def test_rejects_user_record(self) -> None:
        record = _user_record()

        with pytest.raises(MemoryAccessDeniedError, match="not project-scoped"):
            assert_project_record_scope(record, project_id=uuid.uuid4())

    def test_rejects_cross_session_project_record(self) -> None:
        record = _project_record(project_id=uuid.uuid4())

        with pytest.raises(MemoryAccessDeniedError, match="requested session"):
            assert_project_record_scope(record, project_id=uuid.uuid4())
