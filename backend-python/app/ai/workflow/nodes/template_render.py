"""Shared Jinja rendering helpers for workflow node executors."""

from __future__ import annotations

from jinja2 import Environment, StrictUndefined
from jinja2.exceptions import UndefinedError

from app.ai.prompts.exceptions import PromptRenderError
from app.ai.prompts.manager import PromptManager
from app.ai.workflow.graph.node_config import _parse_file_template_ref
from app.ai.workflow.models import WorkflowContext
from app.ai.workflow.nodes.base import WorkflowNodeExecutionError

_FILE_TEMPLATE_PREFIX = "@"
_INLINE_ENV = Environment(autoescape=False, undefined=StrictUndefined)


def render_prompt_template(
    prompt_template: str,
    context: WorkflowContext,
    *,
    prompt_manager: PromptManager,
    node_id: str,
) -> str:
    """Render an inline or file-backed prompt template against workflow context."""
    render_variables = _prompt_render_scope(context)
    if prompt_template.startswith(_FILE_TEMPLATE_PREFIX):
        category, name, version = _parse_file_template_ref(
            prompt_template, node_id=node_id, node_type="LLM"
        )
        try:
            return prompt_manager.render(category, name, version, render_variables)
        except PromptRenderError as exc:
            raise WorkflowNodeExecutionError(
                str(exc), error_code="invalid_config"
            ) from exc

    return _render_inline(prompt_template, render_variables, node_id=node_id)


def render_inline_template(
    template: str,
    context: WorkflowContext,
    *,
    node_id: str,
    field_name: str,
) -> str:
    """Render an inline Jinja template string (no ``@`` file references)."""
    if template.startswith(_FILE_TEMPLATE_PREFIX):
        raise WorkflowNodeExecutionError(
            f"Agent node {node_id!r} config.{field_name} does not support "
            "@ file template references.",
            error_code="invalid_config",
        )
    return _render_inline(
        template,
        _prompt_render_scope(context),
        node_id=node_id,
        field_name=field_name,
    )


def _render_inline(
    template: str,
    render_variables: dict[str, object],
    *,
    node_id: str,
    field_name: str = "prompt_template",
) -> str:
    compiled = _INLINE_ENV.from_string(template)
    try:
        return compiled.render(**render_variables)
    except UndefinedError as exc:
        raise WorkflowNodeExecutionError(
            f"Failed to render {field_name} for node {node_id!r}: {exc}",
            error_code="invalid_config",
        ) from exc


def _prompt_render_scope(context: WorkflowContext) -> dict[str, object]:
    return {
        "trigger_input": context.trigger_input,
        "variables": context.variables,
    }
