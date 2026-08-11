"""Graph validation for workflow definitions (Phase 2)."""

from __future__ import annotations

from collections import defaultdict
from typing import TYPE_CHECKING

from app.ai.workflow.conditions.schema import validate_condition_shape
from app.ai.workflow.exceptions import WorkflowValidationError
from app.ai.workflow.graph.node_config import validate_node_configs
from app.ai.workflow.models import (
    NodeType,
    WorkflowDefinition,
    WorkflowEdge,
    WorkflowNode,
)
from app.ai.workflow.nodes.approval_node import (
    APPROVED_EDGE_ID_KEY,
    REJECTED_EDGE_ID_KEY,
)

if TYPE_CHECKING:
    from app.ai.hitl.policy import ApprovalPolicy
    from app.ai.plugins.registry import PluginRegistry
    from app.ai.plugins.workflow.registry import WorkflowPluginRegistry
    from app.ai.tools.registry import ToolRegistry

_DEFAULT_MAX_NODES = 50
_DEFAULT_MAX_PARALLEL_BRANCHES = 8


class GraphValidator:
    """Validates workflow graph structure before activation (Part I § GraphValidator)."""

    def __init__(
        self,
        *,
        max_nodes_per_definition: int = _DEFAULT_MAX_NODES,
        max_parallel_branches: int = _DEFAULT_MAX_PARALLEL_BRANCHES,
        plugins_enabled: bool = False,
        plugin_registry: PluginRegistry | None = None,
        workflow_plugin_registry: WorkflowPluginRegistry | None = None,
        hitl_enabled: bool = False,
        tool_registry: ToolRegistry | None = None,
        approval_policy: ApprovalPolicy | None = None,
    ) -> None:
        self._max_nodes = max_nodes_per_definition
        self._max_parallel_branches = max_parallel_branches
        self._plugins_enabled = plugins_enabled
        self._plugin_registry = plugin_registry
        self._workflow_plugin_registry = workflow_plugin_registry
        self._hitl_enabled = hitl_enabled
        self._tool_registry = tool_registry
        self._approval_policy = approval_policy

    def validate(self, definition: WorkflowDefinition) -> None:
        """Validate a definition graph; raises ``WorkflowValidationError`` on failure."""
        node_ids = {node.id for node in definition.nodes}
        self._validate_node_count(definition)
        self._validate_entry_node(definition, node_ids)
        self._validate_node_types(definition)
        if not self._plugins_enabled:
            self._reject_plugin_nodes_when_disabled(definition)
        self._validate_node_configs(definition)
        if self._plugins_enabled:
            self._validate_plugin_nodes(definition)
        self._validate_dangling_edges(definition, node_ids)
        self._validate_edge_conditions(definition)
        self._validate_cycles(definition, node_ids)
        self._validate_reachability(definition, node_ids)
        self._validate_fork_join_pairing(definition, node_ids)
        self._validate_approval_nodes(definition)
        self._validate_approval_required_tool_reachability(definition, node_ids)

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
        validate_node_configs(
            definition.nodes,
            workflow_plugin_registry=(
                self._workflow_plugin_registry if self._plugins_enabled else None
            ),
        )

    def _reject_plugin_nodes_when_disabled(
        self, definition: WorkflowDefinition
    ) -> None:
        for node in definition.nodes:
            if node.type is NodeType.PLUGIN:
                raise WorkflowValidationError(
                    f"Node {node.id!r} has type 'plugin' but PLUGINS_ENABLED is false."
                )

    def _validate_plugin_nodes(self, definition: WorkflowDefinition) -> None:
        from app.ai.plugins.models import PluginStatus

        for node in definition.nodes:
            if node.type is not NodeType.PLUGIN:
                continue

            plugin_id = node.config.get("plugin_id")
            plugin_node_type = node.config.get("plugin_node_type")
            if not isinstance(plugin_id, str) or not plugin_id.strip():
                raise WorkflowValidationError(
                    f"Plugin node {node.id!r} requires config.plugin_id."
                )
            if not isinstance(plugin_node_type, str) or not plugin_node_type.strip():
                raise WorkflowValidationError(
                    f"Plugin node {node.id!r} requires config.plugin_node_type."
                )

            record = (
                self._plugin_registry.get(plugin_id)
                if self._plugin_registry is not None
                else None
            )
            if record is None or record.status is not PluginStatus.LOADED:
                raise WorkflowValidationError(
                    f"Plugin node {node.id!r} references unknown or unloaded plugin "
                    f"{plugin_id!r}."
                )

            registry = self._workflow_plugin_registry
            if registry is None or not registry.has(plugin_id, plugin_node_type):
                raise WorkflowValidationError(
                    f"Plugin node {node.id!r} references unknown node type "
                    f"{plugin_node_type!r} for plugin {plugin_id!r}."
                )

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
            if not unconditional and not isinstance(approved_edge_id, str):
                raise WorkflowValidationError(
                    f"Approval node {node.id!r} requires config.approved_edge_id when "
                    "no unconditional outgoing edge is present."
                )

    def _validate_approval_required_tool_reachability(
        self, definition: WorkflowDefinition, node_ids: set[str]
    ) -> None:
        """Fail when a flagged tool is reachable without a preceding approval node."""
        if not self._hitl_enabled:
            return
        if self._tool_registry is None or self._approval_policy is None:
            raise WorkflowValidationError(
                "HITL reachability guard is misconfigured: tool_registry and "
                "approval_policy are required when HITL_ENABLED=true."
            )

        nodes_by_id = {node.id: node for node in definition.nodes}
        forward_adjacency = self._build_adjacency(definition.edges, node_ids)
        reverse_adjacency = self._build_reverse_adjacency(definition.edges, node_ids)
        protected = self._compute_approval_protected_nodes(
            definition.entry_node_id,
            nodes_by_id,
            forward_adjacency,
            reverse_adjacency,
            node_ids,
        )

        for node in definition.nodes:
            if node.type not in (NodeType.TASK, NodeType.AGENT):
                continue
            for tool_name in self._referenced_tool_names(node):
                tool = self._tool_registry.get(tool_name)
                if tool is None or not self._approval_policy.requires_approval(tool):
                    continue
                if protected.get(node.id, False):
                    continue
                raise WorkflowValidationError(
                    f"Node {node.id!r} references approval-required tool "
                    f"{tool_name!r} without a preceding approval node on all paths "
                    f"from entry node {definition.entry_node_id!r}."
                )

    @staticmethod
    def _referenced_tool_names(node: WorkflowNode) -> list[str]:
        if node.type is NodeType.TASK:
            tool_name = node.config.get("tool_name")
            if isinstance(tool_name, str) and tool_name.strip():
                return [tool_name]
            return []
        tool_names = node.config.get("tool_names")
        if not isinstance(tool_names, list):
            return []
        return [name for name in tool_names if isinstance(name, str) and name.strip()]

    @staticmethod
    def _compute_approval_protected_nodes(
        entry_node_id: str,
        nodes_by_id: dict[str, WorkflowNode],
        forward_adjacency: dict[str, list[str]],
        reverse_adjacency: dict[str, list[str]],
        node_ids: set[str],
    ) -> dict[str, bool]:
        """Return nodes where every entry-to-node path crosses an approval node."""
        order = GraphValidator._topological_order(
            entry_node_id, forward_adjacency, node_ids
        )
        protected: dict[str, bool] = {}

        for node_id in order:
            node = nodes_by_id[node_id]
            predecessors = reverse_adjacency[node_id]

            if node_id == entry_node_id:
                protected[node_id] = node.type is NodeType.APPROVAL
                continue

            if not predecessors:
                protected[node_id] = False
                continue

            incoming_protected = []
            for predecessor_id in predecessors:
                predecessor = nodes_by_id[predecessor_id]
                if predecessor.type is NodeType.APPROVAL:
                    incoming_protected.append(True)
                else:
                    incoming_protected.append(protected.get(predecessor_id, False))
            protected[node_id] = all(incoming_protected)

        return protected

    @staticmethod
    def _topological_order(
        entry_node_id: str,
        forward_adjacency: dict[str, list[str]],
        node_ids: set[str],
    ) -> list[str]:
        in_degree = {node_id: 0 for node_id in node_ids}
        for node_id in node_ids:
            for successor in forward_adjacency[node_id]:
                in_degree[successor] += 1

        ready = sorted(node_id for node_id, degree in in_degree.items() if degree == 0)
        if entry_node_id in ready:
            ready.remove(entry_node_id)
            ready.insert(0, entry_node_id)

        order: list[str] = []
        remaining = dict(in_degree)
        while ready:
            node_id = ready.pop(0)
            order.append(node_id)
            for successor in forward_adjacency[node_id]:
                remaining[successor] -= 1
                if remaining[successor] == 0:
                    ready.append(successor)
                    ready.sort()

        return order

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

    @staticmethod
    def _build_reverse_adjacency(
        edges: list[WorkflowEdge], node_ids: set[str]
    ) -> dict[str, list[str]]:
        reverse: dict[str, list[str]] = defaultdict(list)
        for node_id in node_ids:
            reverse[node_id]
        for edge in edges:
            reverse[edge.to_node_id].append(edge.from_node_id)
        return reverse
