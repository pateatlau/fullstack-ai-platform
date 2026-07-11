from typing import cast

import pytest
from fastapi import Request, Response
from httpx import ASGITransport, AsyncClient
from pytest import MonkeyPatch
from starlette.types import Message, Scope

from app.core.config import Settings
from app.main import MAX_REQUEST_BODY_BYTES, app, enforce_request_size
from app.schemas.chat import ChatMessageSchema
from app.providers.factory import ProviderFactory
from tests.fakes import FakeProvider


class ErroringProvider(FakeProvider):
    async def complete_chat(
        self,
        messages: list[ChatMessageSchema],
        model: str,
        temperature: float = 0.7,
    ) -> str:
        del messages, model, temperature
        raise RuntimeError("provider exploded")


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
async def test_chat_endpoint_returns_validation_error_for_empty_messages() -> None:
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://testserver"
    ) as client:
        response = await client.post("/api/chat", json={"messages": []})

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "validation_error"


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
    assert response.json() == {
        "error": {
            "code": "provider_error",
            "message": "Upstream provider failed.",
        }
    }


@pytest.mark.anyio
async def test_request_size_guard_rejects_large_chunked_body() -> None:
    oversized_body = b"x" * (MAX_REQUEST_BODY_BYTES + 1)
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
    assert response.body == (
        b'{"error":{"code":"validation_error","message":"Request body exceeds '
        b'the 16384 byte limit. Reduce message size and retry."}}'
    )
