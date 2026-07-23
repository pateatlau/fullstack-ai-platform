"""Optional post-step quality reflection (Phase 9)."""

from __future__ import annotations

import asyncio
import re
from dataclasses import dataclass
from typing import Any, Literal, cast

from app.ai.agent.executor.result_aggregator import AggregatedToolResults
from app.ai.agent.models.config import AgentConfig
from app.ai.agent.models.context import AgentContext
from app.ai.agent.models.events import ReflectionDecision
from app.ai.agent.models.messages import AgentMessage
from app.ai.agent.models.request import AgentRequest
from app.ai.agent.reflection.quality_checker import evaluate_rule_based, rule_reason
from app.ai.agent.retry import llm_retry_policy_from_config, retry_operation
from app.ai.agent.scratchpad.scratchpad import Scratchpad, ScratchpadMessage
from app.ai.prompts.manager import PromptManager
from app.providers.base import ChatMessageInput, LLMProvider, ProviderCompletion

_REFLECTION_DECISION_PATTERN = re.compile(
    r"\b(REPLAN|RETRY_STEP|CONTINUE|FINISH)\b",
    re.IGNORECASE,
)


@dataclass(frozen=True, slots=True)
class ReflectionResult:
    """Outcome of one reflection pass."""

    decision: ReflectionDecision
    reason: str | None = None
    source: Literal["rules", "llm", "disabled"] = "rules"


class ReflectionEngine:
    """Evaluate step quality and recommend the next control-flow action."""

    def __init__(
        self,
        provider: LLMProvider,
        prompt_manager: PromptManager,
    ) -> None:
        self._provider = provider
        self._prompt_manager = prompt_manager

    async def reflect(
        self,
        *,
        request: AgentRequest,
        context: AgentContext,
        scratchpad: Scratchpad,
        tool_results: AggregatedToolResults | None,
        llm_content: str | None,
    ) -> ReflectionResult:
        """Run reflection for one executed step."""
        config = request.config or AgentConfig()
        if not config.reflection_enabled:
            return ReflectionResult(
                decision=ReflectionDecision.CONTINUE,
                reason="Reflection disabled.",
                source="disabled",
            )

        rule_decision = evaluate_rule_based(
            tool_results=tool_results,
            llm_content=llm_content,
        )
        if rule_decision is not None:
            return ReflectionResult(
                decision=rule_decision,
                reason=rule_reason(
                    rule_decision,
                    tool_results=tool_results,
                    llm_content=llm_content,
                ),
                source="rules",
            )

        return await self._reflect_with_llm(
            request=request,
            context=context,
            scratchpad=scratchpad,
            tool_results=tool_results,
            llm_content=llm_content,
        )

    async def _reflect_with_llm(
        self,
        *,
        request: AgentRequest,
        context: AgentContext,
        scratchpad: Scratchpad,
        tool_results: AggregatedToolResults | None,
        llm_content: str | None,
    ) -> ReflectionResult:
        config = request.config or AgentConfig()
        prompt = self._prompt_manager.render(
            "agent",
            "reflection",
            "1",
            {
                "llm_content": llm_content or "(none)",
                "tool_summary": _format_tool_summary(tool_results),
            },
        )
        if request.system_prompt:
            prompt = f"{request.system_prompt}\n\n{prompt}"

        messages: list[ChatMessageInput] = [{"role": "system", "content": prompt}]
        messages.extend(_scratchpad_messages(scratchpad, request))

        completion = await self._complete_with_retry(
            messages=messages,
            request=request,
            config=config,
        )
        decision, reason = parse_reflection_response(completion.content)
        return ReflectionResult(decision=decision, reason=reason, source="llm")

    async def _complete_with_retry(
        self,
        *,
        messages: list[ChatMessageInput],
        request: AgentRequest,
        config: AgentConfig,
    ) -> ProviderCompletion:
        retry_policy = llm_retry_policy_from_config(config)

        async def operation() -> ProviderCompletion:
            call = self._provider.complete_chat(
                cast(Any, messages),
                request.model,
                request.temperature,
                max_tokens=request.max_tokens,
            )
            if config.timeout_seconds is None:
                return await call
            return await asyncio.wait_for(call, timeout=config.timeout_seconds)

        return await retry_operation(operation, retry_policy)


def parse_reflection_response(
    content: str | None,
) -> tuple[ReflectionDecision, str | None]:
    """Parse an LLM reflection completion into a decision and optional reason."""
    if not content or not content.strip():
        return ReflectionDecision.CONTINUE, "Empty reflection response."

    lines = [line.strip() for line in content.strip().splitlines() if line.strip()]
    first_line = lines[0]
    match = _REFLECTION_DECISION_PATTERN.search(first_line)
    if match is None:
        return ReflectionDecision.CONTINUE, first_line

    decision = ReflectionDecision(match.group(1).upper())
    reason = lines[1] if len(lines) > 1 else first_line
    return decision, reason


def _format_tool_summary(tool_results: AggregatedToolResults | None) -> str:
    if tool_results is None or not tool_results.records:
        return "- (none)"

    lines: list[str] = []
    for record in tool_results.records:
        status = "success" if record.result.success else "failure"
        detail = record.result.error or record.result.data
        lines.append(f"- {record.call.name} ({status}): {detail}")
    return "\n".join(lines)


def _scratchpad_messages(
    scratchpad: Scratchpad,
    request: AgentRequest,
) -> list[ChatMessageInput]:
    if len(scratchpad) > 0:
        return [
            _scratchpad_message_to_input(message)
            for message in scratchpad.to_message_context()
        ]
    return [
        {"role": message.role, "content": message.content}
        for message in request.messages
    ]


def _scratchpad_message_to_input(message: ScratchpadMessage) -> ChatMessageInput:
    if isinstance(message, AgentMessage):
        return {"role": message.role, "content": message.content}
    return message
