"""DefaultAgent / AgentExecutor span instrumentation tests."""

from __future__ import annotations

import logging
import uuid
from collections.abc import Iterator
from unittest.mock import MagicMock, patch

import pytest
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

from app.ai.agent.models.context import AgentContext
from app.ai.agent.models.messages import AgentMessage
from app.ai.agent.models.request import AgentRequest
from app.ai.agent.runtime import DefaultAgent, create_default_agent
from app.ai.agent.scratchpad import ScratchpadStore
from app.ai.observability.tracing.provider import TracerRegistry
from app.ai.prompts.manager import create_prompt_manager
from app.ai.tools.executor import ToolExecutor
from app.ai.tools.registry import ToolRegistry
from app.ai.tools.stubs.echo import ECHO_TOOL_DEFINITION, echo_handler
from app.core.caller import CallerContext
from app.core.config import Settings
from app.providers.base import ProviderToolCall, ProviderToolCompletion
from app.providers.factory import ProviderFactory
from tests.fakes import FakeProvider

pytestmark = pytest.mark.anyio


@pytest.fixture(autouse=True)
def _reset_tracer_registry() -> Iterator[None]:
    TracerRegistry.reset_for_tests()
    yield
    TracerRegistry.reset_for_tests()


@pytest.fixture
def memory_exporter() -> InMemorySpanExporter:
    exporter = InMemorySpanExporter()
    settings = Settings(openai_api_key="test-key", observability_enabled=True)
    TracerRegistry.initialize(
        settings,
        extra_span_processors=[SimpleSpanProcessor(exporter)],
    )
    return exporter


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


def _context(execution_id: str) -> AgentContext:
    return AgentContext(
        execution_id=execution_id,
        caller=CallerContext.for_user(uuid.uuid4()),
    )


def _request(*, content: str = "Echo hello") -> AgentRequest:
    return AgentRequest(
        messages=[AgentMessage(role="user", content=content)],
        model="gpt-4o-mini",
    )


async def test_default_agent_multi_iteration_emits_iteration_and_tool_spans(
    memory_exporter: InMemorySpanExporter,
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
    context = _context("exec-agent-spans")

    response = await agent.run(_request(), context)

    assert response.iterations == 2
    assert response.tools_used == ["echo"]
    spans = memory_exporter.get_finished_spans()
    iteration_spans = [span for span in spans if span.name == "agent.iteration"]
    tool_call_spans = [span for span in spans if span.name == "agent.tool_call"]
    execute_spans = [span for span in spans if span.name == "tool.execute"]

    assert len(iteration_spans) == 2
    assert len(tool_call_spans) == 1
    assert len(execute_spans) == 1

    first_iteration = dict(iteration_spans[0].attributes or {})
    second_iteration = dict(iteration_spans[1].attributes or {})
    assert first_iteration["iteration_index"] == 0
    assert first_iteration["tool_calls_count"] == 1
    assert "finish_reason" not in first_iteration
    assert second_iteration["iteration_index"] == 1
    assert second_iteration["tool_calls_count"] == 0
    assert second_iteration["finish_reason"] == "stop"

    tool_call_attributes = dict(tool_call_spans[0].attributes or {})
    assert tool_call_attributes["tool_name"] == "echo"
    assert isinstance(tool_call_attributes["latency_ms"], int)

    execute_attributes = dict(execute_spans[0].attributes or {})
    assert execute_attributes["tool_name"] == "echo"
    assert execute_attributes["success"] is True
    assert all("hello" not in str(value) for value in execute_attributes.values())


async def test_agent_span_nesting_tool_call_parents_tool_execute(
    memory_exporter: InMemorySpanExporter,
    tool_registry: ToolRegistry,
    prompt_manager,
    scratchpad_store: ScratchpadStore,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    provider = FakeProvider(
        tool_completions=[
            ProviderToolCompletion(
                content="Echoing.",
                tool_calls=[
                    ProviderToolCall(
                        id="call-echo",
                        name="echo",
                        arguments={"message": "nested"},
                    )
                ],
            ),
            ProviderToolCompletion(
                content="Done.", tool_calls=[], finish_reason="stop"
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

    await agent.run(_request(content="Echo nested"), _context("exec-nesting"))

    spans = memory_exporter.get_finished_spans()
    iteration_span = next(span for span in spans if span.name == "agent.iteration")
    tool_call_span = next(span for span in spans if span.name == "agent.tool_call")
    execute_span = next(span for span in spans if span.name == "tool.execute")

    tool_call_parent = tool_call_span.parent
    execute_parent = execute_span.parent
    iteration_context = iteration_span.context
    tool_call_context = tool_call_span.context
    assert tool_call_parent is not None
    assert execute_parent is not None
    assert iteration_context is not None
    assert tool_call_context is not None
    assert tool_call_parent.span_id == iteration_context.span_id
    assert execute_parent.span_id == tool_call_context.span_id


async def test_agent_telemetry_failure_is_fail_open(
    memory_exporter: InMemorySpanExporter,
    tool_registry: ToolRegistry,
    prompt_manager,
    scratchpad_store: ScratchpadStore,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.WARNING)
    provider = FakeProvider(response="Direct answer.")
    agent = _agent(
        provider=provider,
        tool_registry=tool_registry,
        prompt_manager=prompt_manager,
        scratchpad_store=scratchpad_store,
        monkeypatch=monkeypatch,
    )
    broken_tracer = MagicMock()
    broken_tracer.start_span.side_effect = RuntimeError("telemetry down")

    with patch(
        "app.ai.observability.tracing.spans.get_tracer",
        return_value=broken_tracer,
    ):
        response = await agent.run(
            _request(content="Hello"),
            _context("exec-agent-fail-open"),
        )

    assert response.content == "Direct answer."
    assert any(
        "Observability span setup failed" in record.message for record in caplog.records
    )
    assert len(memory_exporter.get_finished_spans()) == 0
