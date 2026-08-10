from __future__ import annotations

from app.ai.plugins.registrar import PluginRegistrar
from app.ai.tools.schemas import ToolDefinition, ToolExecutionContext, ToolResult


class _Handler:
    async def execute(
        self,
        args: dict[str, object],
        context: ToolExecutionContext,
    ) -> ToolResult:
        del args, context
        return ToolResult(success=True)


def register(registrar: PluginRegistrar) -> None:
    registrar.register_tool(
        ToolDefinition(
            name="unprefixed.echo",
            description="missing plugin_id prefix",
            parameters={"type": "object", "properties": {}},
        ),
        _Handler(),
    )
