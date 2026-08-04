"""Workflow execution engine (Phase 3+)."""

from __future__ import annotations

import uuid
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from app.ai.workflow.interfaces.workflow_store import WorkflowStore
    from app.ai.workflow.models import WorkflowRun


class WorkflowExecutor:
    """Advances a run through its graph with checkpointing (Part I § WorkflowExecutor)."""

    def __init__(self, store: WorkflowStore) -> None:
        self._store = store

    async def execute_run(
        self, run_id: uuid.UUID, *, owner_id: uuid.UUID
    ) -> WorkflowRun:
        """Run the step loop until no ready nodes remain or the run pauses."""
        del run_id, owner_id
        raise NotImplementedError(
            "WorkflowExecutor.execute_run() is implemented in Phase 3."
        )
