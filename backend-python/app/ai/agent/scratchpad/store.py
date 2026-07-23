"""In-memory registry of per-execution scratchpads (Phase 3)."""

from __future__ import annotations

from app.ai.agent.exceptions import AgentError
from app.ai.agent.scratchpad.scratchpad import Scratchpad


class ScratchpadAlreadyExistsError(AgentError):
    """Raised when a scratchpad is created twice for the same execution."""

    def __init__(self, execution_id: str) -> None:
        self.execution_id = execution_id
        super().__init__(f"Scratchpad already exists for execution '{execution_id}'.")


class ScratchpadNotFoundError(AgentError):
    """Raised when no scratchpad exists for the requested execution."""

    def __init__(self, execution_id: str) -> None:
        self.execution_id = execution_id
        super().__init__(f"No scratchpad found for execution '{execution_id}'.")


class ScratchpadStore:
    """Execution-scoped scratchpad registry — in-memory only, never persisted."""

    def __init__(self) -> None:
        self._scratchpads: dict[str, Scratchpad] = {}

    def create(self, execution_id: str) -> Scratchpad:
        """Create an isolated scratchpad for one execution."""
        if execution_id in self._scratchpads:
            raise ScratchpadAlreadyExistsError(execution_id)
        scratchpad = Scratchpad(execution_id)
        self._scratchpads[execution_id] = scratchpad
        return scratchpad

    def get(self, execution_id: str) -> Scratchpad | None:
        """Return the scratchpad for an execution, if present."""
        return self._scratchpads.get(execution_id)

    def require(self, execution_id: str) -> Scratchpad:
        """Return the scratchpad for an execution or raise."""
        scratchpad = self.get(execution_id)
        if scratchpad is None:
            raise ScratchpadNotFoundError(execution_id)
        return scratchpad

    def remove(self, execution_id: str) -> None:
        """Remove and clear a scratchpad after execution completes."""
        scratchpad = self._scratchpads.pop(execution_id, None)
        if scratchpad is not None:
            scratchpad.clear()

    def clear(self) -> None:
        """Remove all scratchpads (primarily for tests)."""
        for scratchpad in self._scratchpads.values():
            scratchpad.clear()
        self._scratchpads.clear()


_default_store = ScratchpadStore()


def get_scratchpad_store() -> ScratchpadStore:
    """Return the process-wide scratchpad registry."""
    return _default_store
