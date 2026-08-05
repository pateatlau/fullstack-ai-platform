"""Human approval node executor (Phase 7).

Pauses the run at an ``approval`` node until ``WorkflowManager.apply_decision()``
records an owner-scoped approve/reject decision.
"""

from __future__ import annotations

from app.ai.workflow.exceptions import WorkflowValidationError
from app.ai.workflow.models import (
    ApprovalDecision,
    WorkflowContext,
    WorkflowEdge,
    WorkflowNode,
)
from app.ai.workflow.nodes.base import NodeExecutionRequest

APPROVED_EDGE_ID_KEY = "approved_edge_id"
REJECTED_EDGE_ID_KEY = "rejected_edge_id"
_APPROVAL_OUTPUT_STATUS = "waiting_approval"


class ApprovalNodeExecutor:
    """Prepares approval pause metadata; persistence is handled by ``WorkflowExecutor``."""

    async def execute(
        self,
        node: WorkflowNode,
        context: WorkflowContext,
        request: NodeExecutionRequest,
    ) -> dict[str, object]:
        del request
        # TODO(epic-10): enforce workflow_approval_timeout_hours via background jobs.
        return {
            "status": _APPROVAL_OUTPUT_STATUS,
            "node_id": node.id,
            "trigger_input": dict(context.trigger_input),
            "variable_keys": sorted(context.variables.keys()),
        }


def resolve_approval_selected_edge_ids(
    node: WorkflowNode,
    outgoing_edges: list[WorkflowEdge],
    decision: ApprovalDecision,
) -> list[str]:
    """Return the outgoing edge id(s) to activate after an approval decision."""
    if decision is ApprovalDecision.APPROVED:
        configured = node.config.get(APPROVED_EDGE_ID_KEY)
        if isinstance(configured, str) and configured.strip():
            return [configured.strip()]
        unconditional = [edge for edge in outgoing_edges if edge.condition is None]
        if len(unconditional) == 1:
            return [unconditional[0].id]
        if len(outgoing_edges) == 1 and outgoing_edges[0].condition is None:
            return [outgoing_edges[0].id]
        raise WorkflowValidationError(
            f"Approval node {node.id!r} requires config.approved_edge_id when multiple "
            "outgoing edges are present."
        )

    configured = node.config.get(REJECTED_EDGE_ID_KEY)
    if isinstance(configured, str) and configured.strip():
        return [configured.strip()]
    return []


def build_approval_decision_output(
    *,
    node_id: str,
    decision: ApprovalDecision,
    selected_edge_ids: list[str],
) -> dict[str, object]:
    """Build node output persisted to ``WorkflowContext.variables`` after a decision."""
    return {
        "node_id": node_id,
        "decision": decision.value,
        "selected_edge_ids": selected_edge_ids,
    }
