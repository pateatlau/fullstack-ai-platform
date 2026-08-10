"""Execution context passed to workflow plugin executor factories."""

from __future__ import annotations

from dataclasses import dataclass

from app.core.config import Settings


@dataclass(frozen=True)
class WorkflowPluginExecutorContext:
    """Context supplied when a plugin executor factory is invoked at runtime."""

    settings: Settings
