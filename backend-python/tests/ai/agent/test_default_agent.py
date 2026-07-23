"""Tests for default agent runtime entry point (Phase 10)."""

from __future__ import annotations

import ast
import asyncio
import logging
import uuid
from collections.abc import Iterator
from pathlib import Path

import pytest

from app.ai.agent import AgentStreamEventType
from app.ai.agent.models.config import AgentConfig
from app.ai.agent.models.context import AgentContext
from app.ai.agent.models.messages import AgentMessage
from app.ai.agent.models.request import AgentRequest
from app.ai.agent.runtime import DefaultAgent, create_default_agent
from app.ai.agent.scratchpad import ScratchpadStore
from app.ai.deps import get_agent_runtime, get_prompt_manager, get_tool_registry
from app.ai.prompts.manager import create_prompt_manager
from app.ai.tools.executor import ToolExecutor
from app.ai.tools.registry import ToolRegistry
from app.ai.tools.stubs.echo import ECHO_TOOL_DEFINITION, echo_handler
from app.core.caller import CallerContext
from app.core.config import Settings
from app.providers.base import ProviderToolCall, ProviderToolCompletion
from app.providers.factory import ProviderFactory
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
    content: str = "Echo hello",
) -> AgentRequest:
    return AgentRequest(
        messages=[AgentMessage(role="user", content=content)],
        model="gpt-4o-mini",
        config=config,
    )


def _agent(
    *,
    provider: FakeProvider,
    tool_registry: ToolRegistry,
    prompt_manager,
    scratchpad_store: ScratchpadStore,
    monkeypatch: pytest.MonkeyPatch,
) -> DefaultAgent:
    monkeypatch.setattr(
        ProviderFactory,
        "get_provider",
        staticmethod(lambda _name, _settings: provider),
    )
    tool_executor = ToolExecutor(
        registry=tool_registry,
        settings=Settings(request_timeout_seconds=5),
    )
    return create_default_agent(
        settings=Settings(request_timeout_seconds=5),
        tool_registry=tool_registry,
        prompt_manager=prompt_manager,
        tool_executor=tool_executor,
        scratchpad_store=scratchpad_store,
    )


@pytest.mark.anyio
async def test_default_agent_run_without_http(
    tool_registry: ToolRegistry,
    prompt_manager,
    scratchpad_store: ScratchpadStore,
    monkeypatch: pytest.MonkeyPatch,
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
    agent = _agent(
        provider=provider,
        tool_registry=tool_registry,
        prompt_manager=prompt_manager,
        scratchpad_store=scratchpad_store,
        monkeypatch=monkeypatch,
    )
    context = AgentContext(execution_id="exec-default-run")

    response = await agent.run(_request(), context)

    assert response.content == "The echo returned hello."
    assert response.tools_used == ["echo"]
    assert response.iterations == 2
    assert response.finish_reason == "stop"
    assert scratchpad_store.get(context.execution_id) is None


@pytest.mark.anyio
async def test_default_agent_stream_ends_with_complete(
    tool_registry: ToolRegistry,
    prompt_manager,
    scratchpad_store: ScratchpadStore,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    provider = FakeProvider(response="Streamed answer.")
    agent = _agent(
        provider=provider,
        tool_registry=tool_registry,
        prompt_manager=prompt_manager,
        scratchpad_store=scratchpad_store,
        monkeypatch=monkeypatch,
    )
    context = AgentContext(execution_id="exec-default-stream")

    events = [event async for event in agent.stream(_request(), context)]

    assert events
    assert events[0].type == AgentStreamEventType.START
    assert events[-1].type == AgentStreamEventType.COMPLETE
    assert scratchpad_store.get(context.execution_id) is None


@pytest.mark.anyio
async def test_default_agent_stream_includes_token_events(
    tool_registry: ToolRegistry,
    prompt_manager,
    scratchpad_store: ScratchpadStore,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    provider = FakeProvider(response="Hello from stream.")
    agent = _agent(
        provider=provider,
        tool_registry=tool_registry,
        prompt_manager=prompt_manager,
        scratchpad_store=scratchpad_store,
        monkeypatch=monkeypatch,
    )

    events = [
        event
        async for event in agent.stream(
            _request(),
            AgentContext(execution_id="exec-default-tokens"),
        )
    ]

    token_events = [
        event for event in events if event.type == AgentStreamEventType.TOKEN
    ]
    assert token_events
    assert (
        "".join(event.typed_payload().content for event in token_events)  # type: ignore[union-attr]
        == "Hello from stream."
    )


@pytest.mark.anyio
async def test_default_agent_stream_cleans_up_when_consumer_closes_early(
    tool_registry: ToolRegistry,
    prompt_manager,
    scratchpad_store: ScratchpadStore,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    release = asyncio.Event()

    class SlowFinalizeProvider(FakeProvider):
        async def stream_chat(  # type: ignore[override]
            self,
            messages,
            model,
            temperature=0.7,
            *,
            max_tokens=None,
        ):
            await release.wait()
            async for chunk in super().stream_chat(
                messages,
                model,
                temperature,
                max_tokens=max_tokens,
            ):
                yield chunk

    provider = SlowFinalizeProvider(response="Slow answer.")
    agent = _agent(
        provider=provider,
        tool_registry=tool_registry,
        prompt_manager=prompt_manager,
        scratchpad_store=scratchpad_store,
        monkeypatch=monkeypatch,
    )
    context = AgentContext(execution_id="exec-default-early-close")

    async for event in agent.stream(_request(), context):
        assert event.type == AgentStreamEventType.START
        break

    release.set()
    await asyncio.sleep(0)

    assert scratchpad_store.get(context.execution_id) is None


@pytest.mark.anyio
async def test_default_agent_logs_structured_fields(
    tool_registry: ToolRegistry,
    prompt_manager,
    scratchpad_store: ScratchpadStore,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.INFO, logger="app.ai.agent.runtime.default_agent")
    provider = FakeProvider(response="Logged answer.")
    agent = _agent(
        provider=provider,
        tool_registry=tool_registry,
        prompt_manager=prompt_manager,
        scratchpad_store=scratchpad_store,
        monkeypatch=monkeypatch,
    )
    context = AgentContext(execution_id="exec-default-log")

    await agent.run(_request(), context)

    records = [
        record
        for record in caplog.records
        if record.name == "app.ai.agent.runtime.default_agent"
    ]
    assert records
    record = records[-1]
    assert getattr(record, "agent_execution_id") == "exec-default-log"
    assert getattr(record, "agent_iterations") == 1
    assert getattr(record, "agent_tools_used") == []
    assert "Logged answer." not in caplog.text


@pytest.mark.anyio
async def test_default_agent_resolves_caller_from_context(
    tool_registry: ToolRegistry,
    prompt_manager,
    scratchpad_store: ScratchpadStore,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: list[CallerContext] = []

    class CapturingEchoHandler:
        async def execute(self, args, context):  # noqa: ANN001
            captured.append(context.caller)
            return await echo_handler().execute(args, context)

    registry = ToolRegistry()
    registry.register(ECHO_TOOL_DEFINITION, CapturingEchoHandler())
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
            ProviderToolCompletion(content="Done.", tool_calls=[]),
        ]
    )
    agent = _agent(
        provider=provider,
        tool_registry=registry,
        prompt_manager=prompt_manager,
        scratchpad_store=scratchpad_store,
        monkeypatch=monkeypatch,
    )
    user_id = uuid.uuid4()
    context = AgentContext(
        execution_id="exec-default-caller",
        caller=CallerContext.for_user(user_id),
    )

    await agent.run(_request(), context)

    assert captured
    assert captured[0].user_id == user_id


def test_get_agent_runtime_wires_dependencies(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    provider = FakeProvider(response="wired")
    monkeypatch.setattr(
        ProviderFactory,
        "get_provider",
        staticmethod(lambda _name, _settings: provider),
    )
    get_prompt_manager.cache_clear()
    try:
        agent = get_agent_runtime(
            settings=Settings(
                llm_provider="openai",
                openai_api_key="sk-placeholder",
                request_timeout_seconds=5,
            ),
            tool_registry=get_tool_registry(),
            prompt_manager=get_prompt_manager(),
            tool_executor=ToolExecutor(
                registry=get_tool_registry(),
                settings=Settings(request_timeout_seconds=5),
            ),
        )
    finally:
        get_prompt_manager.cache_clear()

    assert isinstance(agent, DefaultAgent)


def test_runtime_modules_have_no_transport_or_domain_imports() -> None:
    repo_root = Path(__file__).resolve().parents[3]
    module_paths = [
        repo_root / "app/ai/agent/runtime/default_agent.py",
        repo_root / "app/ai/agent/runtime/factory.py",
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
