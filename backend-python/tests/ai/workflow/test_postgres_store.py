"""Tests for PostgresWorkflowStore helpers."""

from __future__ import annotations

import datetime
import uuid

import pytest

from app.ai.workflow.exceptions import WorkflowValidationError
from app.ai.workflow.providers.postgres import _definition_to_domain
from app.db.models import WorkflowDefinitionRecord

_NOW = datetime.datetime.now(datetime.UTC)


def _record(*, graph: dict[str, object]) -> WorkflowDefinitionRecord:
    return WorkflowDefinitionRecord(
        id=uuid.uuid4(),
        owner_id=uuid.uuid4(),
        name="Test Workflow",
        description=None,
        version=1,
        status="draft",
        entry_node_id="start",
        graph=graph,
        metadata_json={},
        created_at=_NOW,
        updated_at=_NOW,
    )


def test_definition_to_domain_rejects_malformed_node_entry() -> None:
    row = _record(
        graph={
            "nodes": [{"id": "start", "type": "task"}, "not-a-node"],
            "edges": [],
        }
    )

    with pytest.raises(WorkflowValidationError):
        _definition_to_domain(row)


def test_definition_to_domain_rejects_non_list_nodes() -> None:
    row = _record(graph={"nodes": "invalid", "edges": []})

    with pytest.raises(WorkflowValidationError, match="nodes must be a list"):
        _definition_to_domain(row)
