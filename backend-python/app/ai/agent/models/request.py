"""Agent request model (public API — stable after Phase 1)."""

from __future__ import annotations

from pydantic import BaseModel, Field

from app.ai.agent.models.config import AgentConfig
from app.ai.agent.models.messages import AgentMessage


class AgentRequest(BaseModel):
    """Input envelope for a single agent execution."""

    messages: list[AgentMessage] = Field(min_length=1)
    model: str = Field(min_length=1)
    provider: str | None = None
    temperature: float = Field(default=0.7, ge=0, le=2)
    max_tokens: int | None = Field(default=None, ge=1)
    tool_names: list[str] | None = None
    system_prompt: str | None = None
    config: AgentConfig | None = None
