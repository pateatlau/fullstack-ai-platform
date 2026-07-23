"""Agent execution state lifecycle manager (Phase 2)."""

from __future__ import annotations

from app.ai.agent.exceptions import AgentError, AgentIterationLimitError
from app.ai.agent.models.config import AgentConfig
from app.ai.agent.models.context import AgentContext
from app.ai.agent.models.state import (
    TERMINAL_STATUSES,
    VALID_TRANSITIONS,
    AgentExecutionState,
    AgentExecutionStatus,
)


class InvalidStateTransitionError(AgentError):
    """Raised when a lifecycle transition is not allowed from the current status."""

    def __init__(
        self,
        current: AgentExecutionStatus,
        target: AgentExecutionStatus,
    ) -> None:
        self.current = current
        self.target = target
        super().__init__(
            f"Invalid agent state transition: {current.value} -> {target.value}"
        )


class AgentStateManager:
    """Creates and updates :class:`AgentExecutionState` with validated transitions."""

    @staticmethod
    def create_initial_state(
        context: AgentContext,
        config: AgentConfig | None = None,
    ) -> AgentExecutionState:
        """Build execution-scoped state for a new agent run."""
        resolved = config or AgentConfig()
        return AgentExecutionState(
            execution_id=context.execution_id,
            max_iterations=resolved.max_iterations,
            metadata=dict(context.metadata),
        )

    @staticmethod
    def transition(
        state: AgentExecutionState,
        target: AgentExecutionStatus,
        *,
        error_message: str | None = None,
    ) -> AgentExecutionState:
        """Apply a validated lifecycle transition and return an updated state copy."""
        if state.status in TERMINAL_STATUSES:
            raise InvalidStateTransitionError(state.status, target)

        allowed = VALID_TRANSITIONS.get(state.status, frozenset())
        if target not in allowed:
            raise InvalidStateTransitionError(state.status, target)

        updates: dict[str, object] = {"status": target}
        if target == AgentExecutionStatus.FAILED and error_message is not None:
            updates["error_message"] = error_message
        return state.model_copy(update=updates)

    @staticmethod
    def ensure_iterations_remaining(state: AgentExecutionState) -> None:
        """Raise when the execution cannot start another iteration."""
        if state.is_at_iteration_limit():
            raise AgentIterationLimitError(state.max_iterations)

    @staticmethod
    def begin_iteration(state: AgentExecutionState) -> AgentExecutionState:
        """Increment the iteration counter before a planning/execution cycle."""
        AgentStateManager.ensure_iterations_remaining(state)
        next_iteration = state.current_iteration + 1
        return state.model_copy(
            update={
                "current_iteration": next_iteration,
                "iteration_limit_reached": next_iteration >= state.max_iterations,
            }
        )

    @staticmethod
    def record_tool_used(
        state: AgentExecutionState,
        tool_name: str,
    ) -> AgentExecutionState:
        """Append a tool name once to the tools-used list."""
        if tool_name in state.tools_used:
            return state
        return state.model_copy(update={"tools_used": [*state.tools_used, tool_name]})

    @staticmethod
    def record_reflection(state: AgentExecutionState) -> AgentExecutionState:
        """Increment the reflection counter."""
        return state.model_copy(update={"reflection_count": state.reflection_count + 1})

    @staticmethod
    def record_retry(state: AgentExecutionState) -> AgentExecutionState:
        """Increment the retry counter."""
        return state.model_copy(update={"retry_count": state.retry_count + 1})

    @staticmethod
    def record_parallel_tools(
        state: AgentExecutionState,
        count: int,
    ) -> AgentExecutionState:
        """Add to the parallel tool execution counter."""
        if count <= 0:
            return state
        return state.model_copy(
            update={"parallel_tools_count": state.parallel_tools_count + count}
        )

    @staticmethod
    def mark_iteration_limit_reached(state: AgentExecutionState) -> AgentExecutionState:
        """Flag that the iteration budget has been exhausted."""
        return state.model_copy(update={"iteration_limit_reached": True})
