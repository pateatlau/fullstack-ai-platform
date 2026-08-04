"""Node-level retry policy (Phase 8)."""

from __future__ import annotations

from app.ai.workflow.models import NodeRetryPolicy


class RetryPolicy:
    """Wraps ``app/core/retry.py`` for workflow node retries (Phase 8)."""

    def __init__(self, policy: NodeRetryPolicy | None = None) -> None:
        self._policy = policy

    def max_retries(self) -> int:
        """Return configured max retries for a node."""
        if self._policy is None:
            return 0
        return self._policy.max_retries
