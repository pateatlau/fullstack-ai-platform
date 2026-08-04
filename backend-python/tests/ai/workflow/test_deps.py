"""Tests for Workflow dependency injection wiring in app.ai.deps."""

from __future__ import annotations

import uuid
from unittest.mock import AsyncMock

import pytest

from app.ai.deps import (
    get_tool_executor,
    get_tool_registry,
    get_workflow_manager,
    get_workflow_store,
)
from app.ai.workflow.manager import WorkflowManager
from app.ai.workflow.models import NodeType
from app.ai.workflow.nodes.task_node import TaskNodeExecutor
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
    tool_executor = get_tool_executor(registry=get_tool_registry(), settings=settings)

    manager = get_workflow_manager(
        store=get_workflow_store(session=session, settings=settings),
        settings=settings,
        tool_executor=tool_executor,
    )

    assert isinstance(manager, WorkflowManager)
    assert isinstance(manager._node_executors[NodeType.TASK], TaskNodeExecutor)
    assert manager._background_store_factory is not None


@pytest.mark.anyio
async def test_postgres_store_approval_methods_raise_not_implemented() -> None:
    store = PostgresWorkflowStore(
        session=AsyncMock(), settings=Settings(openai_api_key="test-key")
    )

    with pytest.raises(NotImplementedError):
        await store.record_approval_decision(
            uuid.uuid4(),
            owner_id=uuid.uuid4(),
            decision="approved",  # type: ignore[arg-type]
            decided_by=uuid.uuid4(),
            node_status="succeeded",  # type: ignore[arg-type]
            run=AsyncMock(),
        )
