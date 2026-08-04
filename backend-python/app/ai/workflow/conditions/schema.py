"""Declarative condition DSL shape validation (Phase 2 hook for Phase 4 evaluator)."""

from __future__ import annotations

from app.ai.workflow.exceptions import WorkflowValidationError

CONDITION_OPERATORS = frozenset(
    {"eq", "neq", "gt", "gte", "lt", "lte", "in", "contains", "exists"}
)
_COMPOSITION_KEYS = frozenset({"all", "any"})
_LEAF_KEYS = frozenset({"field", "operator", "value"})


def validate_condition_shape(
    condition: dict[str, object], *, path: str = "condition"
) -> None:
    """Validate a condition DSL dict without evaluating it.

    Supports leaf conditions ``{field, operator, value?}`` and composition via
    ``all`` / ``any`` arrays of nested conditions (Part I § Conditional routing).
    """
    if not isinstance(condition, dict):
        raise WorkflowValidationError(f"{path} must be an object.")

    composition_keys = _COMPOSITION_KEYS.intersection(condition.keys())
    if composition_keys:
        if len(composition_keys) != 1:
            raise WorkflowValidationError(
                f"{path} must use exactly one of 'all' or 'any' for composition."
            )
        if condition.keys() - _COMPOSITION_KEYS:
            raise WorkflowValidationError(
                f"{path} cannot mix composition keys with leaf condition fields."
            )
        key = next(iter(composition_keys))
        children = condition[key]
        if not isinstance(children, list) or not children:
            raise WorkflowValidationError(
                f"{path}.{key} must be a non-empty array of conditions."
            )
        for index, child in enumerate(children):
            if not isinstance(child, dict):
                raise WorkflowValidationError(
                    f"{path}.{key}[{index}] must be an object."
                )
            validate_condition_shape(child, path=f"{path}.{key}[{index}]")
        return

    unknown_keys = set(condition.keys()) - _LEAF_KEYS
    if unknown_keys:
        sorted_keys = ", ".join(sorted(unknown_keys))
        raise WorkflowValidationError(
            f"{path} contains unsupported keys: {sorted_keys}."
        )

    field = condition.get("field")
    if not isinstance(field, str) or not field.strip():
        raise WorkflowValidationError(f"{path}.field must be a non-empty string.")

    operator = condition.get("operator")
    if not isinstance(operator, str) or operator not in CONDITION_OPERATORS:
        supported = ", ".join(sorted(CONDITION_OPERATORS))
        raise WorkflowValidationError(f"{path}.operator must be one of: {supported}.")

    if operator == "exists":
        return

    if "value" not in condition:
        raise WorkflowValidationError(
            f"{path}.value is required for operator {operator!r}."
        )
