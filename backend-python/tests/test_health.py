import pytest
from httpx import ASGITransport, AsyncClient

from app.core.config import APP_VERSION, get_settings
from app.main import app


@pytest.mark.anyio
async def test_health_returns_expected_shape() -> None:
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://testserver"
    ) as client:
        response = await client.get("/api/health")

    assert response.status_code == 200
    assert response.json() == {
        "status": "ok",
        "provider": get_settings().llm_provider,
        "version": APP_VERSION,
        "chat_streaming_enabled": get_settings().chat_streaming_enabled,
    }


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
