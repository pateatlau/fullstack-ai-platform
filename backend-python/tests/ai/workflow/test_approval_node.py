"""Tests for approval nodes, decisions, and pause/resume (Phase 7)."""

from __future__ import annotations

import asyncio
import datetime
import uuid

import pytest

from app.ai.hitl.models import ApprovalKind, ApprovalStatus
from app.ai.tools.executor import ToolExecutor
from app.ai.tools.registry import ToolRegistry
from app.ai.tools.stubs.echo import (
    ECHO_TOOL_DEFINITION,
    ECHO_TOOL_NAME,
    EchoToolHandler,
)
from app.ai.workflow.engine.executor import WorkflowExecutor
from app.ai.workflow.exceptions import (
    WorkflowDecisionConflictError,
    WorkflowNotFoundError,
)
from app.ai.workflow.manager import WorkflowManager
from app.ai.workflow.models import (
    ApprovalDecision,
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
from app.ai.workflow.nodes.approval_node import ApprovalNodeExecutor
from app.ai.workflow.nodes.base import NodeExecutionRequest, WorkflowNodeExecutionError
from app.ai.workflow.nodes.parallel_node import ForkNodeExecutor, JoinNodeExecutor
from app.ai.workflow.nodes.task_node import TaskNodeExecutor
from app.core.config import Settings
from tests.ai.workflow.test_interfaces import FakeWorkflowStore

_NOW = datetime.datetime.now(datetime.UTC)


class FakeTaskExecutor:
    def __init__(self, *, output: dict[str, object] | None = None) -> None:
        self.output: dict[str, object] = output if output is not None else {"ok": True}
        self.calls: list[str] = []

    async def execute(
        self,
        node: WorkflowNode,
        context: WorkflowContext,
        request: NodeExecutionRequest,
    ) -> dict[str, object]:
        del context, request
        self.calls.append(node.id)
        return self.output


def _edge(
    edge_id: str,
    from_node_id: str,
    to_node_id: str,
) -> WorkflowEdge:
    return WorkflowEdge(id=edge_id, from_node_id=from_node_id, to_node_id=to_node_id)


def _definition(
    *,
    owner_id: uuid.UUID,
    nodes: list[WorkflowNode],
    edges: list[WorkflowEdge],
) -> WorkflowDefinition:
    return WorkflowDefinition(
        id=uuid.uuid4(),
        owner_id=owner_id,
        name="Approval Workflow",
        status=DefinitionStatus.ACTIVE,
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


async def _await_scheduled(manager: WorkflowManager) -> None:
    if manager._last_scheduled_run_task is not None:
        await manager._last_scheduled_run_task


def _approval_linear_definition(owner_id: uuid.UUID) -> WorkflowDefinition:
    return _definition(
        owner_id=owner_id,
        nodes=[
            WorkflowNode(id="start", type=NodeType.TASK, config={}),
            WorkflowNode(
                id="approve",
                type=NodeType.APPROVAL,
                config={"approved_edge_id": "approved"},
            ),
            WorkflowNode(id="after", type=NodeType.TASK, config={}),
            WorkflowNode(id="end", type=NodeType.TERMINAL, config={}),
        ],
        edges=[
            _edge("e1", "start", "approve"),
            _edge("approved", "approve", "after"),
            _edge("e3", "after", "end"),
        ],
    )


def _approval_branch_definition(owner_id: uuid.UUID) -> WorkflowDefinition:
    return _definition(
        owner_id=owner_id,
        nodes=[
            WorkflowNode(id="start", type=NodeType.TASK, config={}),
            WorkflowNode(
                id="approve",
                type=NodeType.APPROVAL,
                config={
                    "approved_edge_id": "approved",
                    "rejected_edge_id": "rejected",
                },
            ),
            WorkflowNode(id="approved_task", type=NodeType.TASK, config={}),
            WorkflowNode(id="rejected_task", type=NodeType.TASK, config={}),
            WorkflowNode(id="end", type=NodeType.TERMINAL, config={}),
        ],
        edges=[
            _edge("e1", "start", "approve"),
            _edge("approved", "approve", "approved_task"),
            _edge("rejected", "approve", "rejected_task"),
            _edge("e3", "approved_task", "end"),
            _edge("e4", "rejected_task", "end"),
        ],
    )


@pytest.mark.anyio
async def test_run_pauses_at_approval_node() -> None:
    owner_id = uuid.uuid4()
    store = FakeWorkflowStore()
    definition = await store.create_definition(_approval_linear_definition(owner_id))
    run = await store.create_run(_run(owner_id=owner_id, definition_id=definition.id))
    task_executor = FakeTaskExecutor()
    executor = WorkflowExecutor(
        store,
        {NodeType.TASK: task_executor, NodeType.APPROVAL: ApprovalNodeExecutor()},
    )

    result = await executor.execute_run(run.id, owner_id=owner_id)

    assert result.status is RunStatus.WAITING_APPROVAL
    assert result.current_node_ids == ["approve"]
    assert task_executor.calls == ["start"]
    with_executions = await store.get_run_with_executions(run.id, owner_id=owner_id)
    assert with_executions is not None
    approval_execution = next(
        execution for execution in with_executions[1] if execution.node_id == "approve"
    )
    assert approval_execution.status is NodeStatus.WAITING_APPROVAL


@pytest.mark.anyio
async def test_approve_and_resume_completes_run() -> None:
    owner_id = uuid.uuid4()
    store = FakeWorkflowStore()
    definition = await store.create_definition(_approval_linear_definition(owner_id))
    task_executor = FakeTaskExecutor()
    manager = WorkflowManager(
        store,
        node_executors={
            NodeType.TASK: task_executor,
            NodeType.APPROVAL: ApprovalNodeExecutor(),
        },
    )

    run = await manager.start_run(
        definition.id, owner_id=owner_id, idempotency_key="key-1"
    )
    await _await_scheduled(manager)
    paused = await manager.get_run(run.id, owner_id=owner_id)
    assert paused is not None
    assert paused.status is RunStatus.WAITING_APPROVAL

    with_executions = await store.get_run_with_executions(run.id, owner_id=owner_id)
    assert with_executions is not None
    approval_execution = next(
        execution for execution in with_executions[1] if execution.node_id == "approve"
    )

    continued, _ = await manager.apply_decision(
        run.id,
        approval_execution.id,
        owner_id=owner_id,
        decision=ApprovalDecision.APPROVED,
    )
    await _await_scheduled(manager)
    final = await manager.get_run(run.id, owner_id=owner_id)
    assert final is not None
    assert final.status is RunStatus.COMPLETED
    assert task_executor.calls == ["start", "after"]
    approve_output = final.context.variables["approve"]
    assert isinstance(approve_output, dict)
    assert approve_output["decision"] == "approved"
    assert continued.status is RunStatus.RUNNING


@pytest.mark.anyio
async def test_reject_with_rejected_edge_follows_branch() -> None:
    owner_id = uuid.uuid4()
    store = FakeWorkflowStore()
    definition = await store.create_definition(_approval_branch_definition(owner_id))
    task_executor = FakeTaskExecutor()
    manager = WorkflowManager(
        store,
        node_executors={
            NodeType.TASK: task_executor,
            NodeType.APPROVAL: ApprovalNodeExecutor(),
        },
    )

    run = await manager.start_run(
        definition.id, owner_id=owner_id, idempotency_key="key-1"
    )
    await _await_scheduled(manager)
    with_executions = await store.get_run_with_executions(run.id, owner_id=owner_id)
    assert with_executions is not None
    approval_execution = next(
        execution for execution in with_executions[1] if execution.node_id == "approve"
    )

    continued, _ = await manager.apply_decision(
        run.id,
        approval_execution.id,
        owner_id=owner_id,
        decision=ApprovalDecision.REJECTED,
    )
    await _await_scheduled(manager)
    final = await manager.get_run(run.id, owner_id=owner_id)
    assert final is not None
    assert final.status is RunStatus.COMPLETED
    assert task_executor.calls == ["start", "rejected_task"]
    assert "approved_task" not in task_executor.calls
    approve_output = final.context.variables["approve"]
    assert isinstance(approve_output, dict)
    assert approve_output["decision"] == "rejected"
    assert continued.status is RunStatus.RUNNING


@pytest.mark.anyio
async def test_reject_without_rejected_edge_fails_run() -> None:
    owner_id = uuid.uuid4()
    store = FakeWorkflowStore()
    definition = await store.create_definition(_approval_linear_definition(owner_id))
    manager = WorkflowManager(
        store,
        node_executors={
            NodeType.TASK: FakeTaskExecutor(),
            NodeType.APPROVAL: ApprovalNodeExecutor(),
        },
    )

    run = await manager.start_run(
        definition.id, owner_id=owner_id, idempotency_key="key-1"
    )
    await _await_scheduled(manager)
    with_executions = await store.get_run_with_executions(run.id, owner_id=owner_id)
    assert with_executions is not None
    approval_execution = next(
        execution for execution in with_executions[1] if execution.node_id == "approve"
    )

    failed, _ = await manager.apply_decision(
        run.id,
        approval_execution.id,
        owner_id=owner_id,
        decision=ApprovalDecision.REJECTED,
    )

    assert failed.status is RunStatus.FAILED
    assert failed.error is not None


@pytest.mark.anyio
async def test_duplicate_matching_decision_is_idempotent() -> None:
    owner_id = uuid.uuid4()
    store = FakeWorkflowStore()
    definition = await store.create_definition(_approval_linear_definition(owner_id))
    manager = WorkflowManager(
        store,
        node_executors={
            NodeType.TASK: FakeTaskExecutor(),
            NodeType.APPROVAL: ApprovalNodeExecutor(),
        },
    )

    run = await manager.start_run(
        definition.id, owner_id=owner_id, idempotency_key="key-1"
    )
    await _await_scheduled(manager)
    with_executions = await store.get_run_with_executions(run.id, owner_id=owner_id)
    assert with_executions is not None
    approval_execution = next(
        execution for execution in with_executions[1] if execution.node_id == "approve"
    )

    first, _ = await manager.apply_decision(
        run.id,
        approval_execution.id,
        owner_id=owner_id,
        decision=ApprovalDecision.APPROVED,
    )
    assert first.status is RunStatus.RUNNING
    await _await_scheduled(manager)
    second, _ = await manager.apply_decision(
        run.id,
        approval_execution.id,
        owner_id=owner_id,
        decision=ApprovalDecision.APPROVED,
    )

    assert second.status is RunStatus.COMPLETED


@pytest.mark.anyio
async def test_conflicting_decision_raises_conflict() -> None:
    owner_id = uuid.uuid4()
    store = FakeWorkflowStore()
    definition = await store.create_definition(_approval_linear_definition(owner_id))
    manager = WorkflowManager(
        store,
        node_executors={
            NodeType.TASK: FakeTaskExecutor(),
            NodeType.APPROVAL: ApprovalNodeExecutor(),
        },
    )

    run = await manager.start_run(
        definition.id, owner_id=owner_id, idempotency_key="key-1"
    )
    await _await_scheduled(manager)
    with_executions = await store.get_run_with_executions(run.id, owner_id=owner_id)
    assert with_executions is not None
    approval_execution = next(
        execution for execution in with_executions[1] if execution.node_id == "approve"
    )

    await manager.apply_decision(
        run.id,
        approval_execution.id,
        owner_id=owner_id,
        decision=ApprovalDecision.APPROVED,
    )
    await _await_scheduled(manager)

    with pytest.raises(WorkflowDecisionConflictError):
        await manager.apply_decision(
            run.id,
            approval_execution.id,
            owner_id=owner_id,
            decision=ApprovalDecision.REJECTED,
        )


@pytest.mark.anyio
async def test_non_owner_cannot_apply_decision() -> None:
    owner_id = uuid.uuid4()
    other_owner = uuid.uuid4()
    store = FakeWorkflowStore()
    definition = await store.create_definition(_approval_linear_definition(owner_id))
    manager = WorkflowManager(
        store,
        node_executors={
            NodeType.TASK: FakeTaskExecutor(),
            NodeType.APPROVAL: ApprovalNodeExecutor(),
        },
    )

    run = await manager.start_run(
        definition.id, owner_id=owner_id, idempotency_key="key-1"
    )
    await _await_scheduled(manager)
    with_executions = await store.get_run_with_executions(run.id, owner_id=owner_id)
    assert with_executions is not None
    approval_execution = next(
        execution for execution in with_executions[1] if execution.node_id == "approve"
    )

    with pytest.raises(WorkflowNotFoundError):
        await manager.apply_decision(
            run.id,
            approval_execution.id,
            owner_id=other_owner,
            decision=ApprovalDecision.APPROVED,
        )


@pytest.mark.anyio
async def test_concurrent_decisions_only_one_wins() -> None:
    owner_id = uuid.uuid4()
    store = FakeWorkflowStore()
    definition = await store.create_definition(_approval_branch_definition(owner_id))
    manager = WorkflowManager(
        store,
        node_executors={
            NodeType.TASK: FakeTaskExecutor(),
            NodeType.APPROVAL: ApprovalNodeExecutor(),
        },
    )

    run = await manager.start_run(
        definition.id, owner_id=owner_id, idempotency_key="key-1"
    )
    await _await_scheduled(manager)
    with_executions = await store.get_run_with_executions(run.id, owner_id=owner_id)
    assert with_executions is not None
    approval_execution = next(
        execution for execution in with_executions[1] if execution.node_id == "approve"
    )

    results = await asyncio.gather(
        manager.apply_decision(
            run.id,
            approval_execution.id,
            owner_id=owner_id,
            decision=ApprovalDecision.APPROVED,
        ),
        manager.apply_decision(
            run.id,
            approval_execution.id,
            owner_id=owner_id,
            decision=ApprovalDecision.REJECTED,
        ),
        return_exceptions=True,
    )

    errors = [item for item in results if isinstance(item, Exception)]
    successes = [item for item in results if not isinstance(item, Exception)]
    assert len(successes) == 1
    assert len(errors) == 1
    assert isinstance(errors[0], WorkflowDecisionConflictError)


@pytest.mark.anyio
async def test_pause_and_resume_with_parallel_branches() -> None:
    owner_id = uuid.uuid4()
    store = FakeWorkflowStore()
    definition = _definition(
        owner_id=owner_id,
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
                id="approve",
                type=NodeType.APPROVAL,
                config={"approved_edge_id": "approved"},
            ),
            WorkflowNode(
                id="join",
                type=NodeType.JOIN,
                config={"fork_node_id": "fork", "join_policy": "all"},
            ),
            WorkflowNode(id="end", type=NodeType.TERMINAL, config={}),
        ],
        edges=[
            _edge("e1", "start", "fork"),
            _edge("e2", "fork", "left"),
            _edge("e3", "fork", "right"),
            _edge("e4", "left", "approve"),
            _edge("e5", "right", "approve"),
            _edge("approved", "approve", "join"),
            _edge("e6", "join", "end"),
        ],
    )
    await store.create_definition(definition)
    task_executor = FakeTaskExecutor()
    manager = WorkflowManager(
        store,
        node_executors={
            NodeType.TASK: task_executor,
            NodeType.APPROVAL: ApprovalNodeExecutor(),
            NodeType.FORK: ForkNodeExecutor(max_parallel_branches=8),
            NodeType.JOIN: JoinNodeExecutor(),
        },
    )

    run = await manager.start_run(
        definition.id, owner_id=owner_id, idempotency_key="key-1"
    )
    await _await_scheduled(manager)
    paused = await manager.get_run(run.id, owner_id=owner_id)
    assert paused is not None
    assert paused.status is RunStatus.WAITING_APPROVAL
    assert set(task_executor.calls) == {"start", "left", "right"}

    with_executions = await store.get_run_with_executions(run.id, owner_id=owner_id)
    assert with_executions is not None
    approval_execution = next(
        execution for execution in with_executions[1] if execution.node_id == "approve"
    )

    continued, _ = await manager.apply_decision(
        run.id,
        approval_execution.id,
        owner_id=owner_id,
        decision=ApprovalDecision.APPROVED,
    )
    await _await_scheduled(manager)
    final = await manager.get_run(run.id, owner_id=owner_id)
    assert final is not None
    assert final.status is RunStatus.COMPLETED
    assert set(task_executor.calls) == {"start", "left", "right"}
    assert "join" in final.context.variables
    assert continued.status is RunStatus.RUNNING


@pytest.mark.anyio
async def test_partial_approval_decision_keeps_run_waiting() -> None:
    owner_id = uuid.uuid4()
    store = FakeWorkflowStore()
    definition = _definition(
        owner_id=owner_id,
        nodes=[
            WorkflowNode(id="start", type=NodeType.TASK, config={}),
            WorkflowNode(
                id="approve_a",
                type=NodeType.APPROVAL,
                config={"approved_edge_id": "approved_a"},
            ),
            WorkflowNode(
                id="approve_b",
                type=NodeType.APPROVAL,
                config={"approved_edge_id": "approved_b"},
            ),
            WorkflowNode(id="end", type=NodeType.TERMINAL, config={}),
        ],
        edges=[
            _edge("e1", "start", "approve_a"),
            _edge("e2", "start", "approve_b"),
            _edge("approved_a", "approve_a", "end"),
            _edge("approved_b", "approve_b", "end"),
        ],
    )
    await store.create_definition(definition)
    run_id = uuid.uuid4()
    await store.create_run(
        WorkflowRun(
            id=run_id,
            workflow_definition_id=definition.id,
            owner_id=owner_id,
            idempotency_key="key-1",
            status=RunStatus.WAITING_APPROVAL,
            context=WorkflowContext(variables={"start": {"ok": True}}),
            current_node_ids=["approve_a", "approve_b"],
            checkpoint_version=2,
            created_at=_NOW,
            updated_at=_NOW,
            started_at=_NOW,
        )
    )
    execution_a_id = uuid.uuid4()
    execution_b_id = uuid.uuid4()
    await store.append_node_execution(
        WorkflowNodeExecution(
            id=execution_a_id,
            run_id=run_id,
            node_id="approve_a",
            node_type=NodeType.APPROVAL,
            status=NodeStatus.WAITING_APPROVAL,
            started_at=_NOW,
        )
    )
    await store.append_node_execution(
        WorkflowNodeExecution(
            id=execution_b_id,
            run_id=run_id,
            node_id="approve_b",
            node_type=NodeType.APPROVAL,
            status=NodeStatus.WAITING_APPROVAL,
            started_at=_NOW,
        )
    )

    manager = WorkflowManager(
        store,
        node_executors={
            NodeType.TASK: FakeTaskExecutor(),
            NodeType.APPROVAL: ApprovalNodeExecutor(),
        },
    )

    partial, _ = await manager.apply_decision(
        run_id,
        execution_a_id,
        owner_id=owner_id,
        decision=ApprovalDecision.APPROVED,
    )

    assert partial.status is RunStatus.WAITING_APPROVAL
    assert partial.current_node_ids == ["approve_b"]
    assert manager._last_scheduled_run_task is None

    final, _ = await manager.apply_decision(
        run_id,
        execution_b_id,
        owner_id=owner_id,
        decision=ApprovalDecision.APPROVED,
    )
    await _await_scheduled(manager)
    completed = await manager.get_run(run_id, owner_id=owner_id)

    assert final.status is RunStatus.RUNNING
    assert completed is not None
    assert completed.status is RunStatus.COMPLETED


def _hitl_settings(*, hitl_max_reason_length: int = 2000) -> Settings:
    return Settings(
        openai_api_key="test-key",
        hitl_enabled=True,
        hitl_max_reason_length=hitl_max_reason_length,
    )


def _echo_task_executor() -> TaskNodeExecutor:
    registry = ToolRegistry()
    registry.register(ECHO_TOOL_DEFINITION, EchoToolHandler())
    return TaskNodeExecutor(
        ToolExecutor(registry=registry, settings=Settings(openai_api_key="test-key"))
    )


def _edited_args_approval_definition(owner_id: uuid.UUID) -> WorkflowDefinition:
    return _definition(
        owner_id=owner_id,
        nodes=[
            WorkflowNode(
                id="start",
                type=NodeType.TASK,
                config={
                    "tool_name": ECHO_TOOL_NAME,
                    "arguments_template": {"message": "start"},
                },
            ),
            WorkflowNode(
                id="approve",
                type=NodeType.APPROVAL,
                config={"approved_edge_id": "approved"},
            ),
            WorkflowNode(
                id="after",
                type=NodeType.TASK,
                config={
                    "tool_name": ECHO_TOOL_NAME,
                    "arguments_template": {
                        "message": "{{variables.approve.edited_arguments.message}}"
                    },
                },
            ),
            WorkflowNode(id="end", type=NodeType.TERMINAL, config={}),
        ],
        edges=[
            _edge("e1", "start", "approve"),
            _edge("approved", "approve", "after"),
            _edge("e3", "after", "end"),
        ],
    )


@pytest.mark.anyio
async def test_approve_with_edited_arguments_resolves_downstream_template() -> None:
    owner_id = uuid.uuid4()
    store = FakeWorkflowStore()
    definition = await store.create_definition(
        _edited_args_approval_definition(owner_id)
    )
    task_executor = _echo_task_executor()
    manager = WorkflowManager(
        store,
        settings=_hitl_settings(),
        node_executors={
            NodeType.TASK: task_executor,
            NodeType.APPROVAL: ApprovalNodeExecutor(),
        },
    )

    run = await manager.start_run(
        definition.id, owner_id=owner_id, idempotency_key="key-1"
    )
    await _await_scheduled(manager)

    with_executions = await store.get_run_with_executions(run.id, owner_id=owner_id)
    assert with_executions is not None
    approval_execution = next(
        execution for execution in with_executions[1] if execution.node_id == "approve"
    )

    _, result = await manager.apply_decision(
        run.id,
        approval_execution.id,
        owner_id=owner_id,
        decision=ApprovalDecision.APPROVED,
        edited_arguments={"message": "edited-value"},
    )
    await _await_scheduled(manager)

    final = await manager.get_run(run.id, owner_id=owner_id)
    assert final is not None
    assert final.status is RunStatus.COMPLETED
    after_output = final.context.variables.get("after")
    assert isinstance(after_output, dict)
    assert after_output.get("data") == {"echo": "edited-value"}
    assert result.edited is True
    assert result.final_payload == {"message": "edited-value"}
    assert len(store.approval_revisions) == 1
    assert store.approval_revisions[0].approval_kind is ApprovalKind.WORKFLOW_NODE


@pytest.mark.anyio
async def test_approve_without_body_preserves_epic06_behavior() -> None:
    owner_id = uuid.uuid4()
    store = FakeWorkflowStore()
    definition = await store.create_definition(_approval_linear_definition(owner_id))
    task_executor = FakeTaskExecutor()
    manager = WorkflowManager(
        store,
        settings=_hitl_settings(),
        node_executors={
            NodeType.TASK: task_executor,
            NodeType.APPROVAL: ApprovalNodeExecutor(),
        },
    )

    run = await manager.start_run(
        definition.id, owner_id=owner_id, idempotency_key="key-1"
    )
    await _await_scheduled(manager)
    with_executions = await store.get_run_with_executions(run.id, owner_id=owner_id)
    assert with_executions is not None
    approval_execution = next(
        execution for execution in with_executions[1] if execution.node_id == "approve"
    )

    continued, result = await manager.apply_decision(
        run.id,
        approval_execution.id,
        owner_id=owner_id,
        decision=ApprovalDecision.APPROVED,
    )
    await _await_scheduled(manager)
    final = await manager.get_run(run.id, owner_id=owner_id)

    assert final is not None
    assert final.status is RunStatus.COMPLETED
    assert task_executor.calls == ["start", "after"]
    approve_output = final.context.variables["approve"]
    assert isinstance(approve_output, dict)
    assert approve_output == {
        "node_id": "approve",
        "decision": "approved",
        "selected_edge_ids": ["approved"],
    }
    assert continued.status is RunStatus.RUNNING
    assert result.edited is False
    assert result.final_payload is None
    assert store.approval_revisions == []


@pytest.mark.anyio
async def test_reject_with_reason_persisted_on_execution() -> None:
    owner_id = uuid.uuid4()
    store = FakeWorkflowStore()
    definition = await store.create_definition(_approval_branch_definition(owner_id))
    manager = WorkflowManager(
        store,
        settings=_hitl_settings(),
        node_executors={
            NodeType.TASK: FakeTaskExecutor(),
            NodeType.APPROVAL: ApprovalNodeExecutor(),
        },
    )

    run = await manager.start_run(
        definition.id, owner_id=owner_id, idempotency_key="key-1"
    )
    await _await_scheduled(manager)
    with_executions = await store.get_run_with_executions(run.id, owner_id=owner_id)
    assert with_executions is not None
    approval_execution = next(
        execution for execution in with_executions[1] if execution.node_id == "approve"
    )

    _, result = await manager.apply_decision(
        run.id,
        approval_execution.id,
        owner_id=owner_id,
        decision=ApprovalDecision.REJECTED,
        reason="Not acceptable",
    )
    await _await_scheduled(manager)

    updated_execution = store._executions[approval_execution.id]
    assert updated_execution.reason == "Not acceptable"
    assert result.reason == "Not acceptable"
    assert result.status is ApprovalStatus.REJECTED


@pytest.mark.anyio
async def test_reason_truncated_to_hitl_max_reason_length() -> None:
    owner_id = uuid.uuid4()
    store = FakeWorkflowStore()
    definition = await store.create_definition(_approval_linear_definition(owner_id))
    manager = WorkflowManager(
        store,
        settings=_hitl_settings(hitl_max_reason_length=10),
        node_executors={
            NodeType.TASK: FakeTaskExecutor(),
            NodeType.APPROVAL: ApprovalNodeExecutor(),
        },
    )

    run = await manager.start_run(
        definition.id, owner_id=owner_id, idempotency_key="key-1"
    )
    await _await_scheduled(manager)
    with_executions = await store.get_run_with_executions(run.id, owner_id=owner_id)
    assert with_executions is not None
    approval_execution = next(
        execution for execution in with_executions[1] if execution.node_id == "approve"
    )

    long_reason = "x" * 25
    _, result = await manager.apply_decision(
        run.id,
        approval_execution.id,
        owner_id=owner_id,
        decision=ApprovalDecision.APPROVED,
        reason=long_reason,
    )

    assert result.reason == "x" * 10
    assert store._executions[approval_execution.id].reason == "x" * 10


class _FailingAfterApprovalTaskExecutor:
    async def execute(
        self,
        node: WorkflowNode,
        context: WorkflowContext,
        request: NodeExecutionRequest,
    ) -> dict[str, object]:
        del node, context, request
        raise WorkflowNodeExecutionError("downstream failed")


class _SwitchingTaskExecutor:
    """Runs the start task normally, then fails on the post-approval task."""

    def __init__(self) -> None:
        self._start = FakeTaskExecutor()
        self._fail = _FailingAfterApprovalTaskExecutor()
        self.fail_after_approval = False

    async def execute(
        self,
        node: WorkflowNode,
        context: WorkflowContext,
        request: NodeExecutionRequest,
    ) -> dict[str, object]:
        if self.fail_after_approval and node.id == "after":
            return await self._fail.execute(node, context, request)
        return await self._start.execute(node, context, request)


@pytest.mark.anyio
async def test_downstream_failure_preserves_approval_decision() -> None:
    owner_id = uuid.uuid4()
    store = FakeWorkflowStore()
    definition = await store.create_definition(_approval_linear_definition(owner_id))
    task_executor = _SwitchingTaskExecutor()
    manager = WorkflowManager(
        store,
        settings=_hitl_settings(),
        node_executors={
            NodeType.TASK: task_executor,
            NodeType.APPROVAL: ApprovalNodeExecutor(),
        },
    )

    run = await manager.start_run(
        definition.id, owner_id=owner_id, idempotency_key="key-1"
    )
    await _await_scheduled(manager)
    with_executions = await store.get_run_with_executions(run.id, owner_id=owner_id)
    assert with_executions is not None
    approval_execution = next(
        execution for execution in with_executions[1] if execution.node_id == "approve"
    )

    task_executor.fail_after_approval = True
    _, result = await manager.apply_decision(
        run.id,
        approval_execution.id,
        owner_id=owner_id,
        decision=ApprovalDecision.APPROVED,
        edited_arguments={"message": "will-fail"},
    )
    await _await_scheduled(manager)

    failed_run = await manager.get_run(run.id, owner_id=owner_id)
    approval_record = store._executions[approval_execution.id]
    assert failed_run is not None
    assert failed_run.status is RunStatus.FAILED
    assert approval_record.decision is ApprovalDecision.APPROVED
    assert approval_record.status is NodeStatus.SUCCEEDED
    assert result.status is ApprovalStatus.APPROVED


class TestApprovalNodeExecutor:
    @pytest.mark.anyio
    async def test_returns_waiting_metadata(self) -> None:
        executor = ApprovalNodeExecutor()
        node = WorkflowNode(id="approve", type=NodeType.APPROVAL, config={})
        context = WorkflowContext(trigger_input={"topic": "demo"})

        output = await executor.execute(
            node,
            context,
            NodeExecutionRequest(
                owner_id=uuid.uuid4(),
                execution_receipt_id="run:approve:1",
            ),
        )

        assert output["status"] == "waiting_approval"
        assert output["trigger_input"] == {"topic": "demo"}
