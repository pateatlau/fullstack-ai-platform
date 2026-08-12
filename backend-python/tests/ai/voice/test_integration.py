"""End-to-end voice backend integration — fake STT/TTS + UnifiedChatService wiring."""

from __future__ import annotations

import asyncio
import json
import uuid
from collections.abc import AsyncIterable, AsyncIterator
from typing import Any

import pytest
from fastapi import Request

from app.ai.voice.chat_bridge import VoiceChatBridge, parse_sse_frame
from app.ai.voice.config import VoiceConfig
from app.ai.voice.interrupt import InterruptController
from app.ai.voice.streaming import VoiceStreamBridge
from app.ai.voice.tts import TtsPipeline
from app.core.caller import CallerContext
from app.schemas.chat import (
    ApprovalRequiredFrame,
    ChatMessageSchema,
    ChatRequestSchema,
    DeltaFrame,
    EndFrame,
    ProposedToolCallFrame,
    StartFrame,
    ToolEndFrame,
    ToolStartFrame,
)
from app.services.chat_service import format_sse

pytestmark = pytest.mark.anyio


class FakeTtsProvider:
    """Deterministic TTS double for integration tests."""

    def __init__(self, audio: bytes = b"\x01\x02\x03\x04") -> None:
        self._audio = audio
        self.synthesized: list[str] = []

    async def synthesize_stream(
        self, text_chunks: AsyncIterable[str]
    ) -> AsyncIterator[bytes]:
        async for text in text_chunks:
            self.synthesized.append(text)
        yield self._audio


class FakeUnifiedChatService:
    """Yield canned SSE frames like ``UnifiedChatService.stream_execute``."""

    def __init__(
        self,
        *,
        include_tools: bool = False,
        delta_delay_seconds: float = 0.0,
    ) -> None:
        self._include_tools = include_tools
        self._delta_delay_seconds = delta_delay_seconds
        self.requests: list[ChatRequestSchema] = []

    async def stream_execute(
        self,
        request: ChatRequestSchema,
        http_request: Request,
        caller: CallerContext | None = None,
        prep: object | None = None,
    ) -> AsyncIterator[str]:
        del http_request, caller, prep
        self.requests.append(request)
        response_id = "resp_test123"
        yield format_sse("start", StartFrame(id=response_id))
        if self._include_tools:
            yield format_sse(
                "tool_start",
                ToolStartFrame(
                    id=response_id,
                    tool_name="web_search",
                    call_id="call_1",
                ),
            )
            yield format_sse(
                "tool_end",
                ToolEndFrame(
                    id=response_id,
                    tool_name="web_search",
                    call_id="call_1",
                    success=True,
                ),
            )
        yield format_sse("delta", DeltaFrame(id=response_id, content="Hello "))
        if self._delta_delay_seconds:
            await asyncio.sleep(self._delta_delay_seconds)
        yield format_sse("delta", DeltaFrame(id=response_id, content="world."))
        yield format_sse("end", EndFrame(id=response_id, finish_reason="stop"))


class NoopChatService:
    async def prepare_stream(
        self, request: ChatRequestSchema, caller: CallerContext
    ) -> None:
        del request, caller
        return None


async def _run_bridge_turn(*, include_tools: bool = True) -> list[dict[str, Any]]:
    messages: list[dict[str, Any]] = []

    async def capture(payload: str) -> None:
        messages.append(json.loads(payload))

    bridge = VoiceStreamBridge(VoiceConfig())
    interrupt = InterruptController()
    tts = TtsPipeline(FakeTtsProvider(), VoiceConfig())
    voice_session_id = "vs_integration"

    chat_bridge = VoiceChatBridge(
        stream_bridge=bridge,
        tts_pipeline=tts,
        interrupt=interrupt,
        voice_session_id=voice_session_id,
        send_json=capture,
    )

    unified = FakeUnifiedChatService(include_tools=include_tools)
    caller = CallerContext.for_user(uuid.uuid4())
    request = ChatRequestSchema(
        messages=[ChatMessageSchema(role="user", content="What is up?")],
    )

    await chat_bridge.run_turn(
        unified_service=unified,  # type: ignore[arg-type]
        chat_service=NoopChatService(),  # type: ignore[arg-type]
        request=request,
        http_request=Request({"type": "http", "method": "GET", "path": "/"}),
        caller=caller,
        prep=None,
    )
    return messages


async def test_parse_sse_frame_round_trip() -> None:
    frame = format_sse("delta", DeltaFrame(id="resp_1", content="hi"))
    parsed = parse_sse_frame(frame)
    assert parsed is not None
    event, data = parsed
    assert event == "delta"
    assert data["content"] == "hi"


async def test_voice_chat_bridge_maps_sse_to_ws_and_tts() -> None:
    messages = await _run_bridge_turn()

    types = [message["type"] for message in messages]
    assert "assistant_text_delta" in types
    assert "tool_start" in types
    assert "tool_end" in types
    assert "audio_out" in types
    assert "turn_complete" in types

    deltas = [
        message["text"]
        for message in messages
        if message["type"] == "assistant_text_delta"
    ]
    assert "".join(deltas) == "Hello world."

    # `turn_complete` mirrors the SSE `end` frame, so it lands once the text is
    # final — ahead of the trailing audio still being synthesised.
    assert types.index("turn_complete") > types.index("assistant_text_delta")

    turn_complete = messages[types.index("turn_complete")]
    assert turn_complete["tools_used"] == ["web_search"]


async def test_voice_chat_bridge_uses_unified_chat_request() -> None:
    bridge = VoiceStreamBridge(VoiceConfig())
    interrupt = InterruptController()
    tts_provider = FakeTtsProvider()
    tts = TtsPipeline(tts_provider, VoiceConfig())
    sent: list[str] = []

    async def capture(payload: str) -> None:
        sent.append(payload)

    unified = FakeUnifiedChatService()
    caller = CallerContext.for_user(uuid.uuid4())
    request = ChatRequestSchema(
        messages=[ChatMessageSchema(role="user", content="Tell me a joke")],
        use_web_search=True,
    )

    chat_bridge = VoiceChatBridge(
        stream_bridge=bridge,
        tts_pipeline=tts,
        interrupt=interrupt,
        voice_session_id="vs_req",
        send_json=capture,
    )
    await chat_bridge.run_turn(
        unified_service=unified,  # type: ignore[arg-type]
        chat_service=NoopChatService(),  # type: ignore[arg-type]
        request=request,
        http_request=Request({"type": "http", "method": "GET", "path": "/"}),
        caller=caller,
        prep=None,
    )

    assert len(unified.requests) == 1
    assert unified.requests[0].use_web_search is True
    assert tts_provider.synthesized
    assert "Hello" in tts_provider.synthesized[0]


async def test_voice_chat_bridge_cancelled_turn_skips_turn_complete() -> None:
    bridge = VoiceStreamBridge(VoiceConfig())
    interrupt = InterruptController()
    tts = TtsPipeline(FakeTtsProvider(), VoiceConfig())
    sent: list[dict[str, Any]] = []

    async def capture(payload: str) -> None:
        sent.append(json.loads(payload))

    class SlowUnified(FakeUnifiedChatService):
        async def stream_execute(self, request, http_request, caller=None, prep=None):
            del request, http_request, caller, prep
            response_id = "resp_slow"
            yield format_sse("start", StartFrame(id=response_id))
            await asyncio.sleep(0.2)
            yield format_sse("delta", DeltaFrame(id=response_id, content="late"))
            yield format_sse("end", EndFrame(id=response_id, finish_reason="stop"))

    voice_session_id = "vs_cancel"
    chat_bridge = VoiceChatBridge(
        stream_bridge=bridge,
        tts_pipeline=tts,
        interrupt=interrupt,
        voice_session_id=voice_session_id,
        send_json=capture,
    )

    turn_task = asyncio.create_task(
        chat_bridge.run_turn(
            unified_service=SlowUnified(),  # type: ignore[arg-type]
            chat_service=NoopChatService(),  # type: ignore[arg-type]
            request=ChatRequestSchema(
                messages=[ChatMessageSchema(role="user", content="wait")],
            ),
            http_request=Request({"type": "http", "method": "GET", "path": "/"}),
            caller=CallerContext.for_user(uuid.uuid4()),
            prep=None,
        )
    )
    interrupt.register_llm_task(voice_session_id, turn_task)
    await asyncio.sleep(0.05)
    await interrupt.cancel_all(voice_session_id)

    with pytest.raises(asyncio.CancelledError):
        await turn_task

    assert not any(message.get("type") == "turn_complete" for message in sent)


async def test_voice_chat_bridge_surfaces_approval_required_error() -> None:
    """Voice turns cannot resume HITL approvals; surface a clear WS error."""

    class ApprovalUnified(FakeUnifiedChatService):
        async def stream_execute(self, request, http_request, caller=None, prep=None):
            del request, http_request, caller, prep
            response_id = "resp_approval"
            approval_id = uuid.uuid4()
            correlation_id = uuid.uuid4()
            yield format_sse("start", StartFrame(id=response_id))
            yield format_sse(
                "approval_required",
                ApprovalRequiredFrame(
                    id=response_id,
                    approval_id=approval_id,
                    approval_correlation_id=correlation_id,
                    proposed_calls=[
                        ProposedToolCallFrame(
                            name="web_search",
                            arguments={"query": "news"},
                            call_id="call_1",
                        )
                    ],
                ),
            )

    bridge = VoiceStreamBridge(VoiceConfig())
    interrupt = InterruptController()
    tts = TtsPipeline(FakeTtsProvider(), VoiceConfig())
    sent: list[dict[str, Any]] = []

    async def capture(payload: str) -> None:
        sent.append(json.loads(payload))

    chat_bridge = VoiceChatBridge(
        stream_bridge=bridge,
        tts_pipeline=tts,
        interrupt=interrupt,
        voice_session_id="vs_approval",
        send_json=capture,
    )
    await chat_bridge.run_turn(
        unified_service=ApprovalUnified(),  # type: ignore[arg-type]
        chat_service=NoopChatService(),  # type: ignore[arg-type]
        request=ChatRequestSchema(
            messages=[ChatMessageSchema(role="user", content="Search the web")],
            use_web_search=True,
        ),
        http_request=Request({"type": "http", "method": "GET", "path": "/"}),
        caller=CallerContext.for_user(uuid.uuid4()),
        prep=None,
    )

    errors = [message for message in sent if message.get("type") == "error"]
    assert len(errors) == 1
    assert errors[0]["code"] == "approval_required"
    assert not any(message.get("type") == "turn_complete" for message in sent)
