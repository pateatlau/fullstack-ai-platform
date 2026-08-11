"""HITL decide/resume tests (Epic 09 Phase 3)."""

from __future__ import annotations

import uuid
from collections.abc import Iterator

import pytest

from app.ai.agent.executor import AgentExecutor, ToolRunner
from app.ai.agent.models.config import AgentConfig
from app.ai.agent.models.context import AgentContext
from app.ai.agent.models.messages import AgentMessage
from app.ai.agent.models.request import AgentRequest
from app.ai.agent.models.state import AgentExecutionState, AgentExecutionStatus
from app.ai.agent.planner import ReActPlanner
from app.ai.agent.scratchpad import ScratchpadStore
from app.ai.agent.streaming import InMemoryStreamPublisher, NoOpStreamPublisher
from app.ai.hitl.exceptions import (
    ApprovalDecisionConflictError,
    ApprovalValidationError,
)
from app.ai.hitl.models import ApprovalKind, ApprovalStatus, ProposedToolCall
from app.ai.hitl.service import AgentApprovalService
from app.ai.tools.executor import ToolExecutor
from app.ai.tools.registry import ToolRegistry
from app.ai.tools.schemas import ToolDefinition, ToolExecutionContext, ToolResult
from app.core.caller import CallerContext
from app.core.config import Settings
from app.providers.base import ProviderToolCall, ProviderToolCompletion
from tests.ai.hitl.fakes import InMemoryApprovalStore
from tests.fakes import FakeChatStore, FakeProvider


class SensitiveHandler:
    call_count: int = 0
    last_path: str | None = None

    async def execute(
        self,
        args: dict[str, object],
        context: ToolExecutionContext,
    ) -> ToolResult:
        del context
        SensitiveHandler.call_count += 1
        SensitiveHandler.last_path = str(args.get("path"))
        return ToolResult(success=True, data={"deleted": True})


class FailingHandler:
    async def execute(
        self,
        args: dict[str, object],
        context: ToolExecutionContext,
    ) -> ToolResult:
        del args, context
        return ToolResult(success=False, error="boom", error_code="tool_failed")


@pytest.fixture(autouse=True)
def _reset_handlers() -> Iterator[None]:
    SensitiveHandler.call_count = 0
    SensitiveHandler.last_path = None
    yield


def _registry(*, failing: bool = False) -> ToolRegistry:
    registry = ToolRegistry()
    handler = FailingHandler() if failing else SensitiveHandler()
    registry.register(
        ToolDefinition(
            name="delete_file",
            description="delete",
            parameters={
                "type": "object",
                "properties": {"path": {"type": "string"}},
                "required": ["path"],
            },
            requires_approval=True,
        ),
        handler,
    )
    return registry


async def _seed_pending(
    *,
    store: InMemoryApprovalStore,
    chat_store: FakeChatStore,
    owner_id: uuid.UUID,
) -> tuple[uuid.UUID, uuid.UUID]:
    session = await chat_store.create_session(user_id=owner_id)
    approval = await store.create(
        session_id=session.id,
        owner_id=owner_id,
        execution_id="exec-decide",
        approval_correlation_id=uuid.uuid4(),
        proposed_calls=[
            ProposedToolCall(
                name="delete_file", arguments={"path": "/tmp/x"}, call_id="c1"
            )
        ],
        paused_scratchpad=[{"kind": "thought", "content": "delete it"}],
        paused_state=AgentExecutionState(
            execution_id="exec-decide",
            status=AgentExecutionStatus.WAITING_APPROVAL,
            current_iteration=1,
        ).model_dump(mode="json"),
    )
    placeholder = await chat_store.add_message(
        session_id=session.id,
        seq=1,
        role="assistant",
        content="",
        status="waiting_approval",
        pending_approval_id=approval.id,
    )
    await store.link_pending_message(approval.id, pending_message_id=placeholder.id)
    return approval.id, session.id


def _service(
    store: InMemoryApprovalStore,
    chat_store: FakeChatStore,
    registry: ToolRegistry,
) -> AgentApprovalService:
    return AgentApprovalService(
        approval_store=store,
        chat_store=chat_store,
        tool_registry=registry,
        tool_executor=ToolExecutor(registry=registry, settings=Settings()),
        scratchpad_store=ScratchpadStore(),
    )


def _resume_executor(
    *,
    registry: ToolRegistry,
    scratchpad_store: ScratchpadStore,
    provider: FakeProvider | None = None,
) -> AgentExecutor:
    from app.ai.prompts.manager import create_prompt_manager

    provider = provider or FakeProvider(response="Done.")
    tool_executor = ToolExecutor(registry=registry, settings=Settings())
    runner = ToolRunner(
        tool_executor=tool_executor,
        tool_registry=registry,
        stream_publisher=NoOpStreamPublisher(),
        hitl_enabled=False,
    )
    return AgentExecutor(
        planner=ReActPlanner(
            provider=provider,
            tool_registry=registry,
            prompt_manager=create_prompt_manager(),
            scratchpad_store=scratchpad_store,
        ),
        provider=provider,
        tool_runner=runner,
        stream_publisher=NoOpStreamPublisher(),
        scratchpad_store=scratchpad_store,
        prompt_manager=create_prompt_manager(),
    )


@pytest.mark.anyio
async def test_approve_as_is_executes_and_finalizes() -> None:
    owner_id = uuid.uuid4()
    store = InMemoryApprovalStore()
    chat_store = FakeChatStore()
    registry = _registry()
    service = _service(store, chat_store, registry)
    scratchpad_store = ScratchpadStore()
    approval_id, session_id = await _seed_pending(
        store=store, chat_store=chat_store, owner_id=owner_id
    )
    caller = CallerContext.for_user(owner_id)

    _, response = await service.approve_and_resume(
        approval_id,
        owner_id=owner_id,
        executor=_resume_executor(registry=registry, scratchpad_store=scratchpad_store),
        request=AgentRequest(
            messages=[AgentMessage(role="user", content="delete")],
            model="gpt-4o-mini",
            config=AgentConfig(max_iterations=2),
        ),
        context=AgentContext(
            execution_id="exec-decide",
            caller=caller,
            session_id=session_id,
        ),
        tool_context=ToolExecutionContext(caller=caller),
        stream_publisher=InMemoryStreamPublisher(),
    )

    assert SensitiveHandler.call_count == 1
    assert SensitiveHandler.last_path == "/tmp/x"
    assert response.finish_reason == "stop"
    approval = await store.get(approval_id)
    assert approval is not None
    assert approval.status == ApprovalStatus.APPROVED
    messages = await chat_store.list_messages(session_id)
    assistant = next(m for m in messages if m.role == "assistant")
    assert assistant.status == "complete"
    assert assistant.content == "Done."


@pytest.mark.anyio
async def test_approve_with_edited_calls_executes_edited_arguments() -> None:
    owner_id = uuid.uuid4()
    store = InMemoryApprovalStore()
    chat_store = FakeChatStore()
    registry = _registry()
    service = _service(store, chat_store, registry)
    scratchpad_store = ScratchpadStore()
    approval_id, session_id = await _seed_pending(
        store=store, chat_store=chat_store, owner_id=owner_id
    )
    caller = CallerContext.for_user(owner_id)
    edited = [
        ProposedToolCall(
            name="delete_file", arguments={"path": "/edited"}, call_id="c1"
        )
    ]

    await service.approve_and_resume(
        approval_id,
        owner_id=owner_id,
        executor=_resume_executor(registry=registry, scratchpad_store=scratchpad_store),
        request=AgentRequest(
            messages=[AgentMessage(role="user", content="delete")],
            model="gpt-4o-mini",
            config=AgentConfig(max_iterations=2),
        ),
        context=AgentContext(
            execution_id="exec-decide",
            caller=caller,
            session_id=session_id,
        ),
        tool_context=ToolExecutionContext(caller=caller),
        stream_publisher=InMemoryStreamPublisher(),
        edited_calls=edited,
    )

    assert SensitiveHandler.last_path == "/edited"
    revisions = await store.list_revisions(
        approval_id, approval_kind=ApprovalKind.AGENT_TOOL
    )
    assert len(revisions) == 1


@pytest.mark.anyio
async def test_approve_invalid_edited_calls_stays_pending() -> None:
    owner_id = uuid.uuid4()
    store = InMemoryApprovalStore()
    chat_store = FakeChatStore()
    registry = _registry()
    service = _service(store, chat_store, registry)
    approval_id, session_id = await _seed_pending(
        store=store, chat_store=chat_store, owner_id=owner_id
    )
    caller = CallerContext.for_user(owner_id)

    with pytest.raises(ApprovalValidationError):
        await service.approve_and_resume(
            approval_id,
            owner_id=owner_id,
            executor=_resume_executor(
                registry=registry, scratchpad_store=ScratchpadStore()
            ),
            request=AgentRequest(
                messages=[AgentMessage(role="user", content="delete")],
                model="gpt-4o-mini",
            ),
            context=AgentContext(
                execution_id="exec-decide",
                caller=caller,
                session_id=session_id,
            ),
            tool_context=ToolExecutionContext(caller=caller),
            stream_publisher=InMemoryStreamPublisher(),
            edited_calls=[
                ProposedToolCall(
                    name="delete_file",
                    arguments={"path": 1},  # type: ignore[arg-type]
                    call_id="c1",
                )
            ],
        )

    approval = await store.get(approval_id)
    assert approval is not None
    assert approval.status == ApprovalStatus.PENDING
    assert not store.revisions
    assert SensitiveHandler.call_count == 0


@pytest.mark.anyio
async def test_reject_never_executes_tool() -> None:
    owner_id = uuid.uuid4()
    store = InMemoryApprovalStore()
    chat_store = FakeChatStore()
    registry = _registry()
    service = _service(store, chat_store, registry)
    approval_id, session_id = await _seed_pending(
        store=store, chat_store=chat_store, owner_id=owner_id
    )

    result = await service.decide(
        approval_id,
        owner_id=owner_id,
        decision="rejected",
        reason="too risky",
    )

    assert result.status == ApprovalStatus.REJECTED
    assert SensitiveHandler.call_count == 0
    messages = await chat_store.list_messages(session_id)
    assistant = next(m for m in messages if m.role == "assistant")
    assert assistant.status == "rejected"


@pytest.mark.anyio
async def test_duplicate_decision_raises_409() -> None:
    owner_id = uuid.uuid4()
    store = InMemoryApprovalStore()
    chat_store = FakeChatStore()
    service = _service(store, chat_store, registry=_registry())
    approval_id, _ = await _seed_pending(
        store=store, chat_store=chat_store, owner_id=owner_id
    )
    await service.decide(approval_id, owner_id=owner_id, decision="rejected")

    with pytest.raises(ApprovalDecisionConflictError):
        await service.decide(approval_id, owner_id=owner_id, decision="rejected")


@pytest.mark.anyio
async def test_execution_failure_keeps_approval_approved() -> None:
    owner_id = uuid.uuid4()
    store = InMemoryApprovalStore()
    chat_store = FakeChatStore()
    registry = _registry(failing=True)
    service = _service(store, chat_store, registry)
    scratchpad_store = ScratchpadStore()
    approval_id, session_id = await _seed_pending(
        store=store, chat_store=chat_store, owner_id=owner_id
    )
    caller = CallerContext.for_user(owner_id)

    _, response = await service.approve_and_resume(
        approval_id,
        owner_id=owner_id,
        executor=_resume_executor(
            registry=registry,
            scratchpad_store=scratchpad_store,
            provider=FakeProvider(response="Done."),
        ),
        request=AgentRequest(
            messages=[AgentMessage(role="user", content="delete")],
            model="gpt-4o-mini",
            config=AgentConfig(max_iterations=2),
        ),
        context=AgentContext(
            execution_id="exec-decide",
            caller=caller,
            session_id=session_id,
        ),
        tool_context=ToolExecutionContext(caller=caller),
        stream_publisher=InMemoryStreamPublisher(),
    )

    approval = await store.get(approval_id)
    assert approval is not None
    assert approval.status == ApprovalStatus.APPROVED
    messages = await chat_store.list_messages(session_id)
    assistant = next(m for m in messages if m.role == "assistant")
    assert assistant.status == "error"
    assert response.finish_reason == "stop"


@pytest.mark.anyio
async def test_revise_then_approve_uses_latest_revision() -> None:
    owner_id = uuid.uuid4()
    store = InMemoryApprovalStore()
    chat_store = FakeChatStore()
    registry = _registry()
    service = _service(store, chat_store, registry)
    scratchpad_store = ScratchpadStore()
    approval_id, session_id = await _seed_pending(
        store=store, chat_store=chat_store, owner_id=owner_id
    )
    caller = CallerContext.for_user(owner_id)
    revised = [
        ProposedToolCall(
            name="delete_file", arguments={"path": "/revised"}, call_id="c1"
        )
    ]
    await service.revise(approval_id, edited_calls=revised, owner_id=owner_id)

    await service.approve_and_resume(
        approval_id,
        owner_id=owner_id,
        executor=_resume_executor(registry=registry, scratchpad_store=scratchpad_store),
        request=AgentRequest(
            messages=[AgentMessage(role="user", content="delete")],
            model="gpt-4o-mini",
            config=AgentConfig(max_iterations=2),
        ),
        context=AgentContext(
            execution_id="exec-decide",
            caller=caller,
            session_id=session_id,
        ),
        tool_context=ToolExecutionContext(caller=caller),
        stream_publisher=InMemoryStreamPublisher(),
    )

    assert SensitiveHandler.last_path == "/revised"
    revisions = await store.list_revisions(
        approval_id, approval_kind=ApprovalKind.AGENT_TOOL
    )
    assert len(revisions) == 1


@pytest.mark.anyio
async def test_resumed_turn_second_pause_new_correlation_id() -> None:
    from app.ai.hitl.policy import ApprovalPolicy
    from app.ai.prompts.manager import create_prompt_manager

    owner_id = uuid.uuid4()
    store = InMemoryApprovalStore()
    chat_store = FakeChatStore()
    registry = _registry()
    service = _service(store, chat_store, registry)
    scratchpad_store = ScratchpadStore()
    approval_id, session_id = await _seed_pending(
        store=store, chat_store=chat_store, owner_id=owner_id
    )
    caller = CallerContext.for_user(owner_id)
    provider = FakeProvider(
        tool_completions=[
            ProviderToolCompletion(
                content="Deleting again.",
                tool_calls=[
                    ProviderToolCall(
                        id="tc-2",
                        name="delete_file",
                        arguments={"path": "/tmp/y"},
                    )
                ],
            ),
            ProviderToolCompletion(
                content="Done.", finish_reason="stop", tool_calls=[]
            ),
        ]
    )
    tool_executor = ToolExecutor(registry=registry, settings=Settings())
    runner = ToolRunner(
        tool_executor=tool_executor,
        tool_registry=registry,
        stream_publisher=InMemoryStreamPublisher(),
        hitl_enabled=True,
        approval_policy=ApprovalPolicy(required_tool_names=frozenset()),
        approval_service=service,
    )
    executor = AgentExecutor(
        planner=ReActPlanner(
            provider=provider,
            tool_registry=registry,
            prompt_manager=create_prompt_manager(),
            scratchpad_store=scratchpad_store,
        ),
        provider=provider,
        tool_runner=runner,
        stream_publisher=InMemoryStreamPublisher(),
        scratchpad_store=scratchpad_store,
        prompt_manager=create_prompt_manager(),
    )

    _, response = await service.approve_and_resume(
        approval_id,
        owner_id=owner_id,
        executor=executor,
        request=AgentRequest(
            messages=[AgentMessage(role="user", content="delete")],
            model="gpt-4o-mini",
            config=AgentConfig(max_iterations=3),
        ),
        context=AgentContext(
            execution_id="exec-decide",
            caller=caller,
            session_id=session_id,
        ),
        tool_context=ToolExecutionContext(caller=caller),
        stream_publisher=InMemoryStreamPublisher(),
    )

    assert response.finish_reason == "waiting_approval"
    assert len(store.rows) == 2
    assert (
        store.rows[0].approval_correlation_id != store.rows[1].approval_correlation_id
    )
    messages = await chat_store.list_messages(session_id)
    assistants = [m for m in messages if m.role == "assistant"]
    assert len(assistants) == 2
    assert assistants[0].status == "complete"
    assert assistants[0].pending_approval_id is None
    assert assistants[1].status == "waiting_approval"
