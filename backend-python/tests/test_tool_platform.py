"""Tool platform lifecycle tests (Phase 3)."""

from __future__ import annotations

import logging
import uuid
from collections.abc import Iterator
from typing import ClassVar

import pytest

from app.ai.deps import get_tool_executor, get_tool_registry
from app.ai.tools.executor import ToolExecutor
from app.ai.tools.registry import ToolAlreadyRegisteredError, ToolRegistry
from app.ai.tools.schemas import ToolCall, ToolDefinition, ToolExecutionContext
from app.ai.tools.stubs.echo import (
    ECHO_TOOL_DEFINITION,
    EchoToolHandler,
    SlowEchoToolHandler,
    echo_handler,
)
from app.core.caller import CallerContext
from app.core.config import Settings


@pytest.fixture(autouse=True)
def _clear_tool_registry_cache() -> Iterator[None]:
    get_tool_registry.cache_clear()
    yield
    get_tool_registry.cache_clear()


@pytest.fixture
def registry() -> ToolRegistry:
    reg = ToolRegistry()
    reg.register(ECHO_TOOL_DEFINITION, echo_handler())
    return reg


@pytest.fixture
def executor(registry: ToolRegistry) -> ToolExecutor:
    settings = Settings(request_timeout_seconds=1)
    return ToolExecutor(registry=registry, settings=settings)


@pytest.fixture
def user_context() -> ToolExecutionContext:
    return ToolExecutionContext(
        caller=CallerContext.for_user(uuid.uuid4()),
        request_id="req-test-123",
    )


@pytest.fixture
def guest_context() -> ToolExecutionContext:
    return ToolExecutionContext(
        caller=CallerContext.anonymous(guest_id=uuid.uuid4()),
        request_id="req-guest-456",
    )


def test_registration_and_lookup(registry: ToolRegistry) -> None:
    assert registry.get("echo") == ECHO_TOOL_DEFINITION
    tools = registry.list_tools()
    assert len(tools) == 1
    assert tools[0].name == "echo"


def test_duplicate_registration_rejected(registry: ToolRegistry) -> None:
    with pytest.raises(ToolAlreadyRegisteredError, match="already registered"):
        registry.register(ECHO_TOOL_DEFINITION, echo_handler())


def test_get_schemas_for_llm(registry: ToolRegistry) -> None:
    schemas = registry.get_schemas_for_llm()
    assert schemas == [
        {
            "type": "function",
            "function": {
                "name": "echo",
                "description": "Echo a message back to the caller",
                "parameters": ECHO_TOOL_DEFINITION.parameters,
            },
        }
    ]


@pytest.mark.anyio
async def test_validation_failure_skips_handler(
    executor: ToolExecutor,
    user_context: ToolExecutionContext,
) -> None:
    class TrackingHandler(EchoToolHandler):
        calls: ClassVar[int] = 0

        async def execute(self, args, context):  # type: ignore[no-untyped-def]
            TrackingHandler.calls += 1
            return await super().execute(args, context)

    executor._registry.register(
        ToolDefinition(
            name="tracked",
            description="tracked",
            parameters={
                "type": "object",
                "properties": {"message": {"type": "string"}},
                "required": ["message"],
            },
        ),
        TrackingHandler(),
    )
    TrackingHandler.calls = 0

    result = await executor.execute(
        ToolCall(name="tracked", arguments={}),
        user_context,
    )

    assert result.success is False
    assert result.error_code == "validation_error"
    assert "Missing required argument" in (result.error or "")
    assert TrackingHandler.calls == 0


@pytest.mark.anyio
async def test_authorization_denial_for_guest(
    executor: ToolExecutor,
    guest_context: ToolExecutionContext,
) -> None:
    class TrackingHandler(EchoToolHandler):
        calls: ClassVar[int] = 0

        async def execute(self, args, context):  # type: ignore[no-untyped-def]
            TrackingHandler.calls += 1
            return await super().execute(args, context)

    executor._registry.register(
        ToolDefinition(
            name="tracked_guest",
            description="tracked",
            parameters=ECHO_TOOL_DEFINITION.parameters,
        ),
        TrackingHandler(),
    )
    TrackingHandler.calls = 0

    result = await executor.execute(
        ToolCall(name="tracked_guest", arguments={"message": "hello"}),
        guest_context,
    )

    assert result.success is False
    assert result.error_code == "forbidden"
    assert TrackingHandler.calls == 0


@pytest.mark.anyio
async def test_authorization_success_for_user(
    executor: ToolExecutor,
    user_context: ToolExecutionContext,
) -> None:
    result = await executor.execute(
        ToolCall(name="echo", arguments={"message": "hello"}),
        user_context,
    )

    assert result.success is True
    assert result.data == {"echo": "hello"}


@pytest.mark.anyio
async def test_execution_timeout(
    user_context: ToolExecutionContext,
) -> None:
    registry = ToolRegistry()
    registry.register(
        ECHO_TOOL_DEFINITION,
        SlowEchoToolHandler(sleep_seconds=2.0),
    )
    executor = ToolExecutor(
        registry=registry,
        settings=Settings(request_timeout_seconds=1),
    )

    result = await executor.execute(
        ToolCall(name="echo", arguments={"message": "slow"}),
        user_context,
    )

    assert result.success is False
    assert result.error_code == "timeout"


@pytest.mark.anyio
async def test_handler_exception_normalized(
    user_context: ToolExecutionContext,
) -> None:
    class ExplodingHandler:
        async def execute(self, args, context):  # type: ignore[no-untyped-def]
            del args, context
            raise RuntimeError("boom")

    registry = ToolRegistry()
    registry.register(ECHO_TOOL_DEFINITION, ExplodingHandler())
    executor = ToolExecutor(
        registry=registry,
        settings=Settings(request_timeout_seconds=5),
    )

    result = await executor.execute(
        ToolCall(name="echo", arguments={"message": "fail"}),
        user_context,
    )

    assert result.success is False
    assert result.error_code == "handler_error"
    assert result.error == "Tool execution failed"
    assert "boom" not in (result.error or "")


@pytest.mark.anyio
async def test_normalized_success(
    executor: ToolExecutor,
    user_context: ToolExecutionContext,
) -> None:
    result = await executor.execute(
        ToolCall(name="echo", arguments={"message": "ping"}, call_id="call-1"),
        user_context,
    )

    assert result.success is True
    assert result.data == {"echo": "ping"}
    assert result.metadata["tool_name"] == "echo"
    assert result.metadata["call_id"] == "call-1"
    assert isinstance(result.metadata["latency_ms"], int)


@pytest.mark.anyio
async def test_not_found_tool(
    executor: ToolExecutor,
    user_context: ToolExecutionContext,
) -> None:
    result = await executor.execute(
        ToolCall(name="missing", arguments={}),
        user_context,
    )

    assert result.success is False
    assert result.error_code == "not_found"


@pytest.mark.anyio
async def test_metrics_and_logging_fields_success(
    executor: ToolExecutor,
    user_context: ToolExecutionContext,
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.INFO, logger="app.ai.tools.executor")

    await executor.execute(
        ToolCall(name="echo", arguments={"message": "metrics"}),
        user_context,
    )

    records = [
        record
        for record in caplog.records
        if record.name == "app.ai.tools.executor"
        and "Tool execution completed" in record.message
    ]
    assert len(records) == 1
    record = records[0]
    assert getattr(record, "tool_name") == "echo"
    assert getattr(record, "latency_ms") is not None
    assert getattr(record, "success") is True
    assert getattr(record, "request_id") == "req-test-123"
    assert getattr(record, "tool_calls_total") == 1
    assert not hasattr(record, "tool_errors_total")
    assert "metrics" not in record.message


@pytest.mark.anyio
async def test_metrics_and_logging_fields_failure(
    executor: ToolExecutor,
    guest_context: ToolExecutionContext,
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.WARNING, logger="app.ai.tools.executor")

    await executor.execute(
        ToolCall(name="echo", arguments={"message": "denied"}),
        guest_context,
    )

    records = [
        record
        for record in caplog.records
        if record.name == "app.ai.tools.executor"
        and "Tool execution failed" in record.message
    ]
    assert len(records) == 1
    record = records[0]
    assert getattr(record, "tool_name") == "echo"
    assert getattr(record, "tool_calls_total") == 1
    assert getattr(record, "tool_errors_total") == 1
    assert getattr(record, "error_code") == "forbidden"
    assert "denied" not in record.message


def test_di_singleton_registry() -> None:
    first = get_tool_registry()
    second = get_tool_registry()
    assert first is second


def test_di_executor_wiring() -> None:
    registry = get_tool_registry()
    executor = get_tool_executor(
        registry=registry, settings=Settings(), rbac_service=None
    )
    assert executor._registry is registry
