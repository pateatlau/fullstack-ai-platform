"""Tool registry: register, lookup, and expose LLM-compatible schemas."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from app.ai.interfaces.tool_handler import ToolHandler
from app.ai.tools.schemas import ToolDefinition


class ToolAlreadyRegisteredError(ValueError):
    """Raised when registering a tool whose name is already in the registry."""


@dataclass(frozen=True)
class _RegisteredTool:
    definition: ToolDefinition
    handler: ToolHandler


class ToolRegistry:
    """In-memory registry of tool definitions and their handlers."""

    def __init__(self) -> None:
        self._tools: dict[str, _RegisteredTool] = {}

    def register(self, tool: ToolDefinition, handler: ToolHandler) -> None:
        if tool.name in self._tools:
            raise ToolAlreadyRegisteredError(
                f"Tool '{tool.name}' is already registered"
            )
        self._tools[tool.name] = _RegisteredTool(definition=tool, handler=handler)

    def unregister(self, name: str) -> None:
        """Remove a tool registration (used for atomic plugin commit rollback)."""
        self._tools.pop(name, None)

    def get(self, name: str) -> ToolDefinition | None:
        registered = self._tools.get(name)
        return registered.definition if registered is not None else None

    def list_tools(self) -> list[ToolDefinition]:
        return [entry.definition for entry in self._tools.values()]

    def get_handler(self, name: str) -> ToolHandler | None:
        registered = self._tools.get(name)
        return registered.handler if registered is not None else None

    def get_schemas_for_llm(self) -> list[dict[str, Any]]:
        return [
            {
                "type": "function",
                "function": {
                    "name": entry.definition.name,
                    "description": entry.definition.description,
                    "parameters": entry.definition.parameters,
                },
            }
            for entry in self._tools.values()
        ]
