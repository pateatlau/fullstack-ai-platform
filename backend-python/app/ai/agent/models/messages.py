"""Provider-agnostic message models for the agent runtime."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

AgentMessageRole = Literal["system", "user", "assistant"]


class AgentMessage(BaseModel):
    """A single conversational turn passed into the agent runtime."""

    role: AgentMessageRole
    content: str = Field(min_length=1)
