"""Bridge UnifiedChatService SSE stream to voice WS messages and TTS."""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator, Awaitable, Callable
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Protocol, cast

if TYPE_CHECKING:
    from fastapi import Request

from app.ai.voice.interrupt import InterruptController
from app.ai.voice.streaming import VoiceStreamBridge
from app.ai.voice.tts import TtsPipeline
from app.core.caller import CallerContext
from app.schemas.chat import ChatRequestSchema
from app.schemas.voice import (
    AssistantTextDeltaMessage,
    AudioOutMessage,
    ErrorMessage,
    ToolEndMessage,
    ToolStartMessage,
    TurnCompleteMessage,
)
from app.services.chat_service import ChatService, _StreamPrep
from app.services.unified_chat_service import UnifiedChatService


class DisconnectCheck(Protocol):
    async def is_disconnected(self) -> bool: ...


def parse_sse_frame(raw: str) -> tuple[str, dict[str, Any]] | None:
    """Parse one SSE frame string into ``(event_name, data_dict)``."""
    block = raw.strip()
    if not block:
        return None

    event_name: str | None = None
    data_json: str | None = None
    for line in block.splitlines():
        if line.startswith("event: "):
            event_name = line.removeprefix("event: ")
        elif line.startswith("data: "):
            data_json = line.removeprefix("data: ")

    if event_name is None or data_json is None:
        return None

    return event_name, json.loads(data_json)


@dataclass
class VoiceTurnMetadata:
    """Metadata collected from SSE frames for ``turn_complete`` parity."""

    tools_used: list[str] = field(default_factory=list)
    retrieved_chunk_count: int | None = None
    citations: list[dict[str, Any]] | None = None


async def _queue_to_text_stream(
    queue: asyncio.Queue[str | None],
) -> AsyncIterator[str]:
    while True:
        item = await queue.get()
        if item is None:
            break
        yield item


class VoiceChatBridge:
    """Consume ``UnifiedChatService.stream_execute`` and drive WS + TTS output."""

    def __init__(
        self,
        *,
        stream_bridge: VoiceStreamBridge,
        tts_pipeline: TtsPipeline,
        interrupt: InterruptController,
        voice_session_id: str,
        send_json: Callable[[str], Awaitable[None]],
    ) -> None:
        self._stream_bridge = stream_bridge
        self._tts_pipeline = tts_pipeline
        self._interrupt = interrupt
        self._voice_session_id = voice_session_id
        self._send_json = send_json
        self._max_chunk_bytes = stream_bridge.max_chunk_bytes

    async def run_turn(
        self,
        *,
        unified_service: UnifiedChatService,
        chat_service: ChatService,
        request: ChatRequestSchema,
        http_request: DisconnectCheck,
        caller: CallerContext,
        prep: _StreamPrep | None,
    ) -> None:
        """Run one assistant turn: SSE consumption, WS text events, and TTS audio."""
        metadata = VoiceTurnMetadata()
        text_queue: asyncio.Queue[str | None] = asyncio.Queue()
        audio_seq = 0
        turn_completed = False

        async def emit_tts() -> None:
            nonlocal audio_seq
            try:
                async for audio_chunk in self._tts_pipeline.process(
                    _queue_to_text_stream(text_queue)
                ):
                    for chunk in self._split_audio(audio_chunk):
                        payload_b64 = self._stream_bridge.encode_audio_payload(chunk)
                        message = AudioOutMessage(
                            seq=audio_seq, payload_b64=payload_b64
                        )
                        await self._send_json(
                            self._stream_bridge.encode_message(message)
                        )
                        audio_seq += 1
            except asyncio.CancelledError:
                raise

        tts_task = asyncio.create_task(emit_tts())
        self._interrupt.register_tts_pipeline(
            self._voice_session_id, self._tts_pipeline
        )
        self._interrupt.register_tts_task(self._voice_session_id, tts_task)
        self._interrupt.set_turn_active(self._voice_session_id, True)

        try:
            async for sse_frame in unified_service.stream_execute(
                request, cast("Request", http_request), caller, prep
            ):
                parsed = parse_sse_frame(sse_frame)
                if parsed is None:
                    continue
                event, data = parsed

                if event == "start":
                    continue
                if event == "retrieval_complete":
                    metadata.retrieved_chunk_count = int(data.get("chunk_count", 0))
                    continue
                if event == "tool_start":
                    tool_name = str(data.get("tool_name", ""))
                    if tool_name and tool_name not in metadata.tools_used:
                        metadata.tools_used.append(tool_name)
                    await self._send_json(
                        self._stream_bridge.encode_message(
                            ToolStartMessage(name=tool_name)
                        )
                    )
                    continue
                if event == "tool_end":
                    await self._send_json(
                        self._stream_bridge.encode_message(
                            ToolEndMessage(
                                name=str(data.get("tool_name", "")),
                                success=bool(data.get("success", False)),
                            )
                        )
                    )
                    continue
                if event == "delta":
                    text = str(data.get("content", ""))
                    if text:
                        await self._send_json(
                            self._stream_bridge.encode_message(
                                AssistantTextDeltaMessage(text=text)
                            )
                        )
                        await text_queue.put(text)
                    continue
                if event == "error":
                    await self._send_json(
                        self._stream_bridge.encode_message(
                            ErrorMessage(
                                code=str(data.get("code", "chat_error")),
                                message=str(data.get("message", "Chat stream failed")),
                            )
                        )
                    )
                    return
                if event == "end":
                    turn_completed = True
                    break
        finally:
            await text_queue.put(None)
            try:
                await tts_task
            except asyncio.CancelledError:
                pass
            self._interrupt.set_turn_active(self._voice_session_id, False)

        if turn_completed:
            await self._send_json(
                self._stream_bridge.encode_message(
                    TurnCompleteMessage(
                        tools_used=metadata.tools_used or None,
                        retrieved_chunk_count=metadata.retrieved_chunk_count,
                        citations=metadata.citations,
                    )
                )
            )

    def _split_audio(self, audio: bytes) -> list[bytes]:
        max_bytes = self._max_chunk_bytes
        if len(audio) <= max_bytes:
            return [audio]
        return [audio[i : i + max_bytes] for i in range(0, len(audio), max_bytes)]
