"""Workflow execution engine (Phase 3+).

Drives a ``WorkflowRun`` to completion for sequential/branching graphs,
parallel fork/join regions (Phase 5), conditional routing (Phase 4),
LLM/Agent/Approval nodes (Phases 6-7), and retry/crash recovery (Phase 8).
"""

from __future__ import annotations

import asyncio
import datetime
import time
import uuid
from collections.abc import Mapping
from typing import TYPE_CHECKING

from app.ai.hitl.models import ApprovalKind, ApprovalStatus
from app.ai.observability.metrics.instruments import (
    record_workflow_approval_pending_delta,
    record_workflow_checkpoint_metric,
    record_workflow_retry,
)
from app.ai.observability.tracing.spans import (
    approval_span,
    elapsed_ms_since,
    record_approval_span_outcome,
    record_workflow_node_outcome,
    workflow_span,
)
from app.ai.workflow.exceptions import (
    WorkflowConcurrentUpdateError,
    WorkflowNotFoundError,
    WorkflowValidationError,
)
from app.ai.workflow.graph.traversal import (
    SKIPPED_NODE_IDS_KEY,
    collect_incomplete_fork_branch_nodes_to_skip,
    collect_nodes_to_skip,
    find_fork_join_region,
    group_parallel_ready_nodes,
    is_join_ready,
    outgoing_edges,
    resolve_ready_nodes,
)
from app.ai.workflow.models import (
    APPROVAL_REQUESTED_AT_OUTPUT_KEY,
    NodeStatus,
    NodeType,
    RunStatus,
    WorkflowContext,
    WorkflowDefinition,
    WorkflowNode,
    WorkflowNodeExecution,
    WorkflowRun,
)
from app.ai.workflow.nodes.base import (
    NodeExecutionRequest,
    NodeExecutor,
    WorkflowNodeExecutionError,
)
from app.ai.workflow.nodes.parallel_node import (
    JOIN_POLICY_ALL,
    parse_join_config,
)
from app.ai.workflow.retry.policy import RetryPolicy
from app.ai.workflow.retry.recovery import (
    build_execution_receipt_id,
    crash_recovery_allowed,
    execution_interrupted_error,
    extract_execution_receipt_id,
    find_interrupted_executions,
    next_attempt_number,
)
from app.core.logging import get_logger

if TYPE_CHECKING:
    from app.ai.tools.registry import ToolRegistry
    from app.ai.workflow.interfaces.workflow_store import WorkflowStore
    from app.core.config import Settings

_logger = get_logger(__name__)
_DEFAULT_NODE_TIMEOUT_SECONDS = 120
_MAX_CHECKPOINT_RETRIES = 25
_RUN_DURATION_EXCEEDED = "Workflow run exceeded the configured duration limit."


class _TerminalNodeExecutor:
    """No-op executor for implicit/explicit terminal nodes (Part I § Terminal Node)."""

    async def execute(
        self,
        node: WorkflowNode,
        context: WorkflowContext,
        request: NodeExecutionRequest,
    ) -> dict[str, object]:
        del node, context, request
        return {}


class WorkflowExecutor:
    """Advances a run through its graph with checkpointing (Part I § WorkflowExecutor)."""

    def __init__(
        self,
        store: WorkflowStore,
        node_executors: Mapping[NodeType, NodeExecutor],
        *,
        settings: Settings | None = None,
        tool_registry: ToolRegistry | None = None,
    ) -> None:
        self._store = store
        self._node_executors: dict[NodeType, NodeExecutor] = dict(node_executors)
        self._node_executors.setdefault(NodeType.TERMINAL, _TerminalNodeExecutor())
        self._settings = settings
        self._tool_registry = tool_registry
        self._default_node_timeout_seconds = (
            settings.workflow_node_timeout_seconds
            if settings is not None
            else _DEFAULT_NODE_TIMEOUT_SECONDS
        )
        self._max_run_duration_minutes = (
            settings.workflow_max_run_duration_minutes if settings is not None else 60
        )

    async def execute_run(
        self, run_id: uuid.UUID, *, owner_id: uuid.UUID
    ) -> WorkflowRun:
        """Run the step loop until the run completes, fails, or pauses."""
        run = await self._store.get_run(run_id, owner_id=owner_id)
        if run is None:
            raise WorkflowNotFoundError(f"Workflow run {run_id} not found.")

        definition = await self._store.get_definition(
            run.workflow_definition_id, owner_id=owner_id
        )
        if definition is None:
            raise WorkflowNotFoundError(
                f"Workflow definition {run.workflow_definition_id} not found."
            )

        nodes_by_id = {node.id: node for node in definition.nodes}

        with_executions = await self._store.get_run_with_executions(
            run_id, owner_id=owner_id
        )
        if with_executions is None:
            raise WorkflowNotFoundError(f"Workflow run {run_id} not found.")
        run, executions = with_executions

        if self._run_exceeded_duration(run):
            return await self._fail_run_duration(run)

        cancelled = await self._stop_if_cancelled(run_id, owner_id=owner_id)
        if cancelled is not None:
            return cancelled

        while run.status is RunStatus.RUNNING:
            cancelled = await self._stop_if_cancelled(run.id, owner_id=run.owner_id)
            if cancelled is not None:
                return cancelled

            if self._run_exceeded_duration(run):
                return await self._fail_run_duration(run)

            interrupted = find_interrupted_executions(
                current_node_ids=run.current_node_ids,
                executions=executions,
            )
            if interrupted:
                for execution in interrupted:
                    node = nodes_by_id.get(execution.node_id)
                    if node is None:
                        return await self._fail_run(
                            run,
                            error=(
                                f"Interrupted node {execution.node_id!r} is missing "
                                "from the workflow definition."
                            ),
                        )
                    run = await self._resume_interrupted_node(
                        run,
                        node,
                        execution,
                        definition=definition,
                    )
                    if run.status is not RunStatus.RUNNING:
                        return run
                    refreshed = await self._store.get_run_with_executions(
                        run.id, owner_id=run.owner_id
                    )
                    if refreshed is None:
                        raise WorkflowNotFoundError(f"Workflow run {run.id} not found.")
                    run, executions = refreshed
                continue

            ready_node_ids = resolve_ready_nodes(definition, run)
            if not ready_node_ids:
                if run.current_node_ids:
                    _logger.warning(
                        "Workflow run stalled with in-progress nodes",
                        run_id=str(run.id),
                        current_node_ids=list(run.current_node_ids),
                    )
                    break
                run = await self._complete_run(run)
                break

            parallel_groups, sequential = group_parallel_ready_nodes(
                definition, ready_node_ids
            )
            if parallel_groups:
                for group in parallel_groups:
                    run = await self._execute_nodes_parallel(
                        run,
                        group,
                        nodes_by_id,
                        definition=definition,
                        executions=executions,
                    )
                    if run.status is not RunStatus.RUNNING:
                        break
                continue

            if sequential:
                run = await self._execute_node(
                    run,
                    nodes_by_id[sequential[0]],
                    definition=definition,
                    executions=executions,
                )
                refreshed = await self._store.get_run_with_executions(
                    run.id, owner_id=run.owner_id
                )
                if refreshed is None:
                    raise WorkflowNotFoundError(f"Workflow run {run.id} not found.")
                run, executions = refreshed

        return run

    async def _resume_interrupted_node(
        self,
        run: WorkflowRun,
        node: WorkflowNode,
        execution: WorkflowNodeExecution,
        *,
        definition: WorkflowDefinition,
    ) -> WorkflowRun:
        allowed, reason = crash_recovery_allowed(
            execution,
            node_config=node.config,
            registry=self._tool_registry,
        )
        if not allowed:
            return await self._fail_run(
                run,
                error=reason or "Crash recovery is not allowed for this node.",
            )

        _logger.info(
            "Recovering interrupted workflow node",
            run_id=str(run.id),
            node_id=node.id,
            attempt=execution.attempt,
        )
        interrupted_receipt = extract_execution_receipt_id(execution) or (
            build_execution_receipt_id(
                run_id=run.id, node_id=node.id, attempt=execution.attempt
            )
        )
        if execution.status is NodeStatus.RUNNING:
            await self._mark_attempt_failed(
                execution,
                error=execution_interrupted_error(),
            )

        refreshed = await self._store.get_run_with_executions(
            run.id, owner_id=run.owner_id
        )
        if refreshed is None:
            raise WorkflowNotFoundError(f"Workflow run {run.id} not found.")
        run, all_executions = refreshed
        return await self._execute_node(
            run,
            node,
            definition=definition,
            executions=all_executions,
            recovery_receipt_id=interrupted_receipt,
        )

    async def _execute_node(
        self,
        run: WorkflowRun,
        node: WorkflowNode,
        *,
        definition: WorkflowDefinition,
        executions: list[WorkflowNodeExecution] | None = None,
        recovery_receipt_id: str | None = None,
    ) -> WorkflowRun:
        cancelled = await self._stop_if_cancelled(run.id, owner_id=run.owner_id)
        if cancelled is not None:
            return cancelled

        retry_policy = RetryPolicy(node.retry_policy, settings=self._settings)
        attempt = next_attempt_number(executions or [], node.id)
        receipt_id = recovery_receipt_id or build_execution_receipt_id(
            run_id=run.id, node_id=node.id, attempt=attempt
        )

        retries_remaining = retry_policy.max_retries()
        retry_index = 0
        while True:
            cancelled = await self._stop_if_cancelled(run.id, owner_id=run.owner_id)
            if cancelled is not None:
                return cancelled

            attempt_start = time.perf_counter()
            with workflow_span("node") as node_span:
                now = _utcnow()
                pending = WorkflowNodeExecution(
                    id=uuid.uuid4(),
                    run_id=run.id,
                    node_id=node.id,
                    node_type=node.type,
                    attempt=attempt,
                    status=NodeStatus.RUNNING,
                    input={"execution_receipt_id": receipt_id, "config": node.config},
                    started_at=now,
                )
                await self._store.append_node_execution(pending)
                recovery_receipt_id = None

                if node.id not in run.current_node_ids:
                    run = await self._checkpoint_with_retry(
                        run, add_current_node=node.id
                    )

                executor = self._node_executors.get(node.type)
                if executor is None:
                    record_workflow_node_outcome(
                        node_span,
                        node_type=node.type.value,
                        attempt=attempt,
                        status=NodeStatus.FAILED.value,
                        latency_ms=elapsed_ms_since(attempt_start),
                    )
                    return await self._fail_node(
                        run,
                        pending,
                        error=f"No node executor registered for type {node.type.value!r}.",
                    )

                request = NodeExecutionRequest(
                    owner_id=run.owner_id,
                    execution_receipt_id=receipt_id,
                    outgoing_edges=tuple(outgoing_edges(definition, node.id)),
                )
                timeout_seconds = (
                    node.timeout_seconds or self._default_node_timeout_seconds
                )
                try:
                    output = await asyncio.wait_for(
                        executor.execute(node, run.context, request),
                        timeout=timeout_seconds,
                    )
                except WorkflowNodeExecutionError as exc:
                    fresh = await self._store.get_run(run.id, owner_id=run.owner_id)
                    if fresh is not None and self._should_ignore_branch_failure(
                        definition, fresh, node
                    ):
                        record_workflow_node_outcome(
                            node_span,
                            node_type=node.type.value,
                            attempt=attempt,
                            status=NodeStatus.SKIPPED.value,
                            latency_ms=elapsed_ms_since(attempt_start),
                        )
                        return await self._skip_branch_node(
                            fresh, pending, definition=definition, reason=str(exc)
                        )
                    if retry_policy.is_retryable(exc) and retries_remaining > 0:
                        await self._mark_attempt_failed(
                            pending,
                            error=str(exc),
                        )
                        record_workflow_node_outcome(
                            node_span,
                            node_type=node.type.value,
                            attempt=attempt,
                            status=NodeStatus.FAILED.value,
                            latency_ms=elapsed_ms_since(attempt_start),
                        )
                        record_workflow_retry(node_type=node.type.value)
                        retries_remaining -= 1
                        attempt += 1
                        receipt_id = build_execution_receipt_id(
                            run_id=run.id, node_id=node.id, attempt=attempt
                        )
                        await _sleep_before_retry(retry_policy, retry_index)
                        retry_index += 1
                        continue
                    record_workflow_node_outcome(
                        node_span,
                        node_type=node.type.value,
                        attempt=attempt,
                        status=NodeStatus.FAILED.value,
                        latency_ms=elapsed_ms_since(attempt_start),
                    )
                    return await self._fail_node(run, pending, error=str(exc))
                except TimeoutError:
                    error = f"Node {node.id!r} execution timed out."
                    if retries_remaining > 0:
                        await self._mark_attempt_failed(pending, error=error)
                        record_workflow_node_outcome(
                            node_span,
                            node_type=node.type.value,
                            attempt=attempt,
                            status=NodeStatus.FAILED.value,
                            latency_ms=elapsed_ms_since(attempt_start),
                        )
                        record_workflow_retry(node_type=node.type.value)
                        retries_remaining -= 1
                        attempt += 1
                        receipt_id = build_execution_receipt_id(
                            run_id=run.id, node_id=node.id, attempt=attempt
                        )
                        await _sleep_before_retry(retry_policy, retry_index)
                        retry_index += 1
                        continue
                    record_workflow_node_outcome(
                        node_span,
                        node_type=node.type.value,
                        attempt=attempt,
                        status=NodeStatus.FAILED.value,
                        latency_ms=elapsed_ms_since(attempt_start),
                    )
                    return await self._fail_node(run, pending, error=error)
                except Exception:  # noqa: BLE001 - node failures must never crash the run loop
                    _logger.exception(
                        "Unhandled workflow node execution error",
                        run_id=str(run.id),
                        node_id=node.id,
                    )
                    record_workflow_node_outcome(
                        node_span,
                        node_type=node.type.value,
                        attempt=attempt,
                        status=NodeStatus.FAILED.value,
                        latency_ms=elapsed_ms_since(attempt_start),
                    )
                    return await self._fail_node(
                        run, pending, error=f"Node {node.id!r} execution failed."
                    )

                cancelled = await self._stop_if_cancelled(run.id, owner_id=run.owner_id)
                if cancelled is not None:
                    record_workflow_node_outcome(
                        node_span,
                        node_type=node.type.value,
                        attempt=attempt,
                        status=NodeStatus.CANCELLED.value,
                        latency_ms=elapsed_ms_since(attempt_start),
                    )
                    return cancelled

                if node.type is NodeType.APPROVAL:
                    record_workflow_node_outcome(
                        node_span,
                        node_type=node.type.value,
                        attempt=attempt,
                        status=NodeStatus.WAITING_APPROVAL.value,
                        latency_ms=elapsed_ms_since(attempt_start),
                    )
                    return await self._pause_for_approval(
                        run, pending, output=output, definition=definition, node=node
                    )

                branch_count = output.get("branch_count")
                record_workflow_node_outcome(
                    node_span,
                    node_type=node.type.value,
                    attempt=attempt,
                    status=NodeStatus.SUCCEEDED.value,
                    latency_ms=elapsed_ms_since(attempt_start),
                    parallel_branch_count=(
                        branch_count
                        if node.type is NodeType.FORK and isinstance(branch_count, int)
                        else None
                    ),
                )
                return await self._succeed_node(
                    run, pending, output=output, definition=definition
                )

    async def _mark_attempt_failed(
        self,
        execution: WorkflowNodeExecution,
        *,
        error: str,
    ) -> None:
        now = _utcnow()
        await self._store.append_node_execution(
            execution.model_copy(
                update={
                    "status": NodeStatus.FAILED,
                    "error": error,
                    "completed_at": now,
                }
            )
        )

    def _run_exceeded_duration(self, run: WorkflowRun) -> bool:
        if run.started_at is None:
            return False
        elapsed = _utcnow() - run.started_at
        return elapsed > datetime.timedelta(minutes=self._max_run_duration_minutes)

    async def _fail_run_duration(self, run: WorkflowRun) -> WorkflowRun:
        _logger.warning(
            "Workflow run exceeded duration guard",
            run_id=str(run.id),
            max_run_duration_minutes=self._max_run_duration_minutes,
        )
        return await self._checkpoint_with_retry(
            run,
            status=RunStatus.FAILED,
            error=_RUN_DURATION_EXCEEDED,
            current_node_ids=[],
            completed_at=_utcnow(),
        )

    async def _fail_run(self, run: WorkflowRun, *, error: str) -> WorkflowRun:
        return await self._checkpoint_with_retry(
            run,
            status=RunStatus.FAILED,
            error=error,
            current_node_ids=[],
            completed_at=_utcnow(),
        )

    async def _execute_nodes_parallel(
        self,
        run: WorkflowRun,
        node_ids: list[str],
        nodes_by_id: dict[str, WorkflowNode],
        *,
        definition: WorkflowDefinition,
        executions: list[WorkflowNodeExecution],
    ) -> WorkflowRun:
        results = await asyncio.gather(
            *[
                self._execute_node(
                    run,
                    nodes_by_id[node_id],
                    definition=definition,
                    executions=executions,
                )
                for node_id in node_ids
            ],
            return_exceptions=True,
        )

        for result in results:
            if isinstance(result, BaseException):
                if isinstance(result, WorkflowNodeExecutionError):
                    return await self._fail_run_from_parallel_error(run, str(result))
                raise result

        latest = await self._store.get_run(run.id, owner_id=run.owner_id)
        if latest is None:
            raise WorkflowNotFoundError(f"Workflow run {run.id} not found.")
        for result in results:
            if isinstance(result, WorkflowRun) and result.status is RunStatus.CANCELLED:
                return result
        for result in results:
            if isinstance(result, WorkflowRun) and result.status is RunStatus.FAILED:
                return result

        return latest

    def _should_ignore_branch_failure(
        self,
        definition: WorkflowDefinition,
        run: WorkflowRun,
        node: WorkflowNode,
    ) -> bool:
        region = find_fork_join_region(definition, node.id)
        if region is None:
            return False
        _, join_id = region
        join_node = next(
            (item for item in definition.nodes if item.id == join_id), None
        )
        if join_node is None or join_node.type is not NodeType.JOIN:
            return False
        join_policy, _, _ = parse_join_config(join_node)
        if join_policy == JOIN_POLICY_ALL:
            return False
        return is_join_ready(definition, run, join_node)

    async def _skip_branch_node(
        self,
        run: WorkflowRun,
        execution: WorkflowNodeExecution,
        *,
        definition: WorkflowDefinition,
        reason: str,
    ) -> WorkflowRun:
        now = _utcnow()
        await self._store.append_node_execution(
            execution.model_copy(
                update={
                    "status": NodeStatus.SKIPPED,
                    "error": reason,
                    "completed_at": now,
                }
            )
        )
        run = await self._checkpoint_with_retry(
            run, remove_current_node=execution.node_id
        )
        existing_skipped_raw = run.context.metadata.get(SKIPPED_NODE_IDS_KEY, [])
        existing_skipped = (
            existing_skipped_raw if isinstance(existing_skipped_raw, list) else []
        )
        if execution.node_id not in existing_skipped:
            updated_context = run.context.model_copy(
                update={
                    "metadata": {
                        **run.context.metadata,
                        SKIPPED_NODE_IDS_KEY: [*existing_skipped, execution.node_id],
                    }
                }
            )
            run = await self._checkpoint_with_retry(run, context=updated_context)
        return run

    async def _fail_run_from_parallel_error(
        self, run: WorkflowRun, error: str
    ) -> WorkflowRun:
        return await self._checkpoint_with_retry(
            run,
            status=RunStatus.FAILED,
            error=error,
            current_node_ids=[],
            completed_at=_utcnow(),
        )

    async def continue_from_approval(
        self,
        run: WorkflowRun,
        *,
        definition: WorkflowDefinition,
        approval_node_id: str,
        selected_edge_ids: list[str],
    ) -> WorkflowRun:
        """Skip unselected branches after an approval decision is persisted."""
        if not selected_edge_ids:
            return run
        return await self._skip_unselected_branches(
            run,
            definition=definition,
            router_node_id=approval_node_id,
            selected_edge_ids=selected_edge_ids,
        )

    async def _pause_for_approval(
        self,
        run: WorkflowRun,
        execution: WorkflowNodeExecution,
        *,
        output: dict[str, object],
        definition: WorkflowDefinition,
        node: WorkflowNode,
    ) -> WorkflowRun:
        del definition
        cancelled = await self._stop_if_cancelled(run.id, owner_id=run.owner_id)
        if cancelled is not None:
            return cancelled

        pause_at = _utcnow()
        await self._store.append_node_execution(
            execution.model_copy(
                update={
                    "status": NodeStatus.WAITING_APPROVAL,
                    "output": {
                        **output,
                        APPROVAL_REQUESTED_AT_OUTPUT_KEY: pause_at.isoformat(),
                    },
                }
            )
        )
        with approval_span(
            approval_id=str(execution.id),
            approval_kind=ApprovalKind.WORKFLOW_NODE.value,
            approval_correlation_id=str(execution.id),
        ) as span:
            record_workflow_approval_pending_delta(1)
            record_approval_span_outcome(
                span,
                approval_status=ApprovalStatus.PENDING.value,
                edited=False,
            )
        return await self._checkpoint_with_retry(
            run,
            status=RunStatus.WAITING_APPROVAL,
        )

    async def _succeed_node(
        self,
        run: WorkflowRun,
        execution: WorkflowNodeExecution,
        *,
        output: dict[str, object],
        definition: WorkflowDefinition,
    ) -> WorkflowRun:
        cancelled = await self._stop_if_cancelled(run.id, owner_id=run.owner_id)
        if cancelled is not None:
            return cancelled

        now = _utcnow()
        await self._store.append_node_execution(
            execution.model_copy(
                update={
                    "status": NodeStatus.SUCCEEDED,
                    "output": output,
                    "completed_at": now,
                }
            )
        )

        for attempt in range(_MAX_CHECKPOINT_RETRIES):
            fresh = await self._store.get_run(run.id, owner_id=run.owner_id)
            if fresh is None:
                raise WorkflowNotFoundError(f"Workflow run {run.id} not found.")
            if fresh.status is RunStatus.CANCELLED:
                return fresh
            updated_context = fresh.context.model_copy(
                update={
                    "variables": {
                        **fresh.context.variables,
                        execution.node_id: output,
                    }
                }
            )
            try:
                run = await self._store.checkpoint_run(
                    fresh.model_copy(
                        update={
                            "context": updated_context,
                            "current_node_ids": _merge_current_node_ids(
                                fresh.current_node_ids, remove=execution.node_id
                            ),
                            "updated_at": _utcnow(),
                            "checkpoint_version": fresh.checkpoint_version + 1,
                        }
                    ),
                    expected_checkpoint_version=fresh.checkpoint_version,
                )
                break
            except WorkflowConcurrentUpdateError:
                if attempt == _MAX_CHECKPOINT_RETRIES - 1:
                    raise
                continue

        if execution.node_type is NodeType.ROUTER:
            selected = output.get("selected_edge_ids")
            if isinstance(selected, list):
                selected_ids = [item for item in selected if isinstance(item, str)]
                run = await self._skip_unselected_branches(
                    run,
                    definition=definition,
                    router_node_id=execution.node_id,
                    selected_edge_ids=selected_ids,
                )
        if execution.node_type is NodeType.JOIN:
            run = await self._handle_join_completion(
                run, definition=definition, join_node_id=execution.node_id
            )
        return run

    async def _handle_join_completion(
        self,
        run: WorkflowRun,
        *,
        definition: WorkflowDefinition,
        join_node_id: str,
    ) -> WorkflowRun:
        join_node = next(
            (node for node in definition.nodes if node.id == join_node_id), None
        )
        if join_node is None or join_node.type is not NodeType.JOIN:
            return run

        join_policy, _, cancel_remaining = parse_join_config(join_node)
        if join_policy == JOIN_POLICY_ALL or not cancel_remaining:
            return run

        fork_node_id = join_node.config.get("fork_node_id")
        if not isinstance(fork_node_id, str):
            return run

        node_ids = collect_incomplete_fork_branch_nodes_to_skip(
            definition,
            fork_node_id=fork_node_id,
            join_node_id=join_node_id,
            run=run,
        )
        if not node_ids:
            return run

        now = _utcnow()
        for node_id in node_ids:
            node = next((item for item in definition.nodes if item.id == node_id), None)
            if node is None:
                continue
            await self._store.append_node_execution(
                WorkflowNodeExecution(
                    id=uuid.uuid4(),
                    run_id=run.id,
                    node_id=node_id,
                    node_type=node.type,
                    attempt=1,
                    status=NodeStatus.SKIPPED,
                    completed_at=now,
                )
            )

        existing_skipped_raw = run.context.metadata.get(SKIPPED_NODE_IDS_KEY, [])
        existing_skipped = (
            existing_skipped_raw if isinstance(existing_skipped_raw, list) else []
        )
        merged_skipped = [
            *(
                item
                for item in existing_skipped
                if isinstance(item, str) and item not in node_ids
            ),
            *node_ids,
        ]
        updated_context = run.context.model_copy(
            update={
                "metadata": {
                    **run.context.metadata,
                    SKIPPED_NODE_IDS_KEY: merged_skipped,
                }
            }
        )
        return await self._checkpoint_with_retry(run, context=updated_context)

    async def _fail_node(
        self, run: WorkflowRun, execution: WorkflowNodeExecution, *, error: str
    ) -> WorkflowRun:
        cancelled = await self._stop_if_cancelled(run.id, owner_id=run.owner_id)
        if cancelled is not None:
            return cancelled

        now = _utcnow()
        await self._store.append_node_execution(
            execution.model_copy(
                update={
                    "status": NodeStatus.FAILED,
                    "error": error,
                    "completed_at": now,
                }
            )
        )
        return await self._checkpoint_with_retry(
            run,
            status=RunStatus.FAILED,
            error=error,
            current_node_ids=[],
            completed_at=now,
        )

    async def _skip_unselected_branches(
        self,
        run: WorkflowRun,
        *,
        definition: WorkflowDefinition,
        router_node_id: str,
        selected_edge_ids: list[str],
    ) -> WorkflowRun:
        node_ids = collect_nodes_to_skip(
            definition,
            router_node_id=router_node_id,
            selected_edge_ids=selected_edge_ids,
            run=run,
        )
        if not node_ids:
            return run

        now = _utcnow()
        for node_id in node_ids:
            node = next((item for item in definition.nodes if item.id == node_id), None)
            if node is None:
                continue
            await self._store.append_node_execution(
                WorkflowNodeExecution(
                    id=uuid.uuid4(),
                    run_id=run.id,
                    node_id=node_id,
                    node_type=node.type,
                    attempt=1,
                    status=NodeStatus.SKIPPED,
                    completed_at=now,
                )
            )

        existing_skipped_raw = run.context.metadata.get(SKIPPED_NODE_IDS_KEY, [])
        existing_skipped = (
            existing_skipped_raw if isinstance(existing_skipped_raw, list) else []
        )
        merged_skipped = [
            *(
                item
                for item in existing_skipped
                if isinstance(item, str) and item not in node_ids
            ),
            *node_ids,
        ]
        updated_context = run.context.model_copy(
            update={
                "metadata": {
                    **run.context.metadata,
                    SKIPPED_NODE_IDS_KEY: merged_skipped,
                }
            }
        )
        return await self._checkpoint_with_retry(run, context=updated_context)

    async def _complete_run(self, run: WorkflowRun) -> WorkflowRun:
        return await self._checkpoint_with_retry(
            run,
            status=RunStatus.COMPLETED,
            current_node_ids=[],
            completed_at=_utcnow(),
        )

    async def _checkpoint_with_retry(
        self, run: WorkflowRun, **updates: object
    ) -> WorkflowRun:
        owner_id = run.owner_id
        run_id = run.id
        add_current = updates.pop("add_current_node", None)
        remove_current = updates.pop("remove_current_node", None)

        for attempt in range(_MAX_CHECKPOINT_RETRIES):
            fresh = await self._store.get_run(run_id, owner_id=owner_id)
            if fresh is None:
                raise WorkflowNotFoundError(f"Workflow run {run_id} not found.")
            if fresh.status is RunStatus.CANCELLED:
                return fresh

            patch: dict[str, object] = dict(updates)
            if "current_node_ids" not in patch and (
                isinstance(add_current, str) or isinstance(remove_current, str)
            ):
                patch["current_node_ids"] = _merge_current_node_ids(
                    fresh.current_node_ids,
                    add=add_current if isinstance(add_current, str) else None,
                    remove=remove_current if isinstance(remove_current, str) else None,
                )
            if "context" in patch and isinstance(patch["context"], WorkflowContext):
                incoming = patch["context"]
                patch["context"] = fresh.context.model_copy(
                    update={
                        "variables": {
                            **fresh.context.variables,
                            **incoming.variables,
                        },
                        "metadata": _merge_context_metadata(
                            fresh.context.metadata,
                            incoming.metadata,
                        ),
                    }
                )

            try:
                checkpoint_start = time.perf_counter()
                result = await self._store.checkpoint_run(
                    fresh.model_copy(
                        update={
                            **patch,
                            "updated_at": _utcnow(),
                            "checkpoint_version": fresh.checkpoint_version + 1,
                        }
                    ),
                    expected_checkpoint_version=fresh.checkpoint_version,
                )
                record_workflow_checkpoint_metric(
                    latency_ms=elapsed_ms_since(checkpoint_start)
                )
                return result
            except WorkflowConcurrentUpdateError:
                if attempt == _MAX_CHECKPOINT_RETRIES - 1:
                    raise

        raise WorkflowValidationError(
            "Workflow run checkpoint merge exceeded retry limit."
        )

    async def _stop_if_cancelled(
        self, run_id: uuid.UUID, *, owner_id: uuid.UUID
    ) -> WorkflowRun | None:
        """Return the persisted run when cancellation has been recorded."""
        fresh = await self._store.get_run(run_id, owner_id=owner_id)
        if fresh is None:
            raise WorkflowNotFoundError(f"Workflow run {run_id} not found.")
        if fresh.status is RunStatus.CANCELLED:
            return fresh
        return None


def _merge_current_node_ids(
    current_node_ids: list[str],
    *,
    add: str | None = None,
    remove: str | None = None,
) -> list[str]:
    result = list(current_node_ids)
    if add is not None and add not in result:
        result.append(add)
    if remove is not None and remove in result:
        result.remove(remove)
    return result


def _merge_context_metadata(
    fresh_metadata: dict[str, object],
    incoming_metadata: dict[str, object],
) -> dict[str, object]:
    """Merge run context metadata without losing list-valued keys on retry."""
    merged = {**fresh_metadata, **incoming_metadata}
    if (
        SKIPPED_NODE_IDS_KEY not in fresh_metadata
        and SKIPPED_NODE_IDS_KEY not in incoming_metadata
    ):
        return merged

    fresh_skipped = fresh_metadata.get(SKIPPED_NODE_IDS_KEY, [])
    incoming_skipped = incoming_metadata.get(SKIPPED_NODE_IDS_KEY, [])
    fresh_ids = [
        item
        for item in (fresh_skipped if isinstance(fresh_skipped, list) else [])
        if isinstance(item, str)
    ]
    incoming_ids = [
        item
        for item in (incoming_skipped if isinstance(incoming_skipped, list) else [])
        if isinstance(item, str)
    ]
    seen = set(fresh_ids)
    merged[SKIPPED_NODE_IDS_KEY] = [
        *fresh_ids,
        *(item for item in incoming_ids if item not in seen),
    ]
    return merged


def _utcnow() -> datetime.datetime:
    return datetime.datetime.now(datetime.UTC)


async def _sleep_before_retry(policy: RetryPolicy, retry_index: int) -> None:
    """Apply exponential backoff between workflow node retry attempts."""
    delay = policy.base_delay_seconds() * (2**retry_index)
    await asyncio.sleep(delay)
