"""ToolExecutor span instrumentation tests."""

from __future__ import annotations

import logging
import uuid
from collections.abc import Iterator
from typing import ClassVar, cast
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

from app.ai.agent.executor import ToolRunner
from app.ai.agent.models.plan import PlannedStep
from app.ai.agent.retry import ToolRetryPolicy
from app.ai.agent import StepAction
from app.ai.observability.tracing.provider import TracerRegistry
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

pytestmark = pytest.mark.anyio


@pytest.fixture(autouse=True)
def _reset_tracer_registry() -> Iterator[None]:
    TracerRegistry.reset_for_tests()
    yield
    TracerRegistry.reset_for_tests()


@pytest.fixture(autouse=True)
def _reset_flaky_handler() -> Iterator[None]:
    FlakyTimeoutHandler.calls = 0
    yield
    FlakyTimeoutHandler.calls = 0


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
def registry() -> ToolRegistry:
    reg = ToolRegistry()
    reg.register(ECHO_TOOL_DEFINITION, echo_handler())
    return reg


@pytest.fixture
def executor(registry: ToolRegistry) -> ToolExecutor:
    return ToolExecutor(registry=registry, settings=Settings(request_timeout_seconds=5))


@pytest.fixture
def user_context() -> ToolExecutionContext:
    return ToolExecutionContext(
        caller=CallerContext.for_user(uuid.uuid4()),
        request_id="req-tool-tracing",
    )


@pytest.fixture
def guest_context() -> ToolExecutionContext:
    return ToolExecutionContext(
        caller=CallerContext.anonymous(guest_id=uuid.uuid4()),
        request_id="req-tool-tracing-guest",
    )


class FailingEchoHandler:
    async def execute(
        self,
        args: dict[str, object],
        context: ToolExecutionContext,
    ) -> ToolResult:
        del args, context
        return ToolResult(
            success=False,
            error="handler failed",
            error_code="handler_error",
        )


class FlakyTimeoutHandler:
    calls: ClassVar[int] = 0

    async def execute(
        self,
        args: dict[str, object],
        context: ToolExecutionContext,
    ) -> ToolResult:
        del context
        FlakyTimeoutHandler.calls += 1
        if FlakyTimeoutHandler.calls == 1:
            return ToolResult(
                success=False,
                error="Tool execution timed out",
                error_code="timeout",
            )
        return ToolResult(success=True, data={"echo": args["message"]})


async def test_tool_execute_success_emits_span_with_outcome_attributes(
    memory_exporter: InMemorySpanExporter,
    executor: ToolExecutor,
    user_context: ToolExecutionContext,
) -> None:
    result = await executor.execute(
        ToolCall(name="echo", arguments={"message": "hello"}),
        user_context,
    )

    assert result.success is True
    spans = memory_exporter.get_finished_spans()
    assert len(spans) == 1
    assert spans[0].name == "tool.execute"
    attributes = dict(spans[0].attributes or {})
    assert attributes["tool_name"] == "echo"
    assert attributes["success"] is True
    assert attributes["authorization_result"] == "allowed"
    assert attributes["retry_count"] == 0
    assert isinstance(attributes["latency_ms"], int)
    assert attributes["latency_ms"] >= 0
    assert all("hello" not in str(value) for value in attributes.values())


async def test_tool_execute_failure_records_error_status_without_raising(
    memory_exporter: InMemorySpanExporter,
    user_context: ToolExecutionContext,
) -> None:
    registry = ToolRegistry()
    registry.register(ECHO_TOOL_DEFINITION, FailingEchoHandler())
    executor = ToolExecutor(
        registry=registry, settings=Settings(request_timeout_seconds=5)
    )

    result = await executor.execute(
        ToolCall(name="echo", arguments={"message": "secret-input"}),
        user_context,
    )

    assert result.success is False
    spans = memory_exporter.get_finished_spans()
    assert len(spans) == 1
    attributes = dict(spans[0].attributes or {})
    assert attributes["success"] is False
    assert attributes["authorization_result"] == "allowed"
    assert spans[0].status.status_code.name == "ERROR"
    assert all("secret-input" not in str(value) for value in attributes.values())


async def test_tool_execute_authorization_denied_records_error_status(
    memory_exporter: InMemorySpanExporter,
    executor: ToolExecutor,
    guest_context: ToolExecutionContext,
) -> None:
    result = await executor.execute(
        ToolCall(name="echo", arguments={"message": "hello"}),
        guest_context,
    )

    assert result.success is False
    assert result.error_code == "forbidden"
    spans = memory_exporter.get_finished_spans()
    assert len(spans) == 1
    attributes = dict(spans[0].attributes or {})
    assert attributes["success"] is False
    assert attributes["authorization_result"] == "denied"
    assert spans[0].status.status_code.name == "ERROR"


async def test_tool_runner_retry_emits_one_span_per_attempt_with_retry_count(
    memory_exporter: InMemorySpanExporter,
    user_context: ToolExecutionContext,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    registry = ToolRegistry()
    registry.register(
        ToolDefinition(
            name="echo",
            description="Flaky echo",
            parameters=ECHO_TOOL_DEFINITION.parameters,
        ),
        FlakyTimeoutHandler(),
    )
    executor = ToolExecutor(
        registry=registry, settings=Settings(request_timeout_seconds=5)
    )
    runner = ToolRunner(
        tool_executor=executor,
        retry_policy=ToolRetryPolicy(max_retries=3),
    )
    monkeypatch.setattr("app.core.retry.asyncio.sleep", AsyncMock())

    step = PlannedStep(
        step_id="retry-step",
        action=StepAction.TOOL_CALL,
        tool_calls=[ToolCall(name="echo", arguments={"message": "recover"})],
    )
    results = await runner.run_tool_steps(
        [step],
        execution_id="exec-retry-spans",
        tool_context=user_context,
    )

    assert results.all_succeeded is True
    tool_spans = [
        span
        for span in memory_exporter.get_finished_spans()
        if span.name == "tool.execute"
    ]
    assert len(tool_spans) == 2
    retry_counts = sorted(
        cast(int, dict(span.attributes or {}).get("retry_count", -1))
        for span in tool_spans
    )
    assert retry_counts == [0, 1]
    assert dict(tool_spans[0].attributes or {})["success"] is False
    assert dict(tool_spans[1].attributes or {})["success"] is True


async def test_tool_telemetry_failure_is_fail_open(
    memory_exporter: InMemorySpanExporter,
    executor: ToolExecutor,
    user_context: ToolExecutionContext,
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.WARNING)
    broken_tracer = MagicMock()
    broken_tracer.start_span.side_effect = RuntimeError("telemetry down")

    with patch(
        "app.ai.observability.tracing.spans.get_tracer",
        return_value=broken_tracer,
    ):
        result = await executor.execute(
            ToolCall(name="echo", arguments={"message": "hello"}),
            user_context,
        )

    assert result.success is True
    assert any(
        "Observability span setup failed" in record.message for record in caplog.records
    )
    assert len(memory_exporter.get_finished_spans()) == 0
