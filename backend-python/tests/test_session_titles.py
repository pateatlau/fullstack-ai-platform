"""Phase 3 tests: auto-generated session titles from first user message."""

from __future__ import annotations

import uuid

import pytest
from pytest import MonkeyPatch

from app.core.caller import CallerContext
from app.core.config import Settings
from app.core.text_utils import derive_session_title
from app.providers.factory import ProviderFactory
from app.schemas.chat import ChatMessageSchema, ChatRequestSchema
from app.services.chat_service import ChatService
from app.services.quota_service import QuotaService
from tests.fakes import FakeChatStore, FakeGuestQuotaStore, FakeProvider, FakeUsageStore
from tests.test_chat_persistence import (
    _patch_provider,
    _request,
    _service,
)


# --------------------------------------------------------------------------- #
# derive_session_title unit tests                                             #
# --------------------------------------------------------------------------- #


def test_derive_session_title_collapses_whitespace() -> None:
    assert derive_session_title("  Hello   world  ") == "Hello world"


def test_derive_session_title_uses_first_line_only() -> None:
    assert derive_session_title("Line one\nLine two") == "Line one"


def test_derive_session_title_truncates_long_message() -> None:
    long_message = "A" * 100
    result = derive_session_title(long_message)
    assert result is not None
    assert len(result) == 50
    assert result == "A" * 50


def test_derive_session_title_whitespace_only_returns_none() -> None:
    assert derive_session_title("   \n\t  ") is None


def test_derive_session_title_plain_message() -> None:
    assert (
        derive_session_title("First message in new session")
        == "First message in new session"
    )


def test_derive_session_title_collapses_tabs_and_newlines_on_first_line() -> None:
    assert derive_session_title("Hello\t\tworld\r\nignored") == "Hello world"


# --------------------------------------------------------------------------- #
# ChatService persistence (unit, fakes)                                       #
# --------------------------------------------------------------------------- #


@pytest.mark.anyio
async def test_first_chat_turn_on_new_session_sets_title(
    monkeypatch: MonkeyPatch,
) -> None:
    _patch_provider(monkeypatch, FakeProvider("reply"))
    settings = Settings(chat_persistence_enabled=True, llm_provider="openai")
    chat_store = FakeChatStore()
    service = _service(settings, chat_store=chat_store)
    caller = CallerContext.for_user(uuid.uuid4())

    result = await service.complete_chat(
        _request("  Hello   from   the   first   turn  "), caller
    )

    assert result.session_id is not None
    session = chat_store.sessions[result.session_id]
    assert session.title == "Hello from the first turn"


@pytest.mark.anyio
async def test_post_empty_session_first_turn_sets_title(
    monkeypatch: MonkeyPatch,
) -> None:
    _patch_provider(monkeypatch, FakeProvider("reply"))
    settings = Settings(chat_persistence_enabled=True, llm_provider="openai")
    chat_store = FakeChatStore()
    service = _service(settings, chat_store=chat_store)
    caller = CallerContext.for_user(uuid.uuid4())

    created = await service.create_session(caller)
    assert created.title is None

    await service.complete_chat(
        _request("Title from deferred first message", session_id=created.id),
        caller,
    )

    assert chat_store.sessions[created.id].title == "Title from deferred first message"


@pytest.mark.anyio
async def test_second_turn_preserves_existing_title(
    monkeypatch: MonkeyPatch,
) -> None:
    _patch_provider(monkeypatch, FakeProvider("reply"))
    settings = Settings(chat_persistence_enabled=True, llm_provider="openai")
    chat_store = FakeChatStore()
    service = _service(settings, chat_store=chat_store)
    caller = CallerContext.for_user(uuid.uuid4())

    first = await service.complete_chat(_request("Original title message"), caller)
    assert first.session_id is not None
    original_title = chat_store.sessions[first.session_id].title

    await service.complete_chat(
        _request(
            "A completely different follow-up question", session_id=first.session_id
        ),
        caller,
    )

    assert chat_store.sessions[first.session_id].title == original_title


@pytest.mark.anyio
async def test_pre_existing_non_null_title_is_preserved(
    monkeypatch: MonkeyPatch,
) -> None:
    _patch_provider(monkeypatch, FakeProvider("reply"))
    settings = Settings(chat_persistence_enabled=True, llm_provider="openai")
    chat_store = FakeChatStore()
    service = _service(settings, chat_store=chat_store)
    caller = CallerContext.for_user(uuid.uuid4())
    session = await chat_store.create_session(
        user_id=caller.user_id, title="Manual title"
    )

    await service.complete_chat(
        _request("Should not overwrite", session_id=session.id), caller
    )

    assert chat_store.sessions[session.id].title == "Manual title"


@pytest.mark.anyio
async def test_maybe_set_session_title_skips_whitespace_only_content() -> None:
    chat_store = FakeChatStore()
    service = _service(Settings(chat_persistence_enabled=True), chat_store=chat_store)
    session = await chat_store.create_session(user_id=uuid.uuid4(), title=None)

    await service._maybe_set_session_title(session, "   \n\t  ")

    assert chat_store.sessions[session.id].title is None


@pytest.mark.anyio
async def test_whitespace_only_first_message_leaves_title_null(
    monkeypatch: MonkeyPatch,
) -> None:
    _patch_provider(monkeypatch, FakeProvider("reply"))
    settings = Settings(chat_persistence_enabled=True, llm_provider="openai")
    chat_store = FakeChatStore()
    service = _service(settings, chat_store=chat_store)
    caller = CallerContext.for_user(uuid.uuid4())
    request = ChatRequestSchema.model_construct(
        messages=[ChatMessageSchema.model_construct(role="user", content="   \n\t  ")]
    )

    result = await service.complete_chat(request, caller)

    assert result.session_id is not None
    assert chat_store.sessions[result.session_id].title is None


@pytest.mark.anyio
async def test_prepare_stream_sets_title_on_first_user_message(
    monkeypatch: MonkeyPatch,
) -> None:
    _patch_provider(monkeypatch, FakeProvider("stream reply"))
    settings = Settings(chat_persistence_enabled=True, llm_provider="openai")
    chat_store = FakeChatStore()
    service = _service(settings, chat_store=chat_store)
    caller = CallerContext.for_user(uuid.uuid4())
    request = _request("Stream path title message")

    prep = await service.prepare_stream(request, caller)
    assert prep is not None
    assert chat_store.sessions[prep.session_id].title == "Stream path title message"


@pytest.mark.anyio
async def test_guest_first_message_sets_title(
    monkeypatch: MonkeyPatch,
) -> None:
    _patch_provider(monkeypatch, FakeProvider("guest reply"))
    settings = Settings(chat_persistence_enabled=True, llm_provider="openai")
    chat_store = FakeChatStore()
    service = _service(settings, chat_store=chat_store)
    caller = CallerContext.anonymous(guest_id=uuid.uuid4())

    result = await service.complete_chat(_request("Guest session title"), caller)

    assert result.session_id is not None
    assert chat_store.sessions[result.session_id].title == "Guest session title"


@pytest.mark.anyio
async def test_title_auto_generated_emits_structured_log(
    monkeypatch: MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    _patch_provider(monkeypatch, FakeProvider("reply"))
    settings = Settings(chat_persistence_enabled=True, llm_provider="openai")
    chat_store = FakeChatStore()
    service = _service(settings, chat_store=chat_store)
    caller = CallerContext.for_user(uuid.uuid4())

    with caplog.at_level("INFO"):
        result = await service.complete_chat(_request("Logged title message"), caller)

    assert result.session_id is not None
    matching = [
        record
        for record in caplog.records
        if getattr(record, "title_auto_generated_total", None) is True
    ]
    assert matching, "Expected title_auto_generated_total structured log"
    assert "Logged title message" not in caplog.text


@pytest.mark.anyio
async def test_tool_chat_complete_sets_title_via_shared_chat_service(
    monkeypatch: MonkeyPatch,
) -> None:
    from app.ai.prompts.manager import create_prompt_manager
    from app.ai.tools.executor import ToolExecutor
    from app.ai.tools.registry import ToolRegistry
    from app.services.tool_chat_service import ToolChatService

    provider = FakeProvider("tool path reply")
    monkeypatch.setattr(
        ProviderFactory,
        "get_provider",
        staticmethod(lambda name=None, settings=None: provider),
    )
    settings = Settings(
        chat_persistence_enabled=True,
        openai_api_key="test-key",
        tools_enabled=True,
    )
    chat_store = FakeChatStore()
    chat_service = ChatService(
        settings,
        chat_store=chat_store,
        usage_store=FakeUsageStore(),
        quota_service=QuotaService(store=FakeGuestQuotaStore(), settings=settings),
        prompt_manager=create_prompt_manager(),
    )
    registry = ToolRegistry()
    tool_service = ToolChatService(
        chat_service=chat_service,
        tool_executor=ToolExecutor(registry=registry, settings=settings),
        tool_registry=registry,
        prompt_manager=create_prompt_manager(),
        settings=settings,
    )
    caller = CallerContext.for_user(uuid.uuid4())

    response = await tool_service.complete_chat(
        ChatRequestSchema(
            messages=[
                ChatMessageSchema(role="user", content="Tool path title message")
            ],
            provider="openai",
            model="gpt-4o-mini",
        ),
        caller,
    )

    assert response.session_id is not None
    assert chat_store.sessions[response.session_id].title == "Tool path title message"
