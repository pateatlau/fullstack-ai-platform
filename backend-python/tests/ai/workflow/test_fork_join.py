"""Tests for fork/join parallel execution (Phase 5)."""

from __future__ import annotations

import asyncio
import datetime
import time
import uuid

import pytest

from app.ai.workflow.engine.executor import WorkflowExecutor
from app.ai.workflow.exceptions import WorkflowValidationError
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
from app.ai.workflow.nodes.parallel_node import ForkNodeExecutor, JoinNodeExecutor
from tests.ai.workflow.test_interfaces import FakeWorkflowStore

_NOW = datetime.datetime.now(datetime.UTC)


class FakeNodeExecutor:
    """Scripted task executor with optional delay and branch-tagged output."""

    def __init__(
        self,
        *,
        delay_seconds: float = 0.0,
        delay_by_node: dict[str, float] | None = None,
        error_on: set[str] | None = None,
        error_delay_seconds: float = 0.0,
    ) -> None:
        self.delay_seconds = delay_seconds
        self.delay_by_node = delay_by_node or {}
        self.error_on = error_on or set()
        self.error_delay_seconds = error_delay_seconds
        self.calls: list[str] = []
        self._lock = asyncio.Lock()

    async def execute(
        self,
        node: WorkflowNode,
        context: WorkflowContext,
        request: NodeExecutionRequest,
    ) -> dict[str, object]:
        del context, request
        async with self._lock:
            self.calls.append(node.id)
        delay = self.delay_by_node.get(node.id, self.delay_seconds)
        if delay:
            await asyncio.sleep(delay)
        if node.id in self.error_on:
            if self.error_delay_seconds:
                await asyncio.sleep(self.error_delay_seconds)
            raise WorkflowNodeExecutionError(f"{node.id} failed")
        return {"node_id": node.id}


class ConflictAwareWorkflowStore(FakeWorkflowStore):
    """Fake store that forces one optimistic retry on the first checkpoint."""

    def __init__(self) -> None:
        super().__init__()
        self._checkpoint_attempts = 0

    async def checkpoint_run(
        self, run: WorkflowRun, *, expected_checkpoint_version: int
    ) -> WorkflowRun:
        self._checkpoint_attempts += 1
        if self._checkpoint_attempts == 1:
            raise WorkflowValidationError(
                "Workflow run checkpoint was modified concurrently; retry the update."
            )
        return await super().checkpoint_run(
            run, expected_checkpoint_version=expected_checkpoint_version
        )


def _node(
    node_id: str,
    node_type: NodeType = NodeType.TASK,
    *,
    config: dict[str, object] | None = None,
) -> WorkflowNode:
    return WorkflowNode(id=node_id, type=node_type, config=config or {})


def _edge(edge_id: str, from_node_id: str, to_node_id: str) -> WorkflowEdge:
    return WorkflowEdge(id=edge_id, from_node_id=from_node_id, to_node_id=to_node_id)


def _fork_join_definition(
    owner_id: uuid.UUID,
    *,
    join_policy: str = "all",
    count: int | None = None,
    cancel_remaining: bool = True,
    branch_count: int = 3,
) -> WorkflowDefinition:
    join_config: dict[str, object] = {
        "fork_node_id": "fork",
        "join_policy": join_policy,
        "cancel_remaining": cancel_remaining,
    }
    if count is not None:
        join_config["count"] = count

    branch_nodes = [_node(f"b{index}") for index in range(1, branch_count + 1)]
    edges = [
        _edge("e-start-fork", "start", "fork"),
        _edge("e-fork-join-short", "fork", "join"),
        _edge("e-join-end", "join", "end"),
    ]
    for index, branch in enumerate(branch_nodes, start=1):
        edges.append(_edge(f"e-fork-{branch.id}", "fork", branch.id))
        edges.append(_edge(f"e-{branch.id}-join", branch.id, "join"))

    return WorkflowDefinition(
        id=uuid.uuid4(),
        owner_id=owner_id,
        name="Fork Join Workflow",
        entry_node_id="start",
        nodes=[
            _node("start"),
            _node("fork", NodeType.FORK, config={"join_node_id": "join"}),
            *branch_nodes,
            _node("join", NodeType.JOIN, config=join_config),
            _node("end", NodeType.TERMINAL),
        ],
        edges=edges,
        created_at=_NOW,
        updated_at=_NOW,
    )


def _run(*, owner_id: uuid.UUID, definition_id: uuid.UUID) -> WorkflowRun:
    return WorkflowRun(
        id=uuid.uuid4(),
        workflow_definition_id=definition_id,
        owner_id=owner_id,
        idempotency_key="fork-join-key",
        status=RunStatus.RUNNING,
        context=WorkflowContext(),
        current_node_ids=[],
        checkpoint_version=0,
        created_at=_NOW,
        updated_at=_NOW,
        started_at=_NOW,
    )


def _executor(
    store: FakeWorkflowStore,
    task_executor: FakeNodeExecutor,
    *,
    max_parallel_branches: int = 8,
) -> WorkflowExecutor:
    return WorkflowExecutor(
        store,
        {
            NodeType.TASK: task_executor,
            NodeType.FORK: ForkNodeExecutor(
                max_parallel_branches=max_parallel_branches
            ),
            NodeType.JOIN: JoinNodeExecutor(),
        },
    )


async def _seed(
    store: FakeWorkflowStore, definition: WorkflowDefinition, owner_id: uuid.UUID
) -> WorkflowRun:
    await store.create_definition(definition)
    return await store.create_run(_run(owner_id=owner_id, definition_id=definition.id))


@pytest.mark.anyio
async def test_fork_join_all_policy_completes_all_branches() -> None:
    owner_id = uuid.uuid4()
    store = FakeWorkflowStore()
    definition = _fork_join_definition(owner_id, join_policy="all")
    run = await _seed(store, definition, owner_id)
    task_executor = FakeNodeExecutor()
    executor = _executor(store, task_executor)

    result = await executor.execute_run(run.id, owner_id=owner_id)

    assert result.status is RunStatus.COMPLETED
    assert task_executor.calls.count("b1") == 1
    assert task_executor.calls.count("b2") == 1
    assert task_executor.calls.count("b3") == 1
    assert result.context.variables["b1"] == {"node_id": "b1"}
    assert result.context.variables["b2"] == {"node_id": "b2"}
    assert result.context.variables["b3"] == {"node_id": "b3"}
    join_output = result.context.variables["join"]
    assert isinstance(join_output, dict)
    assert join_output.get("completed_branch_count") == 3


@pytest.mark.anyio
async def test_fork_join_any_policy_proceeds_on_first_branch() -> None:
    owner_id = uuid.uuid4()
    store = FakeWorkflowStore()
    definition = _fork_join_definition(
        owner_id, join_policy="any", cancel_remaining=True
    )
    run = await _seed(store, definition, owner_id)
    task_executor = FakeNodeExecutor(error_on={"b2", "b3"}, error_delay_seconds=0.05)
    executor = _executor(store, task_executor)

    result = await executor.execute_run(run.id, owner_id=owner_id)

    assert result.status is RunStatus.COMPLETED
    skipped = result.context.metadata.get("skipped_node_ids", [])
    assert isinstance(skipped, list)
    assert "b2" in skipped or "b3" in skipped


@pytest.mark.anyio
async def test_fork_join_count_policy_waits_for_n_branches() -> None:
    owner_id = uuid.uuid4()
    store = FakeWorkflowStore()
    definition = _fork_join_definition(
        owner_id, join_policy="count", count=2, cancel_remaining=True, branch_count=3
    )
    run = await _seed(store, definition, owner_id)
    task_executor = FakeNodeExecutor()
    executor = _executor(store, task_executor)

    result = await executor.execute_run(run.id, owner_id=owner_id)

    assert result.status is RunStatus.COMPLETED
    join_output = result.context.variables["join"]
    assert isinstance(join_output, dict)
    completed = join_output.get("completed_branch_count")
    assert isinstance(completed, int) and completed >= 2


@pytest.mark.anyio
async def test_branch_failure_isolates_and_fails_run() -> None:
    owner_id = uuid.uuid4()
    store = FakeWorkflowStore()
    definition = _fork_join_definition(owner_id)
    run = await _seed(store, definition, owner_id)
    task_executor = FakeNodeExecutor(error_on={"b2"})
    executor = _executor(store, task_executor)

    result = await executor.execute_run(run.id, owner_id=owner_id)

    assert result.status is RunStatus.FAILED
    assert "b2 failed" in (result.error or "")
    with_executions = await store.get_run_with_executions(result.id, owner_id=owner_id)
    assert with_executions is not None
    _, executions = with_executions
    failed = next(item for item in executions if item.node_id == "b2")
    assert failed.status is NodeStatus.FAILED


@pytest.mark.anyio
async def test_fork_exceeding_max_parallel_branches_fails_at_execution() -> None:
    owner_id = uuid.uuid4()
    store = FakeWorkflowStore()
    definition = _fork_join_definition(owner_id, branch_count=3)
    run = await _seed(store, definition, owner_id)
    task_executor = FakeNodeExecutor()
    executor = _executor(store, task_executor, max_parallel_branches=2)

    result = await executor.execute_run(run.id, owner_id=owner_id)

    assert result.status is RunStatus.FAILED
    assert "workflow_max_parallel_branches" in (result.error or "")


@pytest.mark.anyio
async def test_parallel_branches_execute_concurrently() -> None:
    owner_id = uuid.uuid4()
    store = FakeWorkflowStore()
    definition = _fork_join_definition(owner_id, branch_count=2)
    run = await _seed(store, definition, owner_id)
    task_executor = FakeNodeExecutor(delay_seconds=0.1)
    executor = _executor(store, task_executor)

    started = time.monotonic()
    result = await executor.execute_run(run.id, owner_id=owner_id)
    elapsed = time.monotonic() - started

    assert result.status is RunStatus.COMPLETED
    assert elapsed < 0.25


@pytest.mark.anyio
async def test_checkpoint_merge_retries_preserve_branch_outputs() -> None:
    owner_id = uuid.uuid4()
    store = ConflictAwareWorkflowStore()
    definition = _fork_join_definition(owner_id, branch_count=2)
    run = await _seed(store, definition, owner_id)
    task_executor = FakeNodeExecutor()
    executor = _executor(store, task_executor)

    result = await executor.execute_run(run.id, owner_id=owner_id)

    assert result.status is RunStatus.COMPLETED
    assert result.context.variables["b1"] == {"node_id": "b1"}
    assert result.context.variables["b2"] == {"node_id": "b2"}
    assert store._checkpoint_attempts > 1


class TestForkNodeExecutor:
    @pytest.mark.anyio
    async def test_returns_branch_targets(self) -> None:
        executor = ForkNodeExecutor(max_parallel_branches=4)
        node = _node("fork", NodeType.FORK, config={"join_node_id": "join"})
        request = NodeExecutionRequest(
            owner_id=uuid.uuid4(),
            execution_receipt_id="r:fork:1",
            outgoing_edges=(
                _edge("e1", "fork", "b1"),
                _edge("e2", "fork", "b2"),
            ),
        )
        output = await executor.execute(node, WorkflowContext(), request)
        assert output["branch_node_ids"] == ["b1", "b2"]
        assert output["join_node_id"] == "join"


class TestJoinNodeExecutor:
    @pytest.mark.anyio
    async def test_merges_completed_branch_outputs(self) -> None:
        executor = JoinNodeExecutor()
        node = _node(
            "join",
            NodeType.JOIN,
            config={"fork_node_id": "fork", "join_policy": "all"},
        )
        context = WorkflowContext(
            variables={
                "fork": {"branch_node_ids": ["b1", "b2"]},
                "b1": {"node_id": "b1"},
            }
        )
        output = await executor.execute(
            node,
            context,
            NodeExecutionRequest(
                owner_id=uuid.uuid4(), execution_receipt_id="r:join:1"
            ),
        )
        assert output["completed_branch_count"] == 1
        merged = output["merged_branch_outputs"]
        assert isinstance(merged, dict)
        assert merged["b1"] == {"node_id": "b1"}
