"""Tests for ``AgentNodeExecutor`` (Epic 06 Phase 6)."""

from __future__ import annotations

import uuid

import pytest

from app.ai.agent.models.config import AgentConfig
from app.ai.agent.models.context import AgentContext
from app.ai.agent.models.request import AgentRequest
from app.ai.agent.models.response import AgentResponse
from app.ai.workflow.models import NodeType, WorkflowContext, WorkflowNode
from app.ai.workflow.nodes.agent_node import AgentNodeExecutor
from app.ai.workflow.nodes.base import NodeExecutionRequest, WorkflowNodeExecutionError
from app.core.config import Settings


class FakeAgent:
    """Captures ``AgentRequest`` / ``AgentContext`` for assertions."""

    def __init__(
        self,
        *,
        response: AgentResponse | None = None,
        error: Exception | None = None,
    ) -> None:
        self.response = response or AgentResponse(content="done", iterations=2)
        self.error = error
        self.last_request: AgentRequest | None = None
        self.last_context: AgentContext | None = None

    async def run(self, request: AgentRequest, context: AgentContext) -> AgentResponse:
        self.last_request = request
        self.last_context = context
        if self.error is not None:
            raise self.error
        return self.response


def _request(owner_id: uuid.UUID | None = None) -> NodeExecutionRequest:
    return NodeExecutionRequest(
        owner_id=owner_id or uuid.uuid4(),
        execution_receipt_id="run-1:agent:1",
    )


def _settings(*, agent_enabled: bool = True) -> Settings:
    return Settings(openai_api_key="test-key", agent_runtime_enabled=agent_enabled)


@pytest.mark.anyio
async def test_executes_agent_subtask_and_maps_response() -> None:
    agent = FakeAgent(
        response=AgentResponse(
            content="Research complete.",
            tools_used=["web_search"],
            iterations=3,
            finish_reason="stop",
        )
    )
    node = WorkflowNode(
        id="research",
        type=NodeType.AGENT,
        config={
            "goal": "Research {{ variables.topic.name }}",
            "instructions": "Be concise.",
            "tool_names": ["web_search"],
            "max_iterations": 4,
        },
    )
    context = WorkflowContext(variables={"topic": {"name": "widgets"}})
    executor = AgentNodeExecutor(agent, settings=_settings())

    output = await executor.execute(node, context, _request())

    assert output["content"] == "Research complete."
    assert output["tools_used"] == ["web_search"]
    assert output["iterations"] == 3
    assert output["execution_receipt_id"] == "run-1:agent:1"
    assert agent.last_request is not None
    assert agent.last_request.messages[0].content == "Research widgets"
    assert agent.last_request.system_prompt == "Be concise."
    assert agent.last_request.tool_names == ["web_search"]
    assert agent.last_request.config == AgentConfig(max_iterations=4)


@pytest.mark.anyio
async def test_passes_execution_receipt_id_in_agent_context_metadata() -> None:
    agent = FakeAgent()
    node = WorkflowNode(
        id="agent",
        type=NodeType.AGENT,
        config={"goal": "Do work"},
    )
    request = _request()
    executor = AgentNodeExecutor(agent, settings=_settings())

    await executor.execute(node, WorkflowContext(), request)

    assert agent.last_context is not None
    assert agent.last_context.metadata["execution_receipt_id"] == (
        request.execution_receipt_id
    )


@pytest.mark.anyio
async def test_agent_runtime_disabled_fails_with_clear_error() -> None:
    agent = FakeAgent()
    node = WorkflowNode(
        id="agent",
        type=NodeType.AGENT,
        config={"goal": "Do work"},
    )
    executor = AgentNodeExecutor(agent, settings=_settings(agent_enabled=False))

    with pytest.raises(WorkflowNodeExecutionError, match="AGENT_RUNTIME_ENABLED"):
        await executor.execute(node, WorkflowContext(), _request())

    assert agent.last_request is None


@pytest.mark.anyio
async def test_missing_goal_raises_node_execution_error() -> None:
    node = WorkflowNode(id="agent", type=NodeType.AGENT, config={})
    executor = AgentNodeExecutor(FakeAgent(), settings=_settings())

    with pytest.raises(WorkflowNodeExecutionError, match="goal"):
        await executor.execute(node, WorkflowContext(), _request())


@pytest.mark.anyio
async def test_invalid_max_iterations_raises_node_execution_error() -> None:
    agent = FakeAgent()
    node = WorkflowNode(
        id="agent",
        type=NodeType.AGENT,
        config={"goal": "Do work", "max_iterations": 0},
    )
    executor = AgentNodeExecutor(agent, settings=_settings())

    with pytest.raises(WorkflowNodeExecutionError, match="max_iterations") as exc:
        await executor.execute(node, WorkflowContext(), _request())

    assert exc.value.error_code == "invalid_config"
    assert agent.last_request is None


@pytest.mark.anyio
async def test_agent_failure_becomes_node_failure_not_crash() -> None:
    agent = FakeAgent(error=RuntimeError("agent loop failed"))
    node = WorkflowNode(
        id="agent",
        type=NodeType.AGENT,
        config={"goal": "Do work"},
    )
    executor = AgentNodeExecutor(agent, settings=_settings())

    with pytest.raises(WorkflowNodeExecutionError, match="execution failed"):
        await executor.execute(node, WorkflowContext(), _request())
