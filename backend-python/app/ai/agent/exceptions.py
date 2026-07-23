"""Agent runtime exceptions (public API — stable after Phase 1)."""


class AgentError(Exception):
    """Base error for agent runtime failures."""


class AgentIterationLimitError(AgentError):
    """Raised when the agent exceeds ``max_iterations``."""

    def __init__(self, max_iterations: int) -> None:
        self.max_iterations = max_iterations
        super().__init__(f"Agent iteration limit ({max_iterations}) reached.")


class AgentTimeoutError(AgentError):
    """Raised when agent execution exceeds the configured timeout."""

    def __init__(self, timeout_seconds: int) -> None:
        self.timeout_seconds = timeout_seconds
        super().__init__(f"Agent execution timed out after {timeout_seconds} seconds.")
