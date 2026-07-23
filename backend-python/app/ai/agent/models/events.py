"""Stream event models (minimal base for Phase 1; expanded in Phase 5)."""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, Field


class AgentStreamEventType(StrEnum):
    """High-level event categories emitted during agent execution."""

    START = "start"
    PLANNING = "planning"
    TOOL_START = "tool_start"
    TOOL_END = "tool_end"
    TOKEN = "token"
    REFLECTION = "reflection"
    COMPLETE = "complete"
    ERROR = "error"


class AgentStreamEvent(BaseModel):
    """Typed progress event published via :class:`StreamPublisher`."""

    type: AgentStreamEventType
    execution_id: str
    payload: dict[str, object] = Field(default_factory=dict)
