"""Execution-scoped context for a single agent run (public API — stable after Phase 1)."""

from __future__ import annotations

import uuid

from pydantic import BaseModel, Field


class AgentContext(BaseModel):
    """Portable context for one agent execution (not session-scoped)."""

    execution_id: str = Field(default_factory=lambda: uuid.uuid4().hex)
    request_id: str | None = None
    allowed_tool_names: frozenset[str] | None = None
    metadata: dict[str, object] = Field(default_factory=dict)

    model_config = {"arbitrary_types_allowed": True}
