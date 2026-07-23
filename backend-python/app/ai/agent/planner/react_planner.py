"""ReAct-style iterative planner (Phase 6)."""

from __future__ import annotations

import asyncio
from typing import Any

from app.ai.agent.models.config import AgentConfig
from app.ai.agent.models.context import AgentContext
from app.ai.agent.models.messages import AgentMessage
from app.ai.agent.models.plan import ExecutionPlan
from app.ai.agent.models.request import AgentRequest
from app.ai.agent.planner.parser import (
    build_iteration_limit_plan,
    build_no_tools_finalize_plan,
    parse_tool_completion,
)
from app.ai.agent.retry import llm_retry_policy_from_config, retry_operation
from app.ai.agent.scratchpad.scratchpad import ScratchpadMessage
from app.ai.agent.scratchpad.store import ScratchpadStore, get_scratchpad_store
from app.ai.prompts.manager import PromptManager
from app.ai.tools.registry import ToolRegistry
from app.providers.base import ChatMessageInput, LLMProvider, ProviderToolCompletion


class ReActPlanner:
    """Iterative planner that delegates next-action selection to an LLM."""

    def __init__(
        self,
        provider: LLMProvider,
        tool_registry: ToolRegistry,
        prompt_manager: PromptManager,
        *,
        scratchpad_store: ScratchpadStore | None = None,
    ) -> None:
        self._provider = provider
        self._tool_registry = tool_registry
        self._prompt_manager = prompt_manager
        self._scratchpad_store = scratchpad_store or get_scratchpad_store()

    async def plan_next(
        self,
        request: AgentRequest,
        context: AgentContext,
        *,
        iteration: int,
    ) -> ExecutionPlan:
        """Produce the next :class:`ExecutionPlan` for one ReAct iteration."""
        config = request.config or AgentConfig()

        if iteration >= config.max_iterations:
            return build_iteration_limit_plan(iteration=iteration)

        tools = self._resolve_tool_schemas(context, request)
        if not tools:
            return build_no_tools_finalize_plan(iteration=iteration)

        messages = self._build_messages(
            request=request,
            context=context,
            tools=tools,
            iteration=iteration,
            config=config,
        )
        completion = await self._complete_with_tools(
            messages=messages,
            request=request,
            tools=tools,
            config=config,
        )
        self._record_reasoning(context.execution_id, completion.content)
        return parse_tool_completion(completion, iteration=iteration)

    def _resolve_tool_schemas(
        self,
        context: AgentContext,
        request: AgentRequest,
    ) -> list[dict[str, Any]]:
        tools = self._tool_registry.get_schemas_for_llm()
        allowed = context.allowed_tool_names
        if allowed is not None:
            tools = [
                schema
                for schema in tools
                if schema.get("function", {}).get("name") in allowed
            ]
        if request.tool_names is not None:
            requested = set(request.tool_names)
            tools = [
                schema
                for schema in tools
                if schema.get("function", {}).get("name") in requested
            ]
        return tools

    def _build_messages(
        self,
        *,
        request: AgentRequest,
        context: AgentContext,
        tools: list[dict[str, Any]],
        iteration: int,
        config: AgentConfig,
    ) -> list[ChatMessageInput]:
        tool_summaries = [
            {
                "name": schema.get("function", {}).get("name", ""),
                "description": schema.get("function", {}).get("description", ""),
            }
            for schema in tools
        ]
        tool_list = (
            "\n".join(
                f"- {tool['name']}: {tool['description']}" for tool in tool_summaries
            )
            or "- (none)"
        )
        planner_prompt = self._prompt_manager.render(
            "agent",
            "planner",
            "1",
            {
                "tool_list": tool_list,
                "iteration": iteration + 1,
                "max_iterations": config.max_iterations,
            },
        )
        if request.system_prompt:
            planner_prompt = f"{request.system_prompt}\n\n{planner_prompt}"

        messages: list[ChatMessageInput] = [
            {"role": "system", "content": planner_prompt},
        ]
        messages.extend(self._conversation_messages(context, request))
        return messages

    def _conversation_messages(
        self,
        context: AgentContext,
        request: AgentRequest,
    ) -> list[ChatMessageInput]:
        scratchpad = self._scratchpad_store.get(context.execution_id)
        if scratchpad is not None and len(scratchpad) > 0:
            return [
                _scratchpad_message_to_input(message)
                for message in scratchpad.to_message_context()
            ]

        return [_agent_message_to_input(message) for message in request.messages]

    async def _complete_with_tools(
        self,
        *,
        messages: list[ChatMessageInput],
        request: AgentRequest,
        tools: list[dict[str, Any]],
        config: AgentConfig,
    ) -> ProviderToolCompletion:
        retry_policy = llm_retry_policy_from_config(config)

        async def operation() -> ProviderToolCompletion:
            call = self._provider.complete_chat_with_tools(
                messages,
                request.model,
                tools,
                request.temperature,
                max_tokens=request.max_tokens,
            )
            if config.timeout_seconds is None:
                return await call
            return await asyncio.wait_for(call, timeout=config.timeout_seconds)

        return await retry_operation(operation, retry_policy)

    def _record_reasoning(self, execution_id: str, content: str | None) -> None:
        if not content:
            return
        scratchpad = self._scratchpad_store.get(execution_id)
        if scratchpad is not None:
            scratchpad.append_thought(content)


def _agent_message_to_input(message: AgentMessage) -> dict[str, str]:
    return {"role": message.role, "content": message.content}


def _scratchpad_message_to_input(message: ScratchpadMessage) -> ChatMessageInput:
    if isinstance(message, AgentMessage):
        return _agent_message_to_input(message)
    return message
