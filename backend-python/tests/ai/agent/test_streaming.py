"""Tests for agent streaming engine (Phase 5)."""

from __future__ import annotations

import ast
import uuid
from pathlib import Path

import pytest

from app.ai.agent import AgentStreamEvent, AgentStreamEventType
from app.ai.agent.models.events import (
    CompleteEventPayload,
    PlanningEventPayload,
    ReflectionDecision,
    ReflectionEventPayload,
    StartEventPayload,
    TokenEventPayload,
    ToolEndEventPayload,
    ToolStartEventPayload,
)
from app.ai.agent.streaming import (
    InMemoryStreamPublisher,
    NoOpStreamPublisher,
    QueueStreamPublisher,
    StreamPublisherClosedError,
    sse_frame_from_agent_event,
)
from app.schemas.chat import (
    DeltaFrame,
    EndFrame,
    ErrorFrame,
    StartFrame,
    ToolEndFrame,
    ToolStartFrame,
)


def test_agent_stream_event_factories_validate_payloads() -> None:
    session_id = uuid.uuid4()
    event = AgentStreamEvent.start("exec-1", session_id=session_id)
    assert event.type == AgentStreamEventType.START
    start_payload = event.typed_payload()
    assert isinstance(start_payload, StartEventPayload)
    assert start_payload.session_id == session_id

    planning = AgentStreamEvent.planning("exec-1", iteration=2)
    planning_payload = planning.typed_payload()
    assert isinstance(planning_payload, PlanningEventPayload)
    assert planning_payload.iteration == 2

    tool_start = AgentStreamEvent.tool_start(
        "exec-1",
        tool_name="web_search",
        call_id="call-1",
    )
    assert isinstance(tool_start.typed_payload(), ToolStartEventPayload)

    tool_end = AgentStreamEvent.tool_end(
        "exec-1",
        tool_name="web_search",
        call_id="call-1",
        success=True,
    )
    assert isinstance(tool_end.typed_payload(), ToolEndEventPayload)

    token = AgentStreamEvent.token("exec-1", content="Hello")
    assert isinstance(token.typed_payload(), TokenEventPayload)

    reflection = AgentStreamEvent.reflection(
        "exec-1",
        decision=ReflectionDecision.REPLAN,
        reason="All tools failed.",
    )
    assert isinstance(reflection.typed_payload(), ReflectionEventPayload)

    complete = AgentStreamEvent.complete(
        "exec-1",
        finish_reason="stop",
        tools_used=["web_search"],
    )
    assert isinstance(complete.typed_payload(), CompleteEventPayload)

    error = AgentStreamEvent.error(
        "exec-1",
        code="agent_error",
        message="Something went wrong.",
    )
    assert error.type == AgentStreamEventType.ERROR


def test_agent_stream_event_round_trip_with_raw_payload_dict() -> None:
    event = AgentStreamEvent(
        type=AgentStreamEventType.PLANNING,
        execution_id="abc123",
        payload={"iteration": 1},
    )
    restored = AgentStreamEvent.model_validate(event.model_dump())
    assert restored.type == AgentStreamEventType.PLANNING
    planning_payload = restored.typed_payload()
    assert isinstance(planning_payload, PlanningEventPayload)
    assert planning_payload.iteration == 1


@pytest.mark.anyio
async def test_in_memory_stream_publisher_collects_events() -> None:
    publisher = InMemoryStreamPublisher()
    event = AgentStreamEvent.token("exec-1", content="Hi")

    await publisher.publish(event)
    await publisher.close()

    assert publisher.events == [event]

    with pytest.raises(StreamPublisherClosedError):
        await publisher.publish(event)


@pytest.mark.anyio
async def test_queue_stream_publisher_puts_events_on_queue() -> None:
    publisher = QueueStreamPublisher()
    event = AgentStreamEvent.planning("exec-1", iteration=0)

    await publisher.publish(event)
    await publisher.close()

    assert await publisher.queue.get() == event
    assert await publisher.queue.get() is None


@pytest.mark.anyio
async def test_no_op_stream_publisher_discards_events() -> None:
    publisher = NoOpStreamPublisher()
    await publisher.publish(AgentStreamEvent.start("exec-1"))
    await publisher.close()


@pytest.mark.anyio
async def test_queue_stream_publisher_rejects_publish_after_close() -> None:
    publisher = QueueStreamPublisher()
    await publisher.close()

    with pytest.raises(StreamPublisherClosedError):
        await publisher.publish(AgentStreamEvent.start("exec-1"))


def test_sse_frame_from_agent_event_maps_start() -> None:
    session_id = uuid.uuid4()
    event = AgentStreamEvent.start("exec-1", session_id=session_id)

    mapped = sse_frame_from_agent_event(event)

    assert mapped is not None
    event_name, frame = mapped
    assert event_name == "start"
    assert isinstance(frame, StartFrame)
    assert frame.id == "exec-1"
    assert frame.session_id == session_id


def test_sse_frame_from_agent_event_maps_token_to_delta() -> None:
    event = AgentStreamEvent.token("exec-1", content="chunk")

    mapped = sse_frame_from_agent_event(event)

    assert mapped is not None
    event_name, frame = mapped
    assert event_name == "delta"
    assert isinstance(frame, DeltaFrame)
    assert frame.content == "chunk"


def test_sse_frame_from_agent_event_maps_tool_lifecycle() -> None:
    start_event = AgentStreamEvent.tool_start(
        "exec-1",
        tool_name="web_search",
        call_id="call-42",
    )
    end_event = AgentStreamEvent.tool_end(
        "exec-1",
        tool_name="web_search",
        call_id="call-42",
        success=False,
    )

    start_mapped = sse_frame_from_agent_event(start_event)
    end_mapped = sse_frame_from_agent_event(end_event)

    assert start_mapped is not None
    start_name, start_frame = start_mapped
    assert start_name == "tool_start"
    assert isinstance(start_frame, ToolStartFrame)
    assert start_frame.id == "exec-1"
    assert start_frame.tool_name == "web_search"
    assert start_frame.call_id == "call-42"

    assert end_mapped is not None
    end_name, end_frame = end_mapped
    assert end_name == "tool_end"
    assert isinstance(end_frame, ToolEndFrame)
    assert end_frame.id == "exec-1"
    assert end_frame.tool_name == "web_search"
    assert end_frame.call_id == "call-42"
    assert end_frame.success is False


def test_sse_frame_from_agent_event_maps_complete_and_error() -> None:
    complete = AgentStreamEvent.complete("exec-1", finish_reason="length")
    error = AgentStreamEvent.error(
        "exec-1",
        code="iteration_limit",
        message="Max iterations reached.",
    )

    complete_mapped = sse_frame_from_agent_event(complete)
    error_mapped = sse_frame_from_agent_event(error)

    assert complete_mapped is not None
    complete_name, complete_frame = complete_mapped
    assert complete_name == "end"
    assert isinstance(complete_frame, EndFrame)
    assert complete_frame.id == "exec-1"
    assert complete_frame.finish_reason == "length"

    assert error_mapped is not None
    error_name, error_frame = error_mapped
    assert error_name == "error"
    assert isinstance(error_frame, ErrorFrame)
    assert error_frame.id == "exec-1"
    assert error_frame.code == "iteration_limit"
    assert error_frame.message == "Max iterations reached."


def test_sse_frame_from_agent_event_uses_response_id_override() -> None:
    event = AgentStreamEvent.token("exec-1", content="x")

    mapped = sse_frame_from_agent_event(event, response_id="resp-99")

    assert mapped is not None
    _, frame = mapped
    assert frame.id == "resp-99"


def test_sse_frame_from_agent_event_returns_none_for_internal_events() -> None:
    planning = AgentStreamEvent.planning("exec-1", iteration=1)
    reflection = AgentStreamEvent.reflection(
        "exec-1",
        decision="FINISH",
    )

    assert sse_frame_from_agent_event(planning) is None
    assert sse_frame_from_agent_event(reflection) is None


def test_sse_frame_names_match_chat_schema_literals() -> None:
    allowed = {"start", "delta", "tool_start", "tool_end", "end", "error"}
    events = [
        AgentStreamEvent.start("exec-1"),
        AgentStreamEvent.token("exec-1", content="a"),
        AgentStreamEvent.tool_start("exec-1", tool_name="t", call_id="c"),
        AgentStreamEvent.tool_end(
            "exec-1",
            tool_name="t",
            call_id="c",
            success=True,
        ),
        AgentStreamEvent.complete("exec-1"),
        AgentStreamEvent.error("exec-1", code="e", message="m"),
    ]

    for event in events:
        mapped = sse_frame_from_agent_event(event)
        assert mapped is not None
        event_name, frame = mapped
        assert event_name in allowed
        assert frame.type == event_name


def test_publisher_module_has_no_transport_or_domain_imports() -> None:
    repo_root = Path(__file__).resolve().parents[3]
    module_path = repo_root / "app/ai/agent/streaming/publisher.py"
    forbidden_roots = (
        "app.services",
        "app.db",
        "app.schemas.chat",
        "fastapi",
    )

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
        ), f"publisher.py must not import {forbidden}"


def test_adapter_module_does_not_import_format_sse_or_fastapi() -> None:
    repo_root = Path(__file__).resolve().parents[3]
    module_path = repo_root / "app/ai/agent/streaming/adapter.py"
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

    assert "app.services.chat_service" not in imported_modules
    assert not any(module.startswith("fastapi") for module in imported_modules)


@pytest.mark.anyio
async def test_queue_consumer_can_drain_until_sentinel() -> None:
    publisher = QueueStreamPublisher()
    events = [
        AgentStreamEvent.start("exec-1"),
        AgentStreamEvent.token("exec-1", content="a"),
        AgentStreamEvent.complete("exec-1"),
    ]

    for event in events:
        await publisher.publish(event)
    await publisher.close()

    collected: list[AgentStreamEvent | None] = []
    while True:
        item = await publisher.queue.get()
        collected.append(item)
        if item is None:
            break

    assert collected == [*events, None]
