from __future__ import annotations

import uuid

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from app.ai.security.rbac.service import RbacService
from app.ai.security.rbac.store import PostgresRoleStore
from app.core.config import get_settings
from app.core.errors import register_exception_handlers
from app.core.security import create_access_token
from app.db.identity import SqlUserStore
from app.routers import security as security_router


def _auth_headers(user_id: uuid.UUID) -> dict[str, str]:
    token = create_access_token(user_id=user_id, settings=get_settings())
    return {"Authorization": f"Bearer {token}"}


def _build_test_app() -> FastAPI:
    test_app = FastAPI()
    test_app.include_router(security_router.router)
    register_exception_handlers(test_app)
    return test_app


async def _make_user(session) -> uuid.UUID:
    user = await SqlUserStore(session).create(
        sub=f"security-router-{uuid.uuid4()}",
        email=None,
        name=None,
        picture=None,
    )
    return user.id


async def _grant_role(db_session, user_id: uuid.UUID, role_name: str) -> None:
    await RbacService(PostgresRoleStore(db_session)).assign_role(user_id, role_name)


@pytest.mark.anyio
async def test_security_routes_are_feature_flagged_off(
    db_session, monkeypatch: pytest.MonkeyPatch
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


@pytest.mark.anyio
async def test_security_roles_endpoint_requires_rbac_manage(
    db_session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("SECURITY_GOVERNANCE_ENABLED", "true")
    monkeypatch.setenv("SECURITY_RBAC_ENFORCEMENT_ENABLED", "true")
    get_settings.cache_clear()

    member_id = await _make_user(db_session)
    operator_id = await _make_user(db_session)
    await _grant_role(db_session, operator_id, "operator")

    app = _build_test_app()
    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://testserver",
    ) as client:
        member_response = await client.get(
            "/api/security/roles",
            headers=_auth_headers(member_id),
        )
        operator_response = await client.get(
            "/api/security/roles",
            headers=_auth_headers(operator_id),
        )

    get_settings.cache_clear()
    assert member_response.status_code == 403
    assert operator_response.status_code == 200
    assert isinstance(operator_response.json(), list)


@pytest.mark.anyio
async def test_security_policy_summary_and_audit_routes_work_for_operator(
    db_session,
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
