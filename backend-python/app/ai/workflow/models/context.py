"""Canonical ``WorkflowContext`` model (public API — stable after Phase 1)."""

from __future__ import annotations

from pydantic import BaseModel, Field, field_validator

from app.ai.workflow.models.identifiers import validate_identifier_dict_keys


class WorkflowContext(BaseModel):
    """Normalized execution context for a running workflow (Part I § WorkflowContext).

    Node executors read from and write to this object; ``ConditionEvaluator`` reads
    from it only. Persistence is the executor's responsibility, not the node's.
    """

    trigger_input: dict[str, object] = Field(default_factory=dict)
    variables: dict[str, object] = Field(default_factory=dict)
    metadata: dict[str, object] = Field(default_factory=dict)

    @field_validator("trigger_input")
    @classmethod
    def _validate_trigger_input_keys(
        cls, value: dict[str, object]
    ) -> dict[str, object]:
        return validate_identifier_dict_keys(value)
