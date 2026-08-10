"""Process-wide registry for workflow node plugin executors."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any


class WorkflowPluginRegistry:
    """Maps ``(plugin_id, plugin_node_type)`` to executor factories.

    Populated by ``PluginRegistrar.commit()`` in Phase 4; stub for Phase 2 wiring.
    """

    def __init__(self) -> None:
        self._executors: dict[tuple[str, str], Callable[..., Any]] = {}

    def register(
        self,
        *,
        plugin_id: str,
        node_type: str,
        executor_factory: Callable[..., Any],
    ) -> None:
        key = (plugin_id, node_type)
        if key in self._executors:
            raise ValueError(
                f"Workflow node type '{node_type}' already registered for plugin '{plugin_id}'"
            )
        self._executors[key] = executor_factory

    def get(
        self,
        plugin_id: str,
        node_type: str,
    ) -> Callable[..., Any] | None:
        return self._executors.get((plugin_id, node_type))

    def reset_for_tests(self) -> None:
        self._executors.clear()
