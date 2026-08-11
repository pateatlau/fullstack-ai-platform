"""Process-wide registry for workflow node plugin executors."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class WorkflowPluginRegistration:
    """Registered workflow node plugin entry."""

    executor_factory: Callable[..., Any]
    config_schema: dict[str, Any]


class WorkflowPluginRegistry:
    """Maps ``(plugin_id, plugin_node_type)`` to executor factories and config schemas.

    Populated by ``PluginRegistrar.commit()`` in Phase 4.
    """

    def __init__(self) -> None:
        self._entries: dict[tuple[str, str], WorkflowPluginRegistration] = {}

    def register(
        self,
        *,
        plugin_id: str,
        node_type: str,
        executor_factory: Callable[..., Any],
        config_schema: dict[str, Any] | None = None,
    ) -> None:
        key = (plugin_id, node_type)
        if key in self._entries:
            raise ValueError(
                f"Workflow node type '{node_type}' already registered for plugin '{plugin_id}'"
            )
        self._entries[key] = WorkflowPluginRegistration(
            executor_factory=executor_factory,
            config_schema=dict(config_schema or {}),
        )

    def get(
        self,
        plugin_id: str,
        node_type: str,
    ) -> Callable[..., Any] | None:
        entry = self._entries.get((plugin_id, node_type))
        return entry.executor_factory if entry is not None else None

    def get_config_schema(
        self,
        plugin_id: str,
        node_type: str,
    ) -> dict[str, Any] | None:
        entry = self._entries.get((plugin_id, node_type))
        if entry is None:
            return None
        return entry.config_schema or None

    def has(self, plugin_id: str, node_type: str) -> bool:
        return (plugin_id, node_type) in self._entries

    def unregister(self, plugin_id: str, node_type: str) -> None:
        self._entries.pop((plugin_id, node_type), None)

    def reset_for_tests(self) -> None:
        self._entries.clear()
