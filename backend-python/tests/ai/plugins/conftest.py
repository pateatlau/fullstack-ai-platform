"""Shared helpers for plugin unit tests."""

from __future__ import annotations

from pathlib import Path

from app.ai.plugins.loader import PluginLoader
from app.ai.plugins.models import PluginLoadReport
from app.ai.plugins.registry import PluginRegistry
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


def load_plugins(settings: Settings) -> tuple[PluginLoadReport, PluginRegistry]:
    registry = PluginRegistry()
    loader = PluginLoader(settings, registry)
    report = loader.load_all()
    return report, registry
