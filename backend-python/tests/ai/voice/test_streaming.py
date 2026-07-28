"""Tests for voice stream bridge and WebSocket message codec."""

import base64
import json

import pytest

from app.ai.voice.config import VoiceConfig
from app.ai.voice.exceptions import VoiceSessionError
from app.ai.voice.streaming import VoiceStreamBridge
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


@pytest.fixture
def voice_config() -> VoiceConfig:
    """Provide test voice config."""
    return VoiceConfig(max_chunk_bytes=4096)


@pytest.fixture
def bridge(voice_config: VoiceConfig) -> VoiceStreamBridge:
    """Provide voice stream bridge instance."""
    return VoiceStreamBridge(voice_config)


class TestServerMessageEncoding:
    """Test encoding of server → client messages."""

    def test_encode_session_started(self, bridge: VoiceStreamBridge) -> None:
        """Should encode session_started message."""
        msg = SessionStartedMessage(
            voice_session_id="vs_123", audio_format="pcm16_24k_mono"
        )
        encoded = bridge.encode_message(msg)
        decoded = json.loads(encoded)

        assert decoded["type"] == "session_started"
        assert decoded["voice_session_id"] == "vs_123"
        assert decoded["audio_format"] == "pcm16_24k_mono"

    def test_encode_transcript_partial(self, bridge: VoiceStreamBridge) -> None:
        """Should encode transcript_partial with optional stability."""
        msg = TranscriptPartialMessage(text="hello world", stability=0.8)
        encoded = bridge.encode_message(msg)
        decoded = json.loads(encoded)

        assert decoded["type"] == "transcript_partial"
        assert decoded["text"] == "hello world"
        assert decoded["stability"] == 0.8

    def test_encode_transcript_final(self, bridge: VoiceStreamBridge) -> None:
        """Should encode transcript_final."""
        msg = TranscriptFinalMessage(text="complete utterance")
        encoded = bridge.encode_message(msg)
        decoded = json.loads(encoded)

        assert decoded["type"] == "transcript_final"
        assert decoded["text"] == "complete utterance"

    def test_encode_assistant_text_delta(self, bridge: VoiceStreamBridge) -> None:
        """Should encode assistant_text_delta."""
        msg = AssistantTextDeltaMessage(text="The answer is")
        encoded = bridge.encode_message(msg)
        decoded = json.loads(encoded)

        assert decoded["type"] == "assistant_text_delta"
        assert decoded["text"] == "The answer is"

    def test_encode_audio_out(self, bridge: VoiceStreamBridge) -> None:
        """Should encode audio_out with base64 payload."""
        payload = base64.b64encode(b"fake audio data").decode("ascii")
        msg = AudioOutMessage(seq=5, payload_b64=payload)
        encoded = bridge.encode_message(msg)
        decoded = json.loads(encoded)

        assert decoded["type"] == "audio_out"
        assert decoded["seq"] == 5
        assert decoded["payload_b64"] == payload

    def test_encode_tool_start(self, bridge: VoiceStreamBridge) -> None:
        """Should encode tool_start."""
        msg = ToolStartMessage(name="web_search")
        encoded = bridge.encode_message(msg)
        decoded = json.loads(encoded)

        assert decoded["type"] == "tool_start"
        assert decoded["name"] == "web_search"

    def test_encode_tool_end(self, bridge: VoiceStreamBridge) -> None:
        """Should encode tool_end with success flag."""
        msg = ToolEndMessage(name="web_search", success=True)
        encoded = bridge.encode_message(msg)
        decoded = json.loads(encoded)

        assert decoded["type"] == "tool_end"
        assert decoded["name"] == "web_search"
        assert decoded["success"] is True

    def test_encode_interrupted(self, bridge: VoiceStreamBridge) -> None:
        """Should encode interrupted message."""
        msg = InterruptedMessage()
        encoded = bridge.encode_message(msg)
        decoded = json.loads(encoded)

        assert decoded["type"] == "interrupted"

    def test_encode_turn_complete(self, bridge: VoiceStreamBridge) -> None:
        """Should encode turn_complete with optional metadata."""
        msg = TurnCompleteMessage(
            tools_used=["web_search", "calculator"],
            retrieved_chunk_count=3,
            citations=[{"url": "https://example.com"}],
        )
        encoded = bridge.encode_message(msg)
        decoded = json.loads(encoded)

        assert decoded["type"] == "turn_complete"
        assert decoded["tools_used"] == ["web_search", "calculator"]
        assert decoded["retrieved_chunk_count"] == 3
        assert len(decoded["citations"]) == 1

    def test_encode_heartbeat(self, bridge: VoiceStreamBridge) -> None:
        """Should encode heartbeat with timestamp."""
        msg = HeartbeatMessage(ts=1234567890.123)
        encoded = bridge.encode_message(msg)
        decoded = json.loads(encoded)

        assert decoded["type"] == "heartbeat"
        assert decoded["ts"] == 1234567890.123

    def test_encode_session_closed(self, bridge: VoiceStreamBridge) -> None:
        """Should encode session_closed with reason."""
        msg = SessionClosedMessage(reason="timeout")
        encoded = bridge.encode_message(msg)
        decoded = json.loads(encoded)

        assert decoded["type"] == "session_closed"
        assert decoded["reason"] == "timeout"

    def test_encode_error(self, bridge: VoiceStreamBridge) -> None:
        """Should encode error message."""
        msg = ErrorMessage(code="empty_transcript", message="No audio detected")
        encoded = bridge.encode_message(msg)
        decoded = json.loads(encoded)

        assert decoded["type"] == "error"
        assert decoded["code"] == "empty_transcript"
        assert decoded["message"] == "No audio detected"


class TestClientMessageDecoding:
    """Test decoding of client → server messages."""

    def test_decode_audio_in(self, bridge: VoiceStreamBridge) -> None:
        """Should decode valid audio_in message."""
        audio_data = b"x" * 100
        payload = base64.b64encode(audio_data).decode("ascii")
        data = json.dumps(
            {"type": "audio_in", "seq": 10, "payload_b64": payload, "final": False}
        )

        msg = bridge.decode_message(data)

        assert isinstance(msg, AudioInMessage)
        assert msg.seq == 10
        assert msg.payload_b64 == payload
        assert msg.final is False

    def test_decode_audio_in_final(self, bridge: VoiceStreamBridge) -> None:
        """Should decode audio_in with final=true."""
        audio_data = b"y" * 50
        payload = base64.b64encode(audio_data).decode("ascii")
        data = json.dumps(
            {"type": "audio_in", "seq": 2, "payload_b64": payload, "final": True}
        )

        msg = bridge.decode_message(data)

        assert isinstance(msg, AudioInMessage)
        assert msg.final is True

    def test_decode_interrupt(self, bridge: VoiceStreamBridge) -> None:
        """Should decode interrupt message."""
        data = json.dumps({"type": "interrupt"})
        msg = bridge.decode_message(data)

        assert isinstance(msg, InterruptMessage)

    def test_decode_heartbeat(self, bridge: VoiceStreamBridge) -> None:
        """Should decode heartbeat from client."""
        data = json.dumps({"type": "heartbeat", "ts": 9876543210.456})
        msg = bridge.decode_message(data)

        assert isinstance(msg, HeartbeatMessage)
        assert msg.ts == 9876543210.456

    def test_decode_session_end(self, bridge: VoiceStreamBridge) -> None:
        """Should decode session_end message."""
        data = json.dumps({"type": "session_end"})
        msg = bridge.decode_message(data)

        assert isinstance(msg, SessionEndMessage)

    def test_decode_invalid_json(self, bridge: VoiceStreamBridge) -> None:
        """Should reject malformed JSON."""
        with pytest.raises(VoiceSessionError, match="Invalid JSON"):
            bridge.decode_message("{not valid json")

    def test_decode_non_object(self, bridge: VoiceStreamBridge) -> None:
        """Should reject non-object JSON."""
        with pytest.raises(VoiceSessionError, match="must be a JSON object"):
            bridge.decode_message('"string"')

        with pytest.raises(VoiceSessionError, match="must be a JSON object"):
            bridge.decode_message("[1, 2, 3]")

    def test_decode_missing_type(self, bridge: VoiceStreamBridge) -> None:
        """Should reject message without type field."""
        with pytest.raises(VoiceSessionError, match="missing 'type' field"):
            bridge.decode_message('{"seq": 1}')

    def test_decode_unknown_type(self, bridge: VoiceStreamBridge) -> None:
        """Should reject unknown message type."""
        with pytest.raises(VoiceSessionError, match="Unknown message type: foo"):
            bridge.decode_message('{"type": "foo"}')

    def test_decode_invalid_schema(self, bridge: VoiceStreamBridge) -> None:
        """Should reject message with invalid schema."""
        with pytest.raises(VoiceSessionError, match="validation failed"):
            bridge.decode_message('{"type": "audio_in"}')


class TestAudioPayloadCodec:
    """Test base64 audio codec and size limits."""

    def test_decode_audio_payload(self, bridge: VoiceStreamBridge) -> None:
        """Should decode valid base64 to bytes."""
        original = b"\x01\x02\x03\x04\x05"
        encoded = base64.b64encode(original).decode("ascii")
        decoded = bridge.decode_audio_payload(encoded)

        assert decoded == original

    def test_decode_invalid_base64(self, bridge: VoiceStreamBridge) -> None:
        """Should reject invalid base64."""
        with pytest.raises(VoiceSessionError, match="Invalid base64"):
            bridge.decode_audio_payload("not!!!base64")

    def test_encode_audio_payload(self, bridge: VoiceStreamBridge) -> None:
        """Should encode bytes to base64."""
        audio_data = b"test audio chunk"
        encoded = bridge.encode_audio_payload(audio_data)
        decoded = base64.b64decode(encoded)

        assert decoded == audio_data

    def test_encode_oversized_chunk(self, bridge: VoiceStreamBridge) -> None:
        """Should reject chunk exceeding max_chunk_bytes."""
        oversized = b"x" * 5000
        with pytest.raises(VoiceSessionError, match="exceeds max size"):
            bridge.encode_audio_payload(oversized)

    def test_decode_audio_in_oversized(self, bridge: VoiceStreamBridge) -> None:
        """Should reject audio_in with oversized payload during decode."""
        oversized = b"x" * 5000
        payload = base64.b64encode(oversized).decode("ascii")
        data = json.dumps(
            {"type": "audio_in", "seq": 1, "payload_b64": payload, "final": False}
        )

        with pytest.raises(VoiceSessionError, match="exceeds max size"):
            bridge.decode_message(data)

    def test_decode_audio_in_invalid_base64(self, bridge: VoiceStreamBridge) -> None:
        """Should reject audio_in with invalid base64."""
        data = json.dumps(
            {"type": "audio_in", "seq": 1, "payload_b64": "bad!!!", "final": False}
        )

        with pytest.raises(VoiceSessionError, match="Invalid base64"):
            bridge.decode_message(data)


class TestRoundTripCodec:
    """Test round-trip encode/decode for message types."""

    def test_roundtrip_heartbeat(self, bridge: VoiceStreamBridge) -> None:
        """Should roundtrip heartbeat message."""
        msg_out = HeartbeatMessage(ts=111.222)
        encoded = bridge.encode_message(msg_out)
        msg_in = bridge.decode_message(encoded)

        assert isinstance(msg_in, HeartbeatMessage)
        assert msg_in.ts == 111.222

    def test_roundtrip_audio_payload(self, bridge: VoiceStreamBridge) -> None:
        """Should roundtrip audio data through encode/decode."""
        original = b"test audio content"
        encoded = bridge.encode_audio_payload(original)
        decoded = bridge.decode_audio_payload(encoded)

        assert decoded == original


class TestChunkSizeLimits:
    """Test enforcement of voice_max_chunk_bytes."""

    def test_accept_chunk_at_limit(self) -> None:
        """Should accept chunk exactly at max size."""
        config = VoiceConfig(max_chunk_bytes=1000)
        bridge = VoiceStreamBridge(config)
        audio_data = b"x" * 1000
        payload = base64.b64encode(audio_data).decode("ascii")
        data = json.dumps(
            {"type": "audio_in", "seq": 1, "payload_b64": payload, "final": False}
        )

        msg = bridge.decode_message(data)
        assert isinstance(msg, AudioInMessage)

    def test_reject_chunk_over_limit(self) -> None:
        """Should reject chunk one byte over max size."""
        config = VoiceConfig(max_chunk_bytes=1000)
        bridge = VoiceStreamBridge(config)
        audio_data = b"x" * 1001
        payload = base64.b64encode(audio_data).decode("ascii")
        data = json.dumps(
            {"type": "audio_in", "seq": 1, "payload_b64": payload, "final": False}
        )

        with pytest.raises(VoiceSessionError, match="exceeds max size"):
            bridge.decode_message(data)

    def test_custom_max_chunk_bytes(self) -> None:
        """Should respect custom max_chunk_bytes from config."""
        config = VoiceConfig(max_chunk_bytes=512)
        bridge = VoiceStreamBridge(config)
        audio_data = b"x" * 600
        payload = base64.b64encode(audio_data).decode("ascii")
        data = json.dumps(
            {"type": "audio_in", "seq": 1, "payload_b64": payload, "final": False}
        )

        with pytest.raises(VoiceSessionError, match="600 > 512"):
            bridge.decode_message(data)


class TestHeartbeatHandling:
    """Test heartbeat message handling (receive only in Phase 4)."""

    def test_receive_heartbeat_from_client(self, bridge: VoiceStreamBridge) -> None:
        """Should parse client heartbeat message."""
        data = json.dumps({"type": "heartbeat", "ts": 1234.5678})
        msg = bridge.decode_message(data)

        assert isinstance(msg, HeartbeatMessage)
        assert msg.ts == 1234.5678

    def test_send_heartbeat_to_client(self, bridge: VoiceStreamBridge) -> None:
        """Should encode server heartbeat message."""
        msg = HeartbeatMessage(ts=9999.1111)
        encoded = bridge.encode_message(msg)
        decoded = json.loads(encoded)

        assert decoded["type"] == "heartbeat"
        assert decoded["ts"] == 9999.1111
