"""Tests for canonical Workflow domain models."""

from __future__ import annotations

import datetime
import uuid

import pytest
from pydantic import ValidationError

from app.ai.workflow.models import (
    ApprovalDecision,
    DefinitionStatus,
    NodeRetryPolicy,
    NodeStatus,
    NodeType,
    RunStatus,
    WorkflowContext,
    WorkflowDefinition,
    WorkflowEdge,
    WorkflowNode,
    WorkflowNodeExecution,
    WorkflowRun,
)

_NOW = datetime.datetime.now(datetime.UTC)


def _node(node_id: str = "start", node_type: NodeType = NodeType.TASK) -> WorkflowNode:
    return WorkflowNode(id=node_id, type=node_type, config={"tool_name": "echo"})


def _definition(**overrides: object) -> WorkflowDefinition:
    defaults: dict[str, object] = {
        "id": uuid.uuid4(),
        "owner_id": uuid.uuid4(),
        "name": "Sample Workflow",
        "entry_node_id": "start",
        "nodes": [_node()],
        "edges": [],
        "created_at": _NOW,
        "updated_at": _NOW,
    }
    defaults.update(overrides)
    return WorkflowDefinition(**defaults)  # type: ignore[arg-type]


def _run(**overrides: object) -> WorkflowRun:
    defaults: dict[str, object] = {
        "id": uuid.uuid4(),
        "workflow_definition_id": uuid.uuid4(),
        "owner_id": uuid.uuid4(),
        "idempotency_key": "run-key-1",
        "status": RunStatus.PENDING,
        "created_at": _NOW,
        "updated_at": _NOW,
    }
    defaults.update(overrides)
    return WorkflowRun(**defaults)  # type: ignore[arg-type]


class TestEnums:
    def test_node_type_values(self) -> None:
        assert NodeType.TASK == "task"
        assert NodeType.APPROVAL == "approval"
        assert NodeType.TERMINAL == "terminal"

    def test_run_status_values(self) -> None:
        assert RunStatus.WAITING_APPROVAL == "waiting_approval"
        assert RunStatus.COMPLETED == "completed"

    def test_node_status_values(self) -> None:
        assert NodeStatus.SUCCEEDED == "succeeded"
        assert NodeStatus.WAITING_APPROVAL == "waiting_approval"

    def test_definition_status_values(self) -> None:
        assert DefinitionStatus.DRAFT == "draft"
        assert DefinitionStatus.ACTIVE == "active"

    def test_approval_decision_values(self) -> None:
        assert ApprovalDecision.APPROVED == "approved"
        assert ApprovalDecision.REJECTED == "rejected"


class TestWorkflowDefinition:
    def test_valid_minimal_definition(self) -> None:
        definition = _definition()

        assert definition.status is DefinitionStatus.DRAFT
        assert definition.version == 1
        assert definition.nodes[0].id == "start"

    def test_entry_node_must_exist(self) -> None:
        with pytest.raises(ValidationError, match="entry_node_id"):
            _definition(entry_node_id="missing")

    def test_duplicate_node_ids_rejected(self) -> None:
        with pytest.raises(ValidationError, match="Duplicate node id"):
            _definition(nodes=[_node("start"), _node("start")])

    def test_edge_references_unknown_nodes(self) -> None:
        edge = WorkflowEdge(id="e1", from_node_id="start", to_node_id="missing")
        with pytest.raises(ValidationError, match="unknown to_node_id"):
            _definition(edges=[edge])

    def test_blank_name_rejected(self) -> None:
        with pytest.raises(ValidationError):
            _definition(name="   ")

    def test_invalid_node_id_rejected(self) -> None:
        with pytest.raises(ValidationError, match="node id"):
            _node("fetch-user")

    def test_invalid_entry_node_id_rejected(self) -> None:
        with pytest.raises(ValidationError, match="entry_node_id"):
            _definition(
                nodes=[_node("valid_node")],
                entry_node_id="fetch-user",
            )

    def test_serialization_round_trip(self) -> None:
        definition = _definition(
            edges=[
                WorkflowEdge(id="e1", from_node_id="start", to_node_id="start"),
            ],
            nodes=[_node("start"), _node("end", NodeType.TERMINAL)],
            entry_node_id="start",
        )

        restored = WorkflowDefinition.model_validate(definition.model_dump(mode="json"))
        assert restored == definition


class TestWorkflowRun:
    def test_idempotency_key_must_not_be_blank(self) -> None:
        with pytest.raises(ValidationError, match="idempotency_key"):
            _run(idempotency_key="   ")

    def test_context_defaults(self) -> None:
        run = _run()
        assert run.context.trigger_input == {}
        assert run.checkpoint_version == 0

    def test_serialization_round_trip(self) -> None:
        run = _run(
            context=WorkflowContext(
                trigger_input={"query": "hello"},
                variables={"start": {"result": "ok"}},
                metadata={"owner_id": str(uuid.uuid4())},
            ),
            current_node_ids=["start"],
        )

        restored = WorkflowRun.model_validate(run.model_dump(mode="json"))
        assert restored == run


class TestWorkflowNodeExecution:
    def test_defaults(self) -> None:
        execution = WorkflowNodeExecution(
            id=uuid.uuid4(),
            run_id=uuid.uuid4(),
            node_id="start",
            node_type=NodeType.TASK,
            status=NodeStatus.PENDING,
        )

        assert execution.attempt == 1
        assert execution.input == {}
        assert execution.decision is None

    def test_serialization_round_trip(self) -> None:
        execution = WorkflowNodeExecution(
            id=uuid.uuid4(),
            run_id=uuid.uuid4(),
            node_id="approve",
            node_type=NodeType.APPROVAL,
            status=NodeStatus.WAITING_APPROVAL,
            decision=ApprovalDecision.APPROVED,
            decided_by=uuid.uuid4(),
            decided_at=_NOW,
        )

        restored = WorkflowNodeExecution.model_validate(
            execution.model_dump(mode="json")
        )
        assert restored == execution


class TestWorkflowContext:
    def test_defaults_are_empty(self) -> None:
        context = WorkflowContext()

        assert context.trigger_input == {}
        assert context.variables == {}
        assert context.metadata == {}

    def test_invalid_trigger_input_key_rejected(self) -> None:
        with pytest.raises(ValidationError, match="trigger_input key"):
            WorkflowContext(trigger_input={"fetch-user": "value"})

    def test_nested_trigger_input_keys_validated(self) -> None:
        with pytest.raises(ValidationError, match="trigger_input.nested key"):
            WorkflowContext(trigger_input={"nested": {"bad-key": 1}})

    def test_serialization_round_trip(self) -> None:
        context = WorkflowContext(
            trigger_input={"input": 1},
            variables={"node_a": {"value": "x"}},
            metadata={"session_id": "abc"},
        )

        restored = WorkflowContext.model_validate(context.model_dump(mode="json"))
        assert restored == context


class TestNodeRetryPolicy:
    def test_defaults(self) -> None:
        policy = NodeRetryPolicy()

        assert policy.max_retries == 3
        assert policy.base_delay_seconds == 1.0

    def test_negative_retries_rejected(self) -> None:
        with pytest.raises(ValidationError):
            NodeRetryPolicy(max_retries=-1)
