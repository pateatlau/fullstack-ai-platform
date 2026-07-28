"""Voice WebSocket message schemas."""

from typing import Literal

from pydantic import BaseModel, Field


class TranscriptEvent(BaseModel):
    """Speech-to-text transcript event (partial or final)."""

    type: Literal["transcript_partial", "transcript_final"]
    text: str
    stability: float | None = Field(default=None, ge=0.0, le=1.0)


class AudioOutEvent(BaseModel):
    """Text-to-speech audio output event."""

    type: Literal["audio_out"]
    seq: int = Field(ge=0)
    payload_b64: str


# WebSocket message models (no shared base class for type compatibility)


class SessionStartedMessage(BaseModel):
    """Server → Client: handshake complete."""

    type: Literal["session_started"] = "session_started"
    voice_session_id: str
    audio_format: str = "pcm16_24k_mono"


class AudioInMessage(BaseModel):
    """Client → Server: inbound audio chunk."""

    type: Literal["audio_in"] = "audio_in"
    seq: int = Field(ge=0)
    payload_b64: str
    final: bool = False


class TranscriptPartialMessage(BaseModel):
    """Server → Client: interim STT."""

    type: Literal["transcript_partial"] = "transcript_partial"
    text: str
    stability: float | None = Field(default=None, ge=0.0, le=1.0)


class TranscriptFinalMessage(BaseModel):
    """Server → Client: final STT for chat turn."""

    type: Literal["transcript_final"] = "transcript_final"
    text: str


class AssistantTextDeltaMessage(BaseModel):
    """Server → Client: assistant text delta (mirrors SSE delta)."""

    type: Literal["assistant_text_delta"] = "assistant_text_delta"
    text: str


class AudioOutMessage(BaseModel):
    """Server → Client: outbound synthesized audio."""

    type: Literal["audio_out"] = "audio_out"
    seq: int = Field(ge=0)
    payload_b64: str


class ToolStartMessage(BaseModel):
    """Server → Client: tool execution started."""

    type: Literal["tool_start"] = "tool_start"
    name: str


class ToolEndMessage(BaseModel):
    """Server → Client: tool execution completed."""

    type: Literal["tool_end"] = "tool_end"
    name: str
    success: bool


class InterruptMessage(BaseModel):
    """Client → Server: user barge-in."""

    type: Literal["interrupt"] = "interrupt"


class InterruptedMessage(BaseModel):
    """Server → Client: cancel acknowledged."""

    type: Literal["interrupted"] = "interrupted"


class TurnCompleteMessage(BaseModel):
    """Server → Client: turn finished (mirrors SSE end)."""

    type: Literal["turn_complete"] = "turn_complete"
    tools_used: list[str] | None = None
    retrieved_chunk_count: int | None = Field(default=None, ge=0)
    citations: list[dict] | None = None


class HeartbeatMessage(BaseModel):
    """Bidirectional: keep-alive."""

    type: Literal["heartbeat"] = "heartbeat"
    ts: float


class SessionEndMessage(BaseModel):
    """Client → Server: client-initiated teardown."""

    type: Literal["session_end"] = "session_end"


class SessionClosedMessage(BaseModel):
    """Server → Client: session ended."""

    type: Literal["session_closed"] = "session_closed"
    reason: str


class ErrorMessage(BaseModel):
    """Server → Client: recoverable/fatal errors."""

    type: Literal["error"] = "error"
    code: str
    message: str
