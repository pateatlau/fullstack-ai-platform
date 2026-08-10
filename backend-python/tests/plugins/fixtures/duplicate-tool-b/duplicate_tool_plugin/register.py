from __future__ import annotations

from app.ai.plugins.registrar import PluginRegistrar
from app.ai.tools.schemas import ToolDefinition, ToolExecutionContext, ToolResult

SHARED_TOOL_NAME = "com.test.shared"


class _Handler:
    async def execute(
        self,
        args: dict[str, object],
        context: ToolExecutionContext,
    ) -> ToolResult:
        del args, context
        return ToolResult(success=True, data="second")


def register(registrar: PluginRegistrar) -> None:
    registrar.register_tool(
        ToolDefinition(
            name=SHARED_TOOL_NAME,
            description="second registrant",
            parameters={"type": "object", "properties": {}},
        ),
        _Handler(),
    )
