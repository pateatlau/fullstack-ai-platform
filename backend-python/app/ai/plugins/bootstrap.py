"""Startup orchestration for plugin loading."""

from __future__ import annotations

from app.ai.plugins.loader import PluginLoader
from app.ai.plugins.models import PluginLoadReport
from app.ai.plugins.registry import PluginRegistry
from app.ai.plugins.workflow.registry import WorkflowPluginRegistry
from app.ai.prompts.repository import PromptRepository
from app.ai.tools.registry import ToolRegistry
from app.core.config import Settings


def load_plugins(
    settings: Settings,
    *,
    tool_registry: ToolRegistry,
    prompt_repository: PromptRepository | None = None,
    workflow_plugin_registry: WorkflowPluginRegistry | None = None,
    plugin_registry: PluginRegistry | None = None,
) -> PluginLoadReport:
    """Discover and load plugins into platform registries when ``PLUGINS_ENABLED``."""
    registry = plugin_registry or PluginRegistry()
    loader = PluginLoader(
        settings,
        registry,
        tool_registry=tool_registry,
        prompt_repository=prompt_repository,
        workflow_plugin_registry=workflow_plugin_registry,
    )
    return loader.load_all()
