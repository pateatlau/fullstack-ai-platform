"""Task node executor: executes a tool call via the existing ``ToolExecutor``.

Part I § Task Node — reuses the tool platform's validation, authorization,
execution, and normalization unchanged; no tool logic is duplicated here.
"""

from __future__ import annotations

import re

from app.ai.tools.executor import ToolExecutor
from app.ai.tools.schemas import ToolCall, ToolExecutionContext
from app.ai.workflow.models import WorkflowContext, WorkflowNode
from app.ai.workflow.models.identifiers import IDENTIFIER_SEGMENT
from app.ai.workflow.nodes.base import NodeExecutionRequest, WorkflowNodeExecutionError
from app.core.caller import CallerContext

#: Matches a whole-string placeholder such as ``"{{trigger_input.topic}}"``.
_PLACEHOLDER = re.compile(
    rf"^\{{\{{\s*({IDENTIFIER_SEGMENT}(?:\.{IDENTIFIER_SEGMENT})*)\s*\}}\}}$"
)


class TaskNodeExecutor:
    """Executes a single registered tool call for a ``task`` node."""

    def __init__(self, tool_executor: ToolExecutor) -> None:
        self._tool_executor = tool_executor

    async def execute(
        self,
        node: WorkflowNode,
        context: WorkflowContext,
        request: NodeExecutionRequest,
    ) -> dict[str, object]:
        tool_name = node.config.get("tool_name")
        if not isinstance(tool_name, str) or not tool_name.strip():
            raise WorkflowNodeExecutionError(
                f"Task node {node.id!r} requires config.tool_name.",
                error_code="invalid_config",
            )

        arguments_template = node.config.get("arguments_template", {})
        if not isinstance(arguments_template, dict):
            raise WorkflowNodeExecutionError(
                f"Task node {node.id!r} config.arguments_template must be an object.",
                error_code="invalid_config",
            )

        arguments = _resolve_template(arguments_template, context)
        tool_context = ToolExecutionContext(
            caller=CallerContext.for_user(request.owner_id),
            execution_receipt_id=request.execution_receipt_id,
        )
        result = await self._tool_executor.execute(
            ToolCall(name=tool_name, arguments=arguments), tool_context
        )

        if not result.success:
            raise WorkflowNodeExecutionError(
                result.error or f"Tool {tool_name!r} execution failed.",
                error_code=result.error_code,
            )

        return result.model_dump(mode="json")


def _resolve_template(
    template: dict[str, object], context: WorkflowContext
) -> dict[str, object]:
    """Resolve ``{{dot.path}}`` string placeholders against trigger input/variables.

    Only whole-string placeholders are substituted, and only with values
    already present in ``WorkflowContext`` — no arbitrary code evaluation
    (Part I § Design Principles). Non-string and non-placeholder values pass
    through unchanged.
    """
    scope: dict[str, object] = {
        "trigger_input": context.trigger_input,
        "variables": context.variables,
    }
    return {key: _resolve_value(value, scope) for key, value in template.items()}


def _resolve_value(value: object, scope: dict[str, object]) -> object:
    if isinstance(value, str):
        match = _PLACEHOLDER.match(value)
        return _resolve_path(match.group(1), scope) if match is not None else value
    if isinstance(value, dict):
        return {key: _resolve_value(item, scope) for key, item in value.items()}
    if isinstance(value, list):
        return [_resolve_value(item, scope) for item in value]
    return value


def _resolve_path(path: str, scope: dict[str, object]) -> object:
    current: object = scope
    for part in path.split("."):
        if not isinstance(current, dict) or part not in current:
            raise WorkflowNodeExecutionError(
                f"Unresolved arguments_template placeholder {{{{{path}}}}}.",
                error_code="invalid_config",
            )
        current = current[part]
    return current
