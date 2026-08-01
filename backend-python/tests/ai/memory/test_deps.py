"""Tests for Memory dependency injection wiring in app.ai.deps."""

from __future__ import annotations

import uuid
from unittest.mock import AsyncMock

import pytest

from app.ai.deps import get_memory_manager, get_memory_provider
from app.ai.memory.manager import MemoryManager
from app.ai.memory.providers.pgvector import PgVectorMemoryProvider
from app.core.config import Settings


def test_get_memory_provider_returns_pgvector_provider() -> None:
    session = AsyncMock()
    settings = Settings(openai_api_key="test-key")

    provider = get_memory_provider(session=session, settings=settings)

    assert isinstance(provider, PgVectorMemoryProvider)


@pytest.mark.anyio
async def test_get_memory_manager_wires_the_resolved_provider() -> None:
    session = AsyncMock()
    settings = Settings(openai_api_key="test-key")
    provider = get_memory_provider(session=session, settings=settings)

    manager = get_memory_manager(provider=provider)

    assert isinstance(manager, MemoryManager)
    # Phase 1 scaffold: the wired provider is not yet implemented, so
    # delegated calls surface the same NotImplementedError as the provider.
    with pytest.raises(NotImplementedError, match="Phase 3"):
        await manager.get_record(uuid.uuid4(), owner_id=uuid.uuid4())
