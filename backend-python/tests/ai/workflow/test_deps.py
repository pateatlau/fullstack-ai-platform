"""Tests for Workflow dependency injection wiring in app.ai.deps."""

from __future__ import annotations

import uuid
from unittest.mock import AsyncMock

import pytest

from app.ai.deps import get_workflow_manager, get_workflow_store
from app.ai.workflow.manager import WorkflowManager
from app.ai.workflow.providers.postgres import PostgresWorkflowStore
from app.core.config import Settings


def test_get_workflow_store_returns_postgres_provider() -> None:
    session = AsyncMock()
    settings = Settings(openai_api_key="test-key")

    store = get_workflow_store(session=session, settings=settings)

    assert isinstance(store, PostgresWorkflowStore)


def test_get_workflow_manager_wires_the_resolved_store() -> None:
    session = AsyncMock()
    settings = Settings(openai_api_key="test-key")

    manager = get_workflow_manager(
        store=get_workflow_store(session=session, settings=settings),
        settings=settings,
    )

    assert isinstance(manager, WorkflowManager)


@pytest.mark.anyio
async def test_postgres_store_methods_raise_not_implemented() -> None:
    store = PostgresWorkflowStore(
        session=AsyncMock(), settings=Settings(openai_api_key="test-key")
    )

    with pytest.raises(NotImplementedError):
        await store.get_definition(uuid.uuid4(), owner_id=uuid.uuid4())
