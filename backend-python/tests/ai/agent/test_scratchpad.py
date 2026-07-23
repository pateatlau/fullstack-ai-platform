"""Tests for agent scratchpad (Phase 3)."""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

from app.ai.agent.models.context import AgentContext
from app.ai.agent.models.messages import AgentMessage
from app.ai.agent.scratchpad import (
    Scratchpad,
    ScratchpadAlreadyExistsError,
    ScratchpadEntry,
    ScratchpadNotFoundError,
    ScratchpadStore,
)
from app.ai.agent.state.manager import AgentStateManager
from app.providers.base import ChatMessageInput
from app.schemas.chat import ChatMessageSchema
from tests.fakes import FakeProvider


def test_scratchpad_store_creates_isolated_scratchpads() -> None:
    store = ScratchpadStore()
    pad_a = store.create("exec-a")
    pad_b = store.create("exec-b")

    pad_a.append_thought("plan A")
    pad_b.append_thought("plan B")

    assert len(pad_a) == 1
    assert len(pad_b) == 1
    assert pad_a.entries[0].content == "plan A"
    assert pad_b.entries[0].content == "plan B"
    assert store.get("exec-a") is pad_a
    assert store.get("exec-b") is pad_b


def test_scratchpad_store_rejects_duplicate_execution_id() -> None:
    store = ScratchpadStore()
    store.create("exec-dup")

    with pytest.raises(ScratchpadAlreadyExistsError) as exc_info:
        store.create("exec-dup")

    assert exc_info.value.execution_id == "exec-dup"


def test_scratchpad_store_remove_clears_entries() -> None:
    store = ScratchpadStore()
    scratchpad = store.create("exec-remove")
    scratchpad.append_observation("result")

    store.remove("exec-remove")

    assert store.get("exec-remove") is None
    assert len(scratchpad) == 0


def test_scratchpad_store_require_raises_when_missing() -> None:
    store = ScratchpadStore()

    with pytest.raises(ScratchpadNotFoundError) as exc_info:
        store.require("missing-exec")

    assert exc_info.value.execution_id == "missing-exec"


def test_to_message_context_maps_roles_and_tool_results() -> None:
    scratchpad = Scratchpad("exec-msg")
    scratchpad.extend_messages(
        [
            AgentMessage(role="system", content="You are helpful."),
            AgentMessage(role="user", content="Search the web."),
        ]
    )
    scratchpad.append_thought("I should call web_search.")
    scratchpad.append_observation("Found 3 results.")
    scratchpad.append_tool_result(
        tool_call_id="call-1",
        content='{"success": true}',
    )

    messages = scratchpad.to_message_context()

    assert len(messages) == 5
    assert isinstance(messages[0], AgentMessage)
    assert messages[0].role == "system"
    assert messages[0].content == "You are helpful."
    assert isinstance(messages[1], AgentMessage)
    assert messages[1].role == "user"
    assert isinstance(messages[2], AgentMessage)
    assert messages[2].role == "assistant"
    assert messages[2].content == "Thought: I should call web_search."
    assert isinstance(messages[3], AgentMessage)
    assert messages[3].content == "Observation: Found 3 results."
    assert messages[4] == {
        "role": "tool",
        "tool_call_id": "call-1",
        "content": '{"success": true}',
    }


def test_tool_entry_requires_tool_call_id() -> None:
    entry = ScratchpadEntry(kind="tool", content="result")

    with pytest.raises(ValueError, match="tool_call_id"):
        entry.to_message()


@pytest.mark.anyio
async def test_to_message_context_is_compatible_with_llm_provider() -> None:
    scratchpad = Scratchpad("exec-llm")
    scratchpad.extend_messages([AgentMessage(role="user", content="Hello")])
    scratchpad.append_thought("Respond politely.")

    chat_messages = [
        ChatMessageSchema(role=message.role, content=message.content)
        for message in scratchpad.to_message_context()
        if isinstance(message, AgentMessage)
    ]

    provider = FakeProvider(response="Hi there.")
    completion = await provider.complete_chat(chat_messages, "gpt-4o-mini")

    assert completion.content == "Hi there."


@pytest.mark.anyio
async def test_to_message_context_tool_entries_work_with_tool_completion() -> None:
    scratchpad = Scratchpad("exec-tools")
    scratchpad.extend_messages([AgentMessage(role="user", content="Search")])
    scratchpad.append_tool_result(
        tool_call_id="call-1",
        content='{"success": true, "results": []}',
    )

    provider_messages: list[ChatMessageInput] = []
    for message in scratchpad.to_message_context():
        if isinstance(message, AgentMessage):
            provider_messages.append(
                ChatMessageSchema(role=message.role, content=message.content)
            )
        else:
            provider_messages.append(message)

    provider = FakeProvider(response="Done.")
    completion = await provider.complete_chat_with_tools(
        provider_messages,
        "gpt-4o-mini",
        tools=[],
    )

    assert completion.content == "Done."


def test_create_initial_state_wires_scratchpad_in_store() -> None:
    store = ScratchpadStore()
    context = AgentContext(execution_id="exec-state")

    state = AgentStateManager.create_initial_state(context, scratchpad_store=store)

    scratchpad = store.require(context.execution_id)
    assert state.execution_id == context.execution_id
    assert isinstance(scratchpad, Scratchpad)
    assert scratchpad.execution_id == context.execution_id


def test_scratchpad_modules_have_no_db_or_chat_imports() -> None:
    repo_root = Path(__file__).resolve().parents[3]
    module_paths = [
        repo_root / "app/ai/agent/scratchpad/scratchpad.py",
        repo_root / "app/ai/agent/scratchpad/store.py",
    ]
    forbidden_roots = ("app.services", "app.db", "app.schemas.chat", "fastapi")

    for module_path in module_paths:
        tree = ast.parse(module_path.read_text(encoding="utf-8"))
        imported_modules = {
            node.module
            for node in ast.walk(tree)
            if isinstance(node, ast.ImportFrom) and node.module is not None
        }
        imported_modules.update(
            alias.name
            for node in ast.walk(tree)
            if isinstance(node, ast.Import)
            for alias in node.names
        )
        for forbidden in forbidden_roots:
            assert not any(
                module == forbidden or module.startswith(f"{forbidden}.")
                for module in imported_modules
            ), f"{module_path.name} must not import {forbidden}"
