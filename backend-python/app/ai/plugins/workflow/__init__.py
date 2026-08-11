"""Workflow plugin registry (executor map populated in Phase 4)."""

from app.ai.plugins.workflow.registry import WorkflowPluginRegistry
from app.ai.plugins.workflow.plugin_node import PluginNodeExecutor

__all__ = ["PluginNodeExecutor", "WorkflowPluginRegistry"]
