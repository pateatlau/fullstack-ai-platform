"""Plugin architecture — public SDK surface (stable after Phase 1)."""

from __future__ import annotations

from app.ai.plugins.bootstrap import load_plugins
from app.ai.plugins.constants import PLUGIN_API_VERSION, SUPPORTED_PLUGIN_API_VERSIONS
from app.ai.plugins.exceptions import (
    PluginError,
    PluginLoadError,
    PluginManifestError,
    PluginRegistrationError,
)
from app.ai.plugins.loader import PluginLoader
from app.ai.plugins.manifest import PluginManifest
from app.ai.plugins.models import (
    PluginContributionKind,
    PluginDependency,
    PluginLoadFailureReason,
    PluginLoadReport,
    PluginRecord,
    PluginStatus,
)
from app.ai.plugins.registrar import PluginRegistrar
from app.ai.plugins.registry import PluginRegistry
from app.ai.plugins.workflow.plugin_node import PluginNodeExecutor
from app.ai.plugins.workflow.registry import WorkflowPluginRegistry

__all__ = [
    "PLUGIN_API_VERSION",
    "SUPPORTED_PLUGIN_API_VERSIONS",
    "PluginContributionKind",
    "PluginDependency",
    "PluginError",
    "PluginLoadError",
    "PluginLoadFailureReason",
    "PluginLoadReport",
    "PluginLoader",
    "PluginManifest",
    "PluginManifestError",
    "PluginRecord",
    "PluginRegistrar",
    "PluginRegistrationError",
    "PluginRegistry",
    "PluginStatus",
    "PluginNodeExecutor",
    "WorkflowPluginRegistry",
    "load_plugins",
]
