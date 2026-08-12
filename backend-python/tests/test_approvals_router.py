"""Approvals router tests (Epic 09 Phase 3 + Phase 6)."""

from __future__ import annotations

import asyncio
import datetime
import json
import uuid
from typing import Any

import pytest
from httpx import ASGITransport, AsyncClient

from app.ai.deps import get_agent_approval_service, get_approvals_store
from app.ai.hitl.models import (
    ApprovalAuditEntry,
    ApprovalKind,
    ApprovalRevision,
    ApprovalStatus,
    ProposedToolCall,
    RequestMetadata,
)
from app.ai.hitl.service import AgentApprovalService
from app.core.caller import CallerContext
from app.core.config import Settings, get_settings
from app.core.security import create_access_token
from app.db.models import User
from app.main import app
from app.routers.approvals import _stream_approved_decision
from app.schemas.approvals import ApprovalDecideRequest
from tests.ai.hitl.fakes import FakeApprovalsStore, InMemoryApprovalStore
from tests.fakes import FakeChatStore

_NOW = datetime.datetime(2026, 8, 11, 12, 0, tzinfo=datetime.UTC)


def _parse_sse_frames(payload: str) -> list[tuple[str, dict[str, Any]]]:
    frames: list[tuple[str, dict[str, Any]]] = []
    for block in payload.strip().split("\n\n"):
        if not block:
            continue
        event = next(
            line.removeprefix("event: ")
            for line in block.splitlines()
            if line.startswith("event: ")
        )
        data = next(
            line.removeprefix("data: ")
            for line in block.splitlines()
            if line.startswith("data: ")
        )
        frames.append((event, json.loads(data)))
    return frames


@pytest.mark.anyio
async def test_decide_reject_returns_json_when_flag_on(
    monkeypatch: pytest.MonkeyPatch,
    db_session,
) -> None:
    settings = Settings(hitl_enabled=True, tools_enabled=True)
    app.dependency_overrides[get_settings] = lambda: settings

    user = User(
        auth_provider="google",
        external_auth_id=f"hitl-router-{uuid.uuid4().hex}",
        email=f"hitl-router-{uuid.uuid4().hex[:8]}@example.com",
    )
    db_session.add(user)
    await db_session.flush()

    store = InMemoryApprovalStore()
    chat_store = FakeChatStore()
    session = await chat_store.create_session(user_id=user.id)
    approval = await store.create(
        session_id=session.id,
        owner_id=user.id,
        execution_id="exec-router",
        approval_correlation_id=uuid.uuid4(),
        proposed_calls=[
            ProposedToolCall(name="delete_file", arguments={"path": "/x"}, call_id="c1")
        ],
        paused_scratchpad=[],
        paused_state={"execution_id": "exec-router", "status": "waiting_approval"},
    )

    service = AgentApprovalService(
        approval_store=store,
        chat_store=chat_store,
    )
    app.dependency_overrides[get_agent_approval_service] = lambda: service
    token = create_access_token(user_id=user.id, settings=settings)

    try:
        async with AsyncClient(
            transport=ASGITransport(app=app),
            base_url="http://test",
        ) as client:
            response = await client.post(
                f"/api/approvals/{approval.id}/decide",
                json={"decision": "rejected", "reason": "nope"},
                headers={"Authorization": f"Bearer {token}"},
            )
    finally:
        app.dependency_overrides.pop(get_settings, None)
        app.dependency_overrides.pop(get_agent_approval_service, None)

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == ApprovalStatus.REJECTED.value
    assert body["reason"] == "nope"


@pytest.mark.anyio
async def test_decide_returns_503_when_flag_off(db_session) -> None:
    settings = Settings(hitl_enabled=False)
    app.dependency_overrides[get_settings] = lambda: settings

    user = User(
        auth_provider="google",
        external_auth_id=f"hitl-off-{uuid.uuid4().hex}",
        email=f"hitl-off-{uuid.uuid4().hex[:8]}@example.com",
    )
    db_session.add(user)
    await db_session.flush()
    token = create_access_token(user_id=user.id, settings=settings)

    try:
        async with AsyncClient(
            transport=ASGITransport(app=app),
            base_url="http://test",
        ) as client:
            response = await client.post(
                f"/api/approvals/{uuid.uuid4()}/decide",
                json={"decision": "rejected"},
                headers={"Authorization": f"Bearer {token}"},
            )
    finally:
        app.dependency_overrides.pop(get_settings, None)

    assert response.status_code == 503
    assert response.json()["error"]["code"] == "feature_disabled"


@pytest.mark.anyio
async def test_approve_already_decided_returns_409(
    monkeypatch: pytest.MonkeyPatch,
    db_session,
) -> None:
    settings = Settings(hitl_enabled=True, tools_enabled=True)
    app.dependency_overrides[get_settings] = lambda: settings

    user = User(
        auth_provider="google",
        external_auth_id=f"hitl-router-{uuid.uuid4().hex}",
        email=f"hitl-router-{uuid.uuid4().hex[:8]}@example.com",
    )
    db_session.add(user)
    await db_session.flush()

    store = InMemoryApprovalStore()
    chat_store = FakeChatStore()
    session = await chat_store.create_session(user_id=user.id)
    approval = await store.create(
        session_id=session.id,
        owner_id=user.id,
        execution_id="exec-router-approved",
        approval_correlation_id=uuid.uuid4(),
        proposed_calls=[
            ProposedToolCall(name="delete_file", arguments={"path": "/x"}, call_id="c1")
        ],
        paused_scratchpad=[],
        paused_state={
            "execution_id": "exec-router-approved",
            "status": "waiting_approval",
        },
    )
    await store.cas_decide(
        approval.id,
        owner_id=user.id,
        status=ApprovalStatus.REJECTED,
        decided_by=user.id,
    )

    service = AgentApprovalService(
        approval_store=store,
        chat_store=chat_store,
    )
    app.dependency_overrides[get_agent_approval_service] = lambda: service
    token = create_access_token(user_id=user.id, settings=settings)

    try:
        async with AsyncClient(
            transport=ASGITransport(app=app),
            base_url="http://test",
        ) as client:
            response = await client.post(
                f"/api/approvals/{approval.id}/decide",
                json={"decision": "approved"},
                headers={"Authorization": f"Bearer {token}"},
            )
    finally:
        app.dependency_overrides.pop(get_settings, None)
        app.dependency_overrides.pop(get_agent_approval_service, None)

    assert response.status_code == 409
    body = response.json()
    assert body["error"]["code"] == "approval_decision_conflict"


@pytest.mark.anyio
async def test_approve_stream_emits_error_frame_on_resume_failure() -> None:
    """Post-stream failures must close the SSE body with an error frame, not hang."""
    owner_id = uuid.uuid4()
    store = InMemoryApprovalStore()
    chat_store = FakeChatStore()
    session = await chat_store.create_session(user_id=owner_id)
    approval = await store.create(
        session_id=session.id,
        owner_id=owner_id,
        execution_id="exec-stream-conflict",
        approval_correlation_id=uuid.uuid4(),
        proposed_calls=[
            ProposedToolCall(name="delete_file", arguments={"path": "/x"}, call_id="c1")
        ],
        paused_scratchpad=[],
        paused_state={
            "execution_id": "exec-stream-conflict",
            "status": "waiting_approval",
        },
    )
    await store.cas_decide(
        approval.id,
        owner_id=owner_id,
        status=ApprovalStatus.REJECTED,
        decided_by=owner_id,
    )
    service = AgentApprovalService(
        approval_store=store,
        chat_store=chat_store,
    )
    caller = CallerContext.for_user(owner_id)
    settings = Settings(hitl_enabled=True, tools_enabled=True)

    class _StubAgent:
        def create_streaming_executor(self, request, publisher):  # noqa: ANN001
            return object()

    stream = _stream_approved_decision(
        approval_id=approval.id,
        body=ApprovalDecideRequest(decision="approved"),
        reason=None,
        comments=None,
        request_metadata=RequestMetadata(),
        caller=caller,
        settings=settings,
        approval_service=service,
        agent=_StubAgent(),  # type: ignore[arg-type]
        approval=approval,
        placeholder=None,
    )

    chunks: list[str] = []
    async with asyncio.timeout(1):
        async for chunk in stream:
            chunks.append(chunk)

    frames = _parse_sse_frames("".join(chunks))
    assert frames
    event, payload = frames[-1]
    assert event == "error"
    assert payload["code"] == "approval_decision_conflict"


def _agent_audit_entry(
    *,
    owner_entry_id: uuid.UUID | None = None,
    status: str = "pending",
    requested_at: datetime.datetime = _NOW,
) -> ApprovalAuditEntry:
    approval_id = owner_entry_id or uuid.uuid4()
    return ApprovalAuditEntry(
        id=approval_id,
        kind=ApprovalKind.AGENT_TOOL,
        approval_correlation_id=uuid.uuid4(),
        status=status,
        tool_calls=[
            ProposedToolCall(
                name="delete_file", arguments={"path": "/tmp/x"}, call_id="c1"
            )
        ],
        session_id=uuid.uuid4(),
        requested_at=requested_at,
        decide_url=f"/api/approvals/{approval_id}/decide",
    )


def _workflow_audit_entry(
    *,
    requested_at: datetime.datetime = _NOW,
) -> ApprovalAuditEntry:
    approval_id = uuid.uuid4()
    run_id = uuid.uuid4()
    return ApprovalAuditEntry(
        id=approval_id,
        kind=ApprovalKind.WORKFLOW_NODE,
        approval_correlation_id=approval_id,
        status="pending",
        workflow_run_id=run_id,
        workflow_node_id="approve",
        requested_at=requested_at,
        decide_url=f"/api/workflow-runs/{run_id}/nodes/{approval_id}/approve",
    )


@pytest.mark.anyio
async def test_list_approvals_merges_kinds_and_supports_filters(db_session) -> None:
    settings = Settings(hitl_enabled=True)
    app.dependency_overrides[get_settings] = lambda: settings

    user = User(
        auth_provider="google",
        external_auth_id=f"hitl-list-{uuid.uuid4().hex}",
        email=f"hitl-list-{uuid.uuid4().hex[:8]}@example.com",
    )
    db_session.add(user)
    await db_session.flush()

    agent_pending = _agent_audit_entry(status="pending", requested_at=_NOW)
    agent_approved = _agent_audit_entry(
        status="approved",
        requested_at=_NOW - datetime.timedelta(minutes=5),
    )
    workflow_pending = _workflow_audit_entry(
        requested_at=_NOW - datetime.timedelta(minutes=1)
    )
    fake_store = FakeApprovalsStore(
        entries=[agent_pending, agent_approved, workflow_pending],
    )
    app.dependency_overrides[get_approvals_store] = lambda: fake_store
    token = create_access_token(user_id=user.id, settings=settings)

    try:
        async with AsyncClient(
            transport=ASGITransport(app=app),
            base_url="http://test",
        ) as client:
            all_response = await client.get(
                "/api/approvals",
                headers={"Authorization": f"Bearer {token}"},
            )
            pending_response = await client.get(
                "/api/approvals",
                params={
                    "status": "pending",
                    "kind": "agent_tool",
                    "limit": 1,
                    "offset": 0,
                },
                headers={"Authorization": f"Bearer {token}"},
            )
            disabled_response = await client.get("/api/approvals")
    finally:
        app.dependency_overrides.pop(get_settings, None)
        app.dependency_overrides.pop(get_approvals_store, None)

    assert all_response.status_code == 200
    all_body = all_response.json()
    assert all_body["total"] == 3
    assert len(all_body["approvals"]) == 3
    assert all_body["approvals"][0]["id"] == str(agent_pending.id)
    assert all_body["approvals"][1]["id"] == str(workflow_pending.id)
    assert all_body["approvals"][2]["id"] == str(agent_approved.id)
    assert "paused_scratchpad" not in json.dumps(all_body)
    assert "paused_state" not in json.dumps(all_body)
    assert "api_key" not in json.dumps(all_body).lower()

    assert pending_response.status_code == 200
    pending_body = pending_response.json()
    assert pending_body["total"] == 1
    assert pending_body["approvals"][0]["kind"] == "agent_tool"
    assert fake_store.last_list_kwargs == {
        "status": ApprovalStatus.PENDING,
        "kind": ApprovalKind.AGENT_TOOL,
        "limit": 1,
        "offset": 0,
    }

    assert disabled_response.status_code == 401


@pytest.mark.anyio
async def test_list_and_get_approvals_return_503_when_flag_off(db_session) -> None:
    settings = Settings(hitl_enabled=False)
    app.dependency_overrides[get_settings] = lambda: settings

    user = User(
        auth_provider="google",
        external_auth_id=f"hitl-list-off-{uuid.uuid4().hex}",
        email=f"hitl-list-off-{uuid.uuid4().hex[:8]}@example.com",
    )
    db_session.add(user)
    await db_session.flush()
    token = create_access_token(user_id=user.id, settings=settings)

    try:
        async with AsyncClient(
            transport=ASGITransport(app=app),
            base_url="http://test",
        ) as client:
            list_response = await client.get(
                "/api/approvals",
                headers={"Authorization": f"Bearer {token}"},
            )
            detail_response = await client.get(
                f"/api/approvals/{uuid.uuid4()}",
                headers={"Authorization": f"Bearer {token}"},
            )
    finally:
        app.dependency_overrides.pop(get_settings, None)

    assert list_response.status_code == 503
    assert detail_response.status_code == 503


@pytest.mark.anyio
async def test_get_approval_detail_and_revisions(db_session) -> None:
    settings = Settings(hitl_enabled=True)
    app.dependency_overrides[get_settings] = lambda: settings

    user = User(
        auth_provider="google",
        external_auth_id=f"hitl-detail-{uuid.uuid4().hex}",
        email=f"hitl-detail-{uuid.uuid4().hex[:8]}@example.com",
    )
    db_session.add(user)
    await db_session.flush()

    entry = _agent_audit_entry()
    revision_one = ApprovalRevision(
        id=uuid.uuid4(),
        approval_id=entry.id,
        approval_kind=ApprovalKind.AGENT_TOOL,
        revision_number=1,
        edited_by=user.id,
        edited_at=_NOW,
        edited_payload=entry.tool_calls or [],
    )
    revision_two = ApprovalRevision(
        id=uuid.uuid4(),
        approval_id=entry.id,
        approval_kind=ApprovalKind.AGENT_TOOL,
        revision_number=2,
        edited_by=user.id,
        edited_at=_NOW + datetime.timedelta(minutes=1),
        edited_payload=entry.tool_calls or [],
    )
    fake_store = FakeApprovalsStore(
        entries=[entry],
        revisions={entry.id: [revision_two, revision_one]},
    )
    app.dependency_overrides[get_approvals_store] = lambda: fake_store
    token = create_access_token(user_id=user.id, settings=settings)

    try:
        async with AsyncClient(
            transport=ASGITransport(app=app),
            base_url="http://test",
        ) as client:
            detail = await client.get(
                f"/api/approvals/{entry.id}",
                headers={"Authorization": f"Bearer {token}"},
            )
            missing = await client.get(
                f"/api/approvals/{uuid.uuid4()}",
                headers={"Authorization": f"Bearer {token}"},
            )
            revisions = await client.get(
                f"/api/approvals/{entry.id}/revisions",
                headers={"Authorization": f"Bearer {token}"},
            )
            missing_revisions = await client.get(
                f"/api/approvals/{uuid.uuid4()}/revisions",
                headers={"Authorization": f"Bearer {token}"},
            )
    finally:
        app.dependency_overrides.pop(get_settings, None)
        app.dependency_overrides.pop(get_approvals_store, None)

    assert detail.status_code == 200
    detail_body = detail.json()
    assert detail_body["approval_correlation_id"] == str(entry.approval_correlation_id)
    assert detail_body["decide_url"] == entry.decide_url
    assert detail_body["revision_count"] == 0

    assert missing.status_code == 404

    assert revisions.status_code == 200
    revision_body = revisions.json()
    assert [item["revision_number"] for item in revision_body] == [1, 2]

    assert missing_revisions.status_code == 404


@pytest.mark.anyio
async def test_health_includes_hitl_fields(
    monkeypatch: pytest.MonkeyPatch,
    db_session,
) -> None:
    settings = Settings(hitl_enabled=True)
    app.dependency_overrides[get_settings] = lambda: settings
    fake_store = FakeApprovalsStore(pending_count=4)
    app.dependency_overrides[get_approvals_store] = lambda: fake_store

    try:
        async with AsyncClient(
            transport=ASGITransport(app=app),
            base_url="http://test",
        ) as client:
            enabled = await client.get("/api/health")
    finally:
        app.dependency_overrides.pop(get_settings, None)
        app.dependency_overrides.pop(get_approvals_store, None)

    assert enabled.status_code == 200
    body = enabled.json()
    assert body["hitl_enabled"] is True
    assert body["hitl_pending_approvals_count"] == 4
