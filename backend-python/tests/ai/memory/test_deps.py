"""Tests for Memory dependency injection wiring in app.ai.deps."""

from __future__ import annotations

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

    manager = get_memory_manager(
        provider=provider,
        settings=settings,
        embedding_provider=AsyncMock(),
        prompt_manager=AsyncMock(),
    )

    assert isinstance(manager, MemoryManager)
    assert manager.get_record is not None
    assert manager._background_provider_factory is not None
