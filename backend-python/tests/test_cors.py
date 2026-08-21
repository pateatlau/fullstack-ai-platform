"""CORS behavior for allowed origins and error envelopes."""

from __future__ import annotations

import pytest
from httpx import ASGITransport, AsyncClient

from app.core.config import get_settings
from app.core.cors import (
    apply_cors_headers,
    bind_request_origin,
    is_origin_allowed,
    reset_request_origin,
)
from app.core.errors import error_response
from app.main import app


@pytest.fixture(autouse=True)
def _clear_settings_cache():  # pyright: ignore[reportUnusedFunction]
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def test_dev_origin_regex_allows_local_vite_ports() -> None:
    settings = get_settings()
    assert settings.is_development
    assert is_origin_allowed("http://localhost:5174")
    assert is_origin_allowed("http://127.0.0.1:4173")


def test_error_response_applies_cors_for_bound_origin() -> None:
    token = bind_request_origin("http://localhost:5173")
    try:
        response = error_response(500, "internal_error", "Unexpected server error.")
    finally:
        reset_request_origin(token)

    assert response.headers["access-control-allow-origin"] == "http://localhost:5173"
    assert "X-Request-ID" in response.headers["access-control-expose-headers"]


@pytest.mark.anyio
async def test_preflight_allows_alternate_local_vite_port() -> None:
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://testserver"
    ) as client:
        response = await client.options(
            "/api/chat/stream",
            headers={
                "Origin": "http://localhost:5174",
                "Access-Control-Request-Method": "POST",
                "Access-Control-Request-Headers": "content-type",
            },
        )

    assert response.status_code == 200
    assert response.headers["access-control-allow-origin"] == "http://localhost:5174"


@pytest.mark.anyio
@pytest.mark.parametrize("method", ["PUT", "PATCH", "DELETE"])
async def test_preflight_allows_put_patch_delete_and_authorized_headers(
    method: str,
) -> None:
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://testserver"
    ) as client:
        response = await client.options(
            "/api/security/users/123/roles/owner",
            headers={
                "Origin": "http://localhost:5174",
                "Access-Control-Request-Method": method,
                "Access-Control-Request-Headers": "Authorization, Content-Type",
            },
        )

    assert response.status_code == 200
    assert response.headers["access-control-allow-origin"] == "http://localhost:5174"
    assert method in response.headers["access-control-allow-methods"]
    assert "authorization" in response.headers["access-control-allow-headers"].lower()


@pytest.mark.anyio
async def test_validation_error_includes_cors_for_allowed_origin() -> None:
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://testserver"
    ) as client:
        response = await client.post(
            "/api/chat",
            headers={
                "Origin": "http://localhost:5173",
                "Content-Type": "application/json",
            },
            json={"messages": "not-an-array"},
        )

    assert response.status_code == 422
    assert response.headers["access-control-allow-origin"] == "http://localhost:5173"


def test_apply_cors_headers_is_noop_for_disallowed_origin() -> None:
    token = bind_request_origin("https://evil.example")
    try:
        response = apply_cors_headers(error_response(400, "bad", "nope"))
    finally:
        reset_request_origin(token)

    assert "access-control-allow-origin" not in response.headers
