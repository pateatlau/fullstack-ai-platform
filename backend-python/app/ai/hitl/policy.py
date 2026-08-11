"""Approval policy — single decision point for tool-call gating."""

from __future__ import annotations

from app.ai.tools.schemas import ToolDefinition


class ApprovalPolicy:
    """Stateless decision function for whether a tool requires human approval."""

    def __init__(self, *, required_tool_names: frozenset[str]) -> None:
        self._required_tool_names = required_tool_names

    def requires_approval(self, tool: ToolDefinition) -> bool:
        return tool.requires_approval or tool.name in self._required_tool_names
