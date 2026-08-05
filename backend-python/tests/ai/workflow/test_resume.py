"""Tests for ``WorkflowManager.resume()`` crash-recovery entry (Phase 7)."""

from __future__ import annotations

import datetime
import uuid

import pytest

from app.ai.workflow.exceptions import WorkflowNotFoundError, WorkflowValidationError
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
    WorkflowNodeExecution,
    WorkflowRun,
)
from app.ai.workflow.nodes.parallel_node import ForkNodeExecutor, JoinNodeExecutor
from tests.ai.workflow.test_approval_node import FakeTaskExecutor, _await_scheduled
from tests.ai.workflow.test_interfaces import FakeWorkflowStore

_NOW = datetime.datetime.now(datetime.UTC)


def _fork_join_definition(owner_id: uuid.UUID) -> WorkflowDefinition:
    return WorkflowDefinition(
        id=uuid.uuid4(),
        owner_id=owner_id,
        name="Parallel Resume Workflow",
        status=DefinitionStatus.ACTIVE,
        entry_node_id="start",
        nodes=[
            WorkflowNode(id="start", type=NodeType.TASK, config={}),
            WorkflowNode(
                id="fork",
                type=NodeType.FORK,
                config={"join_node_id": "join"},
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
async def test_resume_running_run_schedules_executor() -> None:
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
    assert task_executor.calls

    first_call_count = len(task_executor.calls)
    resumed = await manager.resume(run.id, owner_id=owner_id)
    await _await_scheduled(manager)

    assert resumed.status is RunStatus.COMPLETED
    assert len(task_executor.calls) == first_call_count


@pytest.mark.anyio
async def test_resume_completed_run_is_no_op() -> None:
    owner_id = uuid.uuid4()
    store = FakeWorkflowStore()
    definition = await store.create_definition(_fork_join_definition(owner_id))
    manager = WorkflowManager(
        store,
        node_executors={
            NodeType.TASK: FakeTaskExecutor(),
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

    resumed = await manager.resume(run.id, owner_id=owner_id)

    assert resumed.status is RunStatus.COMPLETED


@pytest.mark.anyio
async def test_resume_waiting_approval_run_is_rejected() -> None:
    owner_id = uuid.uuid4()
    store = FakeWorkflowStore()
    run_id = uuid.uuid4()
    await store.create_run(
        WorkflowRun(
            id=run_id,
            workflow_definition_id=uuid.uuid4(),
            owner_id=owner_id,
            idempotency_key="key-1",
            status=RunStatus.WAITING_APPROVAL,
            context=WorkflowContext(),
            current_node_ids=["approve"],
            checkpoint_version=1,
            created_at=_NOW,
            updated_at=_NOW,
            started_at=_NOW,
        )
    )
    manager = WorkflowManager(store)

    with pytest.raises(WorkflowValidationError):
        await manager.resume(run_id, owner_id=owner_id)


@pytest.mark.anyio
async def test_resume_non_owner_is_rejected() -> None:
    owner_id = uuid.uuid4()
    store = FakeWorkflowStore()
    run_id = uuid.uuid4()
    await store.create_run(
        WorkflowRun(
            id=run_id,
            workflow_definition_id=uuid.uuid4(),
            owner_id=owner_id,
            idempotency_key="key-1",
            status=RunStatus.RUNNING,
            context=WorkflowContext(),
            current_node_ids=["left"],
            checkpoint_version=2,
            created_at=_NOW,
            updated_at=_NOW,
            started_at=_NOW,
        )
    )
    manager = WorkflowManager(store)

    with pytest.raises(WorkflowNotFoundError):
        await manager.resume(run_id, owner_id=uuid.uuid4())


@pytest.mark.anyio
async def test_resume_preserves_parallel_branch_checkpoint() -> None:
    owner_id = uuid.uuid4()
    store = FakeWorkflowStore()
    definition = await store.create_definition(_fork_join_definition(owner_id))
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
                "fork": {"forked": True},
                "left": {"branch": "left"},
            }
        ),
        current_node_ids=[],
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
            node_id="start",
            node_type=NodeType.TASK,
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
            status=NodeStatus.SUCCEEDED,
            completed_at=_NOW,
        )
    )
    await store.append_node_execution(
        WorkflowNodeExecution(
            id=uuid.uuid4(),
            run_id=run_id,
            node_id="fork",
            node_type=NodeType.FORK,
            status=NodeStatus.SUCCEEDED,
            completed_at=_NOW,
        )
    )

    task_executor = FakeTaskExecutor()
    manager = WorkflowManager(
        store,
        node_executors={
            NodeType.TASK: task_executor,
            NodeType.FORK: ForkNodeExecutor(max_parallel_branches=8),
            NodeType.JOIN: JoinNodeExecutor(),
        },
    )

    await manager.resume(run_id, owner_id=owner_id)
    await _await_scheduled(manager)
    final = await manager.get_run(run_id, owner_id=owner_id)
    assert final is not None
    assert final.status is RunStatus.COMPLETED
    assert task_executor.calls == ["right"]
    assert "left" not in task_executor.calls
    assert "start" not in task_executor.calls
