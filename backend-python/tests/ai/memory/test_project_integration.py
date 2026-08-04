"""Integration tests for project memory session ownership (Phase 5)."""

from __future__ import annotations

import datetime
import uuid

import pytest

from app.ai.memory.exceptions import MemoryAccessDeniedError
from app.ai.memory.manager import MemoryManager
from app.ai.memory.models import MemoryRecord, MemoryScope, MemoryType
from app.ai.memory.project import ChatStoreSessionOwnershipChecker
from app.ai.memory.providers.pgvector import PgVectorMemoryProvider
from app.core.config import Settings
from app.db.chat import SqlChatStore
from app.db.identity import SqlUserStore

DIMENSIONS = 1536
_NOW = datetime.datetime.now(datetime.timezone.utc)


def _embedding() -> list[float]:
    return [0.1] * DIMENSIONS


async def _make_user(session) -> uuid.UUID:
    user = await SqlUserStore(session).create(
        sub=f"memory-{uuid.uuid4()}", email=None, name=None, picture=None
    )
    return user.id


async def _make_session(session, *, user_id: uuid.UUID) -> uuid.UUID:
    chat_session = await SqlChatStore(session).create_session(user_id=user_id)
    return chat_session.id


def _manager(
    provider: PgVectorMemoryProvider, chat_store: SqlChatStore
) -> MemoryManager:
    return MemoryManager(
        provider=provider,
        settings=Settings(openai_api_key="test-key"),
        session_ownership_checker=ChatStoreSessionOwnershipChecker(chat_store),
    )


@pytest.mark.anyio
async def test_list_project_memories_enforces_session_ownership(db_session) -> None:
    owner_id = await _make_user(db_session)
    other_owner = await _make_user(db_session)
    owned_session = await _make_session(db_session, user_id=owner_id)
    chat_store = SqlChatStore(db_session)
    provider = PgVectorMemoryProvider(db_session, Settings(openai_api_key="test-key"))
    manager = _manager(provider, chat_store)
    await provider.create_record(
        MemoryRecord(
            id=uuid.uuid4(),
            memory_type=MemoryType.PROJECT,
            scope=MemoryScope.PROJECT,
            owner_id=owner_id,
            project_id=owned_session,
            content="Owned project fact.",
            embedding=_embedding(),
            created_at=_NOW,
            updated_at=_NOW,
            source="api",
        )
    )

    records = await manager.list_project_memories(
        owner_id=owner_id, project_id=owned_session
    )
    assert len(records) == 1

    with pytest.raises(MemoryAccessDeniedError):
        await manager.list_project_memories(
            owner_id=other_owner, project_id=owned_session
        )
