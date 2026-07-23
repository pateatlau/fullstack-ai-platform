"""Agent runtime configuration (public API — stable after Phase 1)."""

from __future__ import annotations

from pydantic import BaseModel, Field


class AgentConfig(BaseModel):
    """Per-execution agent settings. Defaults match Part I configuration."""

    max_iterations: int = Field(default=5, ge=1)
    reflection_enabled: bool = False
    max_reflections: int = Field(default=2, ge=0)
    max_retries: int = Field(default=3, ge=0)
    retry_base_delay_seconds: float = Field(default=1.0, ge=0.0)
    parallel_tools_enabled: bool = False
    timeout_seconds: int | None = Field(default=None, ge=1)
