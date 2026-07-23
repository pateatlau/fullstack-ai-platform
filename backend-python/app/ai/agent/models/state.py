"""Execution-scoped agent state models (internal — Phase 2)."""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, Field

_SECRET_KEY_FRAGMENTS = ("secret", "password", "token", "api_key", "authorization")


class AgentExecutionStatus(StrEnum):
    """Lifecycle status for a single agent execution."""

    CREATED = "created"
    PLANNING = "planning"
    EXECUTING = "executing"
    REFLECTING = "reflecting"
    COMPLETED = "completed"
    FAILED = "failed"


TERMINAL_STATUSES: frozenset[AgentExecutionStatus] = frozenset(
    {
        AgentExecutionStatus.COMPLETED,
        AgentExecutionStatus.FAILED,
    }
)

VALID_TRANSITIONS: dict[AgentExecutionStatus, frozenset[AgentExecutionStatus]] = {
    AgentExecutionStatus.CREATED: frozenset(
        {AgentExecutionStatus.PLANNING, AgentExecutionStatus.FAILED}
    ),
    AgentExecutionStatus.PLANNING: frozenset(
        {
            AgentExecutionStatus.EXECUTING,
            AgentExecutionStatus.COMPLETED,
            AgentExecutionStatus.FAILED,
        }
    ),
    AgentExecutionStatus.EXECUTING: frozenset(
        {
            AgentExecutionStatus.PLANNING,
            AgentExecutionStatus.REFLECTING,
            AgentExecutionStatus.COMPLETED,
            AgentExecutionStatus.FAILED,
        }
    ),
    AgentExecutionStatus.REFLECTING: frozenset(
        {
            AgentExecutionStatus.PLANNING,
            AgentExecutionStatus.EXECUTING,
            AgentExecutionStatus.COMPLETED,
            AgentExecutionStatus.FAILED,
        }
    ),
    AgentExecutionStatus.COMPLETED: frozenset(),
    AgentExecutionStatus.FAILED: frozenset(),
}


def _is_secret_key(key: str) -> bool:
    lowered = key.lower()
    return any(fragment in lowered for fragment in _SECRET_KEY_FRAGMENTS)


def _redact_secrets(metadata: dict[str, object]) -> dict[str, object]:
    return {key: value for key, value in metadata.items() if not _is_secret_key(key)}


class AgentExecutionState(BaseModel):
    """Mutable snapshot of one agent execution's progress and counters."""

    execution_id: str
    status: AgentExecutionStatus = AgentExecutionStatus.CREATED
    current_iteration: int = Field(default=0, ge=0)
    max_iterations: int = Field(default=5, ge=1)
    tools_used: list[str] = Field(default_factory=list)
    reflection_count: int = Field(default=0, ge=0)
    retry_count: int = Field(default=0, ge=0)
    parallel_tools_count: int = Field(default=0, ge=0)
    iteration_limit_reached: bool = False
    error_message: str | None = None
    metadata: dict[str, object] = Field(default_factory=dict)

    def to_dict(self) -> dict[str, object]:
        """Serialize for logging/observability without secret-bearing metadata keys."""
        return {
            "execution_id": self.execution_id,
            "status": self.status.value,
            "current_iteration": self.current_iteration,
            "max_iterations": self.max_iterations,
            "tools_used": list(self.tools_used),
            "reflection_count": self.reflection_count,
            "retry_count": self.retry_count,
            "parallel_tools_count": self.parallel_tools_count,
            "iteration_limit_reached": self.iteration_limit_reached,
            "error_message": self.error_message,
            "metadata": _redact_secrets(self.metadata),
        }

    def has_remaining_iterations(self) -> bool:
        """Return whether another planning/execution cycle may start."""
        return self.current_iteration < self.max_iterations

    def is_at_iteration_limit(self) -> bool:
        """Return whether the execution has exhausted its iteration budget."""
        return (
            self.iteration_limit_reached
            or self.current_iteration >= self.max_iterations
        )
