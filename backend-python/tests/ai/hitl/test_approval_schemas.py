"""Approval REST schema contract tests."""

from __future__ import annotations

import datetime
import uuid

from app.ai.hitl.models import ApprovalAuditEntry, ApprovalKind, StageDecision
from app.schemas.approvals import ApprovalAuditEntryResponse, StageDecisionResponse

_NOW = datetime.datetime(2026, 8, 11, 12, 0, tzinfo=datetime.UTC)


def test_stage_decision_response_exposes_comments() -> None:
    owner_id = uuid.uuid4()
    entry = StageDecision(
        stage="manager",
        decision="approved",
        decided_by=owner_id,
        decided_at=_NOW,
        reason="looks fine",
        comments="needs security review",
    )

    response = StageDecisionResponse.from_domain(entry)

    assert response.comments == "needs security review"
    assert response.reason == "looks fine"


def test_audit_entry_response_maps_stage_decision_comments() -> None:
    owner_id = uuid.uuid4()
    approval_id = uuid.uuid4()
    stage = StageDecision(
        stage="manager",
        decision="approved",
        decided_by=owner_id,
        decided_at=_NOW,
        comments="needs security review",
    )
    audit_entry = ApprovalAuditEntry(
        id=approval_id,
        kind=ApprovalKind.AGENT_TOOL,
        approval_correlation_id=uuid.uuid4(),
        status="pending",
        requested_at=_NOW,
        decide_url=f"/api/approvals/{approval_id}/decide",
        stage_decisions=[stage],
    )

    response = ApprovalAuditEntryResponse.from_domain(audit_entry)

    assert len(response.stage_decisions) == 1
    assert response.stage_decisions[0].comments == "needs security review"
