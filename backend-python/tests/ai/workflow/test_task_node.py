"""Tests for ``TaskNodeExecutor`` (Epic 06 Phase 3)."""

from __future__ import annotations

import uuid

import pytest

from app.ai.tools.executor import ToolExecutor
from app.ai.tools.registry import ToolRegistry
from app.ai.tools.schemas import ToolExecutionContext, ToolResult
from app.ai.tools.stubs.echo import (
    ECHO_TOOL_DEFINITION,
    ECHO_TOOL_NAME,
    EchoToolHandler,
)
from app.ai.workflow.models import NodeType, WorkflowContext, WorkflowNode
from app.ai.workflow.nodes.base import NodeExecutionRequest, WorkflowNodeExecutionError
from app.ai.workflow.nodes.task_node import TaskNodeExecutor
from app.core.config import Settings


class _RecordingHandler:
    """Captures the ``ToolExecutionContext`` it was invoked with."""

    def __init__(self) -> None:
        self.received_context: ToolExecutionContext | None = None

    async def execute(
        self, args: dict[str, object], context: ToolExecutionContext
    ) -> ToolResult:
        self.received_context = context
        return ToolResult(success=True, data=args)


def _tool_executor(registry: ToolRegistry | None = None) -> ToolExecutor:
    settings = Settings(openai_api_key="test-key")
    return ToolExecutor(registry=registry or ToolRegistry(), settings=settings)


def _request(owner_id: uuid.UUID | None = None) -> NodeExecutionRequest:
    return NodeExecutionRequest(
        owner_id=owner_id or uuid.uuid4(), execution_receipt_id="run-1:start:1"
    )


@pytest.mark.anyio
async def test_executes_registered_tool_with_literal_arguments() -> None:
    registry = ToolRegistry()
    registry.register(ECHO_TOOL_DEFINITION, EchoToolHandler())
    node = WorkflowNode(
        id="start",
        type=NodeType.TASK,
        config={
            "tool_name": ECHO_TOOL_NAME,
            "arguments_template": {"message": "hello"},
        },
    )
    executor = TaskNodeExecutor(_tool_executor(registry))

    output = await executor.execute(node, WorkflowContext(), _request())

    assert output["success"] is True
    assert output["data"] == {"echo": "hello"}


@pytest.mark.anyio
async def test_resolves_placeholder_against_trigger_input() -> None:
    registry = ToolRegistry()
    registry.register(ECHO_TOOL_DEFINITION, EchoToolHandler())
    node = WorkflowNode(
        id="start",
        type=NodeType.TASK,
        config={
            "tool_name": ECHO_TOOL_NAME,
            "arguments_template": {"message": "{{trigger_input.topic}}"},
        },
    )
    context = WorkflowContext(trigger_input={"topic": "release notes"})
    executor = TaskNodeExecutor(_tool_executor(registry))

    output = await executor.execute(node, context, _request())

    assert output["data"] == {"echo": "release notes"}


@pytest.mark.anyio
async def test_resolves_placeholder_against_prior_node_output() -> None:
    registry = ToolRegistry()
    registry.register(ECHO_TOOL_DEFINITION, EchoToolHandler())
    node = WorkflowNode(
        id="second",
        type=NodeType.TASK,
        config={
            "tool_name": ECHO_TOOL_NAME,
            "arguments_template": {"message": "{{variables.first.data.echo}}"},
        },
    )
    context = WorkflowContext(
        variables={"first": {"success": True, "data": {"echo": "hi"}}}
    )
    executor = TaskNodeExecutor(_tool_executor(registry))

    output = await executor.execute(node, context, _request())

    assert output["data"] == {"echo": "hi"}


@pytest.mark.anyio
async def test_passes_execution_receipt_id_through_tool_context() -> None:
    registry = ToolRegistry()
    handler = _RecordingHandler()
    registry.register(ECHO_TOOL_DEFINITION, handler)
    node = WorkflowNode(
        id="start",
        type=NodeType.TASK,
        config={"tool_name": ECHO_TOOL_NAME, "arguments_template": {"message": "hi"}},
    )
    request = _request()
    executor = TaskNodeExecutor(_tool_executor(registry))

    await executor.execute(node, WorkflowContext(), request)

    assert handler.received_context is not None
    assert handler.received_context.execution_receipt_id == request.execution_receipt_id
    assert handler.received_context.caller.user_id == request.owner_id


@pytest.mark.anyio
async def test_missing_tool_name_raises_node_execution_error() -> None:
    node = WorkflowNode(id="start", type=NodeType.TASK, config={})
    executor = TaskNodeExecutor(_tool_executor())

    with pytest.raises(WorkflowNodeExecutionError, match="tool_name"):
        await executor.execute(node, WorkflowContext(), _request())


@pytest.mark.anyio
async def test_unregistered_tool_raises_node_execution_error_not_a_crash() -> None:
    node = WorkflowNode(
        id="start",
        type=NodeType.TASK,
        config={"tool_name": "does_not_exist", "arguments_template": {}},
    )
    executor = TaskNodeExecutor(_tool_executor())

    with pytest.raises(WorkflowNodeExecutionError):
        await executor.execute(node, WorkflowContext(), _request())


@pytest.mark.anyio
async def test_unresolved_placeholder_raises_node_execution_error() -> None:
    registry = ToolRegistry()
    registry.register(ECHO_TOOL_DEFINITION, EchoToolHandler())
    node = WorkflowNode(
        id="start",
        type=NodeType.TASK,
        config={
            "tool_name": ECHO_TOOL_NAME,
            "arguments_template": {"message": "{{trigger_input.missing}}"},
        },
    )
    executor = TaskNodeExecutor(_tool_executor(registry))

    with pytest.raises(WorkflowNodeExecutionError, match="Unresolved"):
        await executor.execute(node, WorkflowContext(), _request())
