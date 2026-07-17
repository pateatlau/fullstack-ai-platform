"""Phase 2 auth tests: Google login, app JWT issuance, and caller resolution.

These are unit tests: the Google verifier and user store are replaced with
in-memory fakes (via FastAPI dependency overrides), so no network calls or
database are involved.
"""

import uuid

import pytest
from httpx import ASGITransport, AsyncClient
from starlette.requests import Request
from starlette.types import Scope

from app.core.caller import CallerContext, get_current_caller
from app.core.config import Settings, get_settings
from app.core.security import (
    InvalidGoogleTokenError,
    create_access_token,
    decode_access_token,
)
from app.main import app
from app.routers.auth import get_google_verifier, get_user_store
from app.services.auth_service import GoogleClaims
from tests.fakes import FakeGoogleVerifier, FakeUserStore


def _make_request(headers: dict[str, str]) -> Request:
    scope: Scope = {
        "type": "http",
        "method": "GET",
        "path": "/",
        "headers": [(k.lower().encode(), v.encode()) for k, v in headers.items()],
    }
    return Request(scope)


def _override_auth(verifier: FakeGoogleVerifier, store: FakeUserStore) -> None:
    app.dependency_overrides[get_google_verifier] = lambda: verifier
    app.dependency_overrides[get_user_store] = lambda: store


@pytest.fixture(autouse=True)
def _clear_overrides():  # pyright: ignore[reportUnusedFunction]
    yield
    app.dependency_overrides.clear()


@pytest.mark.anyio
async def test_google_login_creates_user_and_returns_app_jwt() -> None:
    verifier = FakeGoogleVerifier(
        claims=GoogleClaims(
            sub="google-sub-123",
            email="ada@example.com",
            name="Ada Lovelace",
            picture="https://example.com/ada.png",
        )
    )
    store = FakeUserStore()
    _override_auth(verifier, store)

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://testserver"
    ) as client:
        response = await client.post(
            "/api/auth/google", json={"id_token": "fake-google-token"}
        )

    assert response.status_code == 200
    body = response.json()
    assert body["token_type"] == "bearer"
    assert body["expires_in"] == get_settings().jwt_access_token_expires_minutes * 60
    assert body["user"]["email"] == "ada@example.com"
    assert body["user"]["display_name"] == "Ada Lovelace"

    # A new user was persisted, and the JWT subject matches that user's id.
    assert len(store.users) == 1
    user_id = decode_access_token(body["access_token"], settings=get_settings())
    assert user_id == store.users[0].id
    assert str(user_id) == body["user"]["id"]


@pytest.mark.anyio
async def test_google_login_resolves_existing_user_without_duplicate() -> None:
    store = FakeUserStore()
    existing = await store.create(
        sub="google-sub-123",
        email="old@example.com",
        name="Old Name",
        picture=None,
    )
    verifier = FakeGoogleVerifier(
        claims=GoogleClaims(
            sub="google-sub-123",
            email="new@example.com",
            name="New Name",
            picture="https://example.com/new.png",
        )
    )
    _override_auth(verifier, store)

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://testserver"
    ) as client:
        response = await client.post(
            "/api/auth/google", json={"id_token": "fake-google-token"}
        )

    assert response.status_code == 200
    body = response.json()
    # No duplicate row; the same user id is returned and the profile is refreshed.
    assert len(store.users) == 1
    assert body["user"]["id"] == str(existing.id)
    assert body["user"]["display_name"] == "New Name"
    assert body["user"]["picture_url"] == "https://example.com/new.png"


@pytest.mark.anyio
async def test_google_login_rejects_invalid_token() -> None:
    verifier = FakeGoogleVerifier(error=InvalidGoogleTokenError())
    store = FakeUserStore()
    _override_auth(verifier, store)

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://testserver"
    ) as client:
        response = await client.post("/api/auth/google", json={"id_token": "bad-token"})

    assert response.status_code == 401
    assert response.json()["error"]["code"] == "invalid_google_token"
    assert store.users == []


@pytest.mark.anyio
async def test_google_login_fails_closed_when_client_id_not_configured(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Explicitly set an empty value (not delenv) so this is deterministic even
    # when a local `.env` file has a real GOOGLE_CLIENT_ID: pydantic-settings
    # prioritizes `os.environ` over `env_file`, but only when the var is
    # actually present in `os.environ` — `delenv` makes it "absent" and falls
    # through to the `.env` file's value instead of the empty default.
    monkeypatch.setenv("GOOGLE_CLIENT_ID", "")
    get_settings.cache_clear()
    try:
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://testserver"
        ) as client:
            response = await client.post(
                "/api/auth/google", json={"id_token": "any-token"}
            )
    finally:
        get_settings.cache_clear()

    assert response.status_code == 503
    assert response.json()["error"]["code"] == "auth_not_configured"


@pytest.mark.anyio
async def test_caller_resolution_authenticated_with_valid_jwt() -> None:
    settings = get_settings()
    user_id = uuid.uuid4()
    token = create_access_token(user_id=user_id, settings=settings)

    caller = await get_current_caller(
        _make_request({"Authorization": f"Bearer {token}"}), settings
    )

    assert caller == CallerContext.for_user(user_id)
    assert caller.is_authenticated


def test_access_token_roundtrip() -> None:
    settings = Settings()
    user_id = uuid.uuid4()
    token = create_access_token(user_id=user_id, settings=settings)
    assert decode_access_token(token, settings=settings) == user_id
