"""Dot-path field resolution for the declarative condition DSL."""

from __future__ import annotations

from app.ai.workflow.models import WorkflowContext


def resolve_field(path: str, context: WorkflowContext) -> tuple[bool, object]:
    """Resolve ``path`` against ``WorkflowContext`` without arbitrary code execution.

    Paths may start with ``trigger_input.``, ``metadata.``, or ``variables.``;
    otherwise resolution begins at accumulated node outputs (``variables``).
    Returns ``(exists, value)`` where ``exists`` is False when any segment is
    missing.
    """
    parts = [segment for segment in path.split(".") if segment]
    if not parts:
        return False, None

    root = parts[0]
    if root == "trigger_input":
        current: object = context.trigger_input
        parts = parts[1:]
    elif root == "metadata":
        current = context.metadata
        parts = parts[1:]
    elif root == "variables":
        current = context.variables
        parts = parts[1:]
    else:
        current = context.variables

    for part in parts:
        if not isinstance(current, dict) or part not in current:
            return False, None
        current = current[part]
    return True, current
