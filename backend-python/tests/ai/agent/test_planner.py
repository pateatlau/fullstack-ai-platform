"""Tests for agent ReAct planner (Phase 6)."""

from __future__ import annotations

import ast
from collections.abc import Iterator
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from app.ai.agent import Planner, StepAction
from app.ai.agent.models.config import AgentConfig
from app.ai.agent.models.context import AgentContext
from app.ai.agent.models.messages import AgentMessage
from app.ai.agent.models.request import AgentRequest
from app.ai.agent.planner import (
    ReActPlanner,
    build_iteration_limit_plan,
    parse_tool_completion,
)
from app.ai.agent.scratchpad import ScratchpadStore
from app.ai.prompts.manager import create_prompt_manager
from app.ai.tools.registry import ToolRegistry
from app.ai.tools.stubs.echo import ECHO_TOOL_DEFINITION, echo_handler
from app.providers.base import (
    ChatMessageInput,
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
def prompt_manager():
    return create_prompt_manager()


def _request(
    *,
    config: AgentConfig | None = None,
    tool_names: list[str] | None = None,
) -> AgentRequest:
    return AgentRequest(
        messages=[AgentMessage(role="user", content="Echo hello")],
        model="gpt-4o-mini",
        config=config,
        tool_names=tool_names,
    )


def _planner(
    provider: FakeProvider,
    tool_registry: ToolRegistry,
    prompt_manager,
    scratchpad_store: ScratchpadStore,
) -> ReActPlanner:
    return ReActPlanner(
        provider=provider,
        tool_registry=tool_registry,
        prompt_manager=prompt_manager,
        scratchpad_store=scratchpad_store,
    )


def test_planner_prompt_snapshot(prompt_manager) -> None:
    rendered = prompt_manager.render(
        "agent",
        "planner",
        "1",
        {
            "tool_list": "- echo: Echo a message back to the caller",
            "iteration": 2,
            "max_iterations": 5,
        },
    )
    assert rendered == (
        "You are a ReAct-style planning agent. Choose the next action to fulfill the user's request.\n\n"
        "Available tools:\n"
        "- echo: Echo a message back to the caller\n\n"
        "Rules:\n"
        "- Call one or more tools when you need external data or actions.\n"
        "- When you have enough information to answer, respond without tool calls.\n"
        "- Independent tools may be requested together in a single turn.\n"
        "- Current iteration: 2 of 5."
    )


def test_parse_tool_completion_single_tool() -> None:
    completion = ProviderToolCompletion(
        content="I'll echo the message.",
        tool_calls=[
            ProviderToolCall(
                id="call-1",
                name="echo",
                arguments={"message": "hello"},
            )
        ],
    )

    plan = parse_tool_completion(completion, iteration=1)

    assert plan.is_final is False
    assert len(plan.steps) == 1
    step = plan.steps[0]
    assert step.action == StepAction.TOOL_CALL
    assert len(step.tool_calls) == 1
    assert step.tool_calls[0].name == "echo"
    assert step.tool_calls[0].arguments == {"message": "hello"}
    assert step.tool_calls[0].call_id == "call-1"
    assert step.reasoning == "I'll echo the message."


def test_parse_tool_completion_parallel_tools() -> None:
    completion = ProviderToolCompletion(
        content="Running both echoes in parallel.",
        tool_calls=[
            ProviderToolCall(
                id="call-1",
                name="echo",
                arguments={"message": "alpha"},
            ),
            ProviderToolCall(
                id="call-2",
                name="echo",
                arguments={"message": "beta"},
            ),
        ],
    )

    plan = parse_tool_completion(completion, iteration=2)

    assert plan.is_final is False
    assert len(plan.steps) == 1
    step = plan.steps[0]
    assert step.action == StepAction.TOOL_CALL
    assert len(step.tool_calls) == 2
    assert step.tool_calls[0].call_id == "call-1"
    assert step.tool_calls[1].call_id == "call-2"


def test_parse_tool_completion_finalize() -> None:
    completion = ProviderToolCompletion(
        content="Here is the final answer.",
        tool_calls=[],
        finish_reason="stop",
    )

    plan = parse_tool_completion(completion, iteration=3)

    assert plan.is_final is True
    assert len(plan.steps) == 1
    assert plan.steps[0].action == StepAction.FINALIZE
    assert plan.steps[0].reasoning == "Here is the final answer."


def test_build_iteration_limit_plan() -> None:
    plan = build_iteration_limit_plan(iteration=5)

    assert plan.is_final is True
    assert plan.iteration == 5
    assert plan.steps[0].action == StepAction.FINALIZE
    assert "limit" in (plan.steps[0].reasoning or "").lower()


@pytest.mark.anyio
async def test_react_planner_single_tool_call(
    tool_registry: ToolRegistry,
    prompt_manager,
    scratchpad_store: ScratchpadStore,
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
            )
        ]
    )
    planner = _planner(provider, tool_registry, prompt_manager, scratchpad_store)
    context = AgentContext(execution_id="exec-single")
    scratchpad_store.create(context.execution_id)

    plan = await planner.plan_next(_request(), context, iteration=0)

    assert provider.tool_completion_calls == 1
    assert plan.is_final is False
    assert plan.steps[0].action == StepAction.TOOL_CALL
    assert plan.steps[0].tool_calls[0].name == "echo"
    scratchpad = scratchpad_store.require(context.execution_id)
    assert scratchpad.entries[-1].content == "Echoing now."


@pytest.mark.anyio
async def test_react_planner_parallel_tool_calls(
    tool_registry: ToolRegistry,
    prompt_manager,
    scratchpad_store: ScratchpadStore,
) -> None:
    provider = FakeProvider(
        tool_completions=[
            ProviderToolCompletion(
                content="Parallel echo.",
                tool_calls=[
                    ProviderToolCall(
                        id="call-a",
                        name="echo",
                        arguments={"message": "alpha"},
                    ),
                    ProviderToolCall(
                        id="call-b",
                        name="echo",
                        arguments={"message": "beta"},
                    ),
                ],
            )
        ]
    )
    planner = _planner(provider, tool_registry, prompt_manager, scratchpad_store)

    plan = await planner.plan_next(
        _request(),
        AgentContext(execution_id="exec-parallel"),
        iteration=1,
    )

    assert len(plan.steps) == 1
    assert len(plan.steps[0].tool_calls) == 2


@pytest.mark.anyio
async def test_react_planner_finalize_without_tool_calls(
    tool_registry: ToolRegistry,
    prompt_manager,
    scratchpad_store: ScratchpadStore,
) -> None:
    provider = FakeProvider(
        tool_completions=[
            ProviderToolCompletion(
                content="All done.",
                tool_calls=[],
                finish_reason="stop",
            )
        ]
    )
    planner = _planner(provider, tool_registry, prompt_manager, scratchpad_store)

    plan = await planner.plan_next(
        _request(),
        AgentContext(execution_id="exec-finalize"),
        iteration=0,
    )

    assert plan.is_final is True
    assert plan.steps[0].action == StepAction.FINALIZE


@pytest.mark.anyio
async def test_react_planner_iteration_limit_skips_llm(
    tool_registry: ToolRegistry,
    prompt_manager,
    scratchpad_store: ScratchpadStore,
) -> None:
    provider = FakeProvider()
    planner = _planner(provider, tool_registry, prompt_manager, scratchpad_store)
    config = AgentConfig(max_iterations=3)

    plan = await planner.plan_next(
        _request(config=config),
        AgentContext(execution_id="exec-limit"),
        iteration=3,
    )

    assert provider.tool_completion_calls == 0
    assert plan.is_final is True
    assert plan.steps[0].action == StepAction.FINALIZE
    assert plan.iteration == 3


@pytest.mark.anyio
async def test_react_planner_no_tools_returns_finalize_without_llm(
    prompt_manager,
    scratchpad_store: ScratchpadStore,
) -> None:
    provider = FakeProvider()
    empty_registry = ToolRegistry()
    planner = _planner(provider, empty_registry, prompt_manager, scratchpad_store)

    plan = await planner.plan_next(
        _request(),
        AgentContext(execution_id="exec-no-tools"),
        iteration=0,
    )

    assert provider.tool_completion_calls == 0
    assert plan.is_final is True
    assert plan.steps[0].action == StepAction.FINALIZE


@pytest.mark.anyio
async def test_react_planner_retries_transient_llm_failure(
    tool_registry: ToolRegistry,
    prompt_manager,
    scratchpad_store: ScratchpadStore,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    provider = FakeProvider(
        tool_completions=[
            ProviderToolCompletion(content="Done.", tool_calls=[]),
        ]
    )
    calls = 0
    original = provider.complete_chat_with_tools

    async def flaky_complete(*args, **kwargs):  # type: ignore[no-untyped-def]
        nonlocal calls
        calls += 1
        if calls == 1:
            raise ConnectionError("temporary")
        return await original(*args, **kwargs)

    provider.complete_chat_with_tools = flaky_complete  # type: ignore[method-assign]
    monkeypatch.setattr("app.core.retry.asyncio.sleep", AsyncMock())

    planner = _planner(provider, tool_registry, prompt_manager, scratchpad_store)
    plan = await planner.plan_next(
        _request(config=AgentConfig(max_retries=3)),
        AgentContext(execution_id="exec-retry"),
        iteration=0,
    )

    assert calls == 2
    assert plan.is_final is True


@pytest.mark.anyio
async def test_react_planner_filters_tools_by_context_and_request(
    tool_registry: ToolRegistry,
    prompt_manager,
    scratchpad_store: ScratchpadStore,
) -> None:
    captured_tools: list[list[dict[str, object]]] = []

    class CapturingProvider(FakeProvider):
        async def complete_chat_with_tools(  # type: ignore[override]
            self,
            messages,
            model,
            tools,
            temperature=0.7,
            *,
            max_tokens=None,
        ):
            captured_tools.append(tools)
            return ProviderToolCompletion(content="Done.", tool_calls=[])

    provider = CapturingProvider()
    planner = _planner(provider, tool_registry, prompt_manager, scratchpad_store)
    context = AgentContext(
        execution_id="exec-filter",
        allowed_tool_names=frozenset({"echo"}),
    )

    await planner.plan_next(
        _request(tool_names=["echo"]),
        context,
        iteration=0,
    )

    assert len(captured_tools) == 1
    assert captured_tools[0] == tool_registry.get_schemas_for_llm()


@pytest.mark.anyio
async def test_react_planner_uses_scratchpad_context_when_present(
    tool_registry: ToolRegistry,
    prompt_manager,
    scratchpad_store: ScratchpadStore,
) -> None:
    captured_messages: list[list[ChatMessageInput]] = []

    class CapturingProvider(FakeProvider):
        async def complete_chat_with_tools(  # type: ignore[override]
            self,
            messages,
            model,
            tools,
            temperature=0.7,
            *,
            max_tokens=None,
        ):
            captured_messages.append(messages)
            return ProviderToolCompletion(content="Done.", tool_calls=[])

    provider = CapturingProvider()
    planner = _planner(provider, tool_registry, prompt_manager, scratchpad_store)
    context = AgentContext(execution_id="exec-scratch")
    scratchpad = scratchpad_store.create(context.execution_id)
    scratchpad.append_observation("prior tool result")

    await planner.plan_next(_request(), context, iteration=0)

    assert captured_messages
    roles = [
        message["role"] for message in captured_messages[0] if isinstance(message, dict)
    ]
    assert "system" in roles
    assert "assistant" in roles


def test_react_planner_satisfies_planner_protocol(
    tool_registry: ToolRegistry,
    prompt_manager,
    scratchpad_store: ScratchpadStore,
) -> None:
    provider = FakeProvider()
    planner: Planner = ReActPlanner(
        provider=provider,
        tool_registry=tool_registry,
        prompt_manager=prompt_manager,
        scratchpad_store=scratchpad_store,
    )
    assert planner is not None


def test_planner_modules_have_no_transport_or_domain_imports() -> None:
    repo_root = Path(__file__).resolve().parents[3]
    module_paths = [
        repo_root / "app/ai/agent/planner/parser.py",
        repo_root / "app/ai/agent/planner/react_planner.py",
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
