"""Agent node executor: bounded sub-tasks via the Epic 01 ``DefaultAgent``.

Part I § Agent Node — delegates to the Agent Framework's own ReAct loop and
retry/iteration limits; the workflow engine does not wrap or duplicate them.
"""

from __future__ import annotations

from typing import Protocol, cast

from app.ai.agent.models.config import AgentConfig
from app.ai.agent.models.context import AgentContext
from app.ai.agent.models.messages import AgentMessage
from app.ai.agent.models.request import AgentRequest
from app.ai.agent.models.response import AgentResponse
from app.ai.agent.runtime.default_agent import DefaultAgent
from app.ai.workflow.models import WorkflowContext, WorkflowNode
from app.ai.workflow.nodes.base import NodeExecutionRequest, WorkflowNodeExecutionError
from app.ai.workflow.nodes.template_render import render_inline_template
from app.core.caller import CallerContext
from app.core.config import Settings
from app.schemas.chat import ProviderName


class _AgentRunner(Protocol):
    async def run(
        self, request: AgentRequest, context: AgentContext
    ) -> AgentResponse: ...


class AgentNodeExecutor:
    """Executes a bounded agent sub-task for an ``agent`` node."""

    def __init__(
        self,
        agent: DefaultAgent | _AgentRunner,
        *,
        settings: Settings,
    ) -> None:
        self._agent = agent
        self._settings = settings

    async def execute(
        self,
        node: WorkflowNode,
        context: WorkflowContext,
        request: NodeExecutionRequest,
    ) -> dict[str, object]:
        if not self._settings.agent_runtime_enabled:
            raise WorkflowNodeExecutionError(
                f"Agent node {node.id!r} requires AGENT_RUNTIME_ENABLED=true.",
                error_code="invalid_config",
            )

        goal_raw = node.config.get("goal")
        if not isinstance(goal_raw, str) or not goal_raw.strip():
            raise WorkflowNodeExecutionError(
                f"Agent node {node.id!r} requires config.goal.",
                error_code="invalid_config",
            )

        goal = render_inline_template(
            goal_raw,
            context,
            node_id=node.id,
            field_name="goal",
        )

        instructions: str | None = None
        instructions_raw = node.config.get("instructions")
        if isinstance(instructions_raw, str) and instructions_raw.strip():
            instructions = render_inline_template(
                instructions_raw,
                context,
                node_id=node.id,
                field_name="instructions",
            )

        tool_names = _optional_string_list(node.config.get("tool_names"))
        agent_config = _build_agent_config(node.config)

        agent_request = AgentRequest(
            messages=[AgentMessage(role="user", content=goal)],
            model=_resolve_model(node.config, self._settings),
            tool_names=tool_names,
            system_prompt=instructions,
            config=agent_config,
        )
        agent_context = AgentContext(
            caller=CallerContext.for_user(request.owner_id),
            metadata={"execution_receipt_id": request.execution_receipt_id},
        )

        try:
            response = await self._agent.run(agent_request, agent_context)
        except WorkflowNodeExecutionError:
            raise
        except Exception as exc:
            raise WorkflowNodeExecutionError(
                f"Agent node {node.id!r} execution failed.",
                error_code="agent_error",
            ) from exc

        return {
            "content": response.content,
            "tools_used": list(response.tools_used),
            "iterations": response.iterations,
            "finish_reason": response.finish_reason,
            "execution_receipt_id": request.execution_receipt_id,
        }


def _build_agent_config(config: dict[str, object]) -> AgentConfig | None:
    max_iterations = config.get("max_iterations")
    if max_iterations is None:
        return None
    if not isinstance(max_iterations, int) or max_iterations < 1:
        raise WorkflowNodeExecutionError(
            "Agent node config.max_iterations must be an integer >= 1.",
            error_code="invalid_config",
        )
    return AgentConfig(max_iterations=max_iterations)


def _optional_string_list(value: object) -> list[str] | None:
    if value is None:
        return None
    if not isinstance(value, list):
        raise WorkflowNodeExecutionError(
            "Agent node config.tool_names must be a list of strings.",
            error_code="invalid_config",
        )
    names = [item for item in value if isinstance(item, str) and item.strip()]
    if not names:
        raise WorkflowNodeExecutionError(
            "Agent node config.tool_names must be a non-empty list.",
            error_code="invalid_config",
        )
    return names


def _resolve_model(config: dict[str, object], settings: Settings) -> str:
    model_override = config.get("model_override")
    if isinstance(model_override, str) and model_override.strip():
        return model_override.strip()

    provider_name_raw = settings.llm_provider
    allowed: tuple[ProviderName, ...] = ("openai", "gemini", "groq", "anthropic")
    if provider_name_raw not in allowed:
        raise WorkflowNodeExecutionError(
            f"Unsupported LLM_PROVIDER {provider_name_raw!r} for agent node execution.",
            error_code="invalid_config",
        )
    provider_name = cast(ProviderName, provider_name_raw)
    defaults: dict[ProviderName, str] = {
        "openai": settings.openai_model,
        "gemini": settings.gemini_model,
        "groq": settings.groq_model,
        "anthropic": settings.anthropic_model,
    }
    return defaults[provider_name]
