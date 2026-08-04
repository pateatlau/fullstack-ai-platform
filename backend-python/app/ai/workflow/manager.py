"""``WorkflowManager`` — single orchestration entry point for the Workflow subsystem.

Public API (stable after Phase 1). Definition CRUD with graph validation landed
in Phase 2; ``start_run()`` and execution scheduling land in Phase 3.
"""

from __future__ import annotations

import asyncio
import datetime
import uuid
from collections.abc import Callable, Mapping
from typing import TYPE_CHECKING

from pydantic import ValidationError
from sqlalchemy.ext.asyncio import AsyncSession

from app.ai.workflow.engine.background import schedule_run_task
from app.ai.workflow.engine.executor import WorkflowExecutor
from app.ai.workflow.exceptions import (
    WorkflowNotFoundError,
    WorkflowValidationError,
)
from app.ai.workflow.graph.validator import GraphValidator
from app.ai.workflow.interfaces.workflow_store import WorkflowStore
from app.ai.workflow.models import (
    DefinitionStatus,
    NodeType,
    RunStatus,
    WorkflowContext,
    WorkflowDefinition,
    WorkflowRun,
)
from app.ai.workflow.models.run import normalize_idempotency_key
from app.core.logging import get_logger

if TYPE_CHECKING:
    from app.ai.workflow.nodes.base import NodeExecutor
    from app.core.config import Settings

#: Builds a fresh ``WorkflowStore`` bound to a session dedicated to background
#: run execution, so scheduled work never depends on a request-scoped session
#: that may already be closed by the time it runs (Part I § Background execution).
BackgroundStoreFactory = Callable[[AsyncSession], WorkflowStore]

_logger = get_logger(__name__)


class WorkflowManager:
    """Coordinates workflow definitions and runs via a ``WorkflowStore``."""

    def __init__(
        self,
        store: WorkflowStore,
        *,
        settings: Settings | None = None,
        node_executors: Mapping[NodeType, "NodeExecutor"] | None = None,
        background_store_factory: BackgroundStoreFactory | None = None,
    ) -> None:
        self._store = store
        self._settings = settings
        self._node_executors: Mapping[NodeType, "NodeExecutor"] = node_executors or {}
        self._background_store_factory = background_store_factory
        #: Most recently scheduled background run task — test-observability seam
        #: only (production callers poll run state via ``get_run()``).
        self._last_scheduled_run_task: asyncio.Task[None] | None = None
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
        now = datetime.datetime.now(datetime.UTC)
        if runs:
            versioned = definition.model_copy(
                update={
                    "id": uuid.uuid4(),
                    "owner_id": existing.owner_id,
                    "version": existing.version + 1,
                    "created_at": now,
                    "updated_at": now,
                }
            )
            return await self._store.create_definition(versioned)

        in_place = definition.model_copy(
            update={
                "id": existing.id,
                "owner_id": existing.owner_id,
                "version": existing.version,
                "created_at": existing.created_at,
                "updated_at": now,
            }
        )
        return await self._store.update_definition(
            in_place,
            expected_version=existing.version,
            require_no_runs=True,
        )

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
                "updated_at": datetime.datetime.now(datetime.UTC),
            }
        )
        return await self._store.update_definition(
            archived, expected_version=existing.version
        )

    async def get_run(
        self, run_id: uuid.UUID, *, owner_id: uuid.UUID
    ) -> WorkflowRun | None:
        """Return an owned workflow run, or ``None`` if it does not exist."""
        return await self._store.get_run(run_id, owner_id=owner_id)

    async def list_runs(
        self,
        *,
        owner_id: uuid.UUID,
        workflow_definition_id: uuid.UUID | None = None,
        status: RunStatus | None = None,
    ) -> list[WorkflowRun]:
        """Return workflow runs owned by ``owner_id``."""
        return await self._store.list_runs(
            owner_id=owner_id,
            workflow_definition_id=workflow_definition_id,
            status=status,
        )

    async def start_run(
        self,
        definition_id: uuid.UUID,
        *,
        owner_id: uuid.UUID,
        idempotency_key: str,
        trigger_input: dict[str, object] | None = None,
        session_id: uuid.UUID | None = None,
    ) -> WorkflowRun:
        """Create (or return the existing) run and schedule it asynchronously.

        Requires ``idempotency_key``; retries with the same
        ``(owner_id, definition_id, idempotency_key)`` return the existing run
        instead of scheduling duplicate work (Part I § Run launch contract).
        Never blocks until terminal completion — callers poll ``get_run()``.
        """
        try:
            normalized_key = normalize_idempotency_key(idempotency_key)
        except ValueError as exc:
            raise WorkflowValidationError(str(exc)) from exc

        definition = await self._store.get_definition(definition_id, owner_id=owner_id)
        if definition is None:
            raise WorkflowNotFoundError(
                f"Workflow definition {definition_id} not found."
            )
        if definition.status is not DefinitionStatus.ACTIVE:
            raise WorkflowValidationError(
                "Workflow definition must be active to start a run."
            )

        now = datetime.datetime.now(datetime.UTC)
        try:
            context = WorkflowContext(trigger_input=trigger_input or {})
        except ValidationError as exc:
            raise WorkflowValidationError(str(exc)) from exc
        run = WorkflowRun(
            id=uuid.uuid4(),
            workflow_definition_id=definition_id,
            owner_id=owner_id,
            idempotency_key=normalized_key,
            session_id=session_id,
            status=RunStatus.RUNNING,
            context=context,
            current_node_ids=[],
            checkpoint_version=0,
            created_at=now,
            updated_at=now,
            started_at=now,
        )
        result, created = await self._store.get_or_create_run(run)
        if created:
            self._schedule_run(result.id, owner_id=owner_id)
        return result

    def _schedule_run(
        self, run_id: uuid.UUID, *, owner_id: uuid.UUID
    ) -> asyncio.Task[None]:
        task = schedule_run_task(self._execute_run(run_id, owner_id=owner_id))
        self._last_scheduled_run_task = task
        return task

    async def _execute_run(self, run_id: uuid.UUID, *, owner_id: uuid.UUID) -> None:
        if self._background_store_factory is None:
            await self._run_with_store(self._store, run_id, owner_id=owner_id)
            return

        from app.db.engine import get_sessionmaker

        sessionmaker = get_sessionmaker()
        async with sessionmaker() as session:
            store = self._background_store_factory(session)
            await self._run_with_store(store, run_id, owner_id=owner_id)

    async def _run_with_store(
        self, store: WorkflowStore, run_id: uuid.UUID, *, owner_id: uuid.UUID
    ) -> None:
        executor = WorkflowExecutor(
            store, self._node_executors, settings=self._settings
        )
        try:
            await executor.execute_run(run_id, owner_id=owner_id)
        except Exception:  # noqa: BLE001 - background execution must never crash the app
            _logger.exception("Workflow run execution failed.", run_id=str(run_id))

    def _validate_before_persist(self, definition: WorkflowDefinition) -> None:
        try:
            self._validator.validate(definition)
        except WorkflowValidationError:
            raise
        except ValueError as exc:
            raise WorkflowValidationError(str(exc)) from exc
