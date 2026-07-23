"""Concrete retry policies for agent LLM and tool operations (Phase 4)."""

from __future__ import annotations

from dataclasses import dataclass

from app.ai.agent.models.config import AgentConfig
from app.ai.agent.retry.classifier import is_retryable_agent_error


@dataclass(frozen=True, slots=True)
class LLMRetryPolicy:
    """Retry policy for LLM provider calls."""

    max_retries: int = 3
    base_delay_seconds: float = 1.0

    def is_retryable(self, exc: BaseException) -> bool:
        return is_retryable_agent_error(exc)


@dataclass(frozen=True, slots=True)
class ToolRetryPolicy:
    """Retry policy for tool execution against transient upstream failures."""

    max_retries: int = 3
    base_delay_seconds: float = 1.0

    def is_retryable(self, exc: BaseException) -> bool:
        return is_retryable_agent_error(exc)


def llm_retry_policy_from_config(config: AgentConfig | None = None) -> LLMRetryPolicy:
    """Build an LLM retry policy from agent configuration defaults."""
    resolved = config or AgentConfig()
    return LLMRetryPolicy(
        max_retries=resolved.max_retries,
        base_delay_seconds=resolved.retry_base_delay_seconds,
    )


def tool_retry_policy_from_config(config: AgentConfig | None = None) -> ToolRetryPolicy:
    """Build a tool retry policy from agent configuration defaults."""
    resolved = config or AgentConfig()
    return ToolRetryPolicy(
        max_retries=resolved.max_retries,
        base_delay_seconds=resolved.retry_base_delay_seconds,
    )
