"""Tests for agent execution loop (Phase 8)."""

from __future__ import annotations

import ast
import uuid
from collections.abc import Iterator
from pathlib import Path

import pytest

from app.ai.agent import StepAction
from app.ai.agent.executor import (
    AgentExecutor,
    FinalizeResult,
    ITERATION_LIMIT_MESSAGE,
)
from app.ai.agent.models.config import AgentConfig
from app.ai.agent.models.context import AgentContext
from app.ai.agent.models.events import AgentStreamEventType
from app.ai.agent.models.messages import AgentMessage
from app.ai.agent.models.plan import ExecutionPlan, PlannedStep
from app.ai.agent.models.request import AgentRequest
from app.ai.agent.planner import ReActPlanner
from app.ai.agent.scratchpad import Scratchpad, ScratchpadStore
from app.ai.agent.executor.llm_step import stream_final_answer
from app.ai.agent.streaming import InMemoryStreamPublisher
from app.ai.prompts.manager import create_prompt_manager
from app.ai.tools.executor import ToolExecutor
from app.ai.tools.registry import ToolRegistry
from app.ai.tools.schemas import ToolCall, ToolExecutionContext
from app.ai.tools.stubs.echo import ECHO_TOOL_DEFINITION, echo_handler
from app.core.caller import CallerContext
from app.core.config import Settings
from app.ai.agent.executor import ToolRunner
from app.providers.base import (
    ProviderCompletion,
    ProviderToolCall,
    ProviderToolCompletion,
)
from tests.fakes import FakeProvider


@pytest.fixture
def scratchpad_store() -> Iterator[ScratchpadStore]:
    store = ScratchpadStore()
    yield store
    store.clear()


@pytest.fixture
def tool_registry() -> ToolRegistry:
    registry = ToolRegistry()
    registry.register(ECHO_TOOL_DEFINITION, echo_handler())
    return registry


@pytest.fixture
def tool_context() -> ToolExecutionContext:
    return ToolExecutionContext(
        caller=CallerContext.for_user(uuid.uuid4()),
        request_id="req-agent-executor",
    )


@pytest.fixture
def prompt_manager():
    return create_prompt_manager()


def _request(
    *,
    config: AgentConfig | None = None,
    content: str = "Echo hello",
) -> AgentRequest:
    return AgentRequest(
        messages=[AgentMessage(role="user", content=content)],
        model="gpt-4o-mini",
        config=config,
    )


def _executor(
    *,
    provider: FakeProvider,
    tool_registry: ToolRegistry,
    prompt_manager,
    scratchpad_store: ScratchpadStore,
    publisher: InMemoryStreamPublisher | None = None,
    parallel_tools_enabled: bool = False,
) -> AgentExecutor:
    tool_executor = ToolExecutor(
        registry=tool_registry,
        settings=Settings(request_timeout_seconds=5),
    )
    publisher = publisher or InMemoryStreamPublisher()
    tool_runner = ToolRunner(
        tool_executor=tool_executor,
        stream_publisher=publisher,
        parallel_tools_enabled=parallel_tools_enabled,
    )
    planner = ReActPlanner(
        provider=provider,
        tool_registry=tool_registry,
        prompt_manager=prompt_manager,
        scratchpad_store=scratchpad_store,
    )
    return AgentExecutor(
        planner=planner,
        provider=provider,
        tool_runner=tool_runner,
        stream_publisher=publisher,
        scratchpad_store=scratchpad_store,
        prompt_manager=prompt_manager,
    )


@pytest.mark.anyio
async def test_agent_executor_e2e_tool_round(
    tool_registry: ToolRegistry,
    prompt_manager,
    scratchpad_store: ScratchpadStore,
    tool_context: ToolExecutionContext,
) -> None:
    provider = FakeProvider(
        tool_completions=[
            ProviderToolCompletion(
                content="Echoing now.",
                tool_calls=[
                    ProviderToolCall(
                        id="call-echo",
                        name="echo",
                        arguments={"message": "hello"},
                    )
                ],
            ),
            ProviderToolCompletion(
                content="The echo returned hello.",
                tool_calls=[],
                finish_reason="stop",
            ),
        ]
    )
    executor = _executor(
        provider=provider,
        tool_registry=tool_registry,
        prompt_manager=prompt_manager,
        scratchpad_store=scratchpad_store,
    )
    context = AgentContext(execution_id="exec-e2e")

    response = await executor.run(_request(), context, tool_context=tool_context)

    assert response.content == "The echo returned hello."
    assert response.tools_used == ["echo"]
    assert response.iterations == 2
    assert response.finish_reason == "stop"
    assert provider.tool_completion_calls == 2
    assert scratchpad_store.get(context.execution_id) is None


@pytest.mark.anyio
async def test_agent_executor_multi_iteration_tool_loop(
    tool_registry: ToolRegistry,
    prompt_manager,
    scratchpad_store: ScratchpadStore,
    tool_context: ToolExecutionContext,
) -> None:
    provider = FakeProvider(
        tool_completions=[
            ProviderToolCompletion(
                content="First echo.",
                tool_calls=[
                    ProviderToolCall(
                        id="call-1",
                        name="echo",
                        arguments={"message": "first"},
                    )
                ],
            ),
            ProviderToolCompletion(
                content="Second echo.",
                tool_calls=[
                    ProviderToolCall(
                        id="call-2",
                        name="echo",
                        arguments={"message": "second"},
                    )
                ],
            ),
            ProviderToolCompletion(
                content="All echoes complete.",
                tool_calls=[],
                finish_reason="stop",
            ),
        ]
    )
    executor = _executor(
        provider=provider,
        tool_registry=tool_registry,
        prompt_manager=prompt_manager,
        scratchpad_store=scratchpad_store,
    )

    response = await executor.run(
        _request(),
        AgentContext(execution_id="exec-multi"),
        tool_context=tool_context,
    )

    assert response.content == "All echoes complete."
    assert response.tools_used == ["echo"]
    assert response.iterations == 3
    assert provider.tool_completion_calls == 3


@pytest.mark.anyio
async def test_agent_executor_llm_only_streams_final_answer(
    prompt_manager,
    scratchpad_store: ScratchpadStore,
    tool_context: ToolExecutionContext,
) -> None:
    empty_registry = ToolRegistry()
    provider = FakeProvider(response="Hello without tools.")
    publisher = InMemoryStreamPublisher()
    executor = _executor(
        provider=provider,
        tool_registry=empty_registry,
        prompt_manager=prompt_manager,
        scratchpad_store=scratchpad_store,
        publisher=publisher,
    )

    response = await executor.run(
        _request(),
        AgentContext(execution_id="exec-llm-only"),
        tool_context=tool_context,
    )

    assert response.content == "Hello without tools."
    assert response.tools_used == []
    assert response.iterations == 1
    assert response.finish_reason == "stop"
    assert provider.tool_completion_calls == 0
    token_events = [
        event for event in publisher.events if event.type == AgentStreamEventType.TOKEN
    ]
    assert token_events
    assert (
        "".join(
            event.typed_payload().content  # type: ignore[union-attr]
            for event in token_events
        )
        == "Hello without tools."
    )


@pytest.mark.anyio
async def test_agent_executor_iteration_limit_message(
    tool_registry: ToolRegistry,
    prompt_manager,
    scratchpad_store: ScratchpadStore,
    tool_context: ToolExecutionContext,
) -> None:
    provider = FakeProvider(
        tool_completions=[
            ProviderToolCompletion(
                content="Keep echoing.",
                tool_calls=[
                    ProviderToolCall(
                        id="call-1",
                        name="echo",
                        arguments={"message": "loop"},
                    )
                ],
            ),
            ProviderToolCompletion(
                content="Still echoing.",
                tool_calls=[
                    ProviderToolCall(
                        id="call-2",
                        name="echo",
                        arguments={"message": "loop"},
                    )
                ],
            ),
        ]
    )
    # Override last completion to have no content — triggers generic limit message.
    provider._tool_completions[-1] = ProviderToolCompletion(
        content=None,
        tool_calls=[
            ProviderToolCall(
                id="call-2",
                name="echo",
                arguments={"message": "loop"},
            )
        ],
    )
    executor = _executor(
        provider=provider,
        tool_registry=tool_registry,
        prompt_manager=prompt_manager,
        scratchpad_store=scratchpad_store,
    )
    config = AgentConfig(max_iterations=2)

    response = await executor.run(
        _request(config=config),
        AgentContext(execution_id="exec-limit"),
        tool_context=tool_context,
    )

    assert response.content == ITERATION_LIMIT_MESSAGE
    assert response.tools_used == ["echo"]
    assert response.iterations == 2
    assert response.finish_reason == "tool_iteration_cap"


@pytest.mark.anyio
async def test_agent_executor_iteration_limit_uses_last_planner_content(
    tool_registry: ToolRegistry,
    prompt_manager,
    scratchpad_store: ScratchpadStore,
    tool_context: ToolExecutionContext,
) -> None:
    provider = FakeProvider(
        tool_completions=[
            ProviderToolCompletion(
                content="Partial answer before cap.",
                tool_calls=[
                    ProviderToolCall(
                        id="call-1",
                        name="echo",
                        arguments={"message": "loop"},
                    )
                ],
            ),
            ProviderToolCompletion(
                content="More tools.",
                tool_calls=[
                    ProviderToolCall(
                        id="call-2",
                        name="echo",
                        arguments={"message": "loop"},
                    )
                ],
            ),
        ]
    )
    executor = _executor(
        provider=provider,
        tool_registry=tool_registry,
        prompt_manager=prompt_manager,
        scratchpad_store=scratchpad_store,
    )

    response = await executor.run(
        _request(config=AgentConfig(max_iterations=2)),
        AgentContext(execution_id="exec-limit-content"),
        tool_context=tool_context,
    )

    assert response.content == "More tools."
    assert response.finish_reason == "tool_iteration_cap"


@pytest.mark.anyio
async def test_agent_executor_emits_streaming_events(
    tool_registry: ToolRegistry,
    prompt_manager,
    scratchpad_store: ScratchpadStore,
    tool_context: ToolExecutionContext,
) -> None:
    provider = FakeProvider(
        tool_completions=[
            ProviderToolCompletion(
                content="Echo.",
                tool_calls=[
                    ProviderToolCall(
                        id="call-echo",
                        name="echo",
                        arguments={"message": "hi"},
                    )
                ],
            ),
            ProviderToolCompletion(
                content="Done.",
                tool_calls=[],
            ),
        ]
    )
    publisher = InMemoryStreamPublisher()
    executor = _executor(
        provider=provider,
        tool_registry=tool_registry,
        prompt_manager=prompt_manager,
        scratchpad_store=scratchpad_store,
        publisher=publisher,
    )

    await executor.run(
        _request(),
        AgentContext(execution_id="exec-stream"),
        tool_context=tool_context,
    )

    event_types = [event.type for event in publisher.events]
    assert AgentStreamEventType.START in event_types
    assert AgentStreamEventType.PLANNING in event_types
    assert AgentStreamEventType.TOOL_START in event_types
    assert AgentStreamEventType.TOOL_END in event_types
    assert AgentStreamEventType.TOKEN in event_types
    assert AgentStreamEventType.COMPLETE in event_types
    assert event_types[0] == AgentStreamEventType.START
    assert event_types[-1] == AgentStreamEventType.COMPLETE


@pytest.mark.anyio
async def test_agent_executor_execute_plan_tool_step(
    tool_registry: ToolRegistry,
    prompt_manager,
    scratchpad_store: ScratchpadStore,
    tool_context: ToolExecutionContext,
) -> None:
    provider = FakeProvider()
    executor = _executor(
        provider=provider,
        tool_registry=tool_registry,
        prompt_manager=prompt_manager,
        scratchpad_store=scratchpad_store,
    )
    plan = ExecutionPlan(
        steps=[
            PlannedStep(
                step_id="tool-step",
                action=StepAction.TOOL_CALL,
                tool_calls=[
                    ToolCall(
                        name="echo",
                        arguments={"message": "plan"},
                        call_id="call-plan",
                    )
                ],
                reasoning="Run echo.",
            )
        ],
        iteration=0,
    )

    response = await executor.execute_plan(
        plan,
        _request(),
        AgentContext(execution_id="exec-plan"),
        tool_context=tool_context,
    )

    assert response.tools_used == ["echo"]
    assert response.finish_reason == "continue"
    scratchpad = scratchpad_store.require("exec-plan")
    assert any(entry.kind == "tool" for entry in scratchpad.entries)


@pytest.mark.anyio
async def test_agent_executor_execute_step_finalize(
    tool_registry: ToolRegistry,
    prompt_manager,
    scratchpad_store: ScratchpadStore,
) -> None:
    provider = FakeProvider()
    publisher = InMemoryStreamPublisher()
    executor = _executor(
        provider=provider,
        tool_registry=tool_registry,
        prompt_manager=prompt_manager,
        scratchpad_store=scratchpad_store,
        publisher=publisher,
    )
    step = PlannedStep(
        step_id="finalize-0",
        action=StepAction.FINALIZE,
        reasoning="Direct finalize answer.",
    )

    result = await executor.execute_step(
        step,
        _request(),
        AgentContext(execution_id="exec-step-finalize"),
    )

    assert isinstance(result, FinalizeResult)
    assert result.content == "Direct finalize answer."
    assert result.finish_reason == "stop"
    token_events = [
        event for event in publisher.events if event.type == AgentStreamEventType.TOKEN
    ]
    assert token_events


@pytest.mark.anyio
async def test_agent_executor_execute_step_llm(
    tool_registry: ToolRegistry,
    prompt_manager,
    scratchpad_store: ScratchpadStore,
) -> None:
    provider = FakeProvider(response="Intermediate LLM output.")
    executor = _executor(
        provider=provider,
        tool_registry=tool_registry,
        prompt_manager=prompt_manager,
        scratchpad_store=scratchpad_store,
    )
    context = AgentContext(execution_id="exec-step-llm")
    scratchpad_store.create(context.execution_id)
    scratchpad_store.require(context.execution_id).extend_messages(
        [AgentMessage(role="user", content="Think")]
    )
    step = PlannedStep(step_id="llm-step", action=StepAction.LLM)

    completion = await executor.execute_step(step, _request(), context)

    assert isinstance(completion, ProviderCompletion)
    assert completion.content == "Intermediate LLM output."
    scratchpad = scratchpad_store.require(context.execution_id)
    assert scratchpad.entries[-1].content == "Intermediate LLM output."


@pytest.mark.anyio
async def test_stream_final_answer_preserves_tool_round_messages_for_provider() -> None:
    """After a tool round, the provider must receive tool-call and tool-result turns."""
    scratchpad = Scratchpad("exec-tool-context")
    scratchpad.extend_messages([AgentMessage(role="user", content="Echo hi")])
    scratchpad.append_provider_message(
        {
            "role": "assistant",
            "content": "Echoing.",
            "tool_calls": [
                {
                    "id": "call-1",
                    "type": "function",
                    "function": {
                        "name": "echo",
                        "arguments": '{"message":"hi"}',
                    },
                }
            ],
        }
    )
    scratchpad.append_tool_result(
        tool_call_id="call-1",
        content='{"success": true, "data": {"echo": "hi"}}',
    )
    captured_messages: list[object] = []

    class CapturingProvider(FakeProvider):
        async def stream_chat(  # type: ignore[override]
            self,
            messages,
            model,
            temperature=0.7,
            *,
            max_tokens=None,
        ):
            captured_messages.extend(messages)
            async for chunk in super().stream_chat(
                messages,
                model,
                temperature,
                max_tokens=max_tokens,
            ):
                yield chunk

    provider = CapturingProvider(response="Done after tools.")
    publisher = InMemoryStreamPublisher()
    request = AgentRequest(
        messages=[AgentMessage(role="user", content="Echo hi")],
        model="gpt-4o-mini",
    )

    content = await stream_final_answer(
        provider,
        request=request,
        scratchpad=scratchpad,
        execution_id="exec-tool-context",
        publisher=publisher,
    )

    assert content == "Done after tools."
    tool_call_message = next(
        message
        for message in captured_messages
        if isinstance(message, dict) and message.get("role") == "assistant"
    )
    tool_result_message = next(
        message
        for message in captured_messages
        if isinstance(message, dict) and message.get("role") == "tool"
    )
    assert tool_call_message.get("tool_calls")
    assert tool_result_message.get("tool_call_id") == "call-1"


def test_executor_modules_have_no_transport_or_domain_imports() -> None:
    repo_root = Path(__file__).resolve().parents[3]
    module_paths = [
        repo_root / "app/ai/agent/executor/agent_executor.py",
        repo_root / "app/ai/agent/executor/finalizer.py",
        repo_root / "app/ai/agent/executor/llm_step.py",
    ]
    forbidden_roots = ("app.services", "app.db", "app.schemas.chat", "fastapi")

    for module_path in module_paths:
        tree = ast.parse(module_path.read_text(encoding="utf-8"))
        imported_modules = {
            node.module
            for node in ast.walk(tree)
            if isinstance(node, ast.ImportFrom) and node.module is not None
        }
        imported_modules.update(
            alias.name
            for node in ast.walk(tree)
            if isinstance(node, ast.Import)
            for alias in node.names
        )
        for forbidden in forbidden_roots:
            assert not any(
                module == forbidden or module.startswith(f"{forbidden}.")
                for module in imported_modules
            ), f"{module_path.name} must not import {forbidden}"
