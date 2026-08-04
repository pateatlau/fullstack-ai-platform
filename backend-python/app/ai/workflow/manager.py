"""``WorkflowManager`` — single orchestration entry point for the Workflow subsystem.

Public API (stable after Phase 1). Definition CRUD with graph validation lands
in Phase 2; ``start_run()`` and execution scheduling land in Phase 3.
"""

from __future__ import annotations

import datetime
import uuid
from typing import TYPE_CHECKING

from app.ai.workflow.exceptions import (
    WorkflowNotFoundError,
    WorkflowValidationError,
)
from app.ai.workflow.graph.validator import GraphValidator
from app.ai.workflow.interfaces.workflow_store import WorkflowStore
from app.ai.workflow.models import DefinitionStatus, WorkflowDefinition, WorkflowRun

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
        self._validator = GraphValidator(
            max_nodes_per_definition=(
                settings.workflow_max_nodes_per_definition
                if settings is not None
                else 50
            ),
            max_parallel_branches=(
                settings.workflow_max_parallel_branches if settings is not None else 8
            ),
        )

    async def get_definition(
        self, definition_id: uuid.UUID, *, owner_id: uuid.UUID
    ) -> WorkflowDefinition | None:
        """Return an owned workflow definition, or ``None`` if it does not exist."""
        return await self._store.get_definition(definition_id, owner_id=owner_id)

    async def list_definitions(
        self,
        *,
        owner_id: uuid.UUID,
        status: DefinitionStatus | None = None,
    ) -> list[WorkflowDefinition]:
        """Return workflow definitions owned by ``owner_id``."""
        return await self._store.list_definitions(owner_id=owner_id, status=status)

    async def create_definition(
        self, definition: WorkflowDefinition
    ) -> WorkflowDefinition:
        """Persist a new workflow definition after graph validation."""
        self._validate_before_persist(definition)
        return await self._store.create_definition(definition)

    async def update_definition(
        self, definition: WorkflowDefinition, *, owner_id: uuid.UUID
    ) -> WorkflowDefinition:
        """Update a definition in place, or create a new version if runs exist."""
        existing = await self._store.get_definition(definition.id, owner_id=owner_id)
        if existing is None:
            raise WorkflowNotFoundError(
                f"Workflow definition {definition.id} not found."
            )

        if definition.owner_id != owner_id:
            raise WorkflowNotFoundError(
                f"Workflow definition {definition.id} not found."
            )

        self._validate_before_persist(definition)

        runs = await self._store.list_runs(
            owner_id=owner_id,
            workflow_definition_id=definition.id,
        )
        if runs:
            now = datetime.datetime.now(datetime.timezone.utc)
            versioned = definition.model_copy(
                update={
                    "id": uuid.uuid4(),
                    "version": existing.version + 1,
                    "created_at": now,
                    "updated_at": now,
                }
            )
            return await self._store.create_definition(versioned)

        return await self._store.update_definition(definition)

    async def archive_definition(
        self, definition_id: uuid.UUID, *, owner_id: uuid.UUID
    ) -> WorkflowDefinition:
        """Mark a workflow definition as archived."""
        existing = await self._store.get_definition(definition_id, owner_id=owner_id)
        if existing is None:
            raise WorkflowNotFoundError(
                f"Workflow definition {definition_id} not found."
            )

        archived = existing.model_copy(
            update={
                "status": DefinitionStatus.ARCHIVED,
                "updated_at": datetime.datetime.now(datetime.timezone.utc),
            }
        )
        return await self._store.update_definition(archived)

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

    def _validate_before_persist(self, definition: WorkflowDefinition) -> None:
        try:
            self._validator.validate(definition)
        except WorkflowValidationError:
            raise
        except ValueError as exc:
            raise WorkflowValidationError(str(exc)) from exc
