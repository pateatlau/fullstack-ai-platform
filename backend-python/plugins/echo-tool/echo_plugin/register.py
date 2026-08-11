from __future__ import annotations

from app.ai.plugins.registrar import PluginRegistrar
from app.ai.tools.schemas import ToolDefinition, ToolExecutionContext, ToolResult

PLUGIN_ID = "com.example.echo"
TOOL_NAME = f"{PLUGIN_ID}.ping"
TOOL_DEFINITION = ToolDefinition(
    name=TOOL_NAME,
    description="Echo a message back from the reference plugin tool.",
    parameters={
        "type": "object",
        "properties": {
            "message": {"type": "string"},
        },
        "required": ["message"],
    },
)


class EchoPingToolHandler:
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
    registrar.register_tool(TOOL_DEFINITION, EchoPingToolHandler())
    registrar.register_prompt_template(
        name="greeting",
        version="1",
        source="Hello {{ user_name }}!",
    )
    registrar.register_prompt_template(
        name="farewell",
        version="1",
        path="templates/farewell.v1.j2",
    )
