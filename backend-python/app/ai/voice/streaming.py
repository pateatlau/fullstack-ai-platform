"""Voice stream bridge — audio chunk framing; WS message codec."""

import base64
import json
from typing import Union

from pydantic import ValidationError

from app.ai.voice.config import VoiceConfig
from app.ai.voice.exceptions import VoiceSessionError
from app.schemas.voice import (
    AssistantTextDeltaMessage,
    AudioInMessage,
    AudioOutMessage,
    ErrorMessage,
    HeartbeatMessage,
    InterruptedMessage,
    InterruptMessage,
    SessionClosedMessage,
    SessionEndMessage,
    SessionStartedMessage,
    ToolEndMessage,
    ToolStartMessage,
    TranscriptFinalMessage,
    TranscriptPartialMessage,
    TurnCompleteMessage,
)

ClientMessage = Union[
    AudioInMessage,
    InterruptMessage,
    HeartbeatMessage,
    SessionEndMessage,
]

ServerMessage = Union[
    SessionStartedMessage,
    TranscriptPartialMessage,
    TranscriptFinalMessage,
    AssistantTextDeltaMessage,
    AudioOutMessage,
    ToolStartMessage,
    ToolEndMessage,
    InterruptedMessage,
    TurnCompleteMessage,
    HeartbeatMessage,
    SessionClosedMessage,
    ErrorMessage,
]


class VoiceStreamBridge:
    """Encode/decode WebSocket JSON frames; bridge audio ↔ pipelines.

    Handles JSON serialization, base64 codec for audio payloads,
    and enforces chunk size limits per VoiceConfig.
    """

    def __init__(self, config: VoiceConfig) -> None:
        """Initialize voice stream bridge.

        Args:
            config: Voice configuration with max_chunk_bytes.
        """
        self._config = config

    def encode_message(self, message: ServerMessage) -> str:
        """Encode a server message to JSON for WebSocket send.

        Args:
            message: Pydantic message model to encode.

        Returns:
            JSON string ready for WebSocket transmission.

        Raises:
            VoiceSessionError: On serialization failure.
        """
        try:
            return message.model_dump_json()
        except Exception as e:
            raise VoiceSessionError(f"Failed to encode message: {e}") from e

    def decode_message(self, data: str) -> ClientMessage:
        """Decode and validate a JSON WebSocket message from client.

        Args:
            data: JSON string from WebSocket.

        Returns:
            Validated client message model.

        Raises:
            VoiceSessionError: On parse/validation failure or unknown message type.
        """
        try:
            raw = json.loads(data)
        except json.JSONDecodeError as e:
            raise VoiceSessionError(f"Invalid JSON: {e}") from e

        if not isinstance(raw, dict):
            raise VoiceSessionError("Message must be a JSON object")

        msg_type = raw.get("type")
        if not msg_type:
            raise VoiceSessionError("Message missing 'type' field")

        try:
            if msg_type == "audio_in":
                msg = AudioInMessage.model_validate(raw)
                self._validate_audio_chunk(msg.payload_b64)
                return msg
            elif msg_type == "interrupt":
                return InterruptMessage.model_validate(raw)
            elif msg_type == "heartbeat":
                return HeartbeatMessage.model_validate(raw)
            elif msg_type == "session_end":
                return SessionEndMessage.model_validate(raw)
            else:
                raise VoiceSessionError(f"Unknown message type: {msg_type}")
        except ValidationError as e:
            raise VoiceSessionError(f"Message validation failed: {e}") from e

    def decode_audio_payload(self, payload_b64: str) -> bytes:
        """Decode base64 audio payload to raw bytes.

        Args:
            payload_b64: Base64-encoded audio data.

        Returns:
            Raw audio bytes.

        Raises:
            VoiceSessionError: On invalid base64.
        """
        try:
            return base64.b64decode(payload_b64, validate=True)
        except Exception as e:
            raise VoiceSessionError(f"Invalid base64 audio payload: {e}") from e

    def encode_audio_payload(self, audio_bytes: bytes) -> str:
        """Encode raw audio bytes to base64.

        Args:
            audio_bytes: Raw audio data.

        Returns:
            Base64-encoded string.

        Raises:
            VoiceSessionError: On chunk size violation.
        """
        if len(audio_bytes) > self._config.max_chunk_bytes:
            raise VoiceSessionError(
                f"Audio chunk exceeds max size: {len(audio_bytes)} > "
                f"{self._config.max_chunk_bytes} bytes"
            )
        return base64.b64encode(audio_bytes).decode("ascii")

    def _validate_audio_chunk(self, payload_b64: str) -> None:
        """Validate inbound audio chunk size.

        Args:
            payload_b64: Base64-encoded payload from client.

        Raises:
            VoiceSessionError: If decoded size exceeds max_chunk_bytes.
        """
        # Early rejection: base64 encodes 3 bytes as 4 chars (4/3 ratio).
        # If encoded length > (max_chunk_bytes * 4/3), decoded will exceed limit.
        max_encoded_len = (self._config.max_chunk_bytes * 4 // 3) + 4
        if len(payload_b64) > max_encoded_len:
            raise VoiceSessionError(
                f"Audio chunk exceeds max size: encoded length {len(payload_b64)} "
                f"exceeds maximum {max_encoded_len}"
            )

        # Decode and validate actual size (reuses existing logic).
        audio_bytes = self.decode_audio_payload(payload_b64)
        if len(audio_bytes) > self._config.max_chunk_bytes:
            raise VoiceSessionError(
                f"Audio chunk exceeds max size: {len(audio_bytes)} > "
                f"{self._config.max_chunk_bytes} bytes"
            )
