from typing import cast
import json

import pytest
from anthropic import APITimeoutError as AnthropicAPITimeoutError
from anthropic import RateLimitError as AnthropicRateLimitError
from fastapi import Request, Response
from httpx import ASGITransport, AsyncClient
from httpx import Request as HTTPXRequest
from httpx import Response as HTTPXResponse
from pytest import MonkeyPatch
from groq import APITimeoutError as GroqAPITimeoutError
from groq import RateLimitError as GroqRateLimitError
from starlette.types import Message, Scope

from app.core.config import Settings, get_settings
from app.main import app, enforce_request_size
from app.schemas.chat import ChatMessageSchema, ChatRequestSchema
from app.providers.base import ProviderCompletion
from app.providers.factory import ProviderFactory
from app.services.chat_service import (
    ChatService,
    ChatServiceError,
    normalize_chat_error,
)
from tests.fakes import FakeProvider
from tests.provider_error_assertions import (
    assert_json_error_has_no_sdk_leakage,
    assert_no_provider_sdk_leakage,
)


class ErroringProvider(FakeProvider):
    async def complete_chat(
        self,
        messages: list[ChatMessageSchema],
        model: str,
        temperature: float = 0.7,
        *,
        max_tokens: int | None = None,
    ) -> ProviderCompletion:
        del messages, model, temperature, max_tokens
        raise RuntimeError("provider exploded")


def _make_httpx_response(status_code: int = 429) -> HTTPXResponse:
    request = HTTPXRequest("POST", "https://example.test/v1/chat")
    return HTTPXResponse(status_code=status_code, request=request)


def _mock_provider_factory(provider: FakeProvider):
    def get_provider(
        name: str | None = None, settings: Settings | None = None
    ) -> FakeProvider:
        del name, settings
        return provider

    return staticmethod(get_provider)


@pytest.mark.anyio
async def test_chat_endpoint_returns_assistant_response(
    monkeypatch: MonkeyPatch,
) -> None:
    fake_provider = FakeProvider("Fake completion response")

    monkeypatch.setattr(
        ProviderFactory,
        "get_provider",
        _mock_provider_factory(fake_provider),
    )

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://testserver"
    ) as client:
        response = await client.post(
            "/api/chat",
            json={
                "messages": [{"role": "user", "content": "Hello"}],
                "provider": "openai",
                "model": "gpt-4o-mini",
            },
        )

    body = response.json()
    assert response.status_code == 200
    assert body["role"] == "assistant"
    assert body["content"] == "Fake completion response"
    assert body["provider"] == "openai"
    assert body["model"] == "gpt-4o-mini"


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("provider", "model"),
    [
        ("openai", "gpt-4o-mini"),
        ("gemini", "gemini-3.1-flash-lite"),
        ("groq", "openai/gpt-oss-20b"),
        ("anthropic", "claude-haiku-4-5-20251001"),
    ],
)
async def test_chat_endpoint_accepts_supported_provider_model_pairs(
    monkeypatch: MonkeyPatch,
    provider: str,
    model: str,
) -> None:
    fake_provider = FakeProvider("Fake completion response")

    monkeypatch.setattr(
        ProviderFactory,
        "get_provider",
        _mock_provider_factory(fake_provider),
    )

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://testserver"
    ) as client:
        response = await client.post(
            "/api/chat",
            json={
                "messages": [{"role": "user", "content": "Hello"}],
                "provider": provider,
                "model": model,
            },
        )

    body = response.json()
    assert response.status_code == 200
    assert body["provider"] == provider
    assert body["model"] == model


@pytest.mark.anyio
async def test_chat_endpoint_accepts_provider_model_configured_via_env(
    monkeypatch: MonkeyPatch,
) -> None:
    monkeypatch.setenv("GROQ_MODEL", "llama-3.3-70b-versatile")
    fake_provider = FakeProvider("Fake completion response")

    monkeypatch.setattr(
        ProviderFactory,
        "get_provider",
        _mock_provider_factory(fake_provider),
    )

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://testserver"
    ) as client:
        response = await client.post(
            "/api/chat",
            json={
                "messages": [{"role": "user", "content": "Hello"}],
                "provider": "groq",
                "model": "llama-3.3-70b-versatile",
            },
        )

    body = response.json()
    assert response.status_code == 200
    assert body["provider"] == "groq"
    assert body["model"] == "llama-3.3-70b-versatile"


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("provider", "expected_model"),
    [
        ("openai", "gpt-4o-mini"),
        ("gemini", "gemini-3.1-flash-lite"),
        ("groq", "openai/gpt-oss-20b"),
        ("anthropic", "claude-haiku-4-5-20251001"),
    ],
)
async def test_chat_endpoint_uses_provider_default_model_when_model_not_set(
    monkeypatch: MonkeyPatch,
    provider: str,
    expected_model: str,
) -> None:
    fake_provider = FakeProvider("Fake completion response")

    monkeypatch.setattr(
        ProviderFactory,
        "get_provider",
        _mock_provider_factory(fake_provider),
    )

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://testserver"
    ) as client:
        response = await client.post(
            "/api/chat",
            json={
                "messages": [{"role": "user", "content": "Hello"}],
                "provider": provider,
            },
        )

    body = response.json()
    assert response.status_code == 200
    assert body["provider"] == provider
    assert body["model"] == expected_model


@pytest.mark.anyio
async def test_chat_endpoint_returns_validation_error_for_empty_messages() -> None:
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://testserver"
    ) as client:
        response = await client.post("/api/chat", json={"messages": []})

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "validation_error"


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("provider", "model"),
    [
        ("openai", "claude-haiku-4-5-20251001"),
        ("groq", "gemini-3.1-flash-lite"),
    ],
)
async def test_chat_endpoint_rejects_invalid_provider_model_combinations(
    provider: str,
    model: str,
) -> None:
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://testserver"
    ) as client:
        response = await client.post(
            "/api/chat",
            json={
                "messages": [{"role": "user", "content": "Hello"}],
                "provider": provider,
                "model": model,
            },
        )

    body = response.json()
    assert response.status_code == 422
    assert body["error"]["code"] == "validation_error"


@pytest.mark.anyio
async def test_chat_endpoint_normalizes_provider_errors(
    monkeypatch: MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        ProviderFactory,
        "get_provider",
        _mock_provider_factory(cast(FakeProvider, ErroringProvider())),
    )

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://testserver"
    ) as client:
        response = await client.post(
            "/api/chat",
            json={"messages": [{"role": "user", "content": "Hello"}]},
        )

    assert response.status_code == 502
    body = response.json()
    assert body["error"]["code"] == "provider_error"
    assert body["error"]["message"] == "Upstream provider failed."
    assert_json_error_has_no_sdk_leakage(body)
    assert body["error"]["request_id"] is not None
    assert response.headers.get("X-Request-ID") == body["error"]["request_id"]


@pytest.mark.parametrize(
    ("exception", "expected_code"),
    [
        (
            GroqRateLimitError(
                "rate limited",
                response=_make_httpx_response(429),
                body=None,
            ),
            "provider_rate_limited",
        ),
        (
            AnthropicRateLimitError(
                "rate limited",
                response=_make_httpx_response(429),
                body=None,
            ),
            "provider_rate_limited",
        ),
        (
            GroqAPITimeoutError(request=HTTPXRequest("POST", "https://example.test")),
            "provider_timeout",
        ),
        (
            AnthropicAPITimeoutError(
                request=HTTPXRequest("POST", "https://example.test")
            ),
            "provider_timeout",
        ),
    ],
)
def test_normalize_chat_error_handles_provider_specific_sdk_errors(
    exception: Exception,
    expected_code: str,
) -> None:
    normalized = normalize_chat_error(exception)

    assert normalized.code == expected_code
    assert_no_provider_sdk_leakage(normalized.message)


@pytest.mark.anyio
async def test_chat_service_rejects_selected_provider_with_missing_api_key() -> None:
    service = ChatService(
        settings=Settings(
            llm_provider="openai",
            openai_api_key="test-openai-key",
            gemini_api_key="test-gemini-key",
            groq_api_key="test-groq-key",
            anthropic_api_key=None,
        )
    )

    request = ChatRequestSchema(
        messages=[ChatMessageSchema(role="user", content="Hello")],
        provider="anthropic",
    )

    with pytest.raises(ChatServiceError) as exc_info:
        await service.complete_chat(request)

    assert exc_info.value.status_code == 422
    assert exc_info.value.code == "validation_error"
    assert "ANTHROPIC_API_KEY" in exc_info.value.message


@pytest.mark.anyio
async def test_chat_service_rejects_invalid_default_provider_setting() -> None:
    service = ChatService(
        settings=Settings(
            llm_provider="invalid-provider",
            openai_api_key="test-openai-key",
            gemini_api_key="test-gemini-key",
            groq_api_key="test-groq-key",
            anthropic_api_key="test-anthropic-key",
        )
    )

    request = ChatRequestSchema(
        messages=[ChatMessageSchema(role="user", content="Hello")]
    )

    with pytest.raises(ChatServiceError) as exc_info:
        await service.complete_chat(request)

    assert exc_info.value.status_code == 422
    assert exc_info.value.code == "validation_error"
    assert "Unsupported provider" in exc_info.value.message


@pytest.mark.anyio
async def test_request_size_guard_rejects_large_chunked_body() -> None:
    body_limit = get_settings().request_body_limit_bytes
    oversized_body = b"x" * (body_limit + 1)
    messages: list[Message] = [
        {"type": "http.request", "body": oversized_body, "more_body": False}
    ]

    async def receive() -> Message:
        if messages:
            return messages.pop(0)
        return {"type": "http.disconnect"}

    scope: Scope = {
        "type": "http",
        "http_version": "1.1",
        "method": "POST",
        "scheme": "http",
        "path": "/api/chat",
        "raw_path": b"/api/chat",
        "query_string": b"",
        "headers": [(b"content-type", b"application/json")],
        "client": ("127.0.0.1", 12345),
        "server": ("testserver", 80),
    }
    request = Request(scope, receive)

    async def call_next(limited_request: Request) -> Response:
        await limited_request.body()
        return Response(status_code=204)

    response = await enforce_request_size(request, call_next)

    assert response.status_code == 413
    body = json.loads(bytes(response.body))
    assert body["error"]["code"] == "validation_error"
    assert body["error"]["message"] == get_settings().request_body_limit_message()
