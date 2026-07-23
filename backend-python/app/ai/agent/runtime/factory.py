"""Factory helpers for wiring the default agent runtime (Phase 10)."""

from __future__ import annotations

from app.ai.agent.runtime.default_agent import DefaultAgent
from app.ai.agent.scratchpad.store import ScratchpadStore
from app.ai.prompts.manager import PromptManager
from app.ai.tools.executor import ToolExecutor
from app.ai.tools.registry import ToolRegistry
from app.core.config import Settings


def create_default_agent(
    *,
    settings: Settings,
    tool_registry: ToolRegistry,
    prompt_manager: PromptManager,
    tool_executor: ToolExecutor,
    scratchpad_store: ScratchpadStore | None = None,
) -> DefaultAgent:
    """Build a :class:`DefaultAgent` with the standard runtime dependencies."""
    return DefaultAgent(
        settings=settings,
        tool_registry=tool_registry,
        prompt_manager=prompt_manager,
        tool_executor=tool_executor,
        scratchpad_store=scratchpad_store,
    )
