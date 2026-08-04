"""``WorkflowManager`` — single orchestration entry point for the Workflow subsystem.

Public API (stable after Phase 1). Definition CRUD with graph validation lands
in Phase 2; ``start_run()`` and execution scheduling land in Phase 3.
"""

from __future__ import annotations

import uuid
from typing import TYPE_CHECKING

from app.ai.workflow.interfaces.workflow_store import WorkflowStore
from app.ai.workflow.models import WorkflowDefinition, WorkflowRun

if TYPE_CHECKING:
    from app.core.config import Settings


class WorkflowManager:
    """Coordinates workflow definitions and runs via a ``WorkflowStore``."""

    def __init__(
        self,
        store: WorkflowStore,
        *,
        settings: Settings | None = None,
    ) -> None:
        self._store = store
        self._settings = settings

    async def get_definition(
        self, definition_id: uuid.UUID, *, owner_id: uuid.UUID
    ) -> WorkflowDefinition | None:
        """Return an owned workflow definition, or ``None`` if it does not exist."""
        return await self._store.get_definition(definition_id, owner_id=owner_id)

    async def create_definition(
        self, definition: WorkflowDefinition
    ) -> WorkflowDefinition:
        """Persist a new workflow definition (graph validation in Phase 2)."""
        raise NotImplementedError(
            "WorkflowManager.create_definition() is implemented in Phase 2."
        )

    async def get_run(
        self, run_id: uuid.UUID, *, owner_id: uuid.UUID
    ) -> WorkflowRun | None:
        """Return an owned workflow run, or ``None`` if it does not exist."""
        return await self._store.get_run(run_id, owner_id=owner_id)

    async def start_run(
        self,
        definition_id: uuid.UUID,
        *,
        owner_id: uuid.UUID,
        idempotency_key: str,
        trigger_input: dict[str, object] | None = None,
        session_id: uuid.UUID | None = None,
    ) -> WorkflowRun:
        """Launch a workflow run asynchronously (implemented in Phase 3)."""
        del definition_id, owner_id, idempotency_key, trigger_input, session_id
        raise NotImplementedError(
            "WorkflowManager.start_run() is implemented in Phase 3."
        )
