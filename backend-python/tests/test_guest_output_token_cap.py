"""Guest output token cap tests (V1.1.1 Phase 1)."""

from __future__ import annotations

import uuid

import pytest
from pydantic_settings import SettingsConfigDict
from starlette.requests import Request
from starlette.types import Message, Scope

from app.core.caller import CallerContext
from app.core.config import Settings
from app.schemas.chat import ChatMessageSchema, ChatRequestSchema
from app.services.chat_service import ChatService
from app.services.max_tokens import resolve_max_tokens
from tests.fakes import FakeChatStore, FakeProvider, FakeUsageStore


def _guest_caller() -> CallerContext:
    return CallerContext(kind="guest", guest_id=uuid.uuid4())


def _user_caller() -> CallerContext:
    return CallerContext(kind="user", user_id=uuid.uuid4())


def _chat_service(
    *,
    settings: Settings,
    provider: FakeProvider,
) -> ChatService:
    service = ChatService(
        settings=settings,
        chat_store=FakeChatStore(),
        usage_store=FakeUsageStore(),
        quota_service=None,
    )
    service._resolve_provider = lambda request: (  # type: ignore[method-assign, assignment]
        provider,
        settings.openai_model,
        "openai",
    )
    return service


@pytest.mark.anyio
async def test_guest_completion_passes_capped_max_tokens_to_provider() -> None:
    settings = Settings(
        llm_provider="openai",
        openai_api_key="test-key",
        guest_max_output_tokens=256,
    )
    provider = FakeProvider()
    service = _chat_service(settings=settings, provider=provider)
    request = ChatRequestSchema(messages=[ChatMessageSchema(role="user", content="Hi")])

    await service.complete_chat(request, _guest_caller())

    assert provider.last_max_tokens == 256


@pytest.mark.anyio
async def test_authenticated_completion_does_not_apply_guest_cap() -> None:
    settings = Settings(
        llm_provider="openai",
        openai_api_key="test-key",
        guest_max_output_tokens=256,
        default_max_tokens=900,
    )
    provider = FakeProvider()
    service = _chat_service(settings=settings, provider=provider)
    request = ChatRequestSchema(messages=[ChatMessageSchema(role="user", content="Hi")])

    await service.complete_chat(request, _user_caller())

    assert provider.last_max_tokens == 900


@pytest.mark.anyio
async def test_guest_stream_passes_capped_max_tokens_to_provider() -> None:
    settings = Settings(
        llm_provider="openai",
        openai_api_key="test-key",
        guest_max_output_tokens=128,
    )
    provider = FakeProvider(response="one two three")
    service = _chat_service(settings=settings, provider=provider)
    request = ChatRequestSchema(messages=[ChatMessageSchema(role="user", content="Hi")])

    scope: Scope = {
        "type": "http",
        "http_version": "1.1",
        "method": "POST",
        "scheme": "http",
        "path": "/api/chat/stream",
        "raw_path": b"/api/chat/stream",
        "query_string": b"",
        "headers": [],
        "client": ("127.0.0.1", 12345),
        "server": ("testserver", 80),
    }

    async def receive() -> Message:
        return {"type": "http.request", "body": b"", "more_body": False}

    http_request = Request(scope, receive)

    frames = [
        frame
        async for frame in service.stream_chat(
            request, http_request, caller=_guest_caller()
        )
    ]

    assert frames
    assert provider.last_stream_max_tokens == 128


def test_resolve_max_tokens_logs_and_caps_for_guest(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        Settings,
        "model_config",
        SettingsConfigDict(env_file=None, extra="ignore"),
    )
    settings = Settings(
        llm_provider="openai",
        openai_api_key="test-key",
        guest_max_output_tokens=512,
        default_max_tokens=4096,
    )
    effective = resolve_max_tokens(
        _guest_caller(),
        settings,
        provider_name="openai",
    )
    assert effective == 512


def test_demo_mode_strict_lowers_guest_cap(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        Settings,
        "model_config",
        SettingsConfigDict(env_file=None, extra="ignore"),
    )
    settings = Settings(
        llm_provider="openai",
        openai_api_key="test-key",
        guest_max_output_tokens=4096,
        demo_mode_strict=True,
    )
    assert settings.effective_guest_max_output_tokens == 512


@pytest.mark.anyio
async def test_guest_summarization_passes_capped_max_tokens_to_provider() -> None:
    settings = Settings(
        llm_provider="openai",
        openai_api_key="test-key",
        chat_persistence_enabled=True,
        guest_max_output_tokens=256,
        summary_trigger_message_count=2,
    )
    provider = FakeProvider(response="A concise summary.")
    service = _chat_service(settings=settings, provider=provider)
    request = ChatRequestSchema(messages=[ChatMessageSchema(role="user", content="Hi")])

    await service.complete_chat(request, _guest_caller())

    assert provider.last_max_tokens == 256
