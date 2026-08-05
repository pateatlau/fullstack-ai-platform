"""Tests for workflow crash recovery and duration guard (Phase 8)."""

from __future__ import annotations

import asyncio
import datetime
import uuid
from dataclasses import dataclass, field

import pytest

from app.ai.tools.registry import ToolRegistry
from app.ai.tools.schemas import ToolDefinition, ToolExecutionContext, ToolResult
from app.ai.workflow.engine.background import (
    is_run_active,
    reconcile_orphaned_runs,
    schedule_run_task,
)
from app.ai.workflow.engine.executor import WorkflowExecutor
from app.ai.workflow.manager import WorkflowManager
from app.ai.workflow.models import (
    DefinitionStatus,
    NodeRetryPolicy,
    NodeStatus,
    NodeType,
    RunStatus,
    WorkflowContext,
    WorkflowDefinition,
    WorkflowEdge,
    WorkflowNode,
    WorkflowNodeExecution,
    WorkflowRun,
)
from app.ai.workflow.nodes.base import NodeExecutionRequest, WorkflowNodeExecutionError
from app.ai.workflow.nodes.parallel_node import ForkNodeExecutor, JoinNodeExecutor
from app.ai.workflow.retry.recovery import execution_interrupted_error
from app.core.config import Settings
from tests.ai.workflow.test_approval_node import FakeTaskExecutor, _await_scheduled
from tests.ai.workflow.test_interfaces import FakeWorkflowStore

_NOW = datetime.datetime.now(datetime.UTC)


@dataclass
class ReceiptAwareToolHandler:
    execution_receipt_aware: bool = True
    calls: list[str | None] = field(default_factory=list)

    async def execute(
        self, args: dict[str, object], context: ToolExecutionContext
    ) -> ToolResult:
        del args
        self.calls.append(context.execution_receipt_id)
        return ToolResult(success=True, data={"ok": True})


@dataclass
class NonReceiptAwareToolHandler:
    calls: list[str | None] = field(default_factory=list)

    async def execute(
        self, args: dict[str, object], context: ToolExecutionContext
    ) -> ToolResult:
        del args
        self.calls.append(context.execution_receipt_id)
        return ToolResult(success=True, data={"ok": True})


class FlakyTaskExecutor:
    def __init__(self, *, fail_times: int = 0) -> None:
        self.fail_times = fail_times
        self.calls = 0
        self.attempts: list[int] = []

    async def execute(
        self,
        node: WorkflowNode,
        context: WorkflowContext,
        request: NodeExecutionRequest,
    ) -> dict[str, object]:
        del context
        self.calls += 1
        attempt = int(request.execution_receipt_id.rsplit(":", maxsplit=1)[-1])
        self.attempts.append(attempt)
        if self.calls <= self.fail_times:
            raise WorkflowNodeExecutionError("temporary", error_code="timeout")
        return {"ok": True}


def _linear_definition(
    owner_id: uuid.UUID, *, node_id: str = "start"
) -> WorkflowDefinition:
    return WorkflowDefinition(
        id=uuid.uuid4(),
        owner_id=owner_id,
        name="Crash Recovery Workflow",
        status=DefinitionStatus.ACTIVE,
        entry_node_id=node_id,
        nodes=[
            WorkflowNode(id=node_id, type=NodeType.TASK, config={}),
            WorkflowNode(id="end", type=NodeType.TERMINAL, config={}),
        ],
        edges=[WorkflowEdge(id="e1", from_node_id=node_id, to_node_id="end")],
        created_at=_NOW,
        updated_at=_NOW,
    )


def _fork_join_definition(owner_id: uuid.UUID) -> WorkflowDefinition:
    return WorkflowDefinition(
        id=uuid.uuid4(),
        owner_id=owner_id,
        name="Parallel Crash Workflow",
        status=DefinitionStatus.ACTIVE,
        entry_node_id="start",
        nodes=[
            WorkflowNode(id="start", type=NodeType.TASK, config={}),
            WorkflowNode(
                id="fork", type=NodeType.FORK, config={"join_node_id": "join"}
            ),
            WorkflowNode(id="left", type=NodeType.TASK, config={}),
            WorkflowNode(id="right", type=NodeType.TASK, config={}),
            WorkflowNode(
                id="join",
                type=NodeType.JOIN,
                config={"fork_node_id": "fork", "join_policy": "all"},
            ),
            WorkflowNode(id="end", type=NodeType.TERMINAL, config={}),
        ],
        edges=[
            WorkflowEdge(id="e1", from_node_id="start", to_node_id="fork"),
            WorkflowEdge(id="e2", from_node_id="fork", to_node_id="left"),
            WorkflowEdge(id="e3", from_node_id="fork", to_node_id="right"),
            WorkflowEdge(id="e4", from_node_id="left", to_node_id="join"),
            WorkflowEdge(id="e5", from_node_id="right", to_node_id="join"),
            WorkflowEdge(id="e6", from_node_id="join", to_node_id="end"),
        ],
        created_at=_NOW,
        updated_at=_NOW,
    )


@pytest.mark.anyio
async def test_crash_recovery_rehydrates_without_reexecuting_succeeded_nodes() -> None:
    owner_id = uuid.uuid4()
    store = FakeWorkflowStore()
    definition = await store.create_definition(_fork_join_definition(owner_id))
    task_executor = FakeTaskExecutor()
    manager = WorkflowManager(
        store,
        node_executors={
            NodeType.TASK: task_executor,
            NodeType.FORK: ForkNodeExecutor(max_parallel_branches=8),
            NodeType.JOIN: JoinNodeExecutor(),
        },
    )

    run = await manager.start_run(
        definition.id, owner_id=owner_id, idempotency_key="key-1"
    )
    await _await_scheduled(manager)
    completed = await manager.get_run(run.id, owner_id=owner_id)
    assert completed is not None
    assert completed.status is RunStatus.COMPLETED

    await store.checkpoint_run(
        completed.model_copy(
            update={
                "status": RunStatus.RUNNING,
                "completed_at": None,
                "checkpoint_version": completed.checkpoint_version + 1,
            }
        ),
        expected_checkpoint_version=completed.checkpoint_version,
    )
    manager._last_scheduled_run_task = None
    first_call_count = len(task_executor.calls)

    await manager.resume(run.id, owner_id=owner_id)
    await _await_scheduled(manager)

    final = await manager.get_run(run.id, owner_id=owner_id)
    assert final is not None
    assert final.status is RunStatus.COMPLETED
    assert len(task_executor.calls) == first_call_count


@pytest.mark.anyio
async def test_crash_mid_task_with_receipt_aware_tool_does_not_duplicate_side_effects() -> (
    None
):
    owner_id = uuid.uuid4()
    store = FakeWorkflowStore()
    handler = ReceiptAwareToolHandler()
    registry = ToolRegistry()
    registry.register(
        ToolDefinition(
            name="receipt_tool",
            description="Receipt-aware test tool",
            parameters={"type": "object", "properties": {}},
        ),
        handler,
    )

    definition = await store.create_definition(
        WorkflowDefinition(
            id=uuid.uuid4(),
            owner_id=owner_id,
            name="Receipt Tool Workflow",
            status=DefinitionStatus.ACTIVE,
            entry_node_id="task",
            nodes=[
                WorkflowNode(
                    id="task",
                    type=NodeType.TASK,
                    config={
                        "tool_name": "receipt_tool",
                        "arguments_template": {},
                    },
                ),
                WorkflowNode(id="end", type=NodeType.TERMINAL, config={}),
            ],
            edges=[WorkflowEdge(id="e1", from_node_id="task", to_node_id="end")],
            created_at=_NOW,
            updated_at=_NOW,
        )
    )

    run_id = uuid.uuid4()
    receipt = f"{run_id}:task:1"
    await store.create_run(
        WorkflowRun(
            id=run_id,
            workflow_definition_id=definition.id,
            owner_id=owner_id,
            idempotency_key="key-1",
            status=RunStatus.RUNNING,
            context=WorkflowContext(),
            current_node_ids=["task"],
            checkpoint_version=1,
            created_at=_NOW,
            updated_at=_NOW,
            started_at=_NOW,
        )
    )
    await store.append_node_execution(
        WorkflowNodeExecution(
            id=uuid.uuid4(),
            run_id=run_id,
            node_id="task",
            node_type=NodeType.TASK,
            attempt=1,
            status=NodeStatus.RUNNING,
            input={
                "execution_receipt_id": receipt,
                "config": definition.nodes[0].config,
            },
            started_at=_NOW,
        )
    )

    from app.ai.workflow.nodes.task_node import TaskNodeExecutor
    from app.ai.tools.executor import ToolExecutor

    settings = Settings(openai_api_key="test-key")
    executor = WorkflowExecutor(
        store,
        {
            NodeType.TASK: TaskNodeExecutor(
                ToolExecutor(registry=registry, settings=settings)
            )
        },
        settings=settings,
        tool_registry=registry,
    )

    final = await executor.execute_run(run_id, owner_id=owner_id)

    assert final.status is RunStatus.COMPLETED
    assert handler.calls == [receipt]


@pytest.mark.anyio
async def test_crash_mid_task_without_receipt_aware_tool_fails_closed() -> None:
    owner_id = uuid.uuid4()
    store = FakeWorkflowStore()
    handler = NonReceiptAwareToolHandler()
    registry = ToolRegistry()
    registry.register(
        ToolDefinition(
            name="plain_tool",
            description="Non receipt-aware test tool",
            parameters={"type": "object", "properties": {}},
        ),
        handler,
    )

    definition = await store.create_definition(
        _linear_definition(owner_id, node_id="task").model_copy(
            update={
                "nodes": [
                    WorkflowNode(
                        id="task",
                        type=NodeType.TASK,
                        config={
                            "tool_name": "plain_tool",
                            "arguments_template": {},
                        },
                    ),
                    WorkflowNode(id="end", type=NodeType.TERMINAL, config={}),
                ],
                "entry_node_id": "task",
            }
        )
    )

    run_id = uuid.uuid4()
    await store.create_run(
        WorkflowRun(
            id=run_id,
            workflow_definition_id=definition.id,
            owner_id=owner_id,
            idempotency_key="key-1",
            status=RunStatus.RUNNING,
            context=WorkflowContext(),
            current_node_ids=["task"],
            checkpoint_version=1,
            created_at=_NOW,
            updated_at=_NOW,
            started_at=_NOW,
        )
    )
    await store.append_node_execution(
        WorkflowNodeExecution(
            id=uuid.uuid4(),
            run_id=run_id,
            node_id="task",
            node_type=NodeType.TASK,
            attempt=1,
            status=NodeStatus.RUNNING,
            input={
                "execution_receipt_id": f"{run_id}:task:1",
                "config": definition.nodes[0].config,
            },
            started_at=_NOW,
        )
    )

    from app.ai.workflow.nodes.task_node import TaskNodeExecutor
    from app.ai.tools.executor import ToolExecutor

    settings = Settings(openai_api_key="test-key")
    executor = WorkflowExecutor(
        store,
        {
            NodeType.TASK: TaskNodeExecutor(
                ToolExecutor(registry=registry, settings=settings)
            )
        },
        settings=settings,
        tool_registry=registry,
    )

    final = await executor.execute_run(run_id, owner_id=owner_id)

    assert final.status is RunStatus.FAILED
    assert "not execution-receipt-aware" in (final.error or "")
    assert handler.calls == []


@pytest.mark.anyio
async def test_mid_fork_join_crash_recovery_completes_remaining_branch() -> None:
    owner_id = uuid.uuid4()
    store = FakeWorkflowStore()
    definition = await store.create_definition(_fork_join_definition(owner_id))
    right_config = {
        "tool_name": "receipt_tool",
        "arguments_template": {},
    }
    updated_nodes = [
        (
            node.model_copy(update={"config": right_config})
            if node.id == "right"
            else node
        )
        for node in definition.nodes
    ]
    definition = definition.model_copy(update={"nodes": updated_nodes})
    store._definitions[definition.id] = definition
    run_id = uuid.uuid4()
    checkpointed = WorkflowRun(
        id=run_id,
        workflow_definition_id=definition.id,
        owner_id=owner_id,
        idempotency_key="key-1",
        status=RunStatus.RUNNING,
        context=WorkflowContext(
            variables={
                "start": {"ok": True},
                "left": {"branch": "left"},
            }
        ),
        current_node_ids=["right"],
        checkpoint_version=3,
        created_at=_NOW,
        updated_at=_NOW,
        started_at=_NOW,
    )
    await store.create_run(checkpointed)
    await store.append_node_execution(
        WorkflowNodeExecution(
            id=uuid.uuid4(),
            run_id=run_id,
            node_id="fork",
            node_type=NodeType.FORK,
            attempt=1,
            status=NodeStatus.SUCCEEDED,
            completed_at=_NOW,
        )
    )
    await store.append_node_execution(
        WorkflowNodeExecution(
            id=uuid.uuid4(),
            run_id=run_id,
            node_id="left",
            node_type=NodeType.TASK,
            attempt=1,
            status=NodeStatus.SUCCEEDED,
            completed_at=_NOW,
        )
    )
    handler = ReceiptAwareToolHandler()
    registry = ToolRegistry()
    registry.register(
        ToolDefinition(
            name="receipt_tool",
            description="Receipt-aware test tool",
            parameters={"type": "object", "properties": {}},
        ),
        handler,
    )
    await store.append_node_execution(
        WorkflowNodeExecution(
            id=uuid.uuid4(),
            run_id=run_id,
            node_id="right",
            node_type=NodeType.TASK,
            attempt=1,
            status=NodeStatus.RUNNING,
            input={
                "execution_receipt_id": f"{run_id}:right:1",
                "config": right_config,
            },
            started_at=_NOW,
        )
    )

    from app.ai.tools.executor import ToolExecutor
    from app.ai.workflow.nodes.task_node import TaskNodeExecutor

    settings = Settings(openai_api_key="test-key")
    manager = WorkflowManager(
        store,
        settings=settings,
        node_executors={
            NodeType.TASK: TaskNodeExecutor(
                ToolExecutor(registry=registry, settings=settings)
            ),
            NodeType.FORK: ForkNodeExecutor(max_parallel_branches=8),
            NodeType.JOIN: JoinNodeExecutor(),
        },
        tool_registry=registry,
    )

    await manager.resume(run_id, owner_id=owner_id)
    await _await_scheduled(manager)

    final = await manager.get_run(run_id, owner_id=owner_id)
    assert final is not None
    assert final.status is RunStatus.COMPLETED
    assert handler.calls == [f"{run_id}:right:1"]
    assert "left" in final.context.variables
    assert "right" in final.context.variables


@pytest.mark.anyio
async def test_retry_tracks_attempt_rows(monkeypatch: pytest.MonkeyPatch) -> None:
    owner_id = uuid.uuid4()
    store = FakeWorkflowStore()
    definition = await store.create_definition(
        _linear_definition(owner_id).model_copy(
            update={
                "nodes": [
                    WorkflowNode(
                        id="start",
                        type=NodeType.TASK,
                        config={},
                        retry_policy=NodeRetryPolicy(
                            max_retries=2, base_delay_seconds=0.0
                        ),
                    ),
                    WorkflowNode(id="end", type=NodeType.TERMINAL, config={}),
                ]
            }
        )
    )
    run = await store.create_run(
        WorkflowRun(
            id=uuid.uuid4(),
            workflow_definition_id=definition.id,
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
    )

    async def noop_sleep(_: float) -> None:
        return None

    monkeypatch.setattr("app.ai.workflow.engine.executor.asyncio.sleep", noop_sleep)

    executor = WorkflowExecutor(
        store,
        {NodeType.TASK: FlakyTaskExecutor(fail_times=2)},
        settings=Settings(openai_api_key="test-key", workflow_max_node_retries=2),
    )
    final = await executor.execute_run(run.id, owner_id=owner_id)

    assert final.status is RunStatus.COMPLETED
    with_executions = await store.get_run_with_executions(run.id, owner_id=owner_id)
    assert with_executions is not None
    _, executions = with_executions
    start_attempts = [item for item in executions if item.node_id == "start"]
    assert [item.attempt for item in start_attempts] == [1, 2, 3]
    assert start_attempts[0].status is NodeStatus.FAILED
    assert start_attempts[-1].status is NodeStatus.SUCCEEDED


@pytest.mark.anyio
async def test_run_duration_guard_fails_long_running_workflow() -> None:
    owner_id = uuid.uuid4()
    store = FakeWorkflowStore()
    definition = await store.create_definition(_linear_definition(owner_id))
    started = _NOW - datetime.timedelta(minutes=61)
    run = await store.create_run(
        WorkflowRun(
            id=uuid.uuid4(),
            workflow_definition_id=definition.id,
            owner_id=owner_id,
            idempotency_key="key-1",
            status=RunStatus.RUNNING,
            context=WorkflowContext(),
            current_node_ids=[],
            checkpoint_version=0,
            created_at=_NOW,
            updated_at=_NOW,
            started_at=started,
        )
    )

    executor = WorkflowExecutor(
        store,
        {NodeType.TASK: FakeTaskExecutor()},
        settings=Settings(
            openai_api_key="test-key", workflow_max_run_duration_minutes=60
        ),
    )
    final = await executor.execute_run(run.id, owner_id=owner_id)

    assert final.status is RunStatus.FAILED
    assert "duration limit" in (final.error or "").lower()


@pytest.mark.anyio
async def test_startup_reconciliation_schedules_orphaned_running_runs() -> None:
    owner_id = uuid.uuid4()
    store = FakeWorkflowStore()
    definition = await store.create_definition(_linear_definition(owner_id))
    run = await store.create_run(
        WorkflowRun(
            id=uuid.uuid4(),
            workflow_definition_id=definition.id,
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
    )

    scheduled: list[uuid.UUID] = []

    async def schedule(run_id: uuid.UUID, owner: uuid.UUID) -> None:
        del owner
        scheduled.append(run_id)

    count = await reconcile_orphaned_runs(store, schedule_run=schedule)

    assert count == 1
    assert scheduled == [run.id]
    assert not is_run_active(run.id)


@pytest.mark.anyio
async def test_schedule_run_task_marks_active_before_coroutine_starts() -> None:
    run_id = uuid.uuid4()
    gate = asyncio.Event()

    async def blocked() -> None:
        gate.set()
        await asyncio.Event().wait()

    task = schedule_run_task(blocked(), run_id=run_id)
    assert is_run_active(run_id)
    await gate.wait()
    assert is_run_active(run_id)

    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert not is_run_active(run_id)


@pytest.mark.anyio
async def test_interrupted_execution_is_marked_execution_interrupted() -> None:
    owner_id = uuid.uuid4()
    store = FakeWorkflowStore()
    handler = ReceiptAwareToolHandler()
    registry = ToolRegistry()
    registry.register(
        ToolDefinition(
            name="receipt_tool",
            description="Receipt-aware test tool",
            parameters={"type": "object", "properties": {}},
        ),
        handler,
    )
    definition = await store.create_definition(
        WorkflowDefinition(
            id=uuid.uuid4(),
            owner_id=owner_id,
            name="Receipt Tool Workflow",
            status=DefinitionStatus.ACTIVE,
            entry_node_id="task",
            nodes=[
                WorkflowNode(
                    id="task",
                    type=NodeType.TASK,
                    config={
                        "tool_name": "receipt_tool",
                        "arguments_template": {},
                    },
                ),
                WorkflowNode(id="end", type=NodeType.TERMINAL, config={}),
            ],
            edges=[WorkflowEdge(id="e1", from_node_id="task", to_node_id="end")],
            created_at=_NOW,
            updated_at=_NOW,
        )
    )
    run_id = uuid.uuid4()
    execution_id = uuid.uuid4()
    await store.create_run(
        WorkflowRun(
            id=run_id,
            workflow_definition_id=definition.id,
            owner_id=owner_id,
            idempotency_key="key-1",
            status=RunStatus.RUNNING,
            context=WorkflowContext(),
            current_node_ids=["task"],
            checkpoint_version=1,
            created_at=_NOW,
            updated_at=_NOW,
            started_at=_NOW,
        )
    )
    await store.append_node_execution(
        WorkflowNodeExecution(
            id=execution_id,
            run_id=run_id,
            node_id="task",
            node_type=NodeType.TASK,
            attempt=1,
            status=NodeStatus.RUNNING,
            input={
                "execution_receipt_id": f"{run_id}:task:1",
                "config": definition.nodes[0].config,
            },
            started_at=_NOW,
        )
    )

    from app.ai.workflow.nodes.task_node import TaskNodeExecutor
    from app.ai.tools.executor import ToolExecutor

    settings = Settings(openai_api_key="test-key")
    executor = WorkflowExecutor(
        store,
        {
            NodeType.TASK: TaskNodeExecutor(
                ToolExecutor(registry=registry, settings=settings)
            )
        },
        settings=settings,
        tool_registry=registry,
    )
    await executor.execute_run(run_id, owner_id=owner_id)

    with_executions = await store.get_run_with_executions(run_id, owner_id=owner_id)
    assert with_executions is not None
    _, executions = with_executions
    interrupted = next(item for item in executions if item.id == execution_id)
    assert interrupted.status is NodeStatus.FAILED
    assert interrupted.error == execution_interrupted_error()
