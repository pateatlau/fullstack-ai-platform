"""Tests for ``WorkflowExecutor`` sequential/branching (non-parallel) execution."""

from __future__ import annotations

import asyncio
import datetime
import uuid

import pytest

from app.ai.workflow.engine.executor import WorkflowExecutor
from app.ai.workflow.exceptions import WorkflowNotFoundError
from app.ai.workflow.models import (
    NodeStatus,
    NodeType,
    RunStatus,
    WorkflowContext,
    WorkflowDefinition,
    WorkflowEdge,
    WorkflowNode,
    WorkflowRun,
)
from app.ai.workflow.nodes.base import NodeExecutionRequest, WorkflowNodeExecutionError
from tests.ai.workflow.test_interfaces import FakeWorkflowStore

_NOW = datetime.datetime.now(datetime.UTC)


class FakeNodeExecutor:
    """Records every invocation and returns/raises a scripted result."""

    def __init__(
        self,
        *,
        output: dict[str, object] | None = None,
        error: str | None = None,
        delay_seconds: float = 0.0,
    ) -> None:
        self.output = output if output is not None else {}
        self.error = error
        self.delay_seconds = delay_seconds
        self.calls: list[
            tuple[WorkflowNode, WorkflowContext, NodeExecutionRequest]
        ] = []

    async def execute(
        self,
        node: WorkflowNode,
        context: WorkflowContext,
        request: NodeExecutionRequest,
    ) -> dict[str, object]:
        self.calls.append((node, context, request))
        if self.delay_seconds:
            await asyncio.sleep(self.delay_seconds)
        if self.error is not None:
            raise WorkflowNodeExecutionError(self.error)
        return self.output


def _definition(
    *, nodes: list[WorkflowNode], edges: list[WorkflowEdge], owner_id: uuid.UUID
) -> WorkflowDefinition:
    return WorkflowDefinition(
        id=uuid.uuid4(),
        owner_id=owner_id,
        name="Test Workflow",
        entry_node_id=nodes[0].id,
        nodes=nodes,
        edges=edges,
        created_at=_NOW,
        updated_at=_NOW,
    )


def _run(*, owner_id: uuid.UUID, definition_id: uuid.UUID) -> WorkflowRun:
    return WorkflowRun(
        id=uuid.uuid4(),
        workflow_definition_id=definition_id,
        owner_id=owner_id,
        idempotency_key="key-1",
        status=RunStatus.RUNNING,
        context=WorkflowContext(),
        current_node_ids=[],
        checkpoint_version=0,
        created_at=_NOW,
        updated_at=_NOW,
        started_at=_NOW,
    )


async def _seeded(
    store: FakeWorkflowStore, definition: WorkflowDefinition, owner_id: uuid.UUID
) -> WorkflowRun:
    await store.create_definition(definition)
    run = _run(owner_id=owner_id, definition_id=definition.id)
    return await store.create_run(run)


@pytest.mark.anyio
async def test_single_node_run_completes() -> None:
    owner_id = uuid.uuid4()
    store = FakeWorkflowStore()
    definition = _definition(
        nodes=[
            WorkflowNode(id="start", type=NodeType.TASK, config={}),
            WorkflowNode(id="end", type=NodeType.TERMINAL, config={}),
        ],
        edges=[WorkflowEdge(id="e1", from_node_id="start", to_node_id="end")],
        owner_id=owner_id,
    )
    run = await _seeded(store, definition, owner_id)
    task_executor = FakeNodeExecutor(output={"data": "ok"})
    executor = WorkflowExecutor(store, {NodeType.TASK: task_executor})

    result = await executor.execute_run(run.id, owner_id=owner_id)

    assert result.status is RunStatus.COMPLETED
    assert result.context.variables["start"] == {"data": "ok"}
    assert result.current_node_ids == []
    assert len(task_executor.calls) == 1


@pytest.mark.anyio
async def test_multi_node_sequential_run_completes_in_order() -> None:
    owner_id = uuid.uuid4()
    store = FakeWorkflowStore()
    definition = _definition(
        nodes=[
            WorkflowNode(id="first", type=NodeType.TASK, config={}),
            WorkflowNode(id="second", type=NodeType.TASK, config={}),
            WorkflowNode(id="end", type=NodeType.TERMINAL, config={}),
        ],
        edges=[
            WorkflowEdge(id="e1", from_node_id="first", to_node_id="second"),
            WorkflowEdge(id="e2", from_node_id="second", to_node_id="end"),
        ],
        owner_id=owner_id,
    )
    run = await _seeded(store, definition, owner_id)
    task_executor = FakeNodeExecutor(output={"value": 1})
    executor = WorkflowExecutor(store, {NodeType.TASK: task_executor})

    result = await executor.execute_run(run.id, owner_id=owner_id)

    assert result.status is RunStatus.COMPLETED
    assert [node.id for node, _, _ in task_executor.calls] == ["first", "second"]
    assert set(result.context.variables) == {"first", "second", "end"}


@pytest.mark.anyio
async def test_branching_non_parallel_run_visits_all_branches() -> None:
    owner_id = uuid.uuid4()
    store = FakeWorkflowStore()
    definition = _definition(
        nodes=[
            WorkflowNode(id="entry", type=NodeType.TASK, config={}),
            WorkflowNode(id="branch_a", type=NodeType.TASK, config={}),
            WorkflowNode(id="branch_b", type=NodeType.TASK, config={}),
            WorkflowNode(id="end", type=NodeType.TERMINAL, config={}),
        ],
        edges=[
            WorkflowEdge(id="e1", from_node_id="entry", to_node_id="branch_a"),
            WorkflowEdge(id="e2", from_node_id="entry", to_node_id="branch_b"),
            WorkflowEdge(id="e3", from_node_id="branch_a", to_node_id="end"),
            WorkflowEdge(id="e4", from_node_id="branch_b", to_node_id="end"),
        ],
        owner_id=owner_id,
    )
    run = await _seeded(store, definition, owner_id)
    task_executor = FakeNodeExecutor(output={"ok": True})
    executor = WorkflowExecutor(store, {NodeType.TASK: task_executor})

    result = await executor.execute_run(run.id, owner_id=owner_id)

    assert result.status is RunStatus.COMPLETED
    visited = {node.id for node, _, _ in task_executor.calls}
    assert visited == {"entry", "branch_a", "branch_b"}


@pytest.mark.anyio
async def test_checkpoint_persisted_after_every_node_transition() -> None:
    owner_id = uuid.uuid4()
    store = FakeWorkflowStore()
    definition = _definition(
        nodes=[
            WorkflowNode(id="start", type=NodeType.TASK, config={}),
            WorkflowNode(id="end", type=NodeType.TERMINAL, config={}),
        ],
        edges=[WorkflowEdge(id="e1", from_node_id="start", to_node_id="end")],
        owner_id=owner_id,
    )
    run = await _seeded(store, definition, owner_id)
    executor = WorkflowExecutor(
        store, {NodeType.TASK: FakeNodeExecutor(output={"x": 1})}
    )

    result = await executor.execute_run(run.id, owner_id=owner_id)

    with_executions = await store.get_run_with_executions(result.id, owner_id=owner_id)
    assert with_executions is not None
    _, executions = with_executions
    by_node = {execution.node_id: execution for execution in executions}
    assert by_node["start"].status is NodeStatus.SUCCEEDED
    assert by_node["start"].output == {"x": 1}
    assert by_node["end"].status is NodeStatus.SUCCEEDED
    # Exactly one persisted row per node attempt (running -> succeeded upsert).
    assert len(executions) == 2
    assert result.checkpoint_version > run.checkpoint_version


@pytest.mark.anyio
async def test_node_failure_fails_run_without_crashing() -> None:
    owner_id = uuid.uuid4()
    store = FakeWorkflowStore()
    definition = _definition(
        nodes=[
            WorkflowNode(id="start", type=NodeType.TASK, config={}),
            WorkflowNode(id="end", type=NodeType.TERMINAL, config={}),
        ],
        edges=[WorkflowEdge(id="e1", from_node_id="start", to_node_id="end")],
        owner_id=owner_id,
    )
    run = await _seeded(store, definition, owner_id)
    executor = WorkflowExecutor(
        store, {NodeType.TASK: FakeNodeExecutor(error="tool exploded")}
    )

    result = await executor.execute_run(run.id, owner_id=owner_id)

    assert result.status is RunStatus.FAILED
    assert result.error == "tool exploded"
    with_executions = await store.get_run_with_executions(result.id, owner_id=owner_id)
    assert with_executions is not None
    _, executions = with_executions
    assert executions[0].status is NodeStatus.FAILED
    assert executions[0].error == "tool exploded"


@pytest.mark.anyio
async def test_node_timeout_fails_run() -> None:
    owner_id = uuid.uuid4()
    store = FakeWorkflowStore()
    definition = _definition(
        nodes=[
            WorkflowNode(id="start", type=NodeType.TASK, config={}, timeout_seconds=1),
            WorkflowNode(id="end", type=NodeType.TERMINAL, config={}),
        ],
        edges=[WorkflowEdge(id="e1", from_node_id="start", to_node_id="end")],
        owner_id=owner_id,
    )
    run = await _seeded(store, definition, owner_id)
    executor = WorkflowExecutor(
        store, {NodeType.TASK: FakeNodeExecutor(delay_seconds=5)}
    )

    result = await executor.execute_run(run.id, owner_id=owner_id)

    assert result.status is RunStatus.FAILED
    assert "timed out" in (result.error or "")


@pytest.mark.anyio
async def test_missing_node_executor_fails_node_not_run() -> None:
    owner_id = uuid.uuid4()
    store = FakeWorkflowStore()
    definition = _definition(
        nodes=[
            WorkflowNode(id="start", type=NodeType.TASK, config={}),
            WorkflowNode(id="end", type=NodeType.TERMINAL, config={}),
        ],
        edges=[WorkflowEdge(id="e1", from_node_id="start", to_node_id="end")],
        owner_id=owner_id,
    )
    run = await _seeded(store, definition, owner_id)
    executor = WorkflowExecutor(store, {})

    result = await executor.execute_run(run.id, owner_id=owner_id)

    assert result.status is RunStatus.FAILED
    assert "No node executor registered" in (result.error or "")


@pytest.mark.anyio
async def test_execute_run_leaves_stalled_run_running_with_in_progress_nodes() -> None:
    """A checkpoint with in-flight nodes must not be falsely marked completed."""
    owner_id = uuid.uuid4()
    store = FakeWorkflowStore()
    definition = _definition(
        nodes=[
            WorkflowNode(id="start", type=NodeType.TASK, config={}),
            WorkflowNode(id="end", type=NodeType.TERMINAL, config={}),
        ],
        edges=[WorkflowEdge(id="e1", from_node_id="start", to_node_id="end")],
        owner_id=owner_id,
    )
    await store.create_definition(definition)
    run = await store.create_run(
        _run(owner_id=owner_id, definition_id=definition.id).model_copy(
            update={"current_node_ids": ["start"]}
        )
    )
    task_executor = FakeNodeExecutor(output={"data": "ok"})
    executor = WorkflowExecutor(store, {NodeType.TASK: task_executor})

    result = await executor.execute_run(run.id, owner_id=owner_id)

    assert result.status is RunStatus.RUNNING
    assert result.current_node_ids == ["start"]
    assert result.completed_at is None
    assert task_executor.calls == []


@pytest.mark.anyio
async def test_execute_run_raises_for_missing_run() -> None:
    store = FakeWorkflowStore()
    executor = WorkflowExecutor(store, {})

    with pytest.raises(WorkflowNotFoundError):
        await executor.execute_run(uuid.uuid4(), owner_id=uuid.uuid4())
