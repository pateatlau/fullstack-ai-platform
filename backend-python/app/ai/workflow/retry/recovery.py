"""Crash-safe recovery helpers for interrupted workflow nodes (Phase 8)."""

from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING

from app.ai.workflow.models import NodeType, WorkflowNodeExecution

if TYPE_CHECKING:
    from app.ai.tools.registry import ToolRegistry

_EXECUTION_INTERRUPTED = "execution_interrupted"

# Side-effecting node types that require receipt/idempotency on crash recovery.
_SIDE_EFFECTING_NODE_TYPES = frozenset({NodeType.TASK, NodeType.LLM, NodeType.AGENT})

# Deterministic nodes safe to re-run after crash without external dedupe.
_DETERMINISTIC_NODE_TYPES = frozenset({NodeType.ROUTER, NodeType.FORK, NodeType.JOIN})


def execution_interrupted_error() -> str:
    """Return the canonical error string for an interrupted node attempt."""
    return _EXECUTION_INTERRUPTED


def is_side_effecting_node_type(node_type: NodeType) -> bool:
    """Return True when a node type may perform external side effects."""
    return node_type in _SIDE_EFFECTING_NODE_TYPES


def is_deterministic_node_type(node_type: NodeType) -> bool:
    """Return True when a node may be re-run directly after crash."""
    return node_type in _DETERMINISTIC_NODE_TYPES


def extract_execution_receipt_id(execution: WorkflowNodeExecution) -> str | None:
    """Return the stable receipt id persisted for an attempt, if present."""
    raw = execution.input.get("execution_receipt_id")
    return raw if isinstance(raw, str) and raw.strip() else None


def build_execution_receipt_id(*, run_id: object, node_id: str, attempt: int) -> str:
    """Build the canonical per-attempt receipt id (Part I § Crash-safe running)."""
    return f"{run_id}:{node_id}:{attempt}"


def is_receipt_aware_tool(
    tool_name: str,
    *,
    registry: ToolRegistry | None,
) -> bool:
    """Return True when the registered tool handler opts into receipt dedupe."""
    if registry is None:
        return False
    handler = registry.get_handler(tool_name)
    if handler is None:
        return False
    return getattr(handler, "execution_receipt_aware", False) is True


def crash_recovery_allowed(
    execution: WorkflowNodeExecution,
    *,
    node_config: Mapping[str, object],
    registry: ToolRegistry | None = None,
) -> tuple[bool, str | None]:
    """Return whether an interrupted side-effecting node may be safely re-executed."""
    effective_config: dict[str, object] = dict(node_config)
    input_config = execution.input.get("config")
    if isinstance(input_config, dict):
        effective_config = {**effective_config, **input_config}

    if execution.node_type in _DETERMINISTIC_NODE_TYPES:
        return True, None
    if execution.node_type is NodeType.APPROVAL:
        return False, "Approval nodes cannot be crash-recovered; use approve/reject."
    if execution.node_type is NodeType.TERMINAL:
        return True, None

    if execution.node_type is NodeType.TASK:
        tool_name = effective_config.get("tool_name")
        if not isinstance(tool_name, str) or not tool_name.strip():
            return False, "Task node is missing tool_name for crash recovery."
        if is_receipt_aware_tool(tool_name.strip(), registry=registry):
            return True, None
        return (
            False,
            "Task node tool is not execution-receipt-aware; crash recovery would "
            "risk duplicate side effects.",
        )

    if execution.node_type in {NodeType.LLM, NodeType.AGENT}:
        return (
            False,
            f"{execution.node_type.value} nodes cannot be crash-recovered without a "
            "platform-wide execution receipt store.",
        )

    return (
        False,
        f"Unsupported node type {execution.node_type.value!r} for crash recovery.",
    )


def latest_execution_by_node(
    executions: list[WorkflowNodeExecution],
) -> dict[str, WorkflowNodeExecution]:
    """Return the highest-attempt execution row per node id."""
    by_node: dict[str, WorkflowNodeExecution] = {}
    for execution in executions:
        existing = by_node.get(execution.node_id)
        if existing is None or execution.attempt > existing.attempt:
            by_node[execution.node_id] = execution
    return by_node


def find_interrupted_executions(
    *,
    current_node_ids: list[str],
    executions: list[WorkflowNodeExecution],
) -> list[WorkflowNodeExecution]:
    """Return running executions for nodes still marked in-flight on the run."""
    latest = latest_execution_by_node(executions)
    interrupted: list[WorkflowNodeExecution] = []
    for node_id in current_node_ids:
        execution = latest.get(node_id)
        if execution is not None and execution.status.value == "running":
            interrupted.append(execution)
    return interrupted


def next_attempt_number(
    executions: list[WorkflowNodeExecution],
    node_id: str,
) -> int:
    """Return the next 1-based attempt number for ``node_id``."""
    attempts = [
        execution.attempt for execution in executions if execution.node_id == node_id
    ]
    return (max(attempts) if attempts else 0) + 1
