"""HITL model serialization round-trip tests (Epic 09 Phase 1)."""

from __future__ import annotations

import datetime
import uuid

import pytest

from app.ai.hitl import (
    AgentToolApproval,
    ApprovalAuditEntry,
    ApprovalKind,
    ApprovalResult,
    ApprovalRevision,
    ApprovalStatus,
    ProposedToolCall,
)
from app.ai.hitl.exceptions import HitlError
from app.ai.tools.registry import ToolRegistry
from app.ai.tools.schemas import ToolDefinition
from app.ai.tools.stubs.echo import ECHO_TOOL_DEFINITION, echo_handler
from app.core.config import Settings, get_settings


class TestProposedToolCall:
    def test_round_trip(self) -> None:
        call = ProposedToolCall(
            name="delete_file",
            arguments={"path": "/tmp/x"},
            call_id="call-1",
        )
        restored = ProposedToolCall.model_validate_json(call.model_dump_json())
        assert restored == call


class TestAgentToolApproval:
    def test_round_trip(self) -> None:
        now = datetime.datetime(2026, 8, 11, 12, 0, tzinfo=datetime.UTC)
        approval_id = uuid.uuid4()
        payload = AgentToolApproval(
            id=approval_id,
            session_id=uuid.uuid4(),
            owner_id=uuid.uuid4(),
            execution_id="exec-1",
            approval_correlation_id=uuid.uuid4(),
            status=ApprovalStatus.PENDING,
            proposed_calls=[
                ProposedToolCall(name="echo", arguments={"message": "hi"}, call_id="c1")
            ],
            edited_calls=None,
            reason=None,
            paused_scratchpad=[{"role": "assistant", "content": "thinking"}],
            paused_state={"step": 1},
            pending_message_id=None,
            requested_at=now,
            decided_at=None,
            decided_by=None,
            created_at=now,
            updated_at=now,
        )
        restored = AgentToolApproval.model_validate_json(payload.model_dump_json())
        assert restored == payload


class TestApprovalRevision:
    def test_agent_tool_round_trip(self) -> None:
        now = datetime.datetime(2026, 8, 11, 12, 0, tzinfo=datetime.UTC)
        payload = ApprovalRevision(
            id=uuid.uuid4(),
            approval_id=uuid.uuid4(),
            approval_kind=ApprovalKind.AGENT_TOOL,
            revision_number=1,
            edited_by=uuid.uuid4(),
            edited_at=now,
            edited_payload=[
                ProposedToolCall(
                    name="echo", arguments={"message": "edited"}, call_id="c1"
                )
            ],
            note="tweaked args",
        )
        restored = ApprovalRevision.model_validate_json(payload.model_dump_json())
        assert restored == payload

    def test_workflow_node_round_trip(self) -> None:
        now = datetime.datetime(2026, 8, 11, 12, 0, tzinfo=datetime.UTC)
        payload = ApprovalRevision(
            id=uuid.uuid4(),
            approval_id=uuid.uuid4(),
            approval_kind=ApprovalKind.WORKFLOW_NODE,
            revision_number=1,
            edited_by=uuid.uuid4(),
            edited_at=now,
            edited_payload={"amount": 100},
            note=None,
        )
        restored = ApprovalRevision.model_validate_json(payload.model_dump_json())
        assert restored == payload


class TestApprovalResult:
    def test_round_trip(self) -> None:
        now = datetime.datetime(2026, 8, 11, 12, 0, tzinfo=datetime.UTC)
        correlation_id = uuid.uuid4()
        payload = ApprovalResult(
            approval_id=uuid.uuid4(),
            approval_kind=ApprovalKind.AGENT_TOOL,
            status=ApprovalStatus.APPROVED,
            edited=True,
            final_payload=[
                ProposedToolCall(name="echo", arguments={"message": "ok"}, call_id="c1")
            ],
            reason="looks good",
            approver=uuid.uuid4(),
            decided_at=now,
            approval_correlation_id=correlation_id,
        )
        restored = ApprovalResult.model_validate_json(payload.model_dump_json())
        assert restored == payload


class TestApprovalAuditEntry:
    def test_round_trip(self) -> None:
        now = datetime.datetime(2026, 8, 11, 12, 0, tzinfo=datetime.UTC)
        payload = ApprovalAuditEntry(
            id=uuid.uuid4(),
            kind=ApprovalKind.WORKFLOW_NODE,
            approval_correlation_id=uuid.uuid4(),
            status="pending",
            tool_calls=None,
            workflow_run_id=uuid.uuid4(),
            workflow_node_id="approval-1",
            session_id=None,
            requested_at=now,
            decided_at=None,
            decided_by=None,
            decision=None,
            reason=None,
            edited=False,
            revision_count=0,
            decide_url="/api/workflow-runs/r/nodes/n/approve",
        )
        restored = ApprovalAuditEntry.model_validate_json(payload.model_dump_json())
        assert restored == payload


class TestApprovalStatusValues:
    @pytest.mark.parametrize(
        "status",
        [
            ApprovalStatus.PENDING,
            ApprovalStatus.APPROVED,
            ApprovalStatus.REJECTED,
            ApprovalStatus.EXPIRED,
            ApprovalStatus.CANCELLED,
        ],
    )
    def test_all_status_values_serialize(self, status: ApprovalStatus) -> None:
        assert ApprovalStatus(status.value) == status


class TestPublicApiExports:
    def test_import_surface(self) -> None:
        from app.ai import hitl
        from app.ai.hitl.exceptions import (
            ApprovalDecisionConflictError,
            ApprovalNotFoundError,
            ApprovalValidationError,
        )

        assert hitl.ApprovalPolicy is not None
        assert issubclass(hitl.ApprovalNotFoundError, HitlError)
        assert issubclass(hitl.ApprovalDecisionConflictError, HitlError)
        assert issubclass(hitl.ApprovalValidationError, HitlError)
        assert issubclass(ApprovalNotFoundError, HitlError)
        assert issubclass(ApprovalDecisionConflictError, HitlError)
        assert issubclass(ApprovalValidationError, HitlError)


class TestToolDefinitionRequiresApproval:
    def test_default_is_false(self) -> None:
        tool = ToolDefinition(
            name="echo",
            description="echo",
            parameters={"type": "object", "properties": {}},
        )
        assert tool.requires_approval is False

    def test_not_exposed_in_llm_schemas(self) -> None:
        registry = ToolRegistry()
        sensitive = ToolDefinition(
            name="delete_file",
            description="delete a file",
            parameters={"type": "object", "properties": {}},
            requires_approval=True,
        )
        registry.register(sensitive, echo_handler())
        registry.register(ECHO_TOOL_DEFINITION, echo_handler())

        schemas = registry.get_schemas_for_llm()
        for schema in schemas:
            function = schema["function"]
            assert "requires_approval" not in function
            assert set(function.keys()) == {"name", "description", "parameters"}


class TestHitlSettings:
    def test_defaults(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("HITL_ENABLED", raising=False)
        monkeypatch.delenv("HITL_REQUIRED_TOOL_NAMES", raising=False)
        monkeypatch.delenv("HITL_APPROVAL_TIMEOUT_HOURS", raising=False)
        monkeypatch.delenv("HITL_MAX_REASON_LENGTH", raising=False)
        get_settings.cache_clear()
        settings = Settings()
        assert settings.hitl_enabled is False
        assert settings.hitl_required_tool_names == []
        assert settings.hitl_approval_timeout_hours == 0
        assert settings.hitl_max_reason_length == 2000
