"""Phase 4 unit tests: provider usage extraction and usage-record building."""

import uuid

import pytest

from app.providers.base import ProviderUsage
from app.services.usage_service import build_usage_record, estimate_tokens
from tests.fakes import FakeChatStore, FakeProvider, FakeUsageStore


@pytest.mark.anyio
async def test_fake_provider_surfaces_provider_usage() -> None:
    completion = await FakeProvider("hi there").complete_chat([], "model")

    assert completion.content == "hi there"
    assert completion.usage == ProviderUsage(
        prompt_tokens=11, completion_tokens=7, total_tokens=18
    )


def test_build_usage_record_prefers_provider_reported() -> None:
    record = build_usage_record(
        provider="openai",
        model="gpt-4o-mini",
        provider_usage=ProviderUsage(
            prompt_tokens=30, completion_tokens=12, total_tokens=42
        ),
        prompt_text="ignored when provider reports",
        completion_text="ignored",
    )

    assert record.token_source == "provider_reported"
    assert (record.prompt_tokens, record.completion_tokens, record.total_tokens) == (
        30,
        12,
        42,
    )


def test_build_usage_record_computes_total_when_missing() -> None:
    record = build_usage_record(
        provider="anthropic",
        model="claude-haiku-4-5-20251001",
        provider_usage=ProviderUsage(
            prompt_tokens=8, completion_tokens=5, total_tokens=None
        ),
        prompt_text="x",
        completion_text="y",
    )

    assert record.token_source == "provider_reported"
    assert record.total_tokens == 13


def test_build_usage_record_estimates_when_provider_omits_usage() -> None:
    prompt = "a" * 40  # ~10 tokens
    completion = "b" * 16  # ~4 tokens
    record = build_usage_record(
        provider="gemini",
        model="gemini-3.1-flash-lite",
        provider_usage=None,
        prompt_text=prompt,
        completion_text=completion,
    )

    assert record.token_source == "estimated"
    assert record.prompt_tokens == estimate_tokens(prompt)
    assert record.completion_tokens == estimate_tokens(completion)
    assert record.total_tokens == record.prompt_tokens + record.completion_tokens


@pytest.mark.anyio
async def test_fake_chat_store_allocates_sequence_and_orders_messages() -> None:
    store = FakeChatStore()
    chat = await store.create_session(user_id=uuid.uuid4(), title="t")

    seqs = [await store.allocate_seq(chat.id) for _ in range(3)]
    assert seqs == [1, 2, 3]

    await store.add_message(session_id=chat.id, seq=2, role="user", content="second")
    await store.add_message(session_id=chat.id, seq=1, role="system", content="first")
    await store.add_message(
        session_id=chat.id,
        seq=3,
        role="assistant",
        content="third",
        provider="openai",
        model="gpt-4o-mini",
    )

    ordered = await store.list_messages(chat.id)
    assert [m.content for m in ordered] == ["first", "second", "third"]


@pytest.mark.anyio
async def test_fake_usage_store_records_event() -> None:
    store = FakeUsageStore()
    session_id = uuid.uuid4()

    event = await store.record(
        session_id=session_id,
        provider="openai",
        model="gpt-4o-mini",
        token_source="provider_reported",
        total_tokens=18,
    )

    assert store.events == [event]
    assert event.session_id == session_id
    assert event.token_source == "provider_reported"
