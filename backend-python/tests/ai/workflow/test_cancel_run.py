"""Tests for workflow run cancellation (Phase 9)."""

from __future__ import annotations

import asyncio
import datetime
import uuid

import pytest

from app.ai.workflow.manager import WorkflowManager
from app.ai.workflow.models import (
    DefinitionStatus,
    NodeStatus,
    NodeType,
    RunStatus,
    WorkflowContext,
    WorkflowDefinition,
    WorkflowEdge,
    WorkflowNode,
)
from app.ai.workflow.nodes.base import NodeExecutionRequest
from tests.ai.workflow.test_interfaces import FakeWorkflowStore
from tests.ai.workflow.test_manager_runs import _await_scheduled

_NOW = datetime.datetime.now(datetime.UTC)


class SlowTaskExecutor:
    """Blocks until released so tests can cancel mid-node."""

    def __init__(self) -> None:
        self.started = asyncio.Event()
        self.release = asyncio.Event()
        self.output: dict[str, object] = {"completed": True}

    async def execute(
        self,
        node: WorkflowNode,
        context: WorkflowContext,
        request: NodeExecutionRequest,
    ) -> dict[str, object]:
        del context, request
        self.started.set()
        await self.release.wait()
        return self.output


def _active_definition(owner_id: uuid.UUID) -> WorkflowDefinition:
    return WorkflowDefinition(
        id=uuid.uuid4(),
        owner_id=owner_id,
        name="Cancel Workflow",
        status=DefinitionStatus.ACTIVE,
        entry_node_id="start",
        nodes=[
            WorkflowNode(id="start", type=NodeType.TASK, config={}),
            WorkflowNode(id="end", type=NodeType.TERMINAL, config={}),
        ],
        edges=[WorkflowEdge(id="e1", from_node_id="start", to_node_id="end")],
        created_at=_NOW,
        updated_at=_NOW,
    )


@pytest.mark.anyio
async def test_cancel_run_stops_in_flight_node_without_persisting_result() -> None:
    store = FakeWorkflowStore()
    owner_id = uuid.uuid4()
    definition = await store.create_definition(_active_definition(owner_id))
    task_executor = SlowTaskExecutor()
    manager = WorkflowManager(
        store,
        node_executors={NodeType.TASK: task_executor},
    )

    run = await manager.start_run(
        definition.id,
        owner_id=owner_id,
        idempotency_key="cancel-mid-node",
    )
    await asyncio.wait_for(task_executor.started.wait(), timeout=1.0)

    cancelled = await manager.cancel_run(run.id, owner_id=owner_id)
    assert cancelled.status is RunStatus.CANCELLED

    task_executor.release.set()
    await _await_scheduled(manager)

    final = await manager.get_run(run.id, owner_id=owner_id)
    assert final is not None
    assert final.status is RunStatus.CANCELLED
    assert "start" not in final.context.variables

    with_executions = await store.get_run_with_executions(run.id, owner_id=owner_id)
    assert with_executions is not None
    _, executions = with_executions
    assert not any(
        execution.status is NodeStatus.SUCCEEDED and execution.output is not None
        for execution in executions
    )
