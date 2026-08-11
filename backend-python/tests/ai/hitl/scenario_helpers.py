"""Shared helpers for HITL reference and adversarial scenario tests."""

from __future__ import annotations

import datetime
import uuid

from app.ai.agent.executor.agent_executor import AgentExecutor
from app.ai.agent.executor.tool_runner import ToolRunner
from app.ai.agent.models.config import AgentConfig
from app.ai.agent.models.context import AgentContext
from app.ai.agent.models.messages import AgentMessage
from app.ai.agent.models.plan import PlannedStep, StepAction
from app.ai.agent.models.request import AgentRequest
from app.ai.agent.models.state import AgentExecutionState, AgentExecutionStatus
from app.ai.agent.planner.react_planner import ReActPlanner
from app.ai.agent.runtime.default_agent import DefaultAgent
from app.ai.agent.scratchpad import Scratchpad, ScratchpadStore
from app.ai.agent.streaming import InMemoryStreamPublisher, NoOpStreamPublisher
from app.ai.hitl.policy import ApprovalPolicy
from app.ai.hitl.service import AgentApprovalService
from app.ai.prompts.manager import create_prompt_manager
from app.ai.tools.executor import ToolExecutor
from app.ai.tools.registry import ToolRegistry
from app.ai.tools.schemas import (
    ToolCall,
    ToolDefinition,
    ToolExecutionContext,
    ToolResult,
)
from app.ai.tools.stubs.echo import ECHO_TOOL_DEFINITION, EchoToolHandler
from app.ai.tools.stubs.send_notification import (
    SEND_NOTIFICATION_TOOL_DEFINITION,
    SEND_NOTIFICATION_TOOL_NAME,
    SendNotificationHandler,
)
from app.ai.workflow.graph.validator import GraphValidator
from app.ai.workflow.manager import WorkflowManager
from app.ai.workflow.models import (
    DefinitionStatus,
    NodeType,
    WorkflowDefinition,
    WorkflowEdge,
    WorkflowNode,
)
from app.ai.workflow.nodes.approval_node import ApprovalNodeExecutor
from app.ai.workflow.nodes.parallel_node import ForkNodeExecutor, JoinNodeExecutor
from app.core.caller import CallerContext
from app.core.config import Settings
from app.providers.base import ProviderToolCall, ProviderToolCompletion, ProviderUsage
from tests.ai.hitl.fakes import InMemoryApprovalStore
from tests.fakes import FakeChatStore, FakeProvider

MCP_TOOL_NAME = "test_server.sensitive_action"
PLUGIN_TOOL_NAME = "com.example.sensitive.ping"
_NOW = datetime.datetime.now(datetime.UTC)


class _SensitiveMcpHandler:
    call_count: int = 0

    @classmethod
    def reset(cls) -> None:
        cls.call_count = 0

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

    @classmethod
    def reset(cls) -> None:
        cls.call_count = 0

    async def execute(
        self,
        args: dict[str, object],
        context: ToolExecutionContext,
    ) -> ToolResult:
        del args, context
        _SensitivePluginHandler.call_count += 1
        return ToolResult(success=True, data={"ok": True})


def hitl_settings(**overrides: object) -> Settings:
    base = {
        "openai_api_key": "test-key",
        "agent_runtime_enabled": True,
        "workflow_engine_enabled": True,
        "hitl_enabled": True,
    }
    base.update(overrides)
    return Settings(**base)  # type: ignore[arg-type]


def reference_tool_registry() -> ToolRegistry:
    registry = ToolRegistry()
    registry.register(SEND_NOTIFICATION_TOOL_DEFINITION, SendNotificationHandler())
    registry.register(ECHO_TOOL_DEFINITION, EchoToolHandler())
    return registry


def mcp_tool_registry() -> ToolRegistry:
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


def plugin_tool_registry() -> ToolRegistry:
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


def build_approval_service(
    *,
    registry: ToolRegistry,
    store: InMemoryApprovalStore | None = None,
    chat_store: FakeChatStore | None = None,
) -> tuple[AgentApprovalService, InMemoryApprovalStore, FakeChatStore]:
    approval_store = store or InMemoryApprovalStore()
    messages = chat_store or FakeChatStore()
    settings = hitl_settings()
    service = AgentApprovalService(
        approval_store=approval_store,
        chat_store=messages,
        tool_registry=registry,
        tool_executor=ToolExecutor(registry=registry, settings=settings),
        scratchpad_store=ScratchpadStore(),
    )
    return service, approval_store, messages


def build_default_agent(
    *,
    registry: ToolRegistry,
    service: AgentApprovalService,
    provider: FakeProvider,
    scratchpad_store: ScratchpadStore | None = None,
) -> DefaultAgent:
    settings = hitl_settings()
    tool_executor = ToolExecutor(registry=registry, settings=settings)
    prompt_manager = create_prompt_manager()
    return DefaultAgent(
        settings=settings,
        tool_registry=registry,
        prompt_manager=prompt_manager,
        tool_executor=tool_executor,
        scratchpad_store=scratchpad_store or ScratchpadStore(),
        approval_policy=ApprovalPolicy(required_tool_names=frozenset()),
        approval_service=service,
    )


def build_resume_executor(
    *,
    registry: ToolRegistry,
    service: AgentApprovalService,
    provider: FakeProvider,
    scratchpad_store: ScratchpadStore | None = None,
) -> AgentExecutor:
    settings = hitl_settings()
    tool_executor = ToolExecutor(registry=registry, settings=settings)
    scratchpad = scratchpad_store or ScratchpadStore()
    prompt_manager = create_prompt_manager()
    runner = ToolRunner(
        tool_executor=tool_executor,
        tool_registry=registry,
        stream_publisher=NoOpStreamPublisher(),
        hitl_enabled=True,
        approval_policy=ApprovalPolicy(required_tool_names=frozenset()),
        approval_service=service,
    )
    return AgentExecutor(
        planner=ReActPlanner(
            provider=provider,
            tool_registry=registry,
            prompt_manager=prompt_manager,
            scratchpad_store=scratchpad,
        ),
        provider=provider,
        tool_runner=runner,
        stream_publisher=NoOpStreamPublisher(),
        scratchpad_store=scratchpad,
        prompt_manager=prompt_manager,
    )


def notification_provider(*, final_text: str = "Notification sent.") -> FakeProvider:
    return FakeProvider(
        tool_completions=[
            ProviderToolCompletion(
                content="Sending notification.",
                tool_calls=[
                    ProviderToolCall(
                        id="call-notify-1",
                        name=SEND_NOTIFICATION_TOOL_NAME,
                        arguments={"message": "hello", "channel": "email"},
                    )
                ],
            ),
            ProviderToolCompletion(
                content=final_text,
                tool_calls=[],
                finish_reason="stop",
                usage=ProviderUsage(
                    prompt_tokens=1,
                    completion_tokens=1,
                    total_tokens=2,
                ),
            ),
        ]
    )


def sequential_notification_provider() -> FakeProvider:
    return FakeProvider(
        tool_completions=[
            ProviderToolCompletion(
                content="First notification.",
                tool_calls=[
                    ProviderToolCall(
                        id="call-notify-1",
                        name=SEND_NOTIFICATION_TOOL_NAME,
                        arguments={"message": "first", "channel": "email"},
                    )
                ],
            ),
            ProviderToolCompletion(
                content="Second notification.",
                tool_calls=[
                    ProviderToolCall(
                        id="call-notify-2",
                        name=SEND_NOTIFICATION_TOOL_NAME,
                        arguments={"message": "second", "channel": "sms"},
                    )
                ],
            ),
            ProviderToolCompletion(
                content="Done.",
                tool_calls=[],
                finish_reason="stop",
                usage=ProviderUsage(
                    prompt_tokens=1,
                    completion_tokens=1,
                    total_tokens=2,
                ),
            ),
        ]
    )


def hitl_graph_validator(registry: ToolRegistry) -> GraphValidator:
    return GraphValidator(
        max_nodes_per_definition=20,
        max_parallel_branches=8,
        hitl_enabled=True,
        tool_registry=registry,
        approval_policy=ApprovalPolicy(required_tool_names=frozenset()),
    )


def edited_args_workflow_definition(owner_id: uuid.UUID) -> WorkflowDefinition:
    return WorkflowDefinition(
        id=uuid.uuid4(),
        owner_id=owner_id,
        name="HITL Reference Workflow",
        status=DefinitionStatus.ACTIVE,
        entry_node_id="start",
        nodes=[
            WorkflowNode(
                id="start",
                type=NodeType.TASK,
                config={
                    "tool_name": "echo",
                    "arguments_template": {"message": "start"},
                },
            ),
            WorkflowNode(
                id="approve",
                type=NodeType.APPROVAL,
                config={"approved_edge_id": "approved"},
            ),
            WorkflowNode(
                id="after",
                type=NodeType.TASK,
                config={
                    "tool_name": "echo",
                    "arguments_template": {
                        "message": "{{variables.approve.edited_arguments.message}}"
                    },
                },
            ),
            WorkflowNode(id="end", type=NodeType.TERMINAL, config={}),
        ],
        edges=[
            WorkflowEdge(id="e1", from_node_id="start", to_node_id="approve"),
            WorkflowEdge(id="approved", from_node_id="approve", to_node_id="after"),
            WorkflowEdge(id="e3", from_node_id="after", to_node_id="end"),
        ],
        created_at=_NOW,
        updated_at=_NOW,
    )


def nested_parallel_approval_definition(owner_id: uuid.UUID) -> WorkflowDefinition:
    return WorkflowDefinition(
        id=uuid.uuid4(),
        owner_id=owner_id,
        name="Nested Parallel Approval Workflow",
        status=DefinitionStatus.ACTIVE,
        entry_node_id="start",
        nodes=[
            WorkflowNode(id="start", type=NodeType.TASK, config={}),
            WorkflowNode(
                id="fork",
                type=NodeType.FORK,
                config={"join_node_id": "join"},
            ),
            WorkflowNode(id="left", type=NodeType.TASK, config={}),
            WorkflowNode(id="right", type=NodeType.TASK, config={}),
            WorkflowNode(
                id="approve_left",
                type=NodeType.APPROVAL,
                config={"approved_edge_id": "left_ok"},
            ),
            WorkflowNode(
                id="approve_right",
                type=NodeType.APPROVAL,
                config={"approved_edge_id": "right_ok"},
            ),
            WorkflowNode(
                id="join",
                type=NodeType.JOIN,
                config={"fork_node_id": "fork", "join_policy": "all"},
            ),
            WorkflowNode(
                id="risky",
                type=NodeType.TASK,
                config={
                    "tool_name": SEND_NOTIFICATION_TOOL_NAME,
                    "arguments_template": {"message": "nested"},
                },
            ),
            WorkflowNode(id="end", type=NodeType.TERMINAL, config={}),
        ],
        edges=[
            WorkflowEdge(id="e1", from_node_id="start", to_node_id="fork"),
            WorkflowEdge(id="e2", from_node_id="fork", to_node_id="left"),
            WorkflowEdge(id="e3", from_node_id="fork", to_node_id="right"),
            WorkflowEdge(id="e4", from_node_id="left", to_node_id="approve_left"),
            WorkflowEdge(id="e5", from_node_id="right", to_node_id="approve_right"),
            WorkflowEdge(id="left_ok", from_node_id="approve_left", to_node_id="join"),
            WorkflowEdge(
                id="right_ok", from_node_id="approve_right", to_node_id="join"
            ),
            WorkflowEdge(id="e8", from_node_id="join", to_node_id="risky"),
            WorkflowEdge(id="e9", from_node_id="risky", to_node_id="end"),
        ],
        created_at=_NOW,
        updated_at=_NOW,
    )


def build_in_memory_workflow_manager(
    *,
    registry: ToolRegistry | None = None,
) -> tuple[WorkflowManager, ToolRegistry]:
    from tests.ai.workflow.test_approval_node import FakeTaskExecutor
    from tests.ai.workflow.test_interfaces import FakeWorkflowStore

    tool_registry = registry or reference_tool_registry()
    settings = hitl_settings()
    store = FakeWorkflowStore()
    manager = WorkflowManager(
        store,
        settings=settings,
        node_executors={
            NodeType.TASK: FakeTaskExecutor(),
            NodeType.APPROVAL: ApprovalNodeExecutor(),
            NodeType.FORK: ForkNodeExecutor(max_parallel_branches=8),
            NodeType.JOIN: JoinNodeExecutor(),
        },
        tool_registry=tool_registry,
    )
    return manager, tool_registry


async def pause_agent_on_notification(
    *,
    service: AgentApprovalService,
    approval_store: InMemoryApprovalStore,
    chat_store: FakeChatStore,
    registry: ToolRegistry,
    owner_id: uuid.UUID,
    provider: FakeProvider | None = None,
) -> tuple[uuid.UUID, uuid.UUID, uuid.UUID, AgentExecutor]:
    """Run agent until the first approval pause; return ids and resume executor."""
    provider = provider or notification_provider()
    session = await chat_store.create_session(user_id=owner_id)
    agent = build_default_agent(
        registry=registry,
        service=service,
        provider=provider,
    )
    caller = CallerContext.for_user(owner_id)
    context = AgentContext(
        execution_id=f"exec-{uuid.uuid4().hex[:8]}",
        caller=caller,
        session_id=session.id,
    )
    request = AgentRequest(
        messages=[AgentMessage(role="user", content="Send a notification")],
        model="gpt-4o-mini",
        config=AgentConfig(max_iterations=3),
    )

    from unittest.mock import patch

    from app.providers.factory import ProviderFactory

    with patch.object(
        ProviderFactory,
        "get_provider",
        staticmethod(lambda _name, _settings: provider),
    ):
        response = await agent.run(request, context)

    assert response.finish_reason == "waiting_approval"
    pending = [row for row in approval_store.rows if row.status.value == "pending"]
    assert len(pending) == 1
    approval_id = pending[0].id
    executor = build_resume_executor(
        registry=registry,
        service=service,
        provider=provider,
    )
    return approval_id, session.id, owner_id, executor


def parallel_fork_join_approval_definition(owner_id: uuid.UUID) -> WorkflowDefinition:
    """Fork/join workflow with a shared approval gate (Epic 06 fixture pattern)."""
    return WorkflowDefinition(
        id=uuid.uuid4(),
        owner_id=owner_id,
        name="Parallel Approval Workflow",
        status=DefinitionStatus.ACTIVE,
        entry_node_id="start",
        nodes=[
            WorkflowNode(id="start", type=NodeType.TASK, config={}),
            WorkflowNode(
                id="fork",
                type=NodeType.FORK,
                config={"join_node_id": "join"},
            ),
            WorkflowNode(id="left", type=NodeType.TASK, config={}),
            WorkflowNode(id="right", type=NodeType.TASK, config={}),
            WorkflowNode(
                id="approve",
                type=NodeType.APPROVAL,
                config={"approved_edge_id": "approved"},
            ),
            WorkflowNode(
                id="join",
                type=NodeType.JOIN,
                config={"fork_node_id": "fork", "join_policy": "all"},
            ),
            WorkflowNode(id="end", type=NodeType.TERMINAL, config={}),
        ],
        edges=[
            WorkflowEdge(id="e1", from_node_id="start", to_node_id="fork"),
            WorkflowEdge(id="e2", from_node_id="fork", to_node_id="left"),
            WorkflowEdge(id="e3", from_node_id="fork", to_node_id="right"),
            WorkflowEdge(id="e4", from_node_id="left", to_node_id="approve"),
            WorkflowEdge(id="e5", from_node_id="right", to_node_id="approve"),
            WorkflowEdge(id="approved", from_node_id="approve", to_node_id="join"),
            WorkflowEdge(id="e6", from_node_id="join", to_node_id="end"),
        ],
        created_at=_NOW,
        updated_at=_NOW,
    )


async def pause_tool_runner_step(
    *,
    registry: ToolRegistry,
    service: AgentApprovalService,
    approval_store: InMemoryApprovalStore,
    chat_store: FakeChatStore,
    tool_name: str,
    arguments: dict[str, object],
) -> uuid.UUID:
    from app.ai.hitl.exceptions import AgentApprovalPauseError

    owner_id = uuid.uuid4()
    session = await chat_store.create_session(user_id=owner_id)
    tool_context = ToolExecutionContext(caller=CallerContext.for_user(owner_id))
    runner = ToolRunner(
        tool_executor=ToolExecutor(registry=registry, settings=hitl_settings()),
        tool_registry=registry,
        stream_publisher=InMemoryStreamPublisher(),
        hitl_enabled=True,
        approval_policy=ApprovalPolicy(required_tool_names=frozenset()),
        approval_service=service,
    )
    scratchpad = Scratchpad(f"exec-{uuid.uuid4().hex[:8]}")
    state = AgentExecutionState(
        execution_id=scratchpad.execution_id,
        status=AgentExecutionStatus.EXECUTING,
    )
    step = PlannedStep(
        step_id="s1",
        action=StepAction.TOOL_CALL,
        tool_calls=[ToolCall(name=tool_name, arguments=arguments)],
    )
    try:
        await runner.run_tool_steps(
            [step],
            execution_id=scratchpad.execution_id,
            tool_context=tool_context,
            scratchpad=scratchpad,
            state=state,
            session_id=session.id,
            owner_id=owner_id,
        )
    except AgentApprovalPauseError:
        pass
    else:
        raise AssertionError("Expected AgentApprovalPauseError")
    pending = next(row for row in approval_store.rows)
    return pending.id
