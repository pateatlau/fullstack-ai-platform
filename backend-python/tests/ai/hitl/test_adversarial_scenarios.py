"""HITL adversarial and edge-case scenario tests (Epic 09 Phase 8)."""

from __future__ import annotations

import asyncio
import uuid
from unittest.mock import patch

import pytest

from app.ai.agent.models.config import AgentConfig
from app.ai.agent.models.context import AgentContext
from app.ai.agent.models.events import AgentStreamEventType
from app.ai.agent.models.messages import AgentMessage
from app.ai.agent.models.request import AgentRequest
from app.ai.agent.streaming import InMemoryStreamPublisher
from app.ai.hitl.exceptions import (
    ApprovalDecisionConflictError,
    ApprovalNotFoundError,
    ApprovalValidationError,
)
from app.ai.hitl.models import (
    ApprovalStatus,
    ProposedToolCall,
    ApprovalResult,
    AgentToolApproval,
)
from app.ai.tools.schemas import ToolExecutionContext
from app.ai.tools.stubs.send_notification import SendNotificationHandler
from app.ai.workflow.exceptions import WorkflowDecisionConflictError
from app.ai.workflow.models import ApprovalDecision, NodeStatus, RunStatus
from app.core.caller import CallerContext
from app.providers.factory import ProviderFactory
from tests.fakes import FakeProvider
from tests.ai.hitl.scenario_helpers import (
    MCP_TOOL_NAME,
    PLUGIN_TOOL_NAME,
    _SensitiveMcpHandler,
    _SensitivePluginHandler,
    build_approval_service,
    build_default_agent,
    build_in_memory_workflow_manager,
    build_resume_executor,
    hitl_graph_validator,
    mcp_tool_registry,
    nested_parallel_approval_definition,
    notification_provider,
    pause_tool_runner_step,
    plugin_tool_registry,
    reference_tool_registry,
    sequential_notification_provider,
)


@pytest.fixture(autouse=True)
def _reset_handlers() -> None:
    SendNotificationHandler.reset()
    _SensitiveMcpHandler.reset()
    _SensitivePluginHandler.reset()


@pytest.mark.anyio
async def test_duplicate_decision_returns_409() -> None:
    owner_id = uuid.uuid4()
    registry = reference_tool_registry()
    service, approval_store, chat_store = build_approval_service(registry=registry)
    session = await chat_store.create_session(user_id=owner_id)
    approval = await approval_store.create(
        session_id=session.id,
        owner_id=owner_id,
        execution_id="exec-dup",
        approval_correlation_id=uuid.uuid4(),
        proposed_calls=[
            ProposedToolCall(
                name="send_notification",
                arguments={"message": "x"},
                call_id="c1",
            )
        ],
        paused_scratchpad=[],
        paused_state={"status": "waiting_approval"},
    )

    await service.decide(approval.id, owner_id=owner_id, decision="rejected")

    with pytest.raises(ApprovalDecisionConflictError):
        await service.decide(approval.id, owner_id=owner_id, decision="rejected")


@pytest.mark.anyio
async def test_concurrent_decisions_only_one_wins() -> None:
    owner_id = uuid.uuid4()
    registry = reference_tool_registry()
    service, approval_store, chat_store = build_approval_service(registry=registry)
    session = await chat_store.create_session(user_id=owner_id)
    approval = await approval_store.create(
        session_id=session.id,
        owner_id=owner_id,
        execution_id="exec-race",
        approval_correlation_id=uuid.uuid4(),
        proposed_calls=[
            ProposedToolCall(
                name="send_notification",
                arguments={"message": "x"},
                call_id="c1",
            )
        ],
        paused_scratchpad=[],
        paused_state={"status": "waiting_approval"},
    )

    results = await asyncio.gather(
        service.decide(approval.id, owner_id=owner_id, decision="approved"),
        service.decide(approval.id, owner_id=owner_id, decision="rejected"),
        return_exceptions=True,
    )
    errors = [item for item in results if isinstance(item, Exception)]
    successes = [item for item in results if not isinstance(item, Exception)]
    assert len(successes) == 1
    assert len(errors) == 1
    assert isinstance(errors[0], ApprovalDecisionConflictError)


@pytest.mark.anyio
async def test_invalid_edited_calls_on_decide_stay_pending() -> None:
    owner_id = uuid.uuid4()
    registry = reference_tool_registry()
    service, approval_store, chat_store = build_approval_service(registry=registry)
    session = await chat_store.create_session(user_id=owner_id)
    approval = await approval_store.create(
        session_id=session.id,
        owner_id=owner_id,
        execution_id="exec-invalid",
        approval_correlation_id=uuid.uuid4(),
        proposed_calls=[
            ProposedToolCall(
                name="send_notification",
                arguments={"message": "x"},
                call_id="c1",
            )
        ],
        paused_scratchpad=[],
        paused_state={"status": "waiting_approval"},
    )
    caller = CallerContext.for_user(owner_id)
    executor = build_resume_executor(
        registry=registry,
        service=service,
        provider=notification_provider(),
    )

    with pytest.raises(ApprovalValidationError):
        await service.approve_and_resume(
            approval.id,
            owner_id=owner_id,
            executor=executor,
            request=AgentRequest(
                messages=[AgentMessage(role="user", content="notify")],
                model="gpt-4o-mini",
            ),
            context=AgentContext(
                execution_id="exec-invalid",
                caller=caller,
                session_id=session.id,
            ),
            tool_context=ToolExecutionContext(caller=caller),
            stream_publisher=InMemoryStreamPublisher(),
            edited_calls=[
                ProposedToolCall(
                    name="send_notification",
                    arguments={"message": 123},  # type: ignore[arg-type]
                    call_id="c1",
                )
            ],
        )

    row = await approval_store.get(approval.id)
    assert row is not None
    assert row.status is ApprovalStatus.PENDING
    assert not approval_store.revisions


@pytest.mark.anyio
async def test_invalid_edited_calls_on_revise_stay_pending() -> None:
    owner_id = uuid.uuid4()
    registry = reference_tool_registry()
    service, approval_store, _chat_store = build_approval_service(registry=registry)
    approval = await approval_store.create(
        session_id=uuid.uuid4(),
        owner_id=owner_id,
        execution_id="exec-revise-invalid",
        approval_correlation_id=uuid.uuid4(),
        proposed_calls=[
            ProposedToolCall(
                name="send_notification",
                arguments={"message": "x"},
                call_id="c1",
            )
        ],
        paused_scratchpad=[],
        paused_state={"status": "waiting_approval"},
    )

    with pytest.raises(ApprovalValidationError):
        await service.revise(
            approval.id,
            owner_id=owner_id,
            edited_calls=[
                ProposedToolCall(
                    name="send_notification",
                    arguments={"message": 1},  # type: ignore[arg-type]
                    call_id="c1",
                )
            ],
        )

    assert not approval_store.revisions


@pytest.mark.anyio
async def test_stale_approval_id_raises_not_found() -> None:
    owner_id = uuid.uuid4()
    service, _, _ = build_approval_service(registry=reference_tool_registry())

    with pytest.raises(ApprovalNotFoundError):
        await service.decide(uuid.uuid4(), owner_id=owner_id, decision="approved")


@pytest.mark.anyio
async def test_decide_on_terminal_approval_raises_conflict() -> None:
    owner_id = uuid.uuid4()
    service, approval_store, chat_store = build_approval_service(
        registry=reference_tool_registry()
    )
    session = await chat_store.create_session(user_id=owner_id)
    approval = await approval_store.create(
        session_id=session.id,
        owner_id=owner_id,
        execution_id="exec-terminal",
        approval_correlation_id=uuid.uuid4(),
        proposed_calls=[
            ProposedToolCall(
                name="send_notification",
                arguments={"message": "x"},
                call_id="c1",
            )
        ],
        paused_scratchpad=[],
        paused_state={"status": "waiting_approval"},
    )
    await service.decide(approval.id, owner_id=owner_id, decision="rejected")

    with pytest.raises(ApprovalDecisionConflictError):
        await service.revise(
            approval.id,
            owner_id=owner_id,
            edited_calls=[
                ProposedToolCall(
                    name="send_notification",
                    arguments={"message": "y"},
                    call_id="c1",
                )
            ],
        )


@pytest.mark.anyio
async def test_plugin_tool_full_pause_decide_resume_loop() -> None:
    registry = plugin_tool_registry()
    service, approval_store, chat_store = build_approval_service(registry=registry)
    approval_id = await pause_tool_runner_step(
        registry=registry,
        service=service,
        approval_store=approval_store,
        chat_store=chat_store,
        tool_name=PLUGIN_TOOL_NAME,
        arguments={"message": "hello"},
    )
    assert _SensitivePluginHandler.call_count == 0

    owner_id = approval_store.rows[0].owner_id
    caller = CallerContext.for_user(owner_id)
    provider = FakeProvider(response="Done.")
    executor = build_resume_executor(
        registry=registry, service=service, provider=provider
    )
    pending = approval_store.rows[0]
    await service.approve_and_resume(
        approval_id,
        owner_id=owner_id,
        executor=executor,
        request=AgentRequest(
            messages=[AgentMessage(role="user", content="plugin")],
            model="gpt-4o-mini",
            config=AgentConfig(max_iterations=2),
        ),
        context=AgentContext(
            execution_id=pending.execution_id,
            caller=caller,
            session_id=pending.session_id,
        ),
        tool_context=ToolExecutionContext(caller=caller),
        stream_publisher=InMemoryStreamPublisher(),
    )

    assert _SensitivePluginHandler.call_count == 1


@pytest.mark.anyio
async def test_mcp_tool_full_pause_decide_resume_loop() -> None:
    registry = mcp_tool_registry()
    service, approval_store, chat_store = build_approval_service(registry=registry)
    approval_id = await pause_tool_runner_step(
        registry=registry,
        service=service,
        approval_store=approval_store,
        chat_store=chat_store,
        tool_name=MCP_TOOL_NAME,
        arguments={},
    )
    assert _SensitiveMcpHandler.call_count == 0

    owner_id = approval_store.rows[0].owner_id
    caller = CallerContext.for_user(owner_id)
    provider = FakeProvider(response="Done.")
    executor = build_resume_executor(
        registry=registry, service=service, provider=provider
    )
    pending = approval_store.rows[0]
    await service.approve_and_resume(
        approval_id,
        owner_id=owner_id,
        executor=executor,
        request=AgentRequest(
            messages=[AgentMessage(role="user", content="mcp")],
            model="gpt-4o-mini",
            config=AgentConfig(max_iterations=2),
        ),
        context=AgentContext(
            execution_id=pending.execution_id,
            caller=caller,
            session_id=pending.session_id,
        ),
        tool_context=ToolExecutionContext(caller=caller),
        stream_publisher=InMemoryStreamPublisher(),
    )

    assert _SensitiveMcpHandler.call_count == 1


@pytest.mark.anyio
async def test_multiple_approvals_in_one_conversation() -> None:
    owner_id = uuid.uuid4()
    registry = reference_tool_registry()
    service, approval_store, chat_store = build_approval_service(registry=registry)
    provider = sequential_notification_provider()
    agent = build_default_agent(registry=registry, service=service, provider=provider)
    session = await chat_store.create_session(user_id=owner_id)
    request = AgentRequest(
        messages=[AgentMessage(role="user", content="Send two notifications")],
        model="gpt-4o-mini",
        config=AgentConfig(max_iterations=4),
    )
    caller = CallerContext.for_user(owner_id)
    context = AgentContext(
        execution_id="exec-multi",
        caller=caller,
        session_id=session.id,
    )

    with patch.object(
        ProviderFactory,
        "get_provider",
        staticmethod(lambda _name, _settings: provider),
    ):
        first = await agent.run(request, context)

    assert first.finish_reason == "waiting_approval"
    assert len(approval_store.rows) == 1
    first_id = approval_store.rows[0].id
    first_correlation = approval_store.rows[0].approval_correlation_id

    executor = build_resume_executor(
        registry=registry, service=service, provider=provider
    )
    _, second = await service.approve_and_resume(
        first_id,
        owner_id=owner_id,
        executor=executor,
        request=request,
        context=context,
        tool_context=ToolExecutionContext(caller=caller),
        stream_publisher=InMemoryStreamPublisher(),
    )

    assert second.finish_reason == "waiting_approval"
    assert len(approval_store.rows) == 2
    assert approval_store.rows[0].approval_correlation_id != (
        approval_store.rows[1].approval_correlation_id
    )
    assert first_correlation != approval_store.rows[1].approval_correlation_id

    _, final = await service.approve_and_resume(
        approval_store.rows[1].id,
        owner_id=owner_id,
        executor=executor,
        request=request,
        context=context,
        tool_context=ToolExecutionContext(caller=caller),
        stream_publisher=InMemoryStreamPublisher(),
    )

    assert final.finish_reason == "stop"
    assert len(SendNotificationHandler.sent_messages) == 2


def test_nested_parallel_workflow_passes_graph_guard() -> None:
    registry = reference_tool_registry()
    validator = hitl_graph_validator(registry)
    definition = nested_parallel_approval_definition(uuid.uuid4())
    validator.validate(definition)


@pytest.mark.anyio
async def test_nested_parallel_workflow_runs_end_to_end() -> None:
    owner_id = uuid.uuid4()
    manager, _ = build_in_memory_workflow_manager()
    definition = await manager._store.create_definition(  # type: ignore[attr-defined]
        nested_parallel_approval_definition(owner_id)
    )
    run = await manager.start_run(
        definition.id,
        owner_id=owner_id,
        idempotency_key="nested-hitl",
    )
    if manager._last_scheduled_run_task is not None:
        await manager._last_scheduled_run_task

    paused = await manager.get_run(run.id, owner_id=owner_id)
    assert paused is not None
    assert paused.status is RunStatus.WAITING_APPROVAL

    with_executions = await manager.get_run_with_executions(run.id, owner_id=owner_id)
    assert with_executions is not None
    pending_approvals = {
        execution.node_id: execution
        for execution in with_executions[1]
        if execution.node_id in {"approve_left", "approve_right"}
        and execution.status is NodeStatus.WAITING_APPROVAL
    }
    assert pending_approvals.keys() == {"approve_left", "approve_right"}

    for node_id in ("approve_left", "approve_right"):
        _, _ = await manager.apply_decision(
            run.id,
            pending_approvals[node_id].id,
            owner_id=owner_id,
            decision=ApprovalDecision.APPROVED,
        )
        if manager._last_scheduled_run_task is not None:
            await manager._last_scheduled_run_task

    completed = await manager.get_run(run.id, owner_id=owner_id)
    assert completed is not None
    assert completed.status is RunStatus.COMPLETED


@pytest.mark.anyio
async def test_streaming_interruption_leaves_resumable_pending_approval() -> None:
    owner_id = uuid.uuid4()
    registry = reference_tool_registry()
    service, approval_store, chat_store = build_approval_service(registry=registry)
    provider = notification_provider()
    agent = build_default_agent(registry=registry, service=service, provider=provider)
    session = await chat_store.create_session(user_id=owner_id)
    request = AgentRequest(
        messages=[AgentMessage(role="user", content="Send a notification")],
        model="gpt-4o-mini",
        config=AgentConfig(max_iterations=3),
    )
    caller = CallerContext.for_user(owner_id)
    context = AgentContext(
        execution_id="exec-stream",
        caller=caller,
        session_id=session.id,
    )

    with patch.object(
        ProviderFactory,
        "get_provider",
        staticmethod(lambda _name, _settings: provider),
    ):
        async for event in agent.stream(request, context):
            if event.type is AgentStreamEventType.APPROVAL_REQUIRED:
                break

    assert approval_store.rows
    pending = approval_store.rows[0]
    assert pending.status is ApprovalStatus.PENDING

    resume_executor = build_resume_executor(
        registry=registry, service=service, provider=provider
    )
    with patch.object(
        ProviderFactory,
        "get_provider",
        staticmethod(lambda _name, _settings: provider),
    ):
        _, response = await service.approve_and_resume(
            pending.id,
            owner_id=owner_id,
            executor=resume_executor,
            request=request,
            context=context,
            tool_context=ToolExecutionContext(caller=caller),
            stream_publisher=InMemoryStreamPublisher(),
        )

    assert response.finish_reason == "stop"
    assert SendNotificationHandler.sent_messages


@pytest.mark.anyio
async def test_workflow_concurrent_decisions_only_one_wins() -> None:
    owner_id = uuid.uuid4()
    manager, _ = build_in_memory_workflow_manager()
    from tests.ai.workflow.test_approval_node import _approval_branch_definition

    definition = await manager._store.create_definition(  # type: ignore[attr-defined]
        _approval_branch_definition(owner_id)
    )
    run = await manager.start_run(
        definition.id,
        owner_id=owner_id,
        idempotency_key="wf-race",
    )
    if manager._last_scheduled_run_task is not None:
        await manager._last_scheduled_run_task

    with_executions = await manager.get_run_with_executions(run.id, owner_id=owner_id)
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
async def test_expiry_sweep_race_with_decide_only_one_wins() -> None:
    owner_id = uuid.uuid4()
    registry = reference_tool_registry()
    service, approval_store, chat_store = build_approval_service(registry=registry)
    session = await chat_store.create_session(user_id=owner_id)
    approval = await approval_store.create(
        session_id=session.id,
        owner_id=owner_id,
        execution_id="exec-expiry-race",
        approval_correlation_id=uuid.uuid4(),
        proposed_calls=[
            ProposedToolCall(
                name="send_notification",
                arguments={"message": "x"},
                call_id="c1",
            )
        ],
        paused_scratchpad=[],
        paused_state={"status": "waiting_approval"},
    )

    results = await asyncio.gather(
        service.decide(approval.id, owner_id=owner_id, decision="rejected"),
        approval_store.cas_expire_pending_sweep(approval.id),
        return_exceptions=True,
    )
    assert not any(isinstance(item, Exception) for item in results)
    final = await approval_store.get(approval.id)
    assert final is not None
    assert final.status in {ApprovalStatus.REJECTED, ApprovalStatus.EXPIRED}
    transitioned = sum(
        1
        for item in results
        if isinstance(item, ApprovalResult)
        or (
            isinstance(item, AgentToolApproval)
            and item.status is ApprovalStatus.EXPIRED
        )
    )
    assert transitioned == 1
