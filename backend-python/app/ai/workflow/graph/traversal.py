"""Topological and ready-node helpers (Phase 3+)."""

from __future__ import annotations

from app.ai.workflow.models import WorkflowDefinition, WorkflowRun


def resolve_ready_nodes(definition: WorkflowDefinition, run: WorkflowRun) -> list[str]:
    """Return node ids ready for execution (implemented in Phase 3)."""
    del definition, run
    raise NotImplementedError("resolve_ready_nodes() is implemented in Phase 3.")
