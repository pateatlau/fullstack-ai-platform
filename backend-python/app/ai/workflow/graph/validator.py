"""Graph validation for workflow definitions (Phase 2)."""

from __future__ import annotations

from collections import defaultdict

from app.ai.workflow.conditions.schema import validate_condition_shape
from app.ai.workflow.exceptions import WorkflowValidationError
from app.ai.workflow.graph.node_config import validate_node_configs
from app.ai.workflow.models import NodeType, WorkflowDefinition, WorkflowEdge
from app.ai.workflow.nodes.approval_node import (
    APPROVED_EDGE_ID_KEY,
    REJECTED_EDGE_ID_KEY,
)

_DEFAULT_MAX_NODES = 50
_DEFAULT_MAX_PARALLEL_BRANCHES = 8


class GraphValidator:
    """Validates workflow graph structure before activation (Part I § GraphValidator)."""

    def __init__(
        self,
        *,
        max_nodes_per_definition: int = _DEFAULT_MAX_NODES,
        max_parallel_branches: int = _DEFAULT_MAX_PARALLEL_BRANCHES,
    ) -> None:
        self._max_nodes = max_nodes_per_definition
        self._max_parallel_branches = max_parallel_branches

    def validate(self, definition: WorkflowDefinition) -> None:
        """Validate a definition graph; raises ``WorkflowValidationError`` on failure."""
        node_ids = {node.id for node in definition.nodes}
        self._validate_node_count(definition)
        self._validate_entry_node(definition, node_ids)
        self._validate_node_types(definition)
        self._validate_node_configs(definition)
        self._validate_dangling_edges(definition, node_ids)
        self._validate_edge_conditions(definition)
        self._validate_cycles(definition, node_ids)
        self._validate_reachability(definition, node_ids)
        self._validate_fork_join_pairing(definition, node_ids)
        self._validate_approval_nodes(definition)

    def _validate_node_count(self, definition: WorkflowDefinition) -> None:
        if len(definition.nodes) > self._max_nodes:
            raise WorkflowValidationError(
                f"Graph exceeds workflow_max_nodes_per_definition "
                f"({self._max_nodes}); found {len(definition.nodes)} nodes."
            )

    def _validate_entry_node(
        self, definition: WorkflowDefinition, node_ids: set[str]
    ) -> None:
        if definition.entry_node_id not in node_ids:
            raise WorkflowValidationError(
                f"entry_node_id {definition.entry_node_id!r} is not present in nodes."
            )

    def _validate_node_types(self, definition: WorkflowDefinition) -> None:
        for node in definition.nodes:
            if node.type not in NodeType:
                raise WorkflowValidationError(
                    f"Node {node.id!r} has unsupported type {node.type!r}."
                )

    def _validate_node_configs(self, definition: WorkflowDefinition) -> None:
        validate_node_configs(definition.nodes)

    def _validate_dangling_edges(
        self, definition: WorkflowDefinition, node_ids: set[str]
    ) -> None:
        for edge in definition.edges:
            if edge.from_node_id not in node_ids:
                raise WorkflowValidationError(
                    f"Edge {edge.id!r} references unknown from_node_id "
                    f"{edge.from_node_id!r}."
                )
            if edge.to_node_id not in node_ids:
                raise WorkflowValidationError(
                    f"Edge {edge.id!r} references unknown to_node_id "
                    f"{edge.to_node_id!r}."
                )

    def _validate_edge_conditions(self, definition: WorkflowDefinition) -> None:
        for edge in definition.edges:
            if edge.condition is None:
                continue
            validate_condition_shape(edge.condition, path=f"edge {edge.id!r} condition")

    def _validate_cycles(
        self, definition: WorkflowDefinition, node_ids: set[str]
    ) -> None:
        adjacency = self._build_adjacency(definition.edges, node_ids)
        visiting: set[str] = set()
        visited: set[str] = set()

        def visit(node_id: str) -> None:
            if node_id in visiting:
                raise WorkflowValidationError(
                    f"Cycle detected involving node {node_id!r}."
                )
            if node_id in visited:
                return
            visiting.add(node_id)
            for neighbor in adjacency[node_id]:
                visit(neighbor)
            visiting.remove(node_id)
            visited.add(node_id)

        for node_id in node_ids:
            visit(node_id)

    def _validate_reachability(
        self, definition: WorkflowDefinition, node_ids: set[str]
    ) -> None:
        adjacency = self._build_adjacency(definition.edges, node_ids)
        reachable: set[str] = set()
        stack = [definition.entry_node_id]

        while stack:
            node_id = stack.pop()
            if node_id in reachable:
                continue
            reachable.add(node_id)
            stack.extend(adjacency[node_id])

        unreachable = sorted(node_ids - reachable)
        if unreachable:
            joined = ", ".join(unreachable)
            raise WorkflowValidationError(
                f"Nodes unreachable from entry_node_id {definition.entry_node_id!r}: "
                f"{joined}."
            )

    def _validate_fork_join_pairing(
        self, definition: WorkflowDefinition, node_ids: set[str]
    ) -> None:
        nodes_by_id = {node.id: node for node in definition.nodes}
        fork_to_join: dict[str, str] = {}
        join_to_fork: dict[str, str] = {}

        for node in definition.nodes:
            if node.type is NodeType.FORK:
                join_node_id = node.config.get("join_node_id")
                if not isinstance(join_node_id, str) or not join_node_id.strip():
                    raise WorkflowValidationError(
                        f"Fork node {node.id!r} requires config.join_node_id."
                    )
                if join_node_id not in node_ids:
                    raise WorkflowValidationError(
                        f"Fork node {node.id!r} references unknown join_node_id "
                        f"{join_node_id!r}."
                    )
                join_node = nodes_by_id[join_node_id]
                if join_node.type is not NodeType.JOIN:
                    raise WorkflowValidationError(
                        f"Fork node {node.id!r} join_node_id {join_node_id!r} "
                        "must reference a join node."
                    )
                if node.id in fork_to_join:
                    raise WorkflowValidationError(
                        f"Duplicate fork node id {node.id!r}."
                    )
                fork_to_join[node.id] = join_node_id

                outgoing = [
                    edge for edge in definition.edges if edge.from_node_id == node.id
                ]
                if len(outgoing) > self._max_parallel_branches:
                    raise WorkflowValidationError(
                        f"Fork node {node.id!r} has {len(outgoing)} outgoing edges; "
                        f"maximum is {self._max_parallel_branches} "
                        "(workflow_max_parallel_branches)."
                    )

            if node.type is NodeType.JOIN:
                fork_node_id = node.config.get("fork_node_id")
                if not isinstance(fork_node_id, str) or not fork_node_id.strip():
                    raise WorkflowValidationError(
                        f"Join node {node.id!r} requires config.fork_node_id."
                    )
                if fork_node_id not in node_ids:
                    raise WorkflowValidationError(
                        f"Join node {node.id!r} references unknown fork_node_id "
                        f"{fork_node_id!r}."
                    )
                fork_node = nodes_by_id[fork_node_id]
                if fork_node.type is not NodeType.FORK:
                    raise WorkflowValidationError(
                        f"Join node {node.id!r} fork_node_id {fork_node_id!r} "
                        "must reference a fork node."
                    )
                if node.id in join_to_fork:
                    raise WorkflowValidationError(
                        f"Duplicate join node id {node.id!r}."
                    )
                join_to_fork[node.id] = fork_node_id

        for fork_id, join_id in fork_to_join.items():
            expected_fork = join_to_fork.get(join_id)
            if expected_fork != fork_id:
                raise WorkflowValidationError(
                    f"Fork node {fork_id!r} and join node {join_id!r} are not "
                    "paired bidirectionally."
                )

        for join_id, fork_id in join_to_fork.items():
            expected_join = fork_to_join.get(fork_id)
            if expected_join != join_id:
                raise WorkflowValidationError(
                    f"Join node {join_id!r} and fork node {fork_id!r} are not "
                    "paired bidirectionally."
                )

    def _validate_approval_nodes(self, definition: WorkflowDefinition) -> None:
        outgoing_by_node: dict[str, list[WorkflowEdge]] = defaultdict(list)
        for edge in definition.edges:
            outgoing_by_node[edge.from_node_id].append(edge)

        for node in definition.nodes:
            if node.type is not NodeType.APPROVAL:
                continue
            outgoing = outgoing_by_node.get(node.id, [])
            if not outgoing:
                raise WorkflowValidationError(
                    f"Approval node {node.id!r} requires at least one outgoing edge."
                )

            outgoing_ids = {edge.id for edge in outgoing}
            approved_edge_id = node.config.get(APPROVED_EDGE_ID_KEY)
            rejected_edge_id = node.config.get(REJECTED_EDGE_ID_KEY)

            if (
                isinstance(approved_edge_id, str)
                and approved_edge_id not in outgoing_ids
            ):
                raise WorkflowValidationError(
                    f"Approval node {node.id!r} config.approved_edge_id "
                    f"{approved_edge_id!r} is not an outgoing edge."
                )
            if (
                isinstance(rejected_edge_id, str)
                and rejected_edge_id not in outgoing_ids
            ):
                raise WorkflowValidationError(
                    f"Approval node {node.id!r} config.rejected_edge_id "
                    f"{rejected_edge_id!r} is not an outgoing edge."
                )
            if (
                isinstance(approved_edge_id, str)
                and isinstance(rejected_edge_id, str)
                and approved_edge_id == rejected_edge_id
            ):
                raise WorkflowValidationError(
                    f"Approval node {node.id!r} approved_edge_id and rejected_edge_id "
                    "must differ."
                )

            unconditional = [edge for edge in outgoing if edge.condition is None]
            if len(unconditional) > 1 and not isinstance(approved_edge_id, str):
                raise WorkflowValidationError(
                    f"Approval node {node.id!r} requires config.approved_edge_id when "
                    "multiple unconditional outgoing edges are present."
                )
            if (
                len(outgoing) > 1
                and not unconditional
                and not isinstance(approved_edge_id, str)
            ):
                raise WorkflowValidationError(
                    f"Approval node {node.id!r} requires config.approved_edge_id when "
                    "all outgoing edges are conditional."
                )

    @staticmethod
    def _build_adjacency(
        edges: list[WorkflowEdge], node_ids: set[str]
    ) -> dict[str, list[str]]:
        adjacency: dict[str, list[str]] = defaultdict(list)
        for node_id in node_ids:
            adjacency[node_id]
        for edge in edges:
            adjacency[edge.from_node_id].append(edge.to_node_id)
        return adjacency
