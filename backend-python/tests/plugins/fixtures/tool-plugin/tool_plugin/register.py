from __future__ import annotations

from app.ai.plugins.registrar import PluginRegistrar
from app.ai.tools.schemas import ToolDefinition, ToolExecutionContext, ToolResult

TOOL_NAME = "com.test.tool.echo"
TOOL_DEFINITION = ToolDefinition(
    name=TOOL_NAME,
    description="Echo a message from plugin args",
    parameters={
        "type": "object",
        "properties": {
            "message": {"type": "string"},
        },
        "required": ["message"],
    },
)


class EchoToolHandler:
    async def execute(
        self,
        args: dict[str, object],
        context: ToolExecutionContext,
    ) -> ToolResult:
        del context
        message = args.get("message")
        if not isinstance(message, str):
            return ToolResult(
                success=False,
                error="message must be a string",
                error_code="validation_error",
            )
        return ToolResult(success=True, data={"message": message})


def register(registrar: PluginRegistrar) -> None:
    registrar.register_tool(TOOL_DEFINITION, EchoToolHandler())
