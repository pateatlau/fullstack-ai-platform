"""Startup orchestration for plugin loading."""

from __future__ import annotations

import logging

from app.ai.plugins.loader import PluginLoader
from app.ai.plugins.models import (
    PluginContributionKind,
    PluginLoadReport,
    PluginRecord,
    PluginStatus,
)
from app.ai.plugins.registry import PluginRegistry
from app.ai.plugins.workflow.registry import WorkflowPluginRegistry
from app.ai.prompts.repository import PromptRepository
from app.ai.tools.registry import ToolRegistry
from app.core.config import Settings

logger = logging.getLogger(__name__)


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
    report = loader.load_all()
    if settings.plugins_enabled:
        _log_plugin_load_report(report)
    return report


def _log_plugin_load_report(report: PluginLoadReport) -> None:
    contribution_counts = _contribution_counts(report.records)
    logger.info(
        "Plugin load complete",
        extra={
            "plugins_loaded": report.loaded_count,
            "plugins_failed": report.failed_count,
            "total_load_duration_ms": report.total_load_duration_ms,
            "tool_contributions": contribution_counts[
                PluginContributionKind.TOOL.value
            ],
            "prompt_contributions": contribution_counts[
                PluginContributionKind.PROMPT.value
            ],
            "workflow_contributions": contribution_counts[
                PluginContributionKind.WORKFLOW_NODE.value
            ],
            "mcp_contributions": contribution_counts[
                PluginContributionKind.MCP_SERVER.value
            ],
        },
    )
    for record in report.records:
        extra: dict[str, object] = {
            "plugin_id": record.plugin_id,
            "status": record.status.value,
            "load_duration_ms": record.load_duration_ms,
        }
        if record.contributions:
            extra["contributions"] = [kind.value for kind in record.contributions]
        if record.failure is not None:
            extra["failure_code"] = record.failure.code
        logger.info("Plugin load record", extra=extra)


def _contribution_counts(
    records: list[PluginRecord],
) -> dict[str, int]:
    counts = {kind.value: 0 for kind in PluginContributionKind}
    for record in records:
        if record.status != PluginStatus.LOADED:
            continue
        for kind in record.contributions:
            counts[kind.value] += 1
    return counts
