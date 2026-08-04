"""Node executor protocol (Phase 3)."""

from __future__ import annotations

from typing import Protocol

from app.ai.workflow.models import WorkflowContext, WorkflowNode


class NodeExecutor(Protocol):
    """Executes a single workflow node type."""

    async def execute(
        self, node: WorkflowNode, context: WorkflowContext
    ) -> dict[str, object]: ...
