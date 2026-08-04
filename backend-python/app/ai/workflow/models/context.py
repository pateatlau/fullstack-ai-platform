"""Canonical ``WorkflowContext`` model (public API — stable after Phase 1)."""

from __future__ import annotations

from pydantic import BaseModel, Field


class WorkflowContext(BaseModel):
    """Normalized execution context for a running workflow (Part I § WorkflowContext).

    Node executors read from and write to this object; ``ConditionEvaluator`` reads
    from it only. Persistence is the executor's responsibility, not the node's.
    """

    trigger_input: dict[str, object] = Field(default_factory=dict)
    variables: dict[str, object] = Field(default_factory=dict)
    metadata: dict[str, object] = Field(default_factory=dict)
