"""``WorkflowManager`` — single orchestration entry point for the Workflow subsystem.

Public API (stable after Phase 1). Definition CRUD with graph validation landed
in Phase 2; ``start_run()`` and execution scheduling land in Phase 3.
"""

from __future__ import annotations

import asyncio
import datetime
import time
import uuid
from collections.abc import Callable, Mapping
from typing import TYPE_CHECKING

from pydantic import ValidationError
from sqlalchemy.ext.asyncio import AsyncSession

from app.ai.observability.metrics.instruments import (
    record_workflow_approval_pending_delta,
    record_workflow_run_started,
)
from app.ai.observability.tracing.spans import (
    capture_current_span_context,
    elapsed_ms_since,
    mark_span_error_status,
    record_workflow_run_outcome,
    SpanContextSnapshot,
    workflow_run_root_span,
)
from app.ai.workflow.engine.background import reconcile_orphaned_runs, schedule_run_task
from app.ai.workflow.engine.executor import WorkflowExecutor
from app.ai.workflow.exceptions import (
    WorkflowApprovalCasMissError,
    WorkflowConcurrentUpdateError,
    WorkflowDecisionConflictError,
    WorkflowNotFoundError,
    WorkflowValidationError,
)
from app.ai.workflow.graph.traversal import outgoing_edges
from app.ai.workflow.graph.validator import GraphValidator
from app.ai.workflow.interfaces.workflow_store import WorkflowStore
from app.ai.workflow.models import (
    ApprovalDecision,
    DefinitionStatus,
    NodeStatus,
    NodeType,
    RunStatus,
    WorkflowContext,
    WorkflowDefinition,
    WorkflowNodeExecution,
    WorkflowRun,
)
from app.ai.workflow.models.run import normalize_idempotency_key
from app.ai.workflow.nodes.approval_node import (
    build_approval_decision_output,
    resolve_approval_selected_edge_ids,
)
from app.core.logging import get_logger

if TYPE_CHECKING:
    from app.ai.plugins.registry import PluginRegistry
    from app.ai.plugins.workflow.registry import WorkflowPluginRegistry
    from app.ai.tools.registry import ToolRegistry
    from app.ai.workflow.nodes.base import NodeExecutor
    from app.core.config import Settings

#: Builds a fresh ``WorkflowStore`` bound to a session dedicated to background
#: run execution, so scheduled work never depends on a request-scoped session
#: that may already be closed by the time it runs (Part I § Background execution).
BackgroundStoreFactory = Callable[[AsyncSession], WorkflowStore]

_logger = get_logger(__name__)
_MAX_APPROVAL_DECISION_RETRIES = 25


def _run_status_after_approval_decision(
    *,
    reject_ends_run: bool,
    remaining_current_node_ids: list[str],
    definition: WorkflowDefinition,
) -> RunStatus:
    """Keep ``waiting_approval`` while other approval nodes remain in progress."""
    if reject_ends_run:
        return RunStatus.FAILED
    approval_node_ids = {
        node.id for node in definition.nodes if node.type is NodeType.APPROVAL
    }
    if any(node_id in approval_node_ids for node_id in remaining_current_node_ids):
        return RunStatus.WAITING_APPROVAL
    return RunStatus.RUNNING


class WorkflowManager:
    """Coordinates workflow definitions and runs via a ``WorkflowStore``."""

    def __init__(
        self,
        store: WorkflowStore,
        *,
        settings: Settings | None = None,
        node_executors: Mapping[NodeType, "NodeExecutor"] | None = None,
        background_store_factory: BackgroundStoreFactory | None = None,
        tool_registry: "ToolRegistry | None" = None,
        plugin_registry: "PluginRegistry | None" = None,
        workflow_plugin_registry: "WorkflowPluginRegistry | None" = None,
    ) -> None:
        self._store = store
        self._settings = settings
        self._node_executors: Mapping[NodeType, "NodeExecutor"] = node_executors or {}
        self._background_store_factory = background_store_factory
        self._tool_registry = tool_registry
        #: Most recently scheduled background run task — test-observability seam
        #: only (production callers poll run state via ``get_run()``).
        self._last_scheduled_run_task: asyncio.Task[None] | None = None
        self._deferred_run_schedules: list[tuple[uuid.UUID, uuid.UUID]] = []
        plugins_enabled = settings.plugins_enabled if settings is not None else False
        self._validator = GraphValidator(
            max_nodes_per_definition=(
                settings.workflow_max_nodes_per_definition
                if settings is not None
                else 50
            ),
            max_parallel_branches=(
                settings.workflow_max_parallel_branches if settings is not None else 8
            ),
            plugins_enabled=plugins_enabled,
            plugin_registry=plugin_registry if plugins_enabled else None,
            workflow_plugin_registry=(
                workflow_plugin_registry if plugins_enabled else None
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
        limit: int | None = None,
        offset: int = 0,
    ) -> list[WorkflowDefinition]:
        """Return workflow definitions owned by ``owner_id``."""
        return await self._store.list_definitions(
            owner_id=owner_id,
            status=status,
            limit=limit,
            offset=offset,
        )

    async def count_definitions(
        self,
        *,
        owner_id: uuid.UUID,
        status: DefinitionStatus | None = None,
    ) -> int:
        """Return the number of workflow definitions owned by ``owner_id``."""
        return await self._store.count_definitions(owner_id=owner_id, status=status)

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

    async def get_run_with_executions(
        self, run_id: uuid.UUID, *, owner_id: uuid.UUID
    ) -> tuple[WorkflowRun, list[WorkflowNodeExecution]] | None:
        """Return an owned run plus its node execution history."""
        return await self._store.get_run_with_executions(run_id, owner_id=owner_id)

    async def list_runs(
        self,
        *,
        owner_id: uuid.UUID,
        workflow_definition_id: uuid.UUID | None = None,
        status: RunStatus | None = None,
        limit: int | None = None,
        offset: int = 0,
    ) -> list[WorkflowRun]:
        """Return workflow runs owned by ``owner_id``."""
        return await self._store.list_runs(
            owner_id=owner_id,
            workflow_definition_id=workflow_definition_id,
            status=status,
            limit=limit,
            offset=offset,
        )

    async def count_runs(
        self,
        *,
        owner_id: uuid.UUID,
        workflow_definition_id: uuid.UUID | None = None,
        status: RunStatus | None = None,
    ) -> int:
        """Return the number of workflow runs owned by ``owner_id``."""
        return await self._store.count_runs(
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
        defer_schedule: bool = False,
    ) -> WorkflowRun:
        """Create (or return the existing) run and schedule it asynchronously.

        Requires ``idempotency_key``; retries with the same
        ``(owner_id, definition_id, idempotency_key)`` return the existing run
        instead of scheduling duplicate work (Part I § Run launch contract).
        Never blocks until terminal completion — callers poll ``get_run()``.

        When ``defer_schedule`` is true, background execution is queued until
        ``flush_deferred_run_schedules()`` — use when the run row must be
        committed before ``WorkflowExecutor`` loads it from the store.
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
            record_workflow_run_started()
            if defer_schedule:
                self._deferred_run_schedules.append((result.id, owner_id))
            else:
                self._schedule_run(
                    result.id,
                    owner_id=owner_id,
                    origin_context=capture_current_span_context(),
                )
        return result

    def flush_deferred_run_schedules(self) -> None:
        """Schedule runs deferred by ``start_run(..., defer_schedule=True)``."""
        pending = self._deferred_run_schedules
        self._deferred_run_schedules = []
        origin_context = capture_current_span_context()
        for run_id, owner_id in pending:
            self._schedule_run(
                run_id,
                owner_id=owner_id,
                origin_context=origin_context,
            )

    def discard_deferred_run_schedules(self) -> None:
        """Drop deferred schedules after a failed commit or setup."""
        self._deferred_run_schedules.clear()

    async def apply_decision(
        self,
        run_id: uuid.UUID,
        node_execution_id: uuid.UUID,
        *,
        owner_id: uuid.UUID,
        decision: ApprovalDecision,
    ) -> WorkflowRun:
        """Record an owner-scoped approval decision and resume when applicable."""
        updated_run: WorkflowRun | None = None
        run_status_after_decision: RunStatus | None = None
        reject_ends_run = False
        approval_node_id = ""
        selected_edge_ids: list[str] = []
        definition: WorkflowDefinition | None = None

        for attempt in range(_MAX_APPROVAL_DECISION_RETRIES):
            run = await self._store.get_run(run_id, owner_id=owner_id)
            if run is None:
                raise WorkflowNotFoundError(f"Workflow run {run_id} not found.")

            with_executions = await self._store.get_run_with_executions(
                run_id, owner_id=owner_id
            )
            if with_executions is None:
                raise WorkflowNotFoundError(f"Workflow run {run_id} not found.")
            _, executions = with_executions
            execution = next(
                (item for item in executions if item.id == node_execution_id), None
            )
            if execution is None:
                raise WorkflowNotFoundError(
                    f"Workflow node execution {node_execution_id} not found."
                )
            if execution.node_type is not NodeType.APPROVAL:
                raise WorkflowValidationError(
                    "Only approval node executions accept approve/reject decisions."
                )

            if run.status is not RunStatus.WAITING_APPROVAL:
                if execution.decision is decision:
                    return run
                if execution.decision is not None:
                    raise WorkflowDecisionConflictError(
                        "Approval decision conflicts with an existing decision."
                    )
                raise WorkflowValidationError(
                    "Workflow run is not waiting for an approval decision."
                )
            if execution.status is not NodeStatus.WAITING_APPROVAL:
                if execution.decision is decision:
                    return run
                if execution.decision is not None:
                    raise WorkflowDecisionConflictError(
                        "Approval decision conflicts with an existing decision."
                    )
                raise WorkflowValidationError(
                    "Workflow node execution is not waiting for a decision."
                )

            if definition is None:
                definition = await self._store.get_definition(
                    run.workflow_definition_id, owner_id=owner_id
                )
                if definition is None:
                    raise WorkflowNotFoundError(
                        f"Workflow definition {run.workflow_definition_id} not found."
                    )

            approval_node = next(
                (node for node in definition.nodes if node.id == execution.node_id),
                None,
            )
            if approval_node is None:
                raise WorkflowValidationError(
                    f"Approval node {execution.node_id!r} is missing from the "
                    "definition."
                )

            node_edges = outgoing_edges(definition, approval_node.id)
            selected_edge_ids = resolve_approval_selected_edge_ids(
                approval_node, node_edges, decision
            )
            node_status = (
                NodeStatus.SUCCEEDED
                if decision is ApprovalDecision.APPROVED
                else NodeStatus.FAILED
            )
            now = datetime.datetime.now(datetime.UTC)
            reject_ends_run = (
                decision is ApprovalDecision.REJECTED and not selected_edge_ids
            )
            output = build_approval_decision_output(
                node_id=approval_node.id,
                decision=decision,
                selected_edge_ids=selected_edge_ids,
            )
            updated_context = run.context.model_copy(
                update={
                    "variables": {
                        **run.context.variables,
                        approval_node.id: output,
                    }
                }
            )
            remaining_current_node_ids = [
                node_id
                for node_id in run.current_node_ids
                if node_id != approval_node.id
            ]
            run_status_after_decision = _run_status_after_approval_decision(
                reject_ends_run=reject_ends_run,
                remaining_current_node_ids=remaining_current_node_ids,
                definition=definition,
            )
            updated_run = run.model_copy(
                update={
                    "status": run_status_after_decision,
                    "context": updated_context,
                    "current_node_ids": remaining_current_node_ids,
                    "error": (
                        f"Approval node {approval_node.id!r} was rejected."
                        if reject_ends_run
                        else None
                    ),
                    "completed_at": now if reject_ends_run else None,
                    "updated_at": now,
                    "checkpoint_version": run.checkpoint_version + 1,
                }
            )
            approval_node_id = approval_node.id

            try:
                await self._store.record_approval_decision(
                    node_execution_id,
                    owner_id=owner_id,
                    decision=decision,
                    decided_by=owner_id,
                    node_status=node_status,
                    run=updated_run,
                )
                break
            except WorkflowApprovalCasMissError as exc:
                existing = exc.execution
                if existing.decision is decision:
                    latest = await self._store.get_run(run_id, owner_id=owner_id)
                    if latest is None:
                        raise WorkflowNotFoundError(
                            f"Workflow run {run_id} not found."
                        ) from exc
                    return latest
                raise WorkflowDecisionConflictError(
                    "Approval decision conflicts with an existing decision."
                ) from exc
            except WorkflowConcurrentUpdateError:
                if attempt < _MAX_APPROVAL_DECISION_RETRIES - 1:
                    continue
                raise
        else:
            raise WorkflowValidationError(
                "Workflow approval decision exceeded concurrent retry limit."
            )

        assert updated_run is not None
        assert definition is not None

        record_workflow_approval_pending_delta(-1)

        if reject_ends_run:
            return updated_run

        executor = WorkflowExecutor(
            self._store,
            self._node_executors,
            settings=self._settings,
            tool_registry=self._tool_registry,
        )
        continued = await executor.continue_from_approval(
            updated_run,
            definition=definition,
            approval_node_id=approval_node_id,
            selected_edge_ids=selected_edge_ids,
        )
        if run_status_after_decision is RunStatus.RUNNING:
            self._schedule_run(
                continued.id,
                owner_id=owner_id,
                origin_context=capture_current_span_context(),
                resume_reason="approval_continue",
            )
        return continued

    async def cancel_run(
        self, run_id: uuid.UUID, *, owner_id: uuid.UUID
    ) -> WorkflowRun:
        """Cancel a ``running`` or ``waiting_approval`` run."""
        for attempt in range(_MAX_APPROVAL_DECISION_RETRIES):
            run = await self._store.get_run(run_id, owner_id=owner_id)
            if run is None:
                raise WorkflowNotFoundError(f"Workflow run {run_id} not found.")

            if run.status in {
                RunStatus.COMPLETED,
                RunStatus.FAILED,
                RunStatus.CANCELLED,
            }:
                return run

            if run.status not in {
                RunStatus.RUNNING,
                RunStatus.WAITING_APPROVAL,
            }:
                raise WorkflowValidationError(
                    f"Workflow run {run_id} cannot be cancelled from status "
                    f"{run.status.value!r}."
                )

            now = datetime.datetime.now(datetime.UTC)
            cancelled = run.model_copy(
                update={
                    "status": RunStatus.CANCELLED,
                    "completed_at": now,
                    "updated_at": now,
                    "checkpoint_version": run.checkpoint_version + 1,
                }
            )
            try:
                return await self._store.checkpoint_run(
                    cancelled,
                    expected_checkpoint_version=run.checkpoint_version,
                )
            except WorkflowConcurrentUpdateError:
                if attempt < _MAX_APPROVAL_DECISION_RETRIES - 1:
                    continue
                raise

        raise WorkflowValidationError(
            "Workflow run cancellation exceeded concurrent retry limit."
        )

    async def resume(self, run_id: uuid.UUID, *, owner_id: uuid.UUID) -> WorkflowRun:
        """Reattach an in-process executor to a crashed ``running`` run."""
        run = await self._store.get_run(run_id, owner_id=owner_id)
        if run is None:
            raise WorkflowNotFoundError(f"Workflow run {run_id} not found.")

        if run.status in {
            RunStatus.COMPLETED,
            RunStatus.FAILED,
            RunStatus.CANCELLED,
        }:
            return run

        if run.status is RunStatus.WAITING_APPROVAL:
            raise WorkflowValidationError(
                "Workflow runs waiting for approval use approve/reject, not resume."
            )

        if run.status is not RunStatus.RUNNING:
            raise WorkflowValidationError(
                f"Workflow run {run_id} cannot be resumed from status "
                f"{run.status.value!r}."
            )

        self._schedule_run(run_id, owner_id=owner_id, resume_reason="resume")
        return run

    def _schedule_run(
        self,
        run_id: uuid.UUID,
        *,
        owner_id: uuid.UUID,
        origin_context: SpanContextSnapshot | None = None,
        resume_reason: str | None = None,
    ) -> asyncio.Task[None]:
        task = schedule_run_task(
            self._execute_run(
                run_id,
                owner_id=owner_id,
                origin_context=origin_context,
                resume_reason=resume_reason,
            ),
            run_id=run_id,
        )
        self._last_scheduled_run_task = task
        return task

    async def reconcile_orphaned_runs(self) -> int:
        """Reattach in-process executors to persisted ``running`` runs."""

        async def _schedule(run_id: uuid.UUID, owner_id: uuid.UUID) -> None:
            self._schedule_run(
                run_id,
                owner_id=owner_id,
                resume_reason="reconcile",
            )

        return await reconcile_orphaned_runs(self._store, schedule_run=_schedule)

    async def _execute_run(
        self,
        run_id: uuid.UUID,
        *,
        owner_id: uuid.UUID,
        origin_context: SpanContextSnapshot | None = None,
        resume_reason: str | None = None,
    ) -> None:
        if self._background_store_factory is None:
            await self._run_with_store(
                self._store,
                run_id,
                owner_id=owner_id,
                origin_context=origin_context,
                resume_reason=resume_reason,
            )
            return

        from app.db.engine import get_sessionmaker

        sessionmaker = get_sessionmaker()
        async with sessionmaker() as session:
            store = self._background_store_factory(session)
            await self._run_with_store(
                store,
                run_id,
                owner_id=owner_id,
                origin_context=origin_context,
                resume_reason=resume_reason,
            )

    async def _run_with_store(
        self,
        store: WorkflowStore,
        run_id: uuid.UUID,
        *,
        owner_id: uuid.UUID,
        origin_context: SpanContextSnapshot | None = None,
        resume_reason: str | None = None,
    ) -> None:
        run_id_str = str(run_id)
        started = time.perf_counter()
        with workflow_run_root_span(
            run_id_str,
            link=origin_context,
            resume_reason=resume_reason,
        ) as run_span:
            executor = WorkflowExecutor(
                store,
                self._node_executors,
                settings=self._settings,
                tool_registry=self._tool_registry,
            )
            try:
                final_run = await executor.execute_run(run_id, owner_id=owner_id)
                record_workflow_run_outcome(
                    run_span,
                    run_id=run_id_str,
                    status=final_run.status.value,
                    latency_ms=elapsed_ms_since(started),
                )
            except Exception:  # noqa: BLE001 - background execution must never crash the app
                mark_span_error_status(run_span, span_name="workflow.run")
                record_workflow_run_outcome(
                    run_span,
                    run_id=run_id_str,
                    status=RunStatus.FAILED.value,
                    latency_ms=elapsed_ms_since(started),
                )
                _logger.exception("Workflow run execution failed.", run_id=run_id_str)

    def _validate_before_persist(self, definition: WorkflowDefinition) -> None:
        try:
            self._validator.validate(definition)
        except WorkflowValidationError:
            raise
        except ValueError as exc:
            raise WorkflowValidationError(str(exc)) from exc
