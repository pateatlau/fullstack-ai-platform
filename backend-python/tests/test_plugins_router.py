"""Plugin REST API integration tests (Epic 08 Phase 6)."""

from __future__ import annotations

import json
import uuid
from collections.abc import Iterator
from pathlib import Path

import pytest
from httpx import ASGITransport, AsyncClient
from fastapi import FastAPI

from app.ai.deps import get_plugin_registry, get_plugins_store
from app.ai.plugins.store import PluginsStore
from app.core.config import get_settings
from app.core.errors import register_exception_handlers
from app.core.security import create_access_token
from app.db.identity import SqlUserStore
from app.db.session import get_db_session
from app.routers import health as health_router
from app.routers import plugins as plugins_router
from tests.ai.plugins.conftest import load_plugins, plugin_settings

_PLUGIN_ROUTES = [
    ("GET", "/api/plugins"),
    ("GET", "/api/plugins/com.example.plugin"),
]


def _auth_headers(user_id: uuid.UUID) -> dict[str, str]:
    token = create_access_token(user_id=user_id, settings=get_settings())
    return {"Authorization": f"Bearer {token}"}


def _assert_plugin_directories_omitted(
    serialized: str,
    plugin_directories: list[str],
) -> None:
    for directory in plugin_directories:
        assert Path(directory).resolve().as_posix() not in serialized
    assert "plugin.yaml" not in serialized


def _build_test_app(*, include_health: bool = False) -> FastAPI:
    test_app = FastAPI()
    test_app.include_router(plugins_router.router)
    if include_health:
        test_app.include_router(health_router.router)
    register_exception_handlers(test_app)
    return test_app


def _bind_db_session(test_app: FastAPI, session) -> None:
    async def _override_db_session():
        yield session

    test_app.dependency_overrides[get_db_session] = _override_db_session


def _clear_router_overrides(test_app: FastAPI) -> None:
    test_app.dependency_overrides.pop(get_plugin_registry, None)
    test_app.dependency_overrides.pop(get_plugins_store, None)
    test_app.dependency_overrides.pop(get_db_session, None)


async def _make_user(session) -> uuid.UUID:
    user = await SqlUserStore(session).create(
        sub=f"plugins-api-{uuid.uuid4()}",
        email=None,
        name=None,
        picture=None,
    )
    return user.id


def _bind_plugin_registry(test_app: FastAPI, registry) -> None:
    store = PluginsStore(registry)

    def _override_registry():
        return registry

    def _override_store():
        return store

    test_app.dependency_overrides[get_plugin_registry] = _override_registry
    test_app.dependency_overrides[get_plugins_store] = _override_store


@pytest.fixture
def plugins_api_app(
    db_session,
    monkeypatch: pytest.MonkeyPatch,
) -> Iterator[FastAPI]:
    monkeypatch.setenv("PLUGINS_ENABLED", "true")
    get_settings.cache_clear()
    test_app = _build_test_app()
    _bind_db_session(test_app, db_session)
    try:
        yield test_app
    finally:
        _clear_router_overrides(test_app)
        get_settings.cache_clear()


@pytest.mark.anyio
@pytest.mark.parametrize(("method", "path"), _PLUGIN_ROUTES)
async def test_plugins_api_disabled_returns_503(
    db_session,
    monkeypatch: pytest.MonkeyPatch,
    method: str,
    path: str,
) -> None:
    user_id = await _make_user(db_session)
    headers = _auth_headers(user_id)
    monkeypatch.setenv("PLUGINS_ENABLED", "false")
    get_settings.cache_clear()
    test_app = _build_test_app()
    _bind_db_session(test_app, db_session)

    async with AsyncClient(
        transport=ASGITransport(app=test_app),
        base_url="http://testserver",
    ) as client:
        response = await client.request(method, path, headers=headers)

    get_settings.cache_clear()
    assert response.status_code == 503
    body = response.json()
    assert body["error"]["code"] == "feature_disabled"
    assert body["error"]["message"] == "Plugins are not enabled on this server."


@pytest.mark.anyio
async def test_plugins_api_requires_authentication(plugins_api_app: FastAPI) -> None:
    async with AsyncClient(
        transport=ASGITransport(app=plugins_api_app),
        base_url="http://testserver",
    ) as client:
        response = await client.get("/api/plugins")

    assert response.status_code == 401
    assert response.json()["error"]["code"] == "authentication_required"


@pytest.mark.anyio
async def test_list_plugins_returns_loaded_and_failed_records(
    db_session,
    plugins_api_app: FastAPI,
) -> None:
    user_id = await _make_user(db_session)
    headers = _auth_headers(user_id)
    _, registry, _tools, _prompts = load_plugins(
        plugin_settings(
            allowlist=[
                "com.test.minimal",
                "com.test.unsupported",
            ]
        )
    )
    _bind_plugin_registry(plugins_api_app, registry)

    async with AsyncClient(
        transport=ASGITransport(app=plugins_api_app),
        base_url="http://testserver",
    ) as client:
        response = await client.get("/api/plugins", headers=headers)

    assert response.status_code == 200
    body = response.json()
    plugin_ids = {item["plugin_id"] for item in body["plugins"]}
    assert "com.test.minimal" in plugin_ids
    assert "com.test.unsupported" in plugin_ids

    loaded = next(
        item for item in body["plugins"] if item["plugin_id"] == "com.test.minimal"
    )
    failed = next(
        item for item in body["plugins"] if item["plugin_id"] == "com.test.unsupported"
    )
    assert loaded["status"] == "loaded"
    assert "load_duration_ms" in loaded
    assert loaded["load_duration_ms"] >= 0
    assert failed["status"] == "failed"
    assert failed["failure"]["code"] == "unsupported_api_version"
    assert failed["failure"]["expected_api_versions"] == ["1"]
    assert failed["failure"]["manifest_api_version"] == "2"
    assert "load_duration_ms" in failed


@pytest.mark.anyio
async def test_list_plugins_includes_null_plugin_id_manifest_failures(
    db_session,
    plugins_api_app: FastAPI,
    tmp_path,
) -> None:
    user_id = await _make_user(db_session)
    headers = _auth_headers(user_id)
    (tmp_path / "empty-plugin").mkdir()
    settings = plugin_settings(directories=[str(tmp_path)])
    _, registry, _tools, _prompts = load_plugins(settings)
    _bind_plugin_registry(plugins_api_app, registry)

    async with AsyncClient(
        transport=ASGITransport(app=plugins_api_app),
        base_url="http://testserver",
    ) as client:
        response = await client.get("/api/plugins", headers=headers)

    assert response.status_code == 200
    body = response.json()
    assert len(body["plugins"]) == 1
    record = body["plugins"][0]
    assert record["plugin_id"] is None
    assert record["failure"]["code"] == "manifest_not_found"
    assert "load_duration_ms" in record
    _assert_plugin_directories_omitted(json.dumps(body), settings.plugin_directories)
    assert "empty-plugin" not in json.dumps(body)


@pytest.mark.anyio
async def test_plugin_responses_omit_metadata_and_paths(
    db_session,
    plugins_api_app: FastAPI,
) -> None:
    user_id = await _make_user(db_session)
    headers = _auth_headers(user_id)
    settings = plugin_settings(allowlist=["com.test.rich"])
    _, registry, _tools, _prompts = load_plugins(settings)
    _bind_plugin_registry(plugins_api_app, registry)

    async with AsyncClient(
        transport=ASGITransport(app=plugins_api_app),
        base_url="http://testserver",
    ) as client:
        list_response = await client.get("/api/plugins", headers=headers)
        detail_response = await client.get(
            "/api/plugins/com.test.rich",
            headers=headers,
        )

    assert list_response.status_code == 200
    assert detail_response.status_code == 200

    serialized = json.dumps(list_response.json()) + json.dumps(detail_response.json())
    assert "metadata" not in list_response.json()["plugins"][0]
    assert "metadata" not in detail_response.json()
    assert "team" not in serialized
    assert "tier" not in serialized
    _assert_plugin_directories_omitted(serialized, settings.plugin_directories)


@pytest.mark.anyio
async def test_non_api_version_failure_omits_api_diagnostic_fields(
    db_session,
    plugins_api_app: FastAPI,
) -> None:
    user_id = await _make_user(db_session)
    headers = _auth_headers(user_id)
    _, registry, _tools, _prompts = load_plugins(
        plugin_settings(allowlist=["com.test.bad-entrypoint"])
    )
    _bind_plugin_registry(plugins_api_app, registry)

    async with AsyncClient(
        transport=ASGITransport(app=plugins_api_app),
        base_url="http://testserver",
    ) as client:
        response = await client.get("/api/plugins", headers=headers)

    record = next(
        item
        for item in response.json()["plugins"]
        if item["plugin_id"] == "com.test.bad-entrypoint"
    )
    failure = record["failure"]
    assert failure["code"] == "entrypoint_import_error"
    assert failure["expected_api_versions"] is None
    assert failure["manifest_api_version"] is None


@pytest.mark.anyio
async def test_get_plugin_detail_includes_dependencies(
    db_session,
    plugins_api_app: FastAPI,
) -> None:
    user_id = await _make_user(db_session)
    headers = _auth_headers(user_id)
    _, registry, _tools, _prompts = load_plugins(
        plugin_settings(allowlist=["com.test.rich"])
    )
    _bind_plugin_registry(plugins_api_app, registry)

    async with AsyncClient(
        transport=ASGITransport(app=plugins_api_app),
        base_url="http://testserver",
    ) as client:
        response = await client.get("/api/plugins/com.test.rich", headers=headers)

    assert response.status_code == 200
    body = response.json()
    assert body["plugin_id"] == "com.test.rich"
    assert body["author"] == "Example Corp"
    assert body["dependencies"] == [
        {"plugin_id": "com.example.memory", "version": ">=1.0.0"}
    ]
    assert "metadata" not in body


@pytest.mark.anyio
async def test_get_plugin_detail_unknown_returns_404(
    db_session,
    plugins_api_app: FastAPI,
) -> None:
    user_id = await _make_user(db_session)
    headers = _auth_headers(user_id)
    _, registry, _tools, _prompts = load_plugins(
        plugin_settings(allowlist=["com.test.minimal"])
    )
    _bind_plugin_registry(plugins_api_app, registry)

    async with AsyncClient(
        transport=ASGITransport(app=plugins_api_app),
        base_url="http://testserver",
    ) as client:
        response = await client.get("/api/plugins/com.test.unknown", headers=headers)

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "plugin_not_found"


@pytest.mark.anyio
async def test_health_includes_plugin_counts(
    db_session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("PLUGINS_ENABLED", "true")
    get_settings.cache_clear()
    _, registry, _tools, _prompts = load_plugins(
        plugin_settings(
            allowlist=[
                "com.test.minimal",
                "com.test.unsupported",
            ]
        )
    )
    test_app = _build_test_app(include_health=True)
    _bind_db_session(test_app, db_session)
    _bind_plugin_registry(test_app, registry)

    try:
        async with AsyncClient(
            transport=ASGITransport(app=test_app),
            base_url="http://testserver",
        ) as client:
            response = await client.get("/api/health")
    finally:
        _clear_router_overrides(test_app)
        get_settings.cache_clear()

    assert response.status_code == 200
    body = response.json()
    assert body["plugins_enabled"] is True
    assert body["plugins_loaded_count"] == registry.loaded_count
    assert body["plugins_failed_count"] == registry.failed_count
    assert body["plugins_loaded_count"] >= 1
    assert body["plugins_failed_count"] >= 1


@pytest.mark.anyio
async def test_health_plugin_counts_zero_when_disabled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("PLUGINS_ENABLED", "false")
    get_settings.cache_clear()
    test_app = _build_test_app(include_health=True)
    register_exception_handlers(test_app)

    try:
        async with AsyncClient(
            transport=ASGITransport(app=test_app),
            base_url="http://testserver",
        ) as client:
            response = await client.get("/api/health")
    finally:
        get_settings.cache_clear()

    assert response.status_code == 200
    body = response.json()
    assert body["plugins_enabled"] is False
    assert body["plugins_loaded_count"] == 0
    assert body["plugins_failed_count"] == 0
