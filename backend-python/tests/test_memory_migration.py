"""Memory tables migration/ORM integration tests (requires pgvector-enabled Postgres)."""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from app.db.chat import SqlChatStore
from app.db.identity import SqlUserStore
from app.db.models import MemoryRecord, UserPreference

DIMENSIONS = 1536


async def _make_user(session) -> uuid.UUID:
    user = await SqlUserStore(session).create(
        sub=f"memory-{uuid.uuid4()}", email=None, name=None, picture=None
    )
    return user.id


async def _make_session(session, *, user_id: uuid.UUID) -> uuid.UUID:
    chat_session = await SqlChatStore(session).create_session(user_id=user_id)
    return chat_session.id


@pytest.mark.anyio
async def test_user_memory_record_round_trip(db_session) -> None:
    owner_id = await _make_user(db_session)
    record = MemoryRecord(
        owner_id=owner_id,
        memory_type="user",
        content="User prefers concise answers.",
        embedding=[0.0] * DIMENSIONS,
        source="api",
    )
    db_session.add(record)
    await db_session.flush()

    fetched = await db_session.scalar(
        select(MemoryRecord).where(MemoryRecord.id == record.id)
    )

    assert fetched is not None
    assert fetched.session_id is None
    assert fetched.lifecycle_state == "created"
    assert fetched.importance == 0.5
    assert fetched.confidence == 0.5
    assert fetched.quality_score == 0.5
    assert fetched.metadata_json == {}


@pytest.mark.anyio
async def test_project_memory_record_scoped_to_session(db_session) -> None:
    owner_id = await _make_user(db_session)
    session_id = await _make_session(db_session, user_id=owner_id)
    record = MemoryRecord(
        owner_id=owner_id,
        session_id=session_id,
        memory_type="project",
        content="This project uses FastAPI + Postgres.",
        source="api",
    )
    db_session.add(record)
    await db_session.flush()

    fetched = await db_session.scalar(
        select(MemoryRecord).where(MemoryRecord.id == record.id)
    )

    assert fetched is not None
    assert fetched.session_id == session_id


@pytest.mark.anyio
async def test_memory_type_check_constraint_rejects_invalid_value(db_session) -> None:
    owner_id = await _make_user(db_session)
    db_session.add(
        MemoryRecord(
            owner_id=owner_id,
            memory_type="conversation",
            content="Invalid memory_type.",
            source="api",
        )
    )

    with pytest.raises(IntegrityError, match="memory_type_valid"):
        await db_session.flush()


@pytest.mark.anyio
async def test_lifecycle_state_check_constraint_rejects_invalid_value(
    db_session,
) -> None:
    owner_id = await _make_user(db_session)
    db_session.add(
        MemoryRecord(
            owner_id=owner_id,
            memory_type="user",
            content="Invalid lifecycle_state.",
            lifecycle_state="expired",
            source="api",
        )
    )

    with pytest.raises(IntegrityError, match="lifecycle_state_valid"):
        await db_session.flush()


@pytest.mark.anyio
async def test_user_preference_round_trip(db_session) -> None:
    user_id = await _make_user(db_session)
    preference = UserPreference(
        user_id=user_id, key="response_tone", value={"tone": "concise"}
    )
    db_session.add(preference)
    await db_session.flush()

    fetched = await db_session.scalar(
        select(UserPreference).where(UserPreference.id == preference.id)
    )

    assert fetched is not None
    assert fetched.value == {"tone": "concise"}


@pytest.mark.anyio
async def test_user_preference_unique_per_user_and_key(db_session) -> None:
    user_id = await _make_user(db_session)
    db_session.add(UserPreference(user_id=user_id, key="response_tone", value={"a": 1}))
    await db_session.flush()

    db_session.add(UserPreference(user_id=user_id, key="response_tone", value={"a": 2}))

    with pytest.raises(IntegrityError, match="uq_user_preferences_user_key"):
        await db_session.flush()
