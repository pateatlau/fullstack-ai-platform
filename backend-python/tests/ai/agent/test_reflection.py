"""Tests for agent reflection engine (Phase 9)."""

from __future__ import annotations

import ast
from collections.abc import Iterator
from pathlib import Path
from typing import ClassVar

import pytest

from app.ai.agent.executor import AgentExecutor, ToolRunner, aggregate_tool_results
from app.ai.agent.executor.result_aggregator import ToolRunRecord
from app.ai.agent.models.config import AgentConfig
from app.ai.agent.models.context import AgentContext
from app.ai.agent.models.events import (
    AgentStreamEventType,
    ReflectionDecision,
    ReflectionEventPayload,
)
from app.ai.agent.models.messages import AgentMessage
from app.ai.agent.models.request import AgentRequest
from app.ai.agent.planner import ReActPlanner
from app.ai.agent.reflection import ReflectionEngine, evaluate_rule_based
from app.ai.agent.reflection.engine import parse_reflection_response
from app.ai.agent.scratchpad import Scratchpad, ScratchpadStore
from app.ai.agent.streaming import InMemoryStreamPublisher
from app.ai.prompts.manager import create_prompt_manager
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
from tests.fakes import FakeProvider


@pytest.fixture
def scratchpad_store() -> Iterator[ScratchpadStore]:
    store = ScratchpadStore()
    yield store
    store.clear()


@pytest.fixture
def prompt_manager():
    return create_prompt_manager()


@pytest.fixture
def tool_context() -> ToolExecutionContext:
    return ToolExecutionContext(
        caller=CallerContext.for_user(__import__("uuid").uuid4()),
        request_id="req-reflection",
    )


@pytest.fixture
def tool_registry() -> ToolRegistry:
    registry = ToolRegistry()
    registry.register(ECHO_TOOL_DEFINITION, echo_handler())
    return registry


def _request(*, config: AgentConfig | None = None) -> AgentRequest:
    return AgentRequest(
        messages=[AgentMessage(role="user", content="Run tools")],
        model="gpt-4o-mini",
        config=config,
    )


def test_reflection_prompt_snapshot(prompt_manager) -> None:
    rendered = prompt_manager.render(
        "agent",
        "reflection",
        "1",
        {
            "llm_content": "Calling echo.",
            "tool_summary": "- echo (success): {'echo': 'hello'}",
        },
    )
    assert rendered == (
        "You are a quality reflection agent. Review the latest agent step and choose the next action.\n\n"
        "Latest planner or LLM content:\n"
        "Calling echo.\n\n"
        "Tool results:\n"
        "- echo (success): {'echo': 'hello'}\n\n"
        "Respond with exactly one decision on the first line: REPLAN, RETRY_STEP, CONTINUE, or FINISH.\n"
        "You may add a short reason on the next line."
    )


@pytest.mark.parametrize(
    ("llm_content", "records", "expected"),
    [
        ("", None, ReflectionDecision.RETRY_STEP),
        ("  ", None, ReflectionDecision.RETRY_STEP),
        (
            "Planner reasoning",
            [
                ToolRunRecord(
                    step_id="s1",
                    call=ToolCall(name="echo", arguments={"message": "x"}),
                    result=ToolResult(success=False, error="failed"),
                )
            ],
            ReflectionDecision.REPLAN,
        ),
        (
            "Planner reasoning",
            [
                ToolRunRecord(
                    step_id="s1",
                    call=ToolCall(name="echo", arguments={"message": "a"}),
                    result=ToolResult(success=True, data={"echo": "a"}),
                ),
                ToolRunRecord(
                    step_id="s1",
                    call=ToolCall(name="echo", arguments={"message": "b"}),
                    result=ToolResult(success=False, error="failed"),
                ),
            ],
            ReflectionDecision.CONTINUE,
        ),
        (
            "Planner reasoning",
            [
                ToolRunRecord(
                    step_id="s1",
                    call=ToolCall(name="echo", arguments={"message": "ok"}),
                    result=ToolResult(success=True, data={"echo": "ok"}),
                )
            ],
            ReflectionDecision.FINISH,
        ),
    ],
)
def test_evaluate_rule_based_decision_paths(
    llm_content: str | None,
    records: list[ToolRunRecord] | None,
    expected: ReflectionDecision,
) -> None:
    tool_results = aggregate_tool_results(records) if records is not None else None
    assert (
        evaluate_rule_based(tool_results=tool_results, llm_content=llm_content)
        == expected
    )


def test_evaluate_rule_based_inconclusive_when_no_rules_match() -> None:
    assert evaluate_rule_based(tool_results=None, llm_content="Still thinking.") is None


@pytest.mark.parametrize(
    ("content", "expected_decision", "expected_reason"),
    [
        ("REPLAN\nTry another tool.", ReflectionDecision.REPLAN, "Try another tool."),
        ("retry_step", ReflectionDecision.RETRY_STEP, "retry_step"),
        ("Decision: CONTINUE", ReflectionDecision.CONTINUE, "Decision: CONTINUE"),
        ("FINISH", ReflectionDecision.FINISH, "FINISH"),
        ("", ReflectionDecision.CONTINUE, "Empty reflection response."),
        ("unclear answer", ReflectionDecision.CONTINUE, "unclear answer"),
    ],
)
def test_parse_reflection_response(
    content: str,
    expected_decision: ReflectionDecision,
    expected_reason: str,
) -> None:
    decision, reason = parse_reflection_response(content)
    assert decision == expected_decision
    assert reason == expected_reason


@pytest.mark.anyio
async def test_reflection_engine_disabled_is_noop(prompt_manager) -> None:
    provider = FakeProvider(response="FINISH")
    engine = ReflectionEngine(provider=provider, prompt_manager=prompt_manager)
    scratchpad = Scratchpad("exec-disabled")
    scratchpad.extend_messages([AgentMessage(role="user", content="Hi")])

    result = await engine.reflect(
        request=_request(config=AgentConfig(reflection_enabled=False)),
        context=AgentContext(execution_id="exec-disabled"),
        scratchpad=scratchpad,
        tool_results=aggregate_tool_results(
            [
                ToolRunRecord(
                    step_id="s1",
                    call=ToolCall(name="echo", arguments={"message": "x"}),
                    result=ToolResult(success=True, data={"echo": "x"}),
                )
            ]
        ),
        llm_content="Done.",
    )

    assert result.decision == ReflectionDecision.CONTINUE
    assert result.source == "disabled"
    assert provider.last_max_tokens is None


@pytest.mark.anyio
async def test_reflection_engine_uses_llm_when_rules_are_inconclusive(
    prompt_manager,
) -> None:
    provider = FakeProvider(response="REPLAN\nNeed a different approach.")
    engine = ReflectionEngine(provider=provider, prompt_manager=prompt_manager)
    scratchpad = Scratchpad("exec-llm")
    scratchpad.extend_messages([AgentMessage(role="user", content="Help")])

    result = await engine.reflect(
        request=_request(config=AgentConfig(reflection_enabled=True)),
        context=AgentContext(execution_id="exec-llm"),
        scratchpad=scratchpad,
        tool_results=None,
        llm_content="Unclear next step.",
    )

    assert result.decision == ReflectionDecision.REPLAN
    assert result.source == "llm"
    assert result.reason == "Need a different approach."


class AlwaysFailHandler:
    async def execute(
        self,
        args: dict[str, object],
        context: ToolExecutionContext,
    ) -> ToolResult:
        del args, context
        return ToolResult(
            success=False, error="always fails", error_code="handler_error"
        )


def _executor_with_reflection(
    *,
    provider: FakeProvider,
    tool_registry: ToolRegistry,
    prompt_manager,
    scratchpad_store: ScratchpadStore,
    publisher: InMemoryStreamPublisher | None = None,
    reflection_enabled: bool = True,
) -> AgentExecutor:
    publisher = publisher or InMemoryStreamPublisher()
    tool_executor = ToolExecutor(
        registry=tool_registry,
        settings=Settings(request_timeout_seconds=5),
    )
    tool_runner = ToolRunner(
        tool_executor=tool_executor,
        stream_publisher=publisher,
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
        reflection_engine=ReflectionEngine(
            provider=provider,
            prompt_manager=prompt_manager,
        ),
    )


@pytest.mark.anyio
async def test_agent_executor_reflection_finish_finalizes_after_successful_tools(
    tool_registry: ToolRegistry,
    prompt_manager,
    scratchpad_store: ScratchpadStore,
    tool_context: ToolExecutionContext,
) -> None:
    provider = FakeProvider(
        tool_completions=[
            ProviderToolCompletion(
                content="Echo now.",
                tool_calls=[
                    ProviderToolCall(
                        id="call-echo",
                        name="echo",
                        arguments={"message": "hello"},
                    )
                ],
            )
        ]
    )
    publisher = InMemoryStreamPublisher()
    executor = _executor_with_reflection(
        provider=provider,
        tool_registry=tool_registry,
        prompt_manager=prompt_manager,
        scratchpad_store=scratchpad_store,
        publisher=publisher,
        reflection_enabled=True,
    )
    config = AgentConfig(reflection_enabled=True, max_reflections=2)

    response = await executor.run(
        _request(config=config),
        AgentContext(execution_id="exec-reflect-finish"),
        tool_context=tool_context,
    )

    assert response.finish_reason == "stop"
    assert response.tools_used == ["echo"]
    assert response.iterations == 1
    reflection_events = [
        event
        for event in publisher.events
        if event.type == AgentStreamEventType.REFLECTION
    ]
    assert len(reflection_events) == 1
    payload = reflection_events[0].typed_payload()
    assert isinstance(payload, ReflectionEventPayload)
    assert payload.decision == ReflectionDecision.FINISH


@pytest.mark.anyio
async def test_agent_executor_reflection_replan_after_all_tools_fail(
    prompt_manager,
    scratchpad_store: ScratchpadStore,
    tool_context: ToolExecutionContext,
) -> None:
    registry = ToolRegistry()
    registry.register(
        ToolDefinition(
            name="fail_tool",
            description="Always fails",
            parameters={
                "type": "object",
                "properties": {"message": {"type": "string"}},
                "required": ["message"],
            },
        ),
        AlwaysFailHandler(),
    )
    provider = FakeProvider(
        tool_completions=[
            ProviderToolCompletion(
                content="Try fail_tool.",
                tool_calls=[
                    ProviderToolCall(
                        id="call-fail",
                        name="fail_tool",
                        arguments={"message": "x"},
                    )
                ],
            ),
            ProviderToolCompletion(
                content="Done after replan.",
                tool_calls=[],
                finish_reason="stop",
            ),
        ]
    )
    publisher = InMemoryStreamPublisher()
    executor = _executor_with_reflection(
        provider=provider,
        tool_registry=registry,
        prompt_manager=prompt_manager,
        scratchpad_store=scratchpad_store,
        publisher=publisher,
    )
    config = AgentConfig(reflection_enabled=True, max_iterations=3)

    response = await executor.run(
        _request(config=config),
        AgentContext(execution_id="exec-reflect-replan"),
        tool_context=tool_context,
    )

    assert response.content == "Done after replan."
    reflection_events = [
        event
        for event in publisher.events
        if event.type == AgentStreamEventType.REFLECTION
    ]
    assert len(reflection_events) == 1
    payload = reflection_events[0].typed_payload()
    assert isinstance(payload, ReflectionEventPayload)
    assert payload.decision == ReflectionDecision.REPLAN


@pytest.mark.anyio
async def test_agent_executor_reflection_continue_on_partial_tool_failure(
    prompt_manager,
    scratchpad_store: ScratchpadStore,
    tool_context: ToolExecutionContext,
) -> None:
    registry = ToolRegistry()
    registry.register(ECHO_TOOL_DEFINITION, echo_handler())

    class FailOnceHandler:
        calls: ClassVar[int] = 0

        async def execute(
            self,
            args: dict[str, object],
            context: ToolExecutionContext,
        ) -> ToolResult:
            del context
            FailOnceHandler.calls += 1
            if FailOnceHandler.calls == 1:
                return ToolResult(success=False, error="temporary")
            return ToolResult(success=True, data={"echo": args.get("message")})

    registry.register(
        ToolDefinition(
            name="flaky",
            description="Fails once",
            parameters={
                "type": "object",
                "properties": {"message": {"type": "string"}},
                "required": ["message"],
            },
        ),
        FailOnceHandler(),
    )
    provider = FakeProvider(
        tool_completions=[
            ProviderToolCompletion(
                content="Run both tools.",
                tool_calls=[
                    ProviderToolCall(
                        id="call-echo",
                        name="echo",
                        arguments={"message": "ok"},
                    ),
                    ProviderToolCall(
                        id="call-flaky",
                        name="flaky",
                        arguments={"message": "maybe"},
                    ),
                ],
            ),
            ProviderToolCompletion(
                content="Recovered.",
                tool_calls=[],
                finish_reason="stop",
            ),
        ]
    )
    publisher = InMemoryStreamPublisher()
    executor = _executor_with_reflection(
        provider=provider,
        tool_registry=registry,
        prompt_manager=prompt_manager,
        scratchpad_store=scratchpad_store,
        publisher=publisher,
    )
    config = AgentConfig(reflection_enabled=True, max_iterations=3)

    response = await executor.run(
        _request(config=config),
        AgentContext(execution_id="exec-reflect-continue"),
        tool_context=tool_context,
    )

    assert response.content == "Recovered."
    payload = next(
        event.typed_payload()
        for event in publisher.events
        if event.type == AgentStreamEventType.REFLECTION
    )
    assert isinstance(payload, ReflectionEventPayload)
    assert payload.decision == ReflectionDecision.CONTINUE


@pytest.mark.anyio
async def test_agent_executor_reflection_retry_step_reexecutes_tools(
    tool_registry: ToolRegistry,
    prompt_manager,
    scratchpad_store: ScratchpadStore,
    tool_context: ToolExecutionContext,
) -> None:
    provider = FakeProvider(
        tool_completions=[
            ProviderToolCompletion(
                content="",
                tool_calls=[
                    ProviderToolCall(
                        id="call-echo",
                        name="echo",
                        arguments={"message": "retry-me"},
                    )
                ],
            ),
            ProviderToolCompletion(
                content="Done after retry.",
                tool_calls=[],
                finish_reason="stop",
            ),
        ]
    )
    publisher = InMemoryStreamPublisher()
    executor = _executor_with_reflection(
        provider=provider,
        tool_registry=tool_registry,
        prompt_manager=prompt_manager,
        scratchpad_store=scratchpad_store,
        publisher=publisher,
    )
    config = AgentConfig(reflection_enabled=True, max_iterations=3)

    response = await executor.run(
        _request(config=config),
        AgentContext(execution_id="exec-reflect-retry"),
        tool_context=tool_context,
    )

    assert response.content == "Done after retry."
    assert response.tools_used == ["echo"]
    payload = next(
        event.typed_payload()
        for event in publisher.events
        if event.type == AgentStreamEventType.REFLECTION
    )
    assert isinstance(payload, ReflectionEventPayload)
    assert payload.decision == ReflectionDecision.RETRY_STEP
    tool_end_events = [
        event
        for event in publisher.events
        if event.type == AgentStreamEventType.TOOL_END
    ]
    assert len(tool_end_events) == 2


@pytest.mark.anyio
async def test_agent_executor_reflection_disabled_emits_no_events(
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
                        arguments={"message": "hello"},
                    )
                ],
            ),
            ProviderToolCompletion(
                content="Final answer.",
                tool_calls=[],
                finish_reason="stop",
            ),
        ]
    )
    publisher = InMemoryStreamPublisher()
    executor = _executor_with_reflection(
        provider=provider,
        tool_registry=tool_registry,
        prompt_manager=prompt_manager,
        scratchpad_store=scratchpad_store,
        publisher=publisher,
        reflection_enabled=False,
    )

    await executor.run(
        _request(config=AgentConfig(reflection_enabled=False)),
        AgentContext(execution_id="exec-reflect-off"),
        tool_context=tool_context,
    )

    assert not any(
        event.type == AgentStreamEventType.REFLECTION for event in publisher.events
    )


@pytest.mark.anyio
async def test_agent_executor_reflection_respects_max_reflections(
    prompt_manager,
    scratchpad_store: ScratchpadStore,
    tool_context: ToolExecutionContext,
) -> None:
    registry = ToolRegistry()
    registry.register(ECHO_TOOL_DEFINITION, echo_handler())

    class FailOnceHandler:
        calls: ClassVar[int] = 0

        async def execute(
            self,
            args: dict[str, object],
            context: ToolExecutionContext,
        ) -> ToolResult:
            del context
            FailOnceHandler.calls += 1
            if FailOnceHandler.calls == 1:
                return ToolResult(success=False, error="temporary")
            return ToolResult(success=True, data={"echo": args.get("message")})

    registry.register(
        ToolDefinition(
            name="flaky",
            description="Fails once",
            parameters={
                "type": "object",
                "properties": {"message": {"type": "string"}},
                "required": ["message"],
            },
        ),
        FailOnceHandler(),
    )
    provider = FakeProvider(
        tool_completions=[
            ProviderToolCompletion(
                content="Mixed tools.",
                tool_calls=[
                    ProviderToolCall(
                        id="call-echo",
                        name="echo",
                        arguments={"message": "one"},
                    ),
                    ProviderToolCall(
                        id="call-flaky",
                        name="flaky",
                        arguments={"message": "two"},
                    ),
                ],
            ),
            ProviderToolCompletion(
                content="Echo only.",
                tool_calls=[
                    ProviderToolCall(
                        id="call-echo-2",
                        name="echo",
                        arguments={"message": "three"},
                    )
                ],
            ),
            ProviderToolCompletion(
                content="Done.",
                tool_calls=[],
                finish_reason="stop",
            ),
        ]
    )
    publisher = InMemoryStreamPublisher()
    executor = _executor_with_reflection(
        provider=provider,
        tool_registry=registry,
        prompt_manager=prompt_manager,
        scratchpad_store=scratchpad_store,
        publisher=publisher,
    )
    config = AgentConfig(
        reflection_enabled=True,
        max_reflections=1,
        max_iterations=3,
    )

    await executor.run(
        _request(config=config),
        AgentContext(execution_id="exec-reflect-cap"),
        tool_context=tool_context,
    )

    reflection_events = [
        event
        for event in publisher.events
        if event.type == AgentStreamEventType.REFLECTION
    ]
    assert len(reflection_events) == 1
    payload = reflection_events[0].typed_payload()
    assert isinstance(payload, ReflectionEventPayload)
    assert payload.decision == ReflectionDecision.CONTINUE


def test_reflection_modules_have_no_transport_or_domain_imports() -> None:
    repo_root = Path(__file__).resolve().parents[3]
    module_paths = [
        repo_root / "app/ai/agent/reflection/engine.py",
        repo_root / "app/ai/agent/reflection/quality_checker.py",
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
