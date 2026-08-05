"""Tests for Workflow dependency injection wiring in app.ai.deps."""

from __future__ import annotations

from unittest.mock import AsyncMock

from app.ai.deps import (
    get_tool_executor,
    get_tool_registry,
    get_workflow_manager,
    get_workflow_store,
)
from app.ai.workflow.manager import WorkflowManager
from app.ai.workflow.models import NodeType
from app.ai.workflow.nodes.approval_node import ApprovalNodeExecutor
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
    assert isinstance(manager._node_executors[NodeType.APPROVAL], ApprovalNodeExecutor)
    assert manager._background_store_factory is not None
