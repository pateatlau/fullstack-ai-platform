"""Approvals router tests for cancel + multi-stage + expiry (Epic 09 recs #2/#3/#5)."""

from __future__ import annotations

import datetime
import uuid
from collections.abc import Callable, Iterator
from typing import Any

import pytest
from httpx import ASGITransport, AsyncClient

from app.ai.deps import get_agent_approval_service
from app.ai.hitl.models import ApprovalStatus, ProposedToolCall
from app.ai.hitl.service import AgentApprovalService
from app.core.config import Settings, get_settings
from app.core.security import create_access_token
from app.db.models import User
from app.main import app
from tests.ai.hitl.fakes import InMemoryApprovalStore
from tests.fakes import FakeChatStore


async def _make_user(db_session) -> User:
    user = User(
        auth_provider="google",
        external_auth_id=f"hitl-cancel-{uuid.uuid4().hex}",
        email=f"hitl-cancel-{uuid.uuid4().hex[:8]}@example.com",
    )
    db_session.add(user)
    await db_session.flush()
    return user


@pytest.fixture
def dependency_overrides() -> Iterator[Callable[[Any, Any], None]]:
    """Install FastAPI dependency overrides and remove them after each test."""
    installed: list[Any] = []

    def install(key: Any, override: Any) -> None:
        app.dependency_overrides[key] = override
        installed.append(key)

    try:
        yield install
    finally:
        for key in installed:
            app.dependency_overrides.pop(key, None)


@pytest.mark.anyio
async def test_cancel_pending_approval_returns_200(
    db_session, dependency_overrides
) -> None:
    settings = Settings(hitl_enabled=True, tools_enabled=True)

    user = await _make_user(db_session)
    store = InMemoryApprovalStore()
    chat_store = FakeChatStore()
    session = await chat_store.create_session(user_id=user.id)
    approval = await store.create(
        session_id=session.id,
        owner_id=user.id,
        execution_id="exec-cancel-router",
        approval_correlation_id=uuid.uuid4(),
        proposed_calls=[
            ProposedToolCall(name="delete_file", arguments={"path": "/x"}, call_id="c1")
        ],
        paused_scratchpad=[],
        paused_state={
            "execution_id": "exec-cancel-router",
            "status": "waiting_approval",
        },
    )
    service = AgentApprovalService(approval_store=store, chat_store=chat_store)
    dependency_overrides(get_settings, lambda: settings)
    dependency_overrides(get_agent_approval_service, lambda: service)
    token = create_access_token(user_id=user.id, settings=settings)

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.post(
            f"/api/approvals/{approval.id}/cancel",
            json={"reason": "not needed anymore"},
            headers={"Authorization": f"Bearer {token}"},
        )

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "cancelled"
    assert body["reason"] == "not needed anymore"


@pytest.mark.anyio
async def test_cancel_already_decided_returns_409(
    db_session, dependency_overrides
) -> None:
    settings = Settings(hitl_enabled=True, tools_enabled=True)

    user = await _make_user(db_session)
    store = InMemoryApprovalStore()
    chat_store = FakeChatStore()
    session = await chat_store.create_session(user_id=user.id)
    approval = await store.create(
        session_id=session.id,
        owner_id=user.id,
        execution_id="exec-cancel-conflict",
        approval_correlation_id=uuid.uuid4(),
        proposed_calls=[
            ProposedToolCall(name="delete_file", arguments={"path": "/x"}, call_id="c1")
        ],
        paused_scratchpad=[],
        paused_state={
            "execution_id": "exec-cancel-conflict",
            "status": "waiting_approval",
        },
    )
    await store.cas_decide(
        approval.id,
        owner_id=user.id,
        status=ApprovalStatus.REJECTED,
        decided_by=user.id,
    )
    service = AgentApprovalService(approval_store=store, chat_store=chat_store)
    dependency_overrides(get_settings, lambda: settings)
    dependency_overrides(get_agent_approval_service, lambda: service)
    token = create_access_token(user_id=user.id, settings=settings)

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.post(
            f"/api/approvals/{approval.id}/cancel",
            json={},
            headers={"Authorization": f"Bearer {token}"},
        )

    assert response.status_code == 409
    assert response.json()["error"]["code"] == "approval_decision_conflict"


@pytest.mark.anyio
async def test_cancel_returns_503_when_flag_off(
    db_session, dependency_overrides
) -> None:
    settings = Settings(hitl_enabled=False)
    user = await _make_user(db_session)
    dependency_overrides(get_settings, lambda: settings)
    token = create_access_token(user_id=user.id, settings=settings)

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.post(
            f"/api/approvals/{uuid.uuid4()}/cancel",
            json={},
            headers={"Authorization": f"Bearer {token}"},
        )

    assert response.status_code == 503


@pytest.mark.anyio
async def test_decide_expired_approval_returns_409_expired_code(
    db_session, dependency_overrides
) -> None:
    settings = Settings(hitl_enabled=True, tools_enabled=True)

    user = await _make_user(db_session)
    store = InMemoryApprovalStore()
    chat_store = FakeChatStore()
    session = await chat_store.create_session(user_id=user.id)
    past = datetime.datetime.now(datetime.UTC) - datetime.timedelta(hours=1)
    approval = await store.create(
        session_id=session.id,
        owner_id=user.id,
        execution_id="exec-expired-router",
        approval_correlation_id=uuid.uuid4(),
        proposed_calls=[
            ProposedToolCall(name="delete_file", arguments={"path": "/x"}, call_id="c1")
        ],
        paused_scratchpad=[],
        paused_state={
            "execution_id": "exec-expired-router",
            "status": "waiting_approval",
        },
        expires_at=past,
    )
    service = AgentApprovalService(approval_store=store, chat_store=chat_store)
    dependency_overrides(get_settings, lambda: settings)
    dependency_overrides(get_agent_approval_service, lambda: service)
    token = create_access_token(user_id=user.id, settings=settings)

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.post(
            f"/api/approvals/{approval.id}/decide",
            json={"decision": "rejected"},
            headers={"Authorization": f"Bearer {token}"},
        )

    assert response.status_code == 409
    assert response.json()["error"]["code"] == "approval_expired"


@pytest.mark.anyio
async def test_decide_intermediate_stage_returns_json_without_streaming(
    db_session,
    dependency_overrides,
) -> None:
    settings = Settings(hitl_enabled=True, tools_enabled=True)

    user = await _make_user(db_session)
    store = InMemoryApprovalStore()
    chat_store = FakeChatStore()
    session = await chat_store.create_session(user_id=user.id)
    approval = await store.create(
        session_id=session.id,
        owner_id=user.id,
        execution_id="exec-stage-router",
        approval_correlation_id=uuid.uuid4(),
        proposed_calls=[
            ProposedToolCall(name="delete_file", arguments={"path": "/x"}, call_id="c1")
        ],
        paused_scratchpad=[],
        paused_state={
            "execution_id": "exec-stage-router",
            "status": "waiting_approval",
        },
        required_stages=["manager", "security"],
    )
    service = AgentApprovalService(approval_store=store, chat_store=chat_store)
    dependency_overrides(get_settings, lambda: settings)
    dependency_overrides(get_agent_approval_service, lambda: service)
    token = create_access_token(user_id=user.id, settings=settings)

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.post(
            f"/api/approvals/{approval.id}/decide",
            json={
                "decision": "approved",
                "reason": "manager ok",
                "comments": "needs security review",
            },
            headers={"Authorization": f"Bearer {token}"},
        )

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("application/json")
    body = response.json()
    assert body["status"] == "pending"
    assert body["outstanding_stages"] == ["security"]
    assert body["comments"] == "needs security review"
    updated = await store.get(approval.id)
    assert updated is not None
    assert len(updated.stage_decisions) == 1
    assert updated.stage_decisions[0].comments == "needs security review"
