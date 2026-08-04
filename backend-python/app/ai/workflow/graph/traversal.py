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
        incoming = incoming_by_node.get(node.id)
        if incoming and all(source_id in resolved for source_id in incoming):
            if _incoming_router_edges_selected(
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

    to_skip: set[str] = set()
    for edge in outgoing_edges(definition, router_node_id):
        if edge.id not in selected and edge.to_node_id not in started:
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


def _incoming_router_edges_selected(
    incoming_edges: list[WorkflowEdge],
    nodes_by_id: dict[str, WorkflowNode],
    run: WorkflowRun,
) -> bool:
    for edge in incoming_edges:
        source = nodes_by_id.get(edge.from_node_id)
        if source is None or source.type is not NodeType.ROUTER:
            continue
        output = run.context.variables.get(edge.from_node_id)
        if not isinstance(output, dict):
            return False
        selected = output.get("selected_edge_ids")
        if not isinstance(selected, list):
            return False
        if edge.id not in selected:
            return False
    return True
