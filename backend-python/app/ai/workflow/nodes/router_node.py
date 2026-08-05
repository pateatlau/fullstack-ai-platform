"""Router node executor — selects outgoing edges via ``ConditionEvaluator``."""

from __future__ import annotations

from app.ai.workflow.conditions.evaluator import ConditionEvaluator
from app.ai.workflow.models import WorkflowContext, WorkflowEdge, WorkflowNode
from app.ai.workflow.nodes.base import NodeExecutionRequest, WorkflowNodeExecutionError

_ROUTING_MODE_EXCLUSIVE = "exclusive"
_ROUTING_MODE_ALL_MATCHING = "all_matching"


class RouterNodeExecutor:
    """Evaluates outgoing edge conditions and selects branch(es) to activate."""

    def __init__(self, evaluator: ConditionEvaluator | None = None) -> None:
        self._evaluator = evaluator or ConditionEvaluator()

    async def execute(
        self,
        node: WorkflowNode,
        context: WorkflowContext,
        request: NodeExecutionRequest,
    ) -> dict[str, object]:
        outgoing_edges = _outgoing_edges_for(node, request)
        if not outgoing_edges:
            raise WorkflowNodeExecutionError(
                f"Router node {node.id!r} has no outgoing edges.",
                error_code="invalid_config",
            )

        routing_mode_value = node.config.get("routing_mode", _ROUTING_MODE_EXCLUSIVE)
        if not isinstance(routing_mode_value, str):
            raise WorkflowNodeExecutionError(
                f"Router node {node.id!r} config.routing_mode must be a string.",
                error_code="invalid_config",
            )
        if routing_mode_value not in {
            _ROUTING_MODE_EXCLUSIVE,
            _ROUTING_MODE_ALL_MATCHING,
        }:
            raise WorkflowNodeExecutionError(
                f"Router node {node.id!r} has unsupported routing_mode "
                f"{routing_mode_value!r}.",
                error_code="invalid_config",
            )

        selected = _select_edges(
            outgoing_edges,
            context,
            self._evaluator,
            routing_mode=routing_mode_value,
        )
        if not selected:
            raise WorkflowNodeExecutionError(
                f"Router node {node.id!r} matched no outgoing edge.",
                error_code="no_matching_edge",
            )
        return {"selected_edge_ids": selected}


def _outgoing_edges_for(
    node: WorkflowNode, request: NodeExecutionRequest
) -> list[WorkflowEdge]:
    edges = list(request.outgoing_edges)
    if not edges:
        raise WorkflowNodeExecutionError(
            f"Router node {node.id!r} requires outgoing_edges on the execution request.",
            error_code="invalid_config",
        )
    return edges


def _select_edges(
    outgoing_edges: list[WorkflowEdge],
    context: WorkflowContext,
    evaluator: ConditionEvaluator,
    *,
    routing_mode: str,
) -> list[str]:
    """Return selected edge ids in declaration order."""
    matching: list[str] = []
    for edge in outgoing_edges:
        if _edge_matches(edge, context, evaluator):
            matching.append(edge.id)
            if routing_mode == _ROUTING_MODE_EXCLUSIVE:
                return matching
    return matching


def _edge_matches(
    edge: WorkflowEdge, context: WorkflowContext, evaluator: ConditionEvaluator
) -> bool:
    if edge.condition is None:
        return True
    return evaluator.evaluate(edge.condition, context)
