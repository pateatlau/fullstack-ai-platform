from __future__ import annotations

import datetime
import uuid
from collections.abc import Iterator
from unittest.mock import AsyncMock

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.ai.security.errors import SecurityErrorCode
from app.ai.security.rbac.models import AuthorizationDecision
from app.ai.security.rbac.service import RbacService
from app.ai.security.rbac.store import PostgresRoleStore
from app.core.config import get_settings
from app.core.errors import register_exception_handlers
from app.core.security import create_access_token
from app.db.identity import SqlUserStore
from app.routers import security as security_router
from app.schemas.security import SecurityAuditEntryResponse


@pytest.fixture(autouse=True)
def _clear_settings_after_monkeypatch(
    monkeypatch: pytest.MonkeyPatch,
) -> Iterator[None]:
    yield
    monkeypatch.undo()
    get_settings.cache_clear()


def _auth_headers(user_id: uuid.UUID) -> dict[str, str]:
    token = create_access_token(user_id=user_id, settings=get_settings())
    return {"Authorization": f"Bearer {token}"}


def _build_test_app() -> FastAPI:
    test_app = FastAPI()
    test_app.include_router(security_router.router)
    register_exception_handlers(test_app)
    return test_app


async def _make_user(session: AsyncSession) -> uuid.UUID:
    user = await SqlUserStore(session).create(
        sub=f"security-router-{uuid.uuid4()}",
        email=None,
        name=None,
        picture=None,
    )
    return user.id


async def _grant_role(
    db_session: AsyncSession, user_id: uuid.UUID, role_name: str
) -> None:
    await RbacService(PostgresRoleStore(db_session)).assign_role(user_id, role_name)
    await db_session.commit()


@pytest.mark.anyio
async def test_security_routes_are_feature_flagged_off(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("SECURITY_GOVERNANCE_ENABLED", "false")
    get_settings.cache_clear()
    user_id = await _make_user(db_session)
    headers = _auth_headers(user_id)
    app = _build_test_app()

    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://testserver",
    ) as client:
        response = await client.get("/api/security/roles", headers=headers)

    get_settings.cache_clear()
    assert response.status_code == 503
    assert response.json()["error"]["code"] == "feature_disabled"


def test_audit_response_redacts_nested_sensitive_metadata() -> None:
    response = SecurityAuditEntryResponse(
        id=uuid.uuid4(),
        occurred_at=datetime.datetime.now(datetime.UTC),
        actor_kind="system",
        action="login.succeeded",
        outcome="success",
        metadata={
            "source": "auth",
            "nested": {
                "api_key": "sk-live-secret",
                "authorization": "Bearer top-secret",
                "details": [{"credential": "password"}, {"status": "ok"}],
            },
        },
    )

    assert response.metadata == {
        "source": "auth",
        "nested": {
            "api_key": "[REDACTED]",
            "authorization": "[REDACTED]",
            "details": [{"credential": "[REDACTED]"}, {"status": "ok"}],
        },
    }


@pytest.mark.anyio
async def test_security_roles_endpoint_requires_rbac_manage(
    db_session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("SECURITY_GOVERNANCE_ENABLED", "true")
    monkeypatch.setenv("SECURITY_RBAC_ENFORCEMENT_ENABLED", "true")
    get_settings.cache_clear()

    member_id = await _make_user(db_session)
    admin_id = await _make_user(db_session)
    await _grant_role(db_session, admin_id, "admin")

    app = _build_test_app()
    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://testserver",
    ) as client:
        member_response = await client.get(
            "/api/security/roles",
            headers=_auth_headers(member_id),
        )
        admin_response = await client.get(
            "/api/security/roles",
            headers=_auth_headers(admin_id),
        )

    get_settings.cache_clear()
    assert member_response.status_code == 403
    assert admin_response.status_code == 200
    assert isinstance(admin_response.json(), list)


@pytest.mark.anyio
async def test_security_mutation_remains_protected_when_rbac_enforcement_is_off(
    db_session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("SECURITY_GOVERNANCE_ENABLED", "true")
    monkeypatch.setenv("SECURITY_RBAC_ENFORCEMENT_ENABLED", "false")
    get_settings.cache_clear()

    member_id = await _make_user(db_session)
    app = _build_test_app()
    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://testserver",
    ) as client:
        response = await client.post(
            f"/api/security/users/{member_id}/roles",
            headers=_auth_headers(member_id),
            json={"role_name": "owner"},
        )

    get_settings.cache_clear()
    assert response.status_code == 403
    assert response.json()["error"]["code"] == "permission_denied"


@pytest.mark.anyio
async def test_member_cannot_self_elevate_and_denial_is_audited(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("SECURITY_GOVERNANCE_ENABLED", "true")
    monkeypatch.setenv("SECURITY_RBAC_ENFORCEMENT_ENABLED", "true")
    monkeypatch.setenv("SECURITY_AUDIT_LOG_ENABLED", "true")
    get_settings.cache_clear()

    member_id = uuid.uuid4()
    rbac_service = AsyncMock(spec=RbacService)
    rbac_service.authorize.return_value = AuthorizationDecision(
        allowed=False,
        permission_key="rbac:manage",
        denial_reason=SecurityErrorCode.PERMISSION_DENIED,
    )
    audit_logger = AsyncMock()

    async def _unused_session() -> None:
        return None

    app = _build_test_app()
    app.dependency_overrides[security_router.get_rbac_service] = lambda: rbac_service
    app.dependency_overrides[security_router.get_audit_logger] = lambda: audit_logger
    app.dependency_overrides[security_router.get_db_session] = _unused_session
    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://testserver",
    ) as client:
        response = await client.post(
            f"/api/security/users/{member_id}/roles",
            headers=_auth_headers(member_id),
            json={"role_name": "owner"},
        )

    assert response.status_code == 403
    assert response.json()["error"]["code"] == "permission_denied"
    rbac_service.assign_role.assert_not_awaited()
    audit_logger.record.assert_awaited_once()
    assert audit_logger.record.await_args.kwargs["action"] == "role.assigned"
    assert audit_logger.record.await_args.kwargs["outcome"].value == "denied"


@pytest.mark.anyio
async def test_list_unknown_user_roles_returns_not_found(
    db_session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("SECURITY_GOVERNANCE_ENABLED", "true")
    monkeypatch.setenv("SECURITY_RBAC_ENFORCEMENT_ENABLED", "true")
    get_settings.cache_clear()

    admin_id = await _make_user(db_session)
    await _grant_role(db_session, admin_id, "admin")
    unknown_user_id = uuid.uuid4()

    app = _build_test_app()
    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://testserver",
    ) as client:
        response = await client.get(
            f"/api/security/users/{unknown_user_id}/roles",
            headers=_auth_headers(admin_id),
        )

    get_settings.cache_clear()
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "user_not_found"


@pytest.mark.anyio
async def test_assign_unknown_role_returns_not_found_and_duplicate_is_idempotent(
    db_session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("SECURITY_GOVERNANCE_ENABLED", "true")
    monkeypatch.setenv("SECURITY_RBAC_ENFORCEMENT_ENABLED", "true")
    get_settings.cache_clear()

    admin_id = await _make_user(db_session)
    target_id = await _make_user(db_session)
    await _grant_role(db_session, admin_id, "admin")

    app = _build_test_app()
    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://testserver",
    ) as client:
        unknown_response = await client.post(
            f"/api/security/users/{target_id}/roles",
            headers=_auth_headers(admin_id),
            json={"role_name": "does-not-exist"},
        )
        first_response = await client.post(
            f"/api/security/users/{target_id}/roles",
            headers=_auth_headers(admin_id),
            json={"role_name": "operator"},
        )
        duplicate_response = await client.post(
            f"/api/security/users/{target_id}/roles",
            headers=_auth_headers(admin_id),
            json={"role_name": "operator"},
        )

    get_settings.cache_clear()
    assert unknown_response.status_code == 404
    assert unknown_response.json()["error"]["code"] == "role_not_found"
    assert first_response.status_code == 200
    assert duplicate_response.status_code == 200
    assert duplicate_response.json()["role_name"] == "operator"


@pytest.mark.anyio
async def test_revoke_unknown_role_returns_not_found(
    db_session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("SECURITY_GOVERNANCE_ENABLED", "true")
    monkeypatch.setenv("SECURITY_RBAC_ENFORCEMENT_ENABLED", "true")
    get_settings.cache_clear()

    admin_id = await _make_user(db_session)
    target_id = await _make_user(db_session)
    await _grant_role(db_session, admin_id, "admin")

    app = _build_test_app()
    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://testserver",
    ) as client:
        response = await client.delete(
            f"/api/security/users/{target_id}/roles/does-not-exist",
            headers=_auth_headers(admin_id),
        )

    get_settings.cache_clear()
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "role_not_found"


@pytest.mark.anyio
async def test_security_policy_summary_and_audit_routes_work_for_operator(
    db_session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("SECURITY_GOVERNANCE_ENABLED", "true")
    monkeypatch.setenv("SECURITY_RBAC_ENFORCEMENT_ENABLED", "true")
    get_settings.cache_clear()

    operator_id = await _make_user(db_session)
    await _grant_role(db_session, operator_id, "operator")

    app = _build_test_app()
    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://testserver",
    ) as client:
        policy_response = await client.get(
            "/api/security/policies",
            headers=_auth_headers(operator_id),
        )
        audit_response = await client.get(
            "/api/security/audit",
            headers=_auth_headers(operator_id),
        )

    get_settings.cache_clear()
    assert policy_response.status_code == 200
    payload = policy_response.json()
    assert payload["security_governance_enabled"] is True
    assert "regex" not in str(payload)
    assert audit_response.status_code == 200
    assert isinstance(audit_response.json().get("items", []), list)
