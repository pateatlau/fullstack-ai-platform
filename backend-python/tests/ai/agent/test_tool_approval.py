"""Agent tool-call approval gate integration tests (Epic 09 Phase 2)."""

from __future__ import annotations

import uuid
from collections.abc import Iterator

import pytest

from app.ai.agent.executor import AgentExecutor, ToolRunner
from app.ai.agent.models.config import AgentConfig
from app.ai.agent.models.context import AgentContext
from app.ai.agent.models.events import AgentStreamEventType
from app.ai.agent.models.messages import AgentMessage
from app.ai.agent.models.plan import PlannedStep, StepAction
from app.ai.agent.models.state import AgentExecutionState, AgentExecutionStatus
from app.ai.agent.models.request import AgentRequest
from app.ai.agent.planner import ReActPlanner
from app.ai.agent.scratchpad import ScratchpadStore
from app.ai.agent.streaming import InMemoryStreamPublisher
from app.ai.hitl.exceptions import AgentApprovalPauseError, HitlError
from app.ai.hitl.policy import ApprovalPolicy
from app.ai.hitl.service import AgentApprovalService
from app.ai.tools.executor import ToolExecutor
from app.ai.tools.registry import ToolRegistry
from app.ai.tools.schemas import (
    ToolCall,
    ToolDefinition,
    ToolExecutionContext,
    ToolResult,
)
from app.ai.tools.stubs.echo import ECHO_TOOL_DEFINITION, echo_handler
from app.core.caller import CallerContext
from app.core.config import Settings
from app.providers.base import ProviderToolCall, ProviderToolCompletion
from tests.ai.hitl.fakes import InMemoryApprovalStore
from tests.fakes import FakeChatStore, FakeProvider


class SensitiveHandler:
    call_count: int = 0

    async def execute(
        self,
        args: dict[str, object],
        context: ToolExecutionContext,
    ) -> ToolResult:
        del context
        SensitiveHandler.call_count += 1
        return ToolResult(success=True, data={"deleted": True})


class TrackingHandler:
    calls: list[str] = []

    async def execute(
        self,
        args: dict[str, object],
        context: ToolExecutionContext,
    ) -> ToolResult:
        del context
        TrackingHandler.calls.append(str(args.get("message", "")))
        return ToolResult(success=True, data={"echo": args.get("message")})


@pytest.fixture(autouse=True)
def _reset_tracking() -> Iterator[None]:
    TrackingHandler.calls = []
    SensitiveHandler.call_count = 0
    yield


@pytest.fixture
def sensitive_registry() -> ToolRegistry:
    registry = ToolRegistry()
    sensitive = ToolDefinition(
        name="delete_file",
        description="delete a file",
        parameters={
            "type": "object",
            "properties": {"path": {"type": "string"}},
            "required": ["path"],
        },
        requires_approval=True,
    )
    registry.register(sensitive, SensitiveHandler())
    registry.register(ECHO_TOOL_DEFINITION, echo_handler())
    return registry


@pytest.fixture
def tool_context() -> ToolExecutionContext:
    return ToolExecutionContext(
        caller=CallerContext.for_user(uuid.uuid4()),
        request_id="req-hitl",
    )


def _approval_service(chat_store: FakeChatStore) -> AgentApprovalService:
    return AgentApprovalService(
        approval_store=InMemoryApprovalStore(),  # type: ignore[arg-type]
        chat_store=chat_store,
    )


@pytest.mark.anyio
async def test_tool_runner_does_not_dispatch_when_approval_required(
    sensitive_registry: ToolRegistry,
    tool_context: ToolExecutionContext,
) -> None:
    executor = ToolExecutor(registry=sensitive_registry, settings=Settings())
    publisher = InMemoryStreamPublisher()
    chat_store = FakeChatStore()
    session = await chat_store.create_session(user_id=tool_context.caller.user_id)  # type: ignore[union-attr]
    approval_service = _approval_service(chat_store)
    runner = ToolRunner(
        tool_executor=executor,
        tool_registry=sensitive_registry,
        stream_publisher=publisher,
        hitl_enabled=True,
        approval_policy=ApprovalPolicy(required_tool_names=frozenset()),
        approval_service=approval_service,
    )
    scratchpad_store = ScratchpadStore()
    scratchpad = scratchpad_store.create("exec-gate")
    state = AgentExecutionState(
        execution_id="exec-gate",
        status=AgentExecutionStatus.EXECUTING,
    )
    step = PlannedStep(
        step_id="s1",
        action=StepAction.TOOL_CALL,
        tool_calls=[ToolCall(name="delete_file", arguments={"path": "/tmp/x"})],
    )

    with pytest.raises(AgentApprovalPauseError):
        await runner.run_tool_steps(
            [step],
            execution_id="exec-gate",
            tool_context=tool_context,
            scratchpad=scratchpad,
            state=state,
            session_id=session.id,
            owner_id=tool_context.caller.user_id,  # type: ignore[union-attr]
        )

    assert SensitiveHandler.call_count == 0
    assert publisher.events[-1].type == AgentStreamEventType.APPROVAL_REQUIRED
    messages = await chat_store.list_messages(session.id)
    assert any(m.status == "waiting_approval" for m in messages)


@pytest.mark.anyio
async def test_flag_off_dispatches_normally(
    sensitive_registry: ToolRegistry,
    tool_context: ToolExecutionContext,
) -> None:
    executor = ToolExecutor(registry=sensitive_registry, settings=Settings())
    chat_store = FakeChatStore()
    runner = ToolRunner(
        tool_executor=executor,
        tool_registry=sensitive_registry,
        hitl_enabled=False,
        approval_policy=ApprovalPolicy(required_tool_names=frozenset()),
        approval_service=_approval_service(chat_store),
    )
    step = PlannedStep(
        step_id="s1",
        action=StepAction.TOOL_CALL,
        tool_calls=[ToolCall(name="delete_file", arguments={"path": "/tmp/x"})],
    )
    results = await runner.run_tool_steps(
        [step],
        execution_id="exec-off",
        tool_context=tool_context,
    )
    assert len(results.records) == 1
    assert results.records[0].result.success is True
    assert not chat_store.messages


@pytest.mark.anyio
async def test_mixed_step_pauses_entire_step(
    tool_context: ToolExecutionContext,
) -> None:
    registry = ToolRegistry()
    registry.register(
        ToolDefinition(
            name="delete_file",
            description="delete",
            parameters={"type": "object", "properties": {}},
            requires_approval=True,
        ),
        SensitiveHandler(),
    )
    registry.register(ECHO_TOOL_DEFINITION, TrackingHandler())
    executor = ToolExecutor(registry=registry, settings=Settings())
    chat_store = FakeChatStore()
    session = await chat_store.create_session(user_id=tool_context.caller.user_id)  # type: ignore[union-attr]
    runner = ToolRunner(
        tool_executor=executor,
        tool_registry=registry,
        hitl_enabled=True,
        approval_policy=ApprovalPolicy(required_tool_names=frozenset()),
        approval_service=_approval_service(chat_store),
    )
    scratchpad_store = ScratchpadStore()
    scratchpad = scratchpad_store.create("exec-mix")
    state = AgentExecutionState(
        execution_id="exec-mix",
        status=AgentExecutionStatus.EXECUTING,
    )
    step = PlannedStep(
        step_id="s1",
        action=StepAction.TOOL_CALL,
        tool_calls=[
            ToolCall(name="echo", arguments={"message": "safe"}, call_id="c1"),
            ToolCall(name="delete_file", arguments={"path": "/tmp/x"}, call_id="c2"),
        ],
    )

    with pytest.raises(AgentApprovalPauseError):
        await runner.run_tool_steps(
            [step],
            execution_id="exec-mix",
            tool_context=tool_context,
            scratchpad=scratchpad,
            state=state,
            session_id=session.id,
            owner_id=tool_context.caller.user_id,  # type: ignore[union-attr]
        )

    assert TrackingHandler.calls == []


@pytest.mark.anyio
async def test_parallel_batch_preflights_before_any_dispatch(
    tool_context: ToolExecutionContext,
) -> None:
    registry = ToolRegistry()
    registry.register(
        ToolDefinition(
            name="delete_file",
            description="delete",
            parameters={"type": "object", "properties": {}},
            requires_approval=True,
        ),
        SensitiveHandler(),
    )
    registry.register(ECHO_TOOL_DEFINITION, TrackingHandler())
    executor = ToolExecutor(registry=registry, settings=Settings())
    chat_store = FakeChatStore()
    session = await chat_store.create_session(user_id=tool_context.caller.user_id)  # type: ignore[union-attr]
    runner = ToolRunner(
        tool_executor=executor,
        tool_registry=registry,
        hitl_enabled=True,
        parallel_tools_enabled=True,
        approval_policy=ApprovalPolicy(required_tool_names=frozenset()),
        approval_service=_approval_service(chat_store),
    )
    scratchpad_store = ScratchpadStore()
    scratchpad = scratchpad_store.create("exec-parallel-preflight")
    state = AgentExecutionState(
        execution_id="exec-parallel-preflight",
        status=AgentExecutionStatus.EXECUTING,
    )
    steps = [
        PlannedStep(
            step_id="safe",
            action=StepAction.TOOL_CALL,
            tool_calls=[
                ToolCall(name="echo", arguments={"message": "safe"}, call_id="c1")
            ],
        ),
        PlannedStep(
            step_id="delete",
            action=StepAction.TOOL_CALL,
            tool_calls=[
                ToolCall(name="delete_file", arguments={"path": "/tmp/x"}, call_id="c2")
            ],
        ),
    ]

    with pytest.raises(AgentApprovalPauseError):
        await runner.run_tool_steps(
            steps,
            execution_id="exec-parallel-preflight",
            tool_context=tool_context,
            scratchpad=scratchpad,
            state=state,
            session_id=session.id,
            owner_id=tool_context.caller.user_id,  # type: ignore[union-attr]
        )

    assert TrackingHandler.calls == []


@pytest.mark.anyio
async def test_later_batch_preflights_before_earlier_batch_runs(
    tool_context: ToolExecutionContext,
) -> None:
    registry = ToolRegistry()
    registry.register(
        ToolDefinition(
            name="delete_file",
            description="delete",
            parameters={"type": "object", "properties": {}},
            requires_approval=True,
        ),
        SensitiveHandler(),
    )
    registry.register(ECHO_TOOL_DEFINITION, TrackingHandler())
    executor = ToolExecutor(registry=registry, settings=Settings())
    chat_store = FakeChatStore()
    session = await chat_store.create_session(user_id=tool_context.caller.user_id)  # type: ignore[union-attr]
    runner = ToolRunner(
        tool_executor=executor,
        tool_registry=registry,
        hitl_enabled=True,
        parallel_tools_enabled=True,
        approval_policy=ApprovalPolicy(required_tool_names=frozenset()),
        approval_service=_approval_service(chat_store),
    )
    scratchpad_store = ScratchpadStore()
    scratchpad = scratchpad_store.create("exec-chain-preflight")
    state = AgentExecutionState(
        execution_id="exec-chain-preflight",
        status=AgentExecutionStatus.EXECUTING,
    )
    steps = [
        PlannedStep(
            step_id="safe",
            action=StepAction.TOOL_CALL,
            tool_calls=[
                ToolCall(name="echo", arguments={"message": "safe"}, call_id="c1")
            ],
        ),
        PlannedStep(
            step_id="delete",
            action=StepAction.TOOL_CALL,
            tool_calls=[
                ToolCall(name="delete_file", arguments={"path": "/tmp/x"}, call_id="c2")
            ],
            depends_on=["safe"],
        ),
    ]

    with pytest.raises(AgentApprovalPauseError):
        await runner.run_tool_steps(
            steps,
            execution_id="exec-chain-preflight",
            tool_context=tool_context,
            scratchpad=scratchpad,
            state=state,
            session_id=session.id,
            owner_id=tool_context.caller.user_id,  # type: ignore[union-attr]
        )

    assert TrackingHandler.calls == []


@pytest.mark.anyio
async def test_hitl_fails_closed_when_pause_context_missing(
    sensitive_registry: ToolRegistry,
    tool_context: ToolExecutionContext,
) -> None:
    executor = ToolExecutor(registry=sensitive_registry, settings=Settings())
    chat_store = FakeChatStore()
    runner = ToolRunner(
        tool_executor=executor,
        tool_registry=sensitive_registry,
        hitl_enabled=True,
        approval_policy=ApprovalPolicy(required_tool_names=frozenset()),
        approval_service=_approval_service(chat_store),
    )
    scratchpad_store = ScratchpadStore()
    scratchpad = scratchpad_store.create("exec-fail-closed")
    state = AgentExecutionState(execution_id="exec-fail-closed")
    step = PlannedStep(
        step_id="s1",
        action=StepAction.TOOL_CALL,
        tool_calls=[ToolCall(name="delete_file", arguments={"path": "/tmp/x"})],
    )

    with pytest.raises(HitlError, match="session_id and owner_id"):
        await runner.run_tool_steps(
            [step],
            execution_id="exec-fail-closed",
            tool_context=tool_context,
            scratchpad=scratchpad,
            state=state,
        )

    assert not chat_store.messages


@pytest.mark.anyio
async def test_hitl_fails_closed_when_approval_service_missing(
    sensitive_registry: ToolRegistry,
    tool_context: ToolExecutionContext,
) -> None:
    executor = ToolExecutor(registry=sensitive_registry, settings=Settings())
    runner = ToolRunner(
        tool_executor=executor,
        tool_registry=sensitive_registry,
        hitl_enabled=True,
        approval_policy=ApprovalPolicy(required_tool_names=frozenset()),
        approval_service=None,
    )
    step = PlannedStep(
        step_id="s1",
        action=StepAction.TOOL_CALL,
        tool_calls=[ToolCall(name="delete_file", arguments={"path": "/tmp/x"})],
    )

    with pytest.raises(HitlError, match="AgentApprovalService"):
        await runner.run_tool_steps(
            [step],
            execution_id="exec-no-service",
            tool_context=tool_context,
        )


@pytest.mark.anyio
async def test_agent_executor_pause_emits_no_complete_event(
    sensitive_registry: ToolRegistry,
    tool_context: ToolExecutionContext,
) -> None:
    from app.ai.prompts.manager import create_prompt_manager

    provider = FakeProvider(
        tool_completions=[
            ProviderToolCompletion(
                content="Deleting now.",
                tool_calls=[
                    ProviderToolCall(
                        id="tc-1",
                        name="delete_file",
                        arguments={"path": "/tmp/x"},
                    )
                ],
            ),
        ]
    )
    scratchpad_store = ScratchpadStore()
    publisher = InMemoryStreamPublisher()
    chat_store = FakeChatStore()
    session = await chat_store.create_session(user_id=tool_context.caller.user_id)  # type: ignore[union-attr]
    tool_executor = ToolExecutor(registry=sensitive_registry, settings=Settings())
    runner = ToolRunner(
        tool_executor=tool_executor,
        tool_registry=sensitive_registry,
        stream_publisher=publisher,
        hitl_enabled=True,
        approval_policy=ApprovalPolicy(required_tool_names=frozenset()),
        approval_service=_approval_service(chat_store),
    )
    executor = AgentExecutor(
        planner=ReActPlanner(
            provider=provider,
            tool_registry=sensitive_registry,
            prompt_manager=create_prompt_manager(),
            scratchpad_store=scratchpad_store,
        ),
        provider=provider,
        tool_runner=runner,
        stream_publisher=publisher,
        scratchpad_store=scratchpad_store,
        prompt_manager=create_prompt_manager(),
    )
    context = AgentContext(
        execution_id="exec-agent",
        caller=tool_context.caller,
        session_id=session.id,
    )
    request = AgentRequest(
        messages=[AgentMessage(role="user", content="delete /tmp/x")],
        model="gpt-4o-mini",
        config=AgentConfig(max_iterations=2),
    )

    response = await executor.run(request, context, tool_context=tool_context)

    assert response.finish_reason == "waiting_approval"
    event_types = [event.type for event in publisher.events]
    assert AgentStreamEventType.APPROVAL_REQUIRED in event_types
    assert AgentStreamEventType.COMPLETE not in event_types
    assert AgentStreamEventType.ERROR not in event_types


@pytest.mark.anyio
async def test_complete_chat_waiting_approval_preserves_placeholder(
    sensitive_registry: ToolRegistry,
    tool_context: ToolExecutionContext,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.ai.agent.adapters.chat_adapter import ChatAgentAdapter
    from app.ai.agent.runtime import create_default_agent
    from app.ai.prompts.manager import create_prompt_manager
    from app.providers.factory import ProviderFactory
    from app.schemas.chat import ChatMessageSchema, ChatRequestSchema
    from app.services.chat_service import ChatService
    from app.services.quota_service import QuotaService
    from tests.fakes import FakeGuestQuotaStore, FakeUsageStore

    provider = FakeProvider(
        tool_completions=[
            ProviderToolCompletion(
                content="Deleting now.",
                tool_calls=[
                    ProviderToolCall(
                        id="tc-1",
                        name="delete_file",
                        arguments={"path": "/tmp/x"},
                    )
                ],
            ),
        ]
    )
    monkeypatch.setattr(
        ProviderFactory,
        "get_provider",
        staticmethod(lambda _name, _settings: provider),
    )
    chat_store = FakeChatStore()
    approval_service = _approval_service(chat_store)
    settings = Settings(
        chat_persistence_enabled=True,
        hitl_enabled=True,
        tools_enabled=True,
        request_timeout_seconds=5,
    )
    tool_executor = ToolExecutor(registry=sensitive_registry, settings=settings)
    agent = create_default_agent(
        settings=settings,
        tool_registry=sensitive_registry,
        prompt_manager=create_prompt_manager(),
        tool_executor=tool_executor,
        approval_policy=ApprovalPolicy(required_tool_names=frozenset()),
        approval_service=approval_service,
    )
    chat_service = ChatService(
        settings,
        chat_store=chat_store,
        usage_store=FakeUsageStore(),
        quota_service=QuotaService(store=FakeGuestQuotaStore(), settings=settings),
        prompt_manager=create_prompt_manager(),
    )
    adapter = ChatAgentAdapter(
        agent=agent, chat_service=chat_service, settings=settings
    )

    response = await adapter.complete_chat(
        ChatRequestSchema(
            messages=[ChatMessageSchema(role="user", content="delete /tmp/x")],
            provider="openai",
            model="gpt-4o-mini",
        ),
        tool_context.caller,
        allowed_tool_names=frozenset({"delete_file"}),
    )

    assert response.content == ""
    assert response.session_id is not None
    messages = await chat_store.list_messages(response.session_id)
    assert [message.role for message in messages] == ["user", "assistant"]
    assert messages[1].status == "waiting_approval"
    assert messages[1].content == ""
    assert messages[1].pending_approval_id is not None


def _hitl_executor(
    *,
    sensitive_registry: ToolRegistry,
    chat_store: FakeChatStore,
    scratchpad_store: ScratchpadStore,
    publisher: InMemoryStreamPublisher,
) -> AgentExecutor:
    from app.ai.prompts.manager import create_prompt_manager

    provider = FakeProvider()
    tool_executor = ToolExecutor(registry=sensitive_registry, settings=Settings())
    runner = ToolRunner(
        tool_executor=tool_executor,
        tool_registry=sensitive_registry,
        stream_publisher=publisher,
        hitl_enabled=True,
        approval_policy=ApprovalPolicy(required_tool_names=frozenset()),
        approval_service=_approval_service(chat_store),
    )
    return AgentExecutor(
        planner=ReActPlanner(
            provider=provider,
            tool_registry=sensitive_registry,
            prompt_manager=create_prompt_manager(),
            scratchpad_store=scratchpad_store,
        ),
        provider=provider,
        tool_runner=runner,
        stream_publisher=publisher,
        scratchpad_store=scratchpad_store,
        prompt_manager=create_prompt_manager(),
    )


def _delete_step() -> PlannedStep:
    return PlannedStep(
        step_id="delete-step",
        action=StepAction.TOOL_CALL,
        tool_calls=[ToolCall(name="delete_file", arguments={"path": "/tmp/x"})],
        reasoning="Delete the file.",
    )


@pytest.mark.anyio
async def test_execute_plan_waiting_approval(
    sensitive_registry: ToolRegistry,
    tool_context: ToolExecutionContext,
) -> None:
    from app.ai.agent.models.config import AgentConfig
    from app.ai.agent.models.context import AgentContext
    from app.ai.agent.models.messages import AgentMessage
    from app.ai.agent.models.plan import ExecutionPlan
    from app.ai.agent.models.request import AgentRequest
    from app.ai.agent.models.response import AgentResponse

    scratchpad_store = ScratchpadStore()
    publisher = InMemoryStreamPublisher()
    chat_store = FakeChatStore()
    session = await chat_store.create_session(user_id=tool_context.caller.user_id)  # type: ignore[union-attr]
    executor = _hitl_executor(
        sensitive_registry=sensitive_registry,
        chat_store=chat_store,
        scratchpad_store=scratchpad_store,
        publisher=publisher,
    )
    context = AgentContext(
        execution_id="exec-plan-pause",
        caller=tool_context.caller,
        session_id=session.id,
    )
    request = AgentRequest(
        messages=[AgentMessage(role="user", content="delete /tmp/x")],
        model="gpt-4o-mini",
        config=AgentConfig(max_iterations=2),
    )

    response = await executor.execute_plan(
        ExecutionPlan(steps=[_delete_step()], iteration=0),
        request,
        context,
        tool_context=tool_context,
    )

    assert isinstance(response, AgentResponse)
    assert response.finish_reason == "waiting_approval"
    assert response.metadata["approval_id"]
    messages = await chat_store.list_messages(session.id)
    assert any(message.status == "waiting_approval" for message in messages)


@pytest.mark.anyio
async def test_execute_step_waiting_approval(
    sensitive_registry: ToolRegistry,
    tool_context: ToolExecutionContext,
) -> None:
    from app.ai.agent.models.config import AgentConfig
    from app.ai.agent.models.context import AgentContext
    from app.ai.agent.models.messages import AgentMessage
    from app.ai.agent.models.request import AgentRequest
    from app.ai.agent.models.response import AgentResponse

    scratchpad_store = ScratchpadStore()
    publisher = InMemoryStreamPublisher()
    chat_store = FakeChatStore()
    session = await chat_store.create_session(user_id=tool_context.caller.user_id)  # type: ignore[union-attr]
    executor = _hitl_executor(
        sensitive_registry=sensitive_registry,
        chat_store=chat_store,
        scratchpad_store=scratchpad_store,
        publisher=publisher,
    )
    context = AgentContext(
        execution_id="exec-step-pause",
        caller=tool_context.caller,
        session_id=session.id,
    )
    request = AgentRequest(
        messages=[AgentMessage(role="user", content="delete /tmp/x")],
        model="gpt-4o-mini",
        config=AgentConfig(max_iterations=2),
    )

    result = await executor.execute_step(
        _delete_step(),
        request,
        context,
        tool_context=tool_context,
    )

    assert isinstance(result, AgentResponse)
    assert result.finish_reason == "waiting_approval"
    assert result.metadata["approval_id"]
    messages = await chat_store.list_messages(session.id)
    assert any(message.status == "waiting_approval" for message in messages)


@pytest.mark.anyio
async def test_sse_adapter_maps_approval_required_frame() -> None:
    from app.ai.agent.models.events import AgentStreamEvent
    from app.ai.agent.streaming.adapter import sse_frame_from_agent_event
    from app.schemas.chat import ApprovalRequiredFrame

    approval_id = uuid.uuid4()
    correlation_id = uuid.uuid4()
    event = AgentStreamEvent.approval_required(
        "exec-sse",
        approval_id=approval_id,
        approval_correlation_id=correlation_id,
        proposed_calls=[
            {"name": "delete_file", "arguments": {"path": "/x"}, "call_id": "c1"}
        ],
    )
    mapped = sse_frame_from_agent_event(event, response_id="resp-1")
    assert mapped is not None
    name, frame = mapped
    assert name == "approval_required"
    assert isinstance(frame, ApprovalRequiredFrame)
    assert frame.approval_id == approval_id
