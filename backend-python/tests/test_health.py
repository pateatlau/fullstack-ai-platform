import pytest
from httpx import ASGITransport, AsyncClient

from app.ai.deps import get_approvals_store, get_plugin_registry
from app.core.config import APP_VERSION, Settings, get_settings
from app.main import app
from app.providers.capabilities import capabilities_by_provider


class _FailingApprovalsStore:
    async def count_pending(self) -> int:
        raise RuntimeError("database unavailable")


@pytest.mark.anyio
async def test_health_hitl_pending_count_defaults_to_zero_on_db_error() -> None:
    settings = Settings(hitl_enabled=True)
    app.dependency_overrides[get_settings] = lambda: settings
    app.dependency_overrides[get_approvals_store] = lambda: _FailingApprovalsStore()

    try:
        async with AsyncClient(
            transport=ASGITransport(app=app),
            base_url="http://testserver",
        ) as client:
            response = await client.get("/api/health")
    finally:
        app.dependency_overrides.pop(get_settings, None)
        app.dependency_overrides.pop(get_approvals_store, None)

    assert response.status_code == 200
    body = response.json()
    assert body["hitl_enabled"] is True
    assert body["hitl_pending_approvals_count"] == 0


@pytest.mark.anyio
async def test_health_returns_expected_shape() -> None:
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://testserver"
    ) as client:
        response = await client.get("/api/health")

    assert response.status_code == 200
    settings = get_settings()
    plugin_registry = get_plugin_registry()
    plugins_enabled = settings.plugins_enabled
    assert response.json() == {
        "status": "ok",
        "provider": settings.llm_provider,
        "version": APP_VERSION,
        "chat_streaming_enabled": settings.chat_streaming_enabled,
        "tools_enabled": settings.tools_enabled,
        "rag_enabled": settings.rag_enabled,
        "voice_enabled": settings.voice_enabled,
        "memory_enabled": settings.memory_enabled,
        "workflow_engine_enabled": settings.workflow_engine_enabled,
        "observability_enabled": settings.observability_enabled,
        "hitl_enabled": settings.hitl_enabled,
        "hitl_pending_approvals_count": 0,
        "background_jobs_enabled": settings.background_jobs_enabled,
        "background_jobs_pending_count": 0,
        "background_jobs_dead_letter_count": 0,
        "plugins_enabled": plugins_enabled,
        "plugins_loaded_count": (
            plugin_registry.loaded_count if plugins_enabled else 0
        ),
        "plugins_failed_count": (
            plugin_registry.failed_count if plugins_enabled else 0
        ),
        "capabilities": {
            "by_provider": capabilities_by_provider(settings),
        },
    }


@pytest.mark.anyio
async def test_health_capabilities_include_tool_calling_flags() -> None:
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://testserver"
    ) as client:
        response = await client.get("/api/health")

    capabilities = response.json()["capabilities"]["by_provider"]
    for provider in ("openai", "gemini", "groq", "anthropic"):
        assert capabilities[provider]["supports_streaming"] is True
        assert capabilities[provider]["supports_tool_calling"] is True


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("provider", "api_key_name"),
    [
        ("openai", "OPENAI_API_KEY"),
        ("gemini", "GEMINI_API_KEY"),
        ("groq", "GROQ_API_KEY"),
        ("anthropic", "ANTHROPIC_API_KEY"),
    ],
)
async def test_health_reports_selected_provider_for_each_supported_provider(
    monkeypatch: pytest.MonkeyPatch,
    provider: str,
    api_key_name: str,
) -> None:
    for env_name in (
        "OPENAI_API_KEY",
        "GEMINI_API_KEY",
        "GROQ_API_KEY",
        "ANTHROPIC_API_KEY",
    ):
        monkeypatch.delenv(env_name, raising=False)

    monkeypatch.setenv("LLM_PROVIDER", provider)
    monkeypatch.setenv(api_key_name, f"test-{provider}-key")
    get_settings.cache_clear()

    try:
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://testserver"
        ) as client:
            response = await client.get("/api/health")
    finally:
        get_settings.cache_clear()

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"


@pytest.mark.anyio
async def test_cors_exposes_guest_token_header_for_allowed_origin() -> None:
    allowed_origin = get_settings().cors_allowed_origins_list[0]
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://testserver"
    ) as client:
        response = await client.get("/api/health", headers={"Origin": allowed_origin})

    assert response.status_code == 200
    assert response.headers["access-control-expose-headers"] == (
        "X-Guest-Token, X-Guest-Quota-Remaining, X-Request-ID"
    )
    assert response.headers["access-control-allow-origin"] == allowed_origin
    assert "access-control-allow-credentials" not in response.headers
