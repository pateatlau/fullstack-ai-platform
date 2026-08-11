from __future__ import annotations

from app.ai.plugins.registrar import PluginRegistrar
from app.ai.workflow.models import WorkflowContext, WorkflowNode
from app.ai.workflow.nodes.base import NodeExecutionRequest

PLUGIN_ID = "com.example.echo.workflow"
NODE_TYPE = "echo"


class EchoContextKeyExecutor:
    """Copies a context key into node output for reference workflow demos."""

    async def execute(
        self,
        node: WorkflowNode,
        context: WorkflowContext,
        request: NodeExecutionRequest,
    ) -> dict[str, object]:
        del request
        message_key = node.config.get("message_key", "input_text")
        if not isinstance(message_key, str):
            return {"value": None}

        value = context.variables.get(message_key)
        if value is None and isinstance(context.trigger_input, dict):
            value = context.trigger_input.get(message_key)
        return {"value": value}


def register(registrar: PluginRegistrar) -> None:
    registrar.register_workflow_node_type(
        node_type=NODE_TYPE,
        executor_factory=lambda _ctx: EchoContextKeyExecutor(),
        config_schema={
            "type": "object",
            "properties": {
                "plugin_id": {"type": "string"},
                "plugin_node_type": {"type": "string"},
                "message_key": {"type": "string"},
            },
            "required": ["plugin_id", "plugin_node_type"],
        },
    )
