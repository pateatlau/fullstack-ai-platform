"""Tests for canonical Memory domain models."""

from __future__ import annotations

import datetime
import uuid

import pytest
from pydantic import ValidationError

from app.ai.memory.lifecycle import LifecycleState
from app.ai.memory.models import MemoryContext, MemoryRecord, MemoryScope, MemoryType

_NOW = datetime.datetime.now(datetime.UTC)


def _user_record(**overrides: object) -> MemoryRecord:
    defaults: dict[str, object] = {
        "id": uuid.uuid4(),
        "memory_type": MemoryType.USER,
        "scope": MemoryScope.USER,
        "owner_id": uuid.uuid4(),
        "content": "User prefers concise answers.",
        "created_at": _NOW,
        "updated_at": _NOW,
        "source": "extraction_v1",
    }
    defaults.update(overrides)
    return MemoryRecord(**defaults)  # type: ignore[arg-type]


def _project_record(**overrides: object) -> MemoryRecord:
    defaults: dict[str, object] = {
        "id": uuid.uuid4(),
        "memory_type": MemoryType.PROJECT,
        "scope": MemoryScope.PROJECT,
        "owner_id": uuid.uuid4(),
        "project_id": uuid.uuid4(),
        "content": "This project uses FastAPI + Postgres.",
        "created_at": _NOW,
        "updated_at": _NOW,
        "source": "extraction_v1",
    }
    defaults.update(overrides)
    return MemoryRecord(**defaults)  # type: ignore[arg-type]


class TestMemoryType:
    def test_values(self) -> None:
        assert MemoryType.USER == "user"
        assert MemoryType.PROJECT == "project"


class TestMemoryScope:
    def test_values(self) -> None:
        assert MemoryScope.USER == "user"
        assert MemoryScope.PROJECT == "project"
        assert MemoryScope.SYSTEM == "system"


class TestMemoryRecord:
    def test_user_memory_defaults(self) -> None:
        record = _user_record()

        assert record.memory_type is MemoryType.USER
        assert record.project_id is None
        assert record.lifecycle_state is LifecycleState.CREATED
        assert record.importance == 0.5
        assert record.confidence == 0.5
        assert record.quality_score == 0.5
        assert record.metadata == {}
        assert record.embedding is None

    def test_project_memory_requires_project_id(self) -> None:
        with pytest.raises(ValidationError, match="project_id is required"):
            _project_record(project_id=None)

    def test_user_memory_rejects_project_id(self) -> None:
        with pytest.raises(ValidationError, match="project_id must be unset"):
            _user_record(project_id=uuid.uuid4())

    def test_scope_must_match_memory_type(self) -> None:
        with pytest.raises(ValidationError, match="scope must match memory_type"):
            _user_record(scope=MemoryScope.PROJECT)

    def test_system_scope_rejected_in_v1(self) -> None:
        with pytest.raises(ValidationError, match="scope must match memory_type"):
            _user_record(scope=MemoryScope.SYSTEM)

    def test_content_must_not_be_blank(self) -> None:
        with pytest.raises(ValidationError):
            _user_record(content="")

    def test_source_must_not_be_blank(self) -> None:
        with pytest.raises(ValidationError):
            _user_record(source="")

    @pytest.mark.parametrize("field", ["importance", "confidence", "quality_score"])
    def test_score_fields_bounded_zero_to_one(self, field: str) -> None:
        with pytest.raises(ValidationError):
            _user_record(**{field: 1.5})
        with pytest.raises(ValidationError):
            _user_record(**{field: -0.1})

    def test_serialization_round_trip(self) -> None:
        record = _project_record(embedding=[0.1, 0.2, 0.3])

        dumped = record.model_dump(mode="json")
        restored = MemoryRecord.model_validate(dumped)

        assert restored == record


class TestMemoryContext:
    def test_defaults_are_empty(self) -> None:
        context = MemoryContext()

        assert context.conversation_summary is None
        assert context.conversation_memories == []
        assert context.user_memories == []
        assert context.project_memories == []
        assert context.preferences == {}
        assert context.metadata == {}
        assert context.token_usage == 0

    def test_holds_retrieved_memories(self) -> None:
        user_memory = _user_record()
        project_memory = _project_record()

        context = MemoryContext(
            conversation_summary="User is debugging a FastAPI app.",
            user_memories=[user_memory],
            project_memories=[project_memory],
            preferences={"response_tone": "concise"},
            token_usage=42,
        )

        assert context.user_memories == [user_memory]
        assert context.project_memories == [project_memory]
        assert context.preferences == {"response_tone": "concise"}
        assert context.token_usage == 42

    def test_token_usage_must_be_non_negative(self) -> None:
        with pytest.raises(ValidationError):
            MemoryContext(token_usage=-1)
