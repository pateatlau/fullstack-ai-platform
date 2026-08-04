"""Node-level retry policy (Phase 8)."""

from __future__ import annotations

from typing import TYPE_CHECKING

from app.ai.workflow.models import NodeRetryPolicy

if TYPE_CHECKING:
    from app.core.config import Settings


class RetryPolicy:
    """Wraps ``app/core/retry.py`` for workflow node retries (Phase 8)."""

    def __init__(
        self,
        policy: NodeRetryPolicy | None = None,
        *,
        settings: Settings | None = None,
    ) -> None:
        if policy is not None:
            self._effective = policy
        elif settings is not None:
            self._effective = NodeRetryPolicy(
                max_retries=settings.workflow_max_node_retries,
                base_delay_seconds=settings.workflow_node_retry_base_delay_seconds,
            )
        else:
            self._effective = NodeRetryPolicy()

    def max_retries(self) -> int:
        """Return configured max retries for a node."""
        return self._effective.max_retries

    def base_delay_seconds(self) -> float:
        """Return configured base delay between retries for a node."""
        return self._effective.base_delay_seconds
