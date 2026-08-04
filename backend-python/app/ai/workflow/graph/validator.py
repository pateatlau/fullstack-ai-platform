"""Graph validation for workflow definitions (Phase 2)."""

from __future__ import annotations

from app.ai.workflow.models import WorkflowDefinition


class GraphValidator:
    """Validates workflow graph structure before activation (Part I § GraphValidator)."""

    def validate(self, definition: WorkflowDefinition) -> None:
        """Validate a definition graph; raises ``WorkflowValidationError`` on failure."""
        del definition
        raise NotImplementedError(
            "GraphValidator.validate() is implemented in Phase 2."
        )
