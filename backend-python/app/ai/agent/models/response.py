"""Agent response model (public API — stable after Phase 1)."""

from __future__ import annotations

from pydantic import BaseModel, Field


class AgentResponse(BaseModel):
    """Normalized result of a completed agent execution."""

    content: str
    tools_used: list[str] = Field(default_factory=list)
    iterations: int = Field(default=0, ge=0)
    finish_reason: str | None = None
    metadata: dict[str, object] = Field(default_factory=dict)
