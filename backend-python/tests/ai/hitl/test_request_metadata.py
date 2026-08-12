"""HITL request metadata capture and client-audit retention tests."""

from __future__ import annotations

import datetime
import uuid

import pytest
from starlette.requests import Request

from app.ai.hitl.models import ApprovalStatus, ProposedToolCall, RequestMetadata
from app.ai.hitl.request_metadata import (
    build_request_metadata,
    resolve_client_source_ip,
)
from app.core.config import Settings
from tests.ai.hitl.fakes import InMemoryApprovalStore
from tests.fakes import FakeChatStore


def _request(
    *,
    client_host: str = "203.0.113.10",
    headers: list[tuple[bytes, bytes]] | None = None,
) -> Request:
    scope = {
        "type": "http",
        "method": "POST",
        "path": "/api/approvals/x/decide",
        "headers": headers or [],
        "client": (client_host, 12345),
        "server": ("testserver", 80),
        "scheme": "http",
        "http_version": "1.1",
    }
    return Request(scope)


def test_resolve_client_source_ip_uses_direct_address_by_default() -> None:
    request = _request(client_host="203.0.113.10")

    assert resolve_client_source_ip(request, trust_forwarded=False) == "203.0.113.10"


def test_resolve_client_source_ip_uses_forwarded_header_when_trusted() -> None:
    request = _request(
        client_host="10.0.0.1",
        headers=[(b"x-forwarded-for", b"198.51.100.20, 10.0.0.1")],
    )

    assert resolve_client_source_ip(request, trust_forwarded=True) == "198.51.100.20"


def test_build_request_metadata_bounds_user_agent() -> None:
    long_agent = "A" * 700
    request = _request(headers=[(b"user-agent", long_agent.encode())])
    settings = Settings(openai_api_key="test-key", hitl_max_user_agent_length=128)

    metadata = build_request_metadata(request, settings)

    assert metadata.source_ip == "203.0.113.10"
    assert metadata.client_metadata["user_agent"] == "A" * 128


@pytest.mark.anyio
async def test_client_audit_retention_purges_stale_pending_metadata() -> None:
    owner_id = uuid.uuid4()
    store = InMemoryApprovalStore(client_audit_retention_days=90)
    chat_store = FakeChatStore()
    session = await chat_store.create_session(user_id=owner_id)
    stale_requested_at = datetime.datetime.now(datetime.UTC) - datetime.timedelta(
        days=120
    )
    approval = await store.create(
        session_id=session.id,
        owner_id=owner_id,
        execution_id="exec-retention",
        approval_correlation_id=uuid.uuid4(),
        proposed_calls=[
            ProposedToolCall(name="delete_file", arguments={"path": "/x"}, call_id="c1")
        ],
        paused_scratchpad=[],
        paused_state={"execution_id": "exec-retention", "status": "waiting_approval"},
        request_metadata=RequestMetadata(
            source_ip="198.51.100.20",
            client_metadata={"user_agent": "test-agent"},
        ),
    )
    store._replace(
        approval.model_copy(
            update={
                "requested_at": stale_requested_at,
                "created_at": stale_requested_at,
                "updated_at": stale_requested_at,
            }
        )
    )

    loaded = await store.get_for_owner(approval.id, owner_id=owner_id)

    assert loaded is not None
    assert loaded.status is ApprovalStatus.PENDING
    assert loaded.source_ip is None
    assert loaded.client_metadata == {}
    persisted = await store.get(approval.id)
    assert persisted is not None
    assert persisted.source_ip is None
    assert persisted.client_metadata == {}
