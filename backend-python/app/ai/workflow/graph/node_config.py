"""Definition-time validation for type-specific workflow node config (Phase 6)."""

from __future__ import annotations

from app.ai.workflow.exceptions import WorkflowValidationError
from app.ai.workflow.models import NodeType, WorkflowNode
from app.ai.workflow.nodes.approval_node import (
    APPROVED_EDGE_ID_KEY,
    REJECTED_EDGE_ID_KEY,
)

_FILE_TEMPLATE_PREFIX = "@"


def validate_node_configs(nodes: list[WorkflowNode]) -> None:
    """Validate per-node config shapes for supported node types."""
    for node in nodes:
        if node.type is NodeType.LLM:
            _validate_llm_node_config(node.id, node.config)
        elif node.type is NodeType.AGENT:
            _validate_agent_node_config(node.id, node.config)
        elif node.type is NodeType.APPROVAL:
            _validate_approval_node_config(node.id, node.config)


def _validate_llm_node_config(node_id: str, config: dict[str, object]) -> None:
    prompt_template = config.get("prompt_template")
    if not isinstance(prompt_template, str) or not prompt_template.strip():
        raise WorkflowValidationError(
            f"LLM node {node_id!r} requires config.prompt_template (non-empty string)."
        )

    model_override = config.get("model_override")
    if model_override is not None and (
        not isinstance(model_override, str) or not model_override.strip()
    ):
        raise WorkflowValidationError(
            f"LLM node {node_id!r} config.model_override must be a non-empty string."
        )

    if isinstance(prompt_template, str) and prompt_template.startswith(
        _FILE_TEMPLATE_PREFIX
    ):
        _parse_file_template_ref(prompt_template, node_id=node_id, node_type="LLM")


def _validate_agent_node_config(node_id: str, config: dict[str, object]) -> None:
    goal = config.get("goal")
    if not isinstance(goal, str) or not goal.strip():
        raise WorkflowValidationError(
            f"Agent node {node_id!r} requires config.goal (non-empty string)."
        )
    _reject_file_template_ref(goal, node_id=node_id, field_name="goal")

    instructions = config.get("instructions")
    if instructions is not None and (
        not isinstance(instructions, str) or not instructions.strip()
    ):
        raise WorkflowValidationError(
            f"Agent node {node_id!r} config.instructions must be a non-empty string."
        )
    if isinstance(instructions, str):
        _reject_file_template_ref(
            instructions, node_id=node_id, field_name="instructions"
        )

    tool_names = config.get("tool_names")
    if tool_names is not None:
        if not isinstance(tool_names, list) or not tool_names:
            raise WorkflowValidationError(
                f"Agent node {node_id!r} config.tool_names must be a non-empty list."
            )
        for index, name in enumerate(tool_names):
            if not isinstance(name, str) or not name.strip():
                raise WorkflowValidationError(
                    f"Agent node {node_id!r} config.tool_names[{index}] "
                    "must be a non-empty string."
                )

    max_iterations = config.get("max_iterations")
    if max_iterations is not None:
        if not isinstance(max_iterations, int) or max_iterations < 1:
            raise WorkflowValidationError(
                f"Agent node {node_id!r} config.max_iterations must be an integer >= 1."
            )

    model_override = config.get("model_override")
    if model_override is not None and (
        not isinstance(model_override, str) or not model_override.strip()
    ):
        raise WorkflowValidationError(
            f"Agent node {node_id!r} config.model_override must be a non-empty string."
        )


def _validate_approval_node_config(node_id: str, config: dict[str, object]) -> None:
    for key in (APPROVED_EDGE_ID_KEY, REJECTED_EDGE_ID_KEY):
        value = config.get(key)
        if value is None:
            continue
        if not isinstance(value, str) or not value.strip():
            raise WorkflowValidationError(
                f"Approval node {node_id!r} config.{key} must be a non-empty string."
            )


def _reject_file_template_ref(value: str, *, node_id: str, field_name: str) -> None:
    """Agent inline fields must not use ``@`` file template references."""
    if value.startswith(_FILE_TEMPLATE_PREFIX):
        raise WorkflowValidationError(
            f"Agent node {node_id!r} config.{field_name} does not support "
            "@ file template references."
        )


def _parse_file_template_ref(
    prompt_template: str,
    *,
    node_id: str,
    node_type: str,
) -> tuple[str, str, str]:
    """Parse ``@category/name/version`` file template references."""
    ref = prompt_template[len(_FILE_TEMPLATE_PREFIX) :]
    parts = ref.split("/")
    if len(parts) != 3 or not all(part.strip() for part in parts):
        raise WorkflowValidationError(
            f"{node_type} node {node_id!r} config.prompt_template file reference "
            f"must match @category/name/version; got {prompt_template!r}."
        )
    return parts[0], parts[1], parts[2]
