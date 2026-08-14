"""HITL reference scenario integration tests (Epic 09 Phase 8)."""

from __future__ import annotations

import uuid
from unittest.mock import patch

import pytest

from app.ai.agent.models.config import AgentConfig
from app.ai.agent.models.context import AgentContext
from app.ai.agent.models.messages import AgentMessage
from app.ai.agent.models.request import AgentRequest
from app.ai.agent.streaming import InMemoryStreamPublisher
from app.ai.hitl.models import ApprovalStatus, ProposedToolCall
from app.ai.tools.schemas import ToolExecutionContext
from app.ai.tools.executor import ToolExecutor
from app.ai.tools.stubs.send_notification import SendNotificationHandler
from app.ai.workflow.models import ApprovalDecision, NodeType, RunStatus
from app.core.caller import CallerContext
from app.providers.factory import ProviderFactory
from tests.ai.hitl.scenario_helpers import (
    build_approval_service,
    build_default_agent,
    build_in_memory_workflow_manager,
    build_resume_executor,
    edited_args_workflow_definition,
    hitl_settings,
    notification_provider,
    reference_tool_registry,
)


@pytest.fixture(autouse=True)
def _reset_notification_handler() -> None:
    SendNotificationHandler.reset()


@pytest.mark.anyio
async def test_agent_full_pause_decide_resume_loop() -> None:
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
        execution_id="exec-ref-agent",
        caller=caller,
        session_id=session.id,
    )

    with patch.object(
        ProviderFactory,
        "get_provider",
        staticmethod(lambda _name, _settings: provider),
    ):
        paused = await agent.run(request, context)

    assert paused.finish_reason == "waiting_approval"
    pending = approval_store.rows[0]
    executor = build_resume_executor(
        registry=registry, service=service, provider=provider
    )
    with patch.object(
        ProviderFactory,
        "get_provider",
        staticmethod(lambda _name, _settings: provider),
    ):
        _, response = await service.approve_and_resume(
            pending.id,
            decider_id=owner_id,
            executor=executor,
            request=request,
            context=context,
            tool_context=ToolExecutionContext(caller=caller),
            stream_publisher=InMemoryStreamPublisher(),
        )

    assert response.finish_reason == "stop"
    assert SendNotificationHandler.sent_messages
    updated = approval_store.rows[0]
    assert updated.status is ApprovalStatus.APPROVED


@pytest.mark.anyio
async def test_workflow_full_pause_decide_edited_continue_loop() -> None:
    from app.ai.workflow.nodes.task_node import TaskNodeExecutor

    owner_id = uuid.uuid4()
    registry = reference_tool_registry()
    tool_executor = ToolExecutor(registry=registry, settings=hitl_settings())
    manager, _registry = build_in_memory_workflow_manager(registry=registry)
    manager._node_executors[NodeType.TASK] = TaskNodeExecutor(tool_executor)  # type: ignore[attr-defined]
    definition = await manager._store.create_definition(  # type: ignore[attr-defined]
        edited_args_workflow_definition(owner_id)
    )
    run = await manager.start_run(
        definition.id,
        owner_id=owner_id,
        idempotency_key="hitl-ref-workflow",
    )
    if manager._last_scheduled_run_task is not None:
        await manager._last_scheduled_run_task

    paused = await manager.get_run(run.id, owner_id=owner_id)
    assert paused is not None
    assert paused.status is RunStatus.WAITING_APPROVAL

    with_executions = await manager.get_run_with_executions(run.id, owner_id=owner_id)
    assert with_executions is not None
    approval_execution = next(
        execution for execution in with_executions[1] if execution.node_id == "approve"
    )

    _, result = await manager.apply_decision(
        run.id,
        approval_execution.id,
        owner_id=owner_id,
        decision=ApprovalDecision.APPROVED,
        edited_arguments={"message": "reference-edited"},
    )
    if manager._last_scheduled_run_task is not None:
        await manager._last_scheduled_run_task

    final = await manager.get_run(run.id, owner_id=owner_id)
    assert final is not None
    assert final.status is RunStatus.COMPLETED
    assert result.edited is True
    after_output = final.context.variables.get("after")
    assert isinstance(after_output, dict)
    assert after_output.get("data") == {"echo": "reference-edited"}


@pytest.mark.anyio
async def test_agent_approve_with_edits_executes_edited_payload() -> None:
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
        execution_id="exec-ref-edits",
        caller=caller,
        session_id=session.id,
    )

    with patch.object(
        ProviderFactory,
        "get_provider",
        staticmethod(lambda _name, _settings: provider),
    ):
        await agent.run(request, context)

    pending = approval_store.rows[0]
    edited = [
        ProposedToolCall(
            name="send_notification",
            arguments={"message": "edited-reference", "channel": "sms"},
            call_id=pending.proposed_calls[0].call_id,
        )
    ]
    executor = build_resume_executor(
        registry=registry, service=service, provider=provider
    )
    with patch.object(
        ProviderFactory,
        "get_provider",
        staticmethod(lambda _name, _settings: provider),
    ):
        await service.approve_and_resume(
            pending.id,
            decider_id=owner_id,
            executor=executor,
            request=request,
            context=context,
            tool_context=ToolExecutionContext(caller=caller),
            stream_publisher=InMemoryStreamPublisher(),
            edited_calls=edited,
        )

    assert SendNotificationHandler.sent_messages[-1]["message"] == "edited-reference"


@pytest.mark.anyio
async def test_agent_reject_skips_tool_execution() -> None:
    owner_id = uuid.uuid4()
    registry = reference_tool_registry()
    service, approval_store, chat_store = build_approval_service(registry=registry)
    provider = notification_provider()
    agent = build_default_agent(registry=registry, service=service, provider=provider)
    session = await chat_store.create_session(user_id=owner_id)
    request = AgentRequest(
        messages=[AgentMessage(role="user", content="Send a notification")],
        model="gpt-4o-mini",
    )
    caller = CallerContext.for_user(owner_id)
    context = AgentContext(
        execution_id="exec-ref-reject",
        caller=caller,
        session_id=session.id,
    )

    with patch.object(
        ProviderFactory,
        "get_provider",
        staticmethod(lambda _name, _settings: provider),
    ):
        await agent.run(request, context)

    result = await service.decide(
        approval_store.rows[0].id,
        decider_id=owner_id,
        decision="rejected",
        reason="not now",
    )

    assert result.status is ApprovalStatus.REJECTED
    assert not SendNotificationHandler.sent_messages
