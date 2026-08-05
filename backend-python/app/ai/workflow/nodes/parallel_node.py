"""Fork and Join node executors (Phase 5 — parallel execution)."""

from __future__ import annotations

from app.ai.workflow.models import WorkflowContext, WorkflowNode
from app.ai.workflow.nodes.base import NodeExecutionRequest, WorkflowNodeExecutionError

JOIN_POLICY_ALL = "all"
JOIN_POLICY_ANY = "any"
JOIN_POLICY_COUNT = "count"

_SUPPORTED_JOIN_POLICIES = frozenset(
    {JOIN_POLICY_ALL, JOIN_POLICY_ANY, JOIN_POLICY_COUNT}
)


def parse_join_config(node: WorkflowNode) -> tuple[str, int, bool]:
    """Return ``(join_policy, count, cancel_remaining)`` for a join node."""
    raw_policy = node.config.get("join_policy", JOIN_POLICY_ALL)
    if not isinstance(raw_policy, str):
        raise WorkflowNodeExecutionError(
            f"Join node {node.id!r} config.join_policy must be a string.",
            error_code="invalid_config",
        )
    if raw_policy not in _SUPPORTED_JOIN_POLICIES:
        raise WorkflowNodeExecutionError(
            f"Join node {node.id!r} has unsupported join_policy {raw_policy!r}.",
            error_code="invalid_config",
        )

    count = 1
    if raw_policy == JOIN_POLICY_COUNT:
        raw_count = node.config.get("count")
        if not isinstance(raw_count, int) or raw_count < 1:
            raise WorkflowNodeExecutionError(
                f"Join node {node.id!r} requires config.count >= 1 when "
                "join_policy is 'count'.",
                error_code="invalid_config",
            )
        count = raw_count

    cancel_remaining = node.config.get("cancel_remaining", True)
    if not isinstance(cancel_remaining, bool):
        raise WorkflowNodeExecutionError(
            f"Join node {node.id!r} config.cancel_remaining must be a boolean.",
            error_code="invalid_config",
        )
    return raw_policy, count, cancel_remaining


class ForkNodeExecutor:
    """Activates all outgoing fork branches (Part I § Fork / Join Node)."""

    def __init__(self, *, max_parallel_branches: int = 8) -> None:
        self._max_parallel_branches = max_parallel_branches

    async def execute(
        self,
        node: WorkflowNode,
        context: WorkflowContext,
        request: NodeExecutionRequest,
    ) -> dict[str, object]:
        del context
        outgoing_edges = list(request.outgoing_edges)
        if not outgoing_edges:
            raise WorkflowNodeExecutionError(
                f"Fork node {node.id!r} has no outgoing edges.",
                error_code="invalid_config",
            )

        join_node_id = node.config.get("join_node_id")
        if not isinstance(join_node_id, str) or not join_node_id.strip():
            raise WorkflowNodeExecutionError(
                f"Fork node {node.id!r} requires config.join_node_id.",
                error_code="invalid_config",
            )

        branch_count = len(outgoing_edges)
        if branch_count > self._max_parallel_branches:
            raise WorkflowNodeExecutionError(
                f"Fork node {node.id!r} fans out to {branch_count} branches; "
                f"maximum is {self._max_parallel_branches} "
                "(workflow_max_parallel_branches).",
                error_code="parallel_limit_exceeded",
            )

        branch_node_ids = [edge.to_node_id for edge in outgoing_edges]
        return {
            "join_node_id": join_node_id,
            "branch_node_ids": branch_node_ids,
            "branch_count": branch_count,
        }


class JoinNodeExecutor:
    """Records join completion once the join policy is satisfied (Phase 5)."""

    async def execute(
        self,
        node: WorkflowNode,
        context: WorkflowContext,
        request: NodeExecutionRequest,
    ) -> dict[str, object]:
        del request
        join_policy, count, cancel_remaining = parse_join_config(node)
        fork_node_id = node.config.get("fork_node_id")
        if not isinstance(fork_node_id, str) or not fork_node_id.strip():
            raise WorkflowNodeExecutionError(
                f"Join node {node.id!r} requires config.fork_node_id.",
                error_code="invalid_config",
            )

        fork_output = context.variables.get(fork_node_id)
        branch_node_ids: list[str] = []
        if isinstance(fork_output, dict):
            raw_branches = fork_output.get("branch_node_ids")
            if isinstance(raw_branches, list):
                branch_node_ids = [
                    item for item in raw_branches if isinstance(item, str)
                ]

        merged_branch_outputs = {
            branch_id: context.variables[branch_id]
            for branch_id in branch_node_ids
            if branch_id in context.variables
        }
        return {
            "fork_node_id": fork_node_id,
            "join_policy": join_policy,
            "count": count,
            "cancel_remaining": cancel_remaining,
            "merged_branch_outputs": merged_branch_outputs,
            "completed_branch_count": len(merged_branch_outputs),
        }
