"""Agent runtime wiring (Phase 10)."""

from app.ai.agent.runtime.default_agent import DefaultAgent
from app.ai.agent.runtime.factory import create_default_agent

__all__ = ["DefaultAgent", "create_default_agent"]
