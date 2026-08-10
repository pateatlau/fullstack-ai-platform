"""Shared helpers for plugin unit tests."""

from __future__ import annotations

from pathlib import Path

from app.ai.plugins.bootstrap import load_plugins as orchestrate_load_plugins
from app.ai.plugins.models import PluginLoadReport
from app.ai.plugins.registry import PluginRegistry
from app.ai.plugins.workflow.registry import WorkflowPluginRegistry
from app.ai.prompts.repository import PromptRepository
from app.ai.tools.registry import ToolRegistry
from app.core.config import Settings

FIXTURES_ROOT = Path(__file__).resolve().parents[2] / "plugins" / "fixtures"


def plugin_settings(
    *,
    enabled: bool = True,
    directories: list[str] | None = None,
    allowlist: list[str] | None = None,
    timeout_seconds: int = 30,
) -> Settings:
    return Settings(
        plugins_enabled=enabled,
        plugin_directories=directories or [str(FIXTURES_ROOT)],
        plugin_allowlist=allowlist or [],
        plugin_registration_wait_timeout_seconds=timeout_seconds,
    )


def load_plugins(
    settings: Settings,
    *,
    tool_registry: ToolRegistry | None = None,
    prompt_repository: PromptRepository | None = None,
    plugin_registry: PluginRegistry | None = None,
    workflow_plugin_registry: WorkflowPluginRegistry | None = None,
) -> tuple[PluginLoadReport, PluginRegistry, ToolRegistry, PromptRepository]:
    registry = plugin_registry or PluginRegistry()
    tools = tool_registry or ToolRegistry()
    prompts = prompt_repository or PromptRepository()
    workflow_registry = workflow_plugin_registry or WorkflowPluginRegistry()
    report = orchestrate_load_plugins(
        settings,
        tool_registry=tools,
        prompt_repository=prompts,
        plugin_registry=registry,
        workflow_plugin_registry=workflow_registry,
    )
    return report, registry, tools, prompts
