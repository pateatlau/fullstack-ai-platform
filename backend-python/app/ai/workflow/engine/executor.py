"""Workflow execution engine (Phase 3+).

Drives a ``WorkflowRun`` to completion for sequential/branching (non-parallel)
graphs of ``task``/``terminal`` nodes, checkpointing after every node
transition (Part I § Execution Pipeline). Conditional routing (Phase 4),
fork/join (Phase 5), LLM/Agent/Approval nodes (Phases 6-7), and retry/crash
recovery (Phase 8) extend this loop in later phases.
"""

from __future__ import annotations

import asyncio
import datetime
import uuid
from collections.abc import Mapping
from typing import TYPE_CHECKING

from app.ai.workflow.exceptions import WorkflowNotFoundError
from app.ai.workflow.graph.traversal import resolve_ready_nodes
from app.ai.workflow.models import (
    NodeStatus,
    NodeType,
    RunStatus,
    WorkflowContext,
    WorkflowNode,
    WorkflowNodeExecution,
    WorkflowRun,
)
from app.ai.workflow.nodes.base import (
    NodeExecutionRequest,
    NodeExecutor,
    WorkflowNodeExecutionError,
)
from app.core.logging import get_logger

if TYPE_CHECKING:
    from app.ai.workflow.interfaces.workflow_store import WorkflowStore
    from app.core.config import Settings

_logger = get_logger(__name__)
_DEFAULT_NODE_TIMEOUT_SECONDS = 120


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
    ) -> None:
        self._store = store
        self._node_executors: dict[NodeType, NodeExecutor] = dict(node_executors)
        self._node_executors.setdefault(NodeType.TERMINAL, _TerminalNodeExecutor())
        self._default_node_timeout_seconds = (
            settings.workflow_node_timeout_seconds
            if settings is not None
            else _DEFAULT_NODE_TIMEOUT_SECONDS
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

        while run.status is RunStatus.RUNNING:
            ready_node_ids = resolve_ready_nodes(definition, run)
            if not ready_node_ids:
                run = await self._complete_run(run)
                break
            run = await self._execute_node(run, nodes_by_id[ready_node_ids[0]])

        return run

    async def _execute_node(self, run: WorkflowRun, node: WorkflowNode) -> WorkflowRun:
        attempt = 1
        receipt_id = f"{run.id}:{node.id}:{attempt}"
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
        run = await self._checkpoint_run(
            run, current_node_ids=[*run.current_node_ids, node.id]
        )

        executor = self._node_executors.get(node.type)
        if executor is None:
            return await self._fail_node(
                run,
                pending,
                error=f"No node executor registered for type {node.type.value!r}.",
            )

        request = NodeExecutionRequest(
            owner_id=run.owner_id, execution_receipt_id=receipt_id
        )
        timeout_seconds = node.timeout_seconds or self._default_node_timeout_seconds
        try:
            output = await asyncio.wait_for(
                executor.execute(node, run.context, request), timeout=timeout_seconds
            )
        except WorkflowNodeExecutionError as exc:
            return await self._fail_node(run, pending, error=str(exc))
        except TimeoutError:
            return await self._fail_node(
                run, pending, error=f"Node {node.id!r} execution timed out."
            )
        except Exception:  # noqa: BLE001 - node failures must never crash the run loop
            _logger.exception(
                "Unhandled workflow node execution error",
                run_id=str(run.id),
                node_id=node.id,
            )
            return await self._fail_node(
                run, pending, error=f"Node {node.id!r} execution failed."
            )

        return await self._succeed_node(run, pending, output=output)

    async def _succeed_node(
        self,
        run: WorkflowRun,
        execution: WorkflowNodeExecution,
        *,
        output: dict[str, object],
    ) -> WorkflowRun:
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
        updated_context = run.context.model_copy(
            update={"variables": {**run.context.variables, execution.node_id: output}}
        )
        return await self._checkpoint_run(
            run,
            context=updated_context,
            current_node_ids=[
                node_id
                for node_id in run.current_node_ids
                if node_id != execution.node_id
            ],
        )

    async def _fail_node(
        self, run: WorkflowRun, execution: WorkflowNodeExecution, *, error: str
    ) -> WorkflowRun:
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
        return await self._checkpoint_run(
            run,
            status=RunStatus.FAILED,
            error=error,
            current_node_ids=[],
            completed_at=now,
        )

    async def _complete_run(self, run: WorkflowRun) -> WorkflowRun:
        return await self._checkpoint_run(
            run,
            status=RunStatus.COMPLETED,
            current_node_ids=[],
            completed_at=_utcnow(),
        )

    async def _checkpoint_run(self, run: WorkflowRun, **updates: object) -> WorkflowRun:
        updated = run.model_copy(
            update={
                **updates,
                "updated_at": _utcnow(),
                "checkpoint_version": run.checkpoint_version + 1,
            }
        )
        return await self._store.checkpoint_run(
            updated, expected_checkpoint_version=run.checkpoint_version
        )


def _utcnow() -> datetime.datetime:
    return datetime.datetime.now(datetime.UTC)
