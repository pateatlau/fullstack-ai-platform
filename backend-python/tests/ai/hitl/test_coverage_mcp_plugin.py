"""MCP and plugin tool HITL coverage tests (Epic 09 Phase 5)."""

from __future__ import annotations

import datetime
import uuid
from collections.abc import Iterator

import pytest

from app.ai.agent.executor import ToolRunner
from app.ai.agent.models.events import AgentStreamEventType
from app.ai.agent.models.plan import PlannedStep, StepAction
from app.ai.agent.models.state import AgentExecutionState, AgentExecutionStatus
from app.ai.agent.scratchpad import ScratchpadStore
from app.ai.agent.streaming import InMemoryStreamPublisher
from app.ai.hitl.exceptions import AgentApprovalPauseError
from app.ai.hitl.policy import ApprovalPolicy
from app.ai.hitl.service import AgentApprovalService
from app.ai.mcp.executor import McpToolExecutionAdapter
from app.ai.tools.executor import ToolExecutor
from app.ai.tools.registry import ToolRegistry
from app.ai.tools.schemas import (
    ToolCall,
    ToolDefinition,
    ToolExecutionContext,
    ToolResult,
)
from app.ai.workflow.exceptions import WorkflowValidationError
from app.ai.workflow.graph.validator import GraphValidator
from app.ai.workflow.models import (
    NodeType,
    WorkflowDefinition,
    WorkflowEdge,
    WorkflowNode,
)
from app.core.caller import CallerContext
from app.core.config import Settings
from tests.ai.hitl.fakes import InMemoryApprovalStore
from tests.fakes import FakeChatStore

MCP_TOOL_NAME = "test_server.sensitive_action"
PLUGIN_TOOL_NAME = "com.example.sensitive.ping"
_NOW = datetime.datetime.now(datetime.UTC)


class _SensitiveMcpHandler:
    call_count: int = 0

    async def execute(
        self,
        args: dict[str, object],
        context: ToolExecutionContext,
    ) -> ToolResult:
        del args, context
        _SensitiveMcpHandler.call_count += 1
        return ToolResult(success=True, data={"ok": True})


class _SensitivePluginHandler:
    call_count: int = 0

    async def execute(
        self,
        args: dict[str, object],
        context: ToolExecutionContext,
    ) -> ToolResult:
        del args, context
        _SensitivePluginHandler.call_count += 1
        return ToolResult(success=True, data={"ok": True})


@pytest.fixture(autouse=True)
def _reset_call_counts() -> Iterator[None]:
    _SensitiveMcpHandler.call_count = 0
    _SensitivePluginHandler.call_count = 0
    yield


def _mcp_tool_registry() -> ToolRegistry:
    registry = ToolRegistry()
    registry.register(
        ToolDefinition(
            name=MCP_TOOL_NAME,
            description="Sensitive MCP tool fixture",
            parameters={"type": "object", "properties": {}},
            requires_approval=True,
        ),
        _SensitiveMcpHandler(),
    )
    return registry


def _plugin_tool_registry() -> ToolRegistry:
    registry = ToolRegistry()
    registry.register(
        ToolDefinition(
            name=PLUGIN_TOOL_NAME,
            description="Sensitive plugin tool fixture",
            parameters={
                "type": "object",
                "properties": {"message": {"type": "string"}},
                "required": ["message"],
            },
            requires_approval=True,
        ),
        _SensitivePluginHandler(),
    )
    return registry


def _approval_service(chat_store: FakeChatStore) -> AgentApprovalService:
    return AgentApprovalService(
        approval_store=InMemoryApprovalStore(),
        chat_store=chat_store,
    )


def _tool_context() -> ToolExecutionContext:
    return ToolExecutionContext(
        caller=CallerContext.for_user(uuid.uuid4()),
        request_id="req-mcp-plugin",
    )


def _hitl_validator(registry: ToolRegistry) -> GraphValidator:
    return GraphValidator(
        max_nodes_per_definition=20,
        max_parallel_branches=8,
        hitl_enabled=True,
        tool_registry=registry,
        approval_policy=ApprovalPolicy(required_tool_names=frozenset()),
    )


def _workflow_definition(*, tool_name: str) -> WorkflowDefinition:
    return WorkflowDefinition(
        id=uuid.uuid4(),
        owner_id=uuid.uuid4(),
        name="Sensitive Tool Workflow",
        entry_node_id="start",
        nodes=[
            WorkflowNode(
                id="start",
                type=NodeType.TASK,
                config={"tool_name": tool_name, "arguments_template": {}},
            ),
            WorkflowNode(id="end", type=NodeType.TERMINAL, config={}),
        ],
        edges=[
            WorkflowEdge(id="e1", from_node_id="start", to_node_id="end"),
        ],
        created_at=_NOW,
        updated_at=_NOW,
    )


@pytest.mark.anyio
async def test_mcp_tool_pauses_agent_path_identically_to_native_tool() -> None:
    registry = _mcp_tool_registry()
    executor = ToolExecutor(registry=registry, settings=Settings())
    publisher = InMemoryStreamPublisher()
    chat_store = FakeChatStore()
    tool_context = _tool_context()
    session = await chat_store.create_session(user_id=tool_context.caller.user_id)  # type: ignore[union-attr]
    runner = ToolRunner(
        tool_executor=executor,
        tool_registry=registry,
        stream_publisher=publisher,
        hitl_enabled=True,
        approval_policy=ApprovalPolicy(required_tool_names=frozenset()),
        approval_service=_approval_service(chat_store),
    )
    scratchpad = ScratchpadStore().create("exec-mcp")
    state = AgentExecutionState(
        execution_id="exec-mcp",
        status=AgentExecutionStatus.EXECUTING,
    )
    step = PlannedStep(
        step_id="s1",
        action=StepAction.TOOL_CALL,
        tool_calls=[ToolCall(name=MCP_TOOL_NAME, arguments={})],
    )

    with pytest.raises(AgentApprovalPauseError):
        await runner.run_tool_steps(
            [step],
            execution_id="exec-mcp",
            tool_context=tool_context,
            scratchpad=scratchpad,
            state=state,
            session_id=session.id,
            owner_id=tool_context.caller.user_id,  # type: ignore[union-attr]
        )

    assert _SensitiveMcpHandler.call_count == 0
    assert publisher.events[-1].type == AgentStreamEventType.APPROVAL_REQUIRED


@pytest.mark.anyio
async def test_plugin_tool_pauses_agent_path_identically_to_native_tool() -> None:
    registry = _plugin_tool_registry()
    executor = ToolExecutor(registry=registry, settings=Settings())
    publisher = InMemoryStreamPublisher()
    chat_store = FakeChatStore()
    tool_context = _tool_context()
    session = await chat_store.create_session(user_id=tool_context.caller.user_id)  # type: ignore[union-attr]
    runner = ToolRunner(
        tool_executor=executor,
        tool_registry=registry,
        stream_publisher=publisher,
        hitl_enabled=True,
        approval_policy=ApprovalPolicy(required_tool_names=frozenset()),
        approval_service=_approval_service(chat_store),
    )
    scratchpad = ScratchpadStore().create("exec-plugin")
    state = AgentExecutionState(
        execution_id="exec-plugin",
        status=AgentExecutionStatus.EXECUTING,
    )
    step = PlannedStep(
        step_id="s1",
        action=StepAction.TOOL_CALL,
        tool_calls=[ToolCall(name=PLUGIN_TOOL_NAME, arguments={"message": "hello"})],
    )

    with pytest.raises(AgentApprovalPauseError):
        await runner.run_tool_steps(
            [step],
            execution_id="exec-plugin",
            tool_context=tool_context,
            scratchpad=scratchpad,
            state=state,
            session_id=session.id,
            owner_id=tool_context.caller.user_id,  # type: ignore[union-attr]
        )

    assert _SensitivePluginHandler.call_count == 0
    assert publisher.events[-1].type == AgentStreamEventType.APPROVAL_REQUIRED


def test_graph_guard_rejects_mcp_tool_without_preceding_approval() -> None:
    registry = _mcp_tool_registry()
    validator = _hitl_validator(registry)

    with pytest.raises(WorkflowValidationError, match=MCP_TOOL_NAME):
        validator.validate(_workflow_definition(tool_name=MCP_TOOL_NAME))


def test_graph_guard_rejects_plugin_tool_without_preceding_approval() -> None:
    registry = _plugin_tool_registry()
    validator = _hitl_validator(registry)

    with pytest.raises(WorkflowValidationError, match=PLUGIN_TOOL_NAME):
        validator.validate(_workflow_definition(tool_name=PLUGIN_TOOL_NAME))


def test_mcp_adapter_is_ordinary_tool_handler() -> None:
    """No MCP-specific approval code — adapter implements ToolHandler only."""
    from unittest.mock import AsyncMock

    client = AsyncMock()
    adapter = McpToolExecutionAdapter(
        server_name="test_server",
        tool_name="sensitive_action",
        client=client,
        metadata={"source": "mcp"},
    )
    assert hasattr(adapter, "execute")
