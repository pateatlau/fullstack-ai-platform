"""Topological / ready-node helpers (Phase 3+).

A node's completion is derived from ``WorkflowRun`` alone (no separate node
execution history lookup): a node has succeeded once its id is a key in
``run.context.variables`` (Part I § WorkflowContext — accumulated node
outputs), and a node is currently active while its id is in
``run.current_node_ids``. Skipped branch nodes are tracked in
``run.context.metadata["skipped_node_ids"]``. This keeps the resolver
storage-agnostic.
"""

from __future__ import annotations

from collections import defaultdict

from app.ai.workflow.models import (
    NodeType,
    WorkflowDefinition,
    WorkflowEdge,
    WorkflowNode,
    WorkflowRun,
)
from app.ai.workflow.nodes.parallel_node import (
    JOIN_POLICY_ALL,
    JOIN_POLICY_ANY,
    JOIN_POLICY_COUNT,
    parse_join_config,
)

SKIPPED_NODE_IDS_KEY = "skipped_node_ids"


def get_skipped_node_ids(run: WorkflowRun) -> set[str]:
    """Return node ids explicitly marked skipped on the run context."""
    raw = run.context.metadata.get(SKIPPED_NODE_IDS_KEY, [])
    if not isinstance(raw, list):
        return set()
    return {item for item in raw if isinstance(item, str)}


def get_succeeded_node_ids(run: WorkflowRun) -> set[str]:
    return set(run.context.variables.keys())


def get_resolved_node_ids(run: WorkflowRun) -> set[str]:
    return get_succeeded_node_ids(run) | get_skipped_node_ids(run)


def outgoing_edges(definition: WorkflowDefinition, node_id: str) -> list[WorkflowEdge]:
    """Return outgoing edges for ``node_id`` in graph declaration order."""
    return [edge for edge in definition.edges if edge.from_node_id == node_id]


def incoming_edges(definition: WorkflowDefinition, node_id: str) -> list[WorkflowEdge]:
    """Return incoming edges for ``node_id`` in graph declaration order."""
    return [edge for edge in definition.edges if edge.to_node_id == node_id]


def get_fork_join_region_nodes(
    definition: WorkflowDefinition, fork_id: str, join_id: str
) -> set[str]:
    """Return node ids strictly between ``fork_id`` and ``join_id`` (exclusive)."""
    adjacency: dict[str, list[str]] = defaultdict(list)
    for edge in definition.edges:
        adjacency[edge.from_node_id].append(edge.to_node_id)

    region: set[str] = set()
    stack = [
        edge.to_node_id for edge in definition.edges if edge.from_node_id == fork_id
    ]
    while stack:
        node_id = stack.pop()
        if node_id == join_id or node_id in region:
            continue
        region.add(node_id)
        for neighbor in adjacency[node_id]:
            if neighbor != join_id:
                stack.append(neighbor)
    return region


def find_fork_join_region(
    definition: WorkflowDefinition, node_id: str
) -> tuple[str, str] | None:
    """Return ``(fork_id, join_id)`` when ``node_id`` lies in a fork/join region."""
    for node in definition.nodes:
        if node.type is not NodeType.FORK:
            continue
        join_node_id = node.config.get("join_node_id")
        if not isinstance(join_node_id, str):
            continue
        if node_id in get_fork_join_region_nodes(definition, node.id, join_node_id):
            return node.id, join_node_id
    return None


def group_parallel_ready_nodes(
    definition: WorkflowDefinition, ready_node_ids: list[str]
) -> tuple[list[list[str]], list[str]]:
    """Split ready ids into fork-region parallel groups and sequential ids."""
    region_groups: dict[tuple[str, str], list[str]] = defaultdict(list)
    sequential: list[str] = []

    for node_id in ready_node_ids:
        region = find_fork_join_region(definition, node_id)
        if region is not None:
            region_groups[region].append(node_id)
        else:
            sequential.append(node_id)

    parallel_groups: list[list[str]] = []
    for group in region_groups.values():
        if len(group) > 1:
            parallel_groups.append(group)
        else:
            sequential.extend(group)

    return parallel_groups, sequential


def _join_branch_incoming_edges(
    definition: WorkflowDefinition, join_node: WorkflowNode
) -> list[WorkflowEdge]:
    """Return join incoming edges that represent branch completions.

    Direct ``fork -> join`` shortcuts are excluded from join-policy counting so
    ``any``/``count(n)`` wait for actual branch nodes, not fork success alone.
    """
    incoming = incoming_edges(definition, join_node.id)
    fork_node_id = join_node.config.get("fork_node_id")
    if not isinstance(fork_node_id, str):
        return incoming

    branch_incoming = [edge for edge in incoming if edge.from_node_id != fork_node_id]
    return branch_incoming


def is_join_ready(
    definition: WorkflowDefinition, run: WorkflowRun, join_node: WorkflowNode
) -> bool:
    """Return whether ``join_node`` satisfies its configured join policy."""
    branch_incoming = _join_branch_incoming_edges(definition, join_node)
    if not branch_incoming:
        return False

    succeeded = get_succeeded_node_ids(run)
    resolved = get_resolved_node_ids(run)
    join_policy, count, _ = parse_join_config(join_node)

    succeeded_sources = sum(
        1 for edge in branch_incoming if edge.from_node_id in succeeded
    )
    if join_policy == JOIN_POLICY_ALL:
        return all(edge.from_node_id in resolved for edge in branch_incoming)
    if join_policy == JOIN_POLICY_ANY:
        return succeeded_sources >= 1
    if join_policy == JOIN_POLICY_COUNT:
        return succeeded_sources >= count
    return False


def collect_incomplete_fork_branch_nodes_to_skip(
    definition: WorkflowDefinition,
    *,
    fork_node_id: str,
    join_node_id: str,
    run: WorkflowRun,
) -> list[str]:
    """Return branch-region nodes to skip after an ``any``/``count`` join fires."""
    region = get_fork_join_region_nodes(definition, fork_node_id, join_node_id)
    resolved = get_resolved_node_ids(run) | set(run.current_node_ids)
    return sorted(node_id for node_id in region if node_id not in resolved)


def resolve_ready_nodes(definition: WorkflowDefinition, run: WorkflowRun) -> list[str]:
    """Return node ids ready for execution, in graph declaration order."""
    nodes_by_id = {node.id: node for node in definition.nodes}
    succeeded = get_succeeded_node_ids(run)
    skipped = get_skipped_node_ids(run)
    resolved = succeeded | skipped
    started = resolved | set(run.current_node_ids)

    incoming_by_node: dict[str, list[str]] = defaultdict(list)
    incoming_edges_by_node: dict[str, list[WorkflowEdge]] = defaultdict(list)
    for edge in definition.edges:
        incoming_by_node[edge.to_node_id].append(edge.from_node_id)
        incoming_edges_by_node[edge.to_node_id].append(edge)

    ready: list[str] = []
    for node in definition.nodes:
        if node.id in started:
            continue
        if node.id == definition.entry_node_id:
            ready.append(node.id)
            continue
        if node.type is NodeType.JOIN:
            if is_join_ready(definition, run, node):
                ready.append(node.id)
            continue
        incoming = incoming_by_node.get(node.id)
        if incoming and all(source_id in resolved for source_id in incoming):
            if _incoming_branch_edges_selected(
                incoming_edges_by_node[node.id], nodes_by_id, run
            ):
                ready.append(node.id)
    return ready


def collect_nodes_to_skip(
    definition: WorkflowDefinition,
    *,
    router_node_id: str,
    selected_edge_ids: list[str],
    run: WorkflowRun,
) -> list[str]:
    """Return downstream node ids that should be marked ``skipped`` after routing."""
    selected = set(selected_edge_ids)
    succeeded = get_succeeded_node_ids(run)
    skipped = get_skipped_node_ids(run)
    started = get_resolved_node_ids(run) | set(run.current_node_ids)

    outgoing = outgoing_edges(definition, router_node_id)
    selected_targets = {edge.to_node_id for edge in outgoing if edge.id in selected}

    to_skip: set[str] = set()
    for edge in outgoing:
        if (
            edge.id not in selected
            and edge.to_node_id not in started
            and edge.to_node_id not in selected_targets
        ):
            to_skip.add(edge.to_node_id)

    changed = True
    while changed:
        changed = False
        for node in definition.nodes:
            if node.id in started or node.id in to_skip:
                continue
            incoming = [edge for edge in definition.edges if edge.to_node_id == node.id]
            if not incoming:
                continue
            sources = [edge.from_node_id for edge in incoming]
            if not all(source in succeeded | skipped | to_skip for source in sources):
                continue
            if any(source in succeeded for source in sources):
                continue
            to_skip.add(node.id)
            changed = True

    return sorted(to_skip)


def _incoming_branch_edges_selected(
    incoming_edges_list: list[WorkflowEdge],
    nodes_by_id: dict[str, WorkflowNode],
    run: WorkflowRun,
) -> bool:
    branch_sources: dict[str, list[WorkflowEdge]] = defaultdict(list)
    for edge in incoming_edges_list:
        source = nodes_by_id.get(edge.from_node_id)
        if source is None or source.type not in {NodeType.ROUTER, NodeType.APPROVAL}:
            continue
        branch_sources[edge.from_node_id].append(edge)

    for source_id, edges in branch_sources.items():
        output = run.context.variables.get(source_id)
        if not isinstance(output, dict):
            return False
        selected = output.get("selected_edge_ids")
        if not isinstance(selected, list):
            return False
        selected_ids = {item for item in selected if isinstance(item, str)}
        if not any(edge.id in selected_ids for edge in edges):
            return False
    return True
