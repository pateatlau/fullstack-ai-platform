"""Node executor protocol (Phase 3)."""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from typing import Protocol

from app.ai.workflow.models import WorkflowContext, WorkflowEdge, WorkflowNode


@dataclass(frozen=True)
class NodeExecutionRequest:
    """Per-attempt metadata handed to a ``NodeExecutor`` alongside the node/context.

    Carries data that is specific to one execution attempt (crash-safe replay
    protocol; Part I § Crash-safe running) rather than to the run as a whole, so
    it is not part of ``WorkflowContext``.
    """

    owner_id: uuid.UUID
    execution_receipt_id: str
    outgoing_edges: tuple[WorkflowEdge, ...] = field(default_factory=tuple)


class WorkflowNodeExecutionError(Exception):
    """Raised by a ``NodeExecutor`` to signal a node-level failure.

    Caught by ``WorkflowExecutor`` and converted into a failed
    ``WorkflowNodeExecution`` + failed run — never a process crash.
    """

    def __init__(self, message: str, *, error_code: str | None = None) -> None:
        super().__init__(message)
        self.error_code = error_code


class NodeExecutor(Protocol):
    """Executes a single workflow node type."""

    async def execute(
        self,
        node: WorkflowNode,
        context: WorkflowContext,
        request: NodeExecutionRequest,
    ) -> dict[str, object]: ...
