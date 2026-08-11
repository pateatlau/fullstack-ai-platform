"""Approvals router tests (Epic 09 Phase 3)."""

from __future__ import annotations

import asyncio
import uuid

import pytest
from httpx import ASGITransport, AsyncClient

from app.ai.deps import get_agent_approval_service
from app.ai.hitl.models import ApprovalStatus, ProposedToolCall
from app.ai.hitl.service import AgentApprovalService
from app.core.caller import CallerContext
from app.core.config import Settings, get_settings
from app.core.errors import AppError
from app.core.security import create_access_token
from app.db.models import User
from app.main import app
from app.routers.approvals import _stream_approved_decision
from app.schemas.approvals import ApprovalDecideRequest
from tests.ai.hitl.fakes import InMemoryApprovalStore
from tests.fakes import FakeChatStore


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
async def test_approve_stream_closes_publisher_on_cas_conflict() -> None:
    """SSE generator must not hang when approve_and_resume fails before executor runs."""
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
        caller=caller,
        settings=settings,
        approval_service=service,
        agent=_StubAgent(),  # type: ignore[arg-type]
    )

    with pytest.raises(AppError) as exc_info:
        async with asyncio.timeout(1):
            async for _ in stream:
                pass

    assert exc_info.value.status_code == 409
