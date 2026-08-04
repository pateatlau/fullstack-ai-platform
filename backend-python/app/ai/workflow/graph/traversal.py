"""Topological / ready-node helpers (Phase 3+).

A node's completion is derived from ``WorkflowRun`` alone (no separate node
execution history lookup): a node has succeeded once its id is a key in
``run.context.variables`` (Part I § WorkflowContext — accumulated node
outputs), and a node is currently active while its id is in
``run.current_node_ids``. This keeps the resolver storage-agnostic.
"""

from __future__ import annotations

from collections import defaultdict

from app.ai.workflow.models import WorkflowDefinition, WorkflowRun


def resolve_ready_nodes(definition: WorkflowDefinition, run: WorkflowRun) -> list[str]:
    """Return node ids ready for execution, in graph declaration order.

    The entry node is ready once it has neither succeeded nor started. Any
    other node is ready once every node at the source of one of its incoming
    edges has succeeded. Conditional routing (Phase 4) and fork/join fan-out
    (Phase 5) are not evaluated here — every declared edge is treated as
    unconditional.
    """
    succeeded_node_ids = set(run.context.variables.keys())
    started_node_ids = succeeded_node_ids | set(run.current_node_ids)

    incoming_by_node: dict[str, list[str]] = defaultdict(list)
    for edge in definition.edges:
        incoming_by_node[edge.to_node_id].append(edge.from_node_id)

    ready: list[str] = []
    for node in definition.nodes:
        if node.id in started_node_ids:
            continue
        if node.id == definition.entry_node_id:
            ready.append(node.id)
            continue
        incoming = incoming_by_node.get(node.id)
        if incoming and all(source_id in succeeded_node_ids for source_id in incoming):
            ready.append(node.id)
    return ready
