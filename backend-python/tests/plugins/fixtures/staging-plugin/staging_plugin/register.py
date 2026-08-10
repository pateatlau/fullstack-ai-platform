from __future__ import annotations

from app.ai.plugins.registrar import PluginRegistrar
from app.ai.tools.schemas import ToolDefinition, ToolExecutionContext, ToolResult


class _EchoHandler:
    async def execute(
        self,
        args: dict[str, object],
        context: ToolExecutionContext,
    ) -> ToolResult:
        del args, context
        return ToolResult(success=True, data="ok")


def register(registrar: PluginRegistrar) -> None:
    registrar.register_tool(
        ToolDefinition(
            name="com.test.staging.echo",
            description="echo",
            parameters={"type": "object", "properties": {}},
        ),
        _EchoHandler(),
    )
    registrar.register_prompt_template(
        name="greeting",
        version="1",
        source="Hello {{ name }}!",
    )
