"""Tests for voice models and configuration."""

import pytest
from pydantic import ValidationError

from app.ai.voice.config import VoiceConfig
from app.schemas.voice import (
    AssistantTextDeltaMessage,
    AudioInMessage,
    AudioOutEvent,
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
    TranscriptEvent,
    TranscriptFinalMessage,
    TranscriptPartialMessage,
    TurnCompleteMessage,
)


class TestVoiceConfig:
    """Test VoiceConfig model."""

    def test_default_values(self):
        """Test default configuration values."""
        config = VoiceConfig()

        assert config.provider == "openai"
        assert config.stt_model == "whisper-1"
        assert config.tts_model == "tts-1"
        assert config.tts_voice == "alloy"
        assert config.sample_rate_hz == 24000
        assert config.audio_encoding == "pcm16"
        assert config.max_chunk_bytes == 4096
        assert config.session_timeout_seconds == 300
        assert config.heartbeat_interval_seconds == 30
        assert config.max_utterance_seconds == 60

    def test_custom_values(self):
        """Test custom configuration values."""
        config = VoiceConfig(
            provider="custom",
            stt_model="custom-stt",
            tts_model="custom-tts",
            tts_voice="custom-voice",
            sample_rate_hz=16000,
            audio_encoding="opus",
            max_chunk_bytes=8192,
            session_timeout_seconds=600,
            heartbeat_interval_seconds=60,
            max_utterance_seconds=120,
        )

        assert config.provider == "custom"
        assert config.stt_model == "custom-stt"
        assert config.tts_model == "custom-tts"
        assert config.tts_voice == "custom-voice"
        assert config.sample_rate_hz == 16000
        assert config.audio_encoding == "opus"
        assert config.max_chunk_bytes == 8192
        assert config.session_timeout_seconds == 600
        assert config.heartbeat_interval_seconds == 60
        assert config.max_utterance_seconds == 120

    def test_frozen_config(self):
        """Test that VoiceConfig is immutable."""
        config = VoiceConfig()

        with pytest.raises(ValidationError):
            config.provider = "new_provider"

    def test_validation_ge_constraints(self):
        """Test validation for ge=1 constraints."""
        with pytest.raises(ValidationError):
            VoiceConfig(sample_rate_hz=0)

        with pytest.raises(ValidationError):
            VoiceConfig(max_chunk_bytes=-1)

        with pytest.raises(ValidationError):
            VoiceConfig(session_timeout_seconds=0)


class TestTranscriptEvent:
    """Test TranscriptEvent model."""

    def test_transcript_partial(self):
        """Test partial transcript event."""
        event = TranscriptEvent(type="transcript_partial", text="Hello")

        assert event.type == "transcript_partial"
        assert event.text == "Hello"
        assert event.stability is None

    def test_transcript_final(self):
        """Test final transcript event."""
        event = TranscriptEvent(type="transcript_final", text="Hello world")

        assert event.type == "transcript_final"
        assert event.text == "Hello world"

    def test_transcript_with_stability(self):
        """Test transcript event with stability score."""
        event = TranscriptEvent(type="transcript_partial", text="Hello", stability=0.8)

        assert event.stability == 0.8

    def test_stability_validation(self):
        """Test stability score validation (0.0 to 1.0)."""
        with pytest.raises(ValidationError):
            TranscriptEvent(type="transcript_partial", text="Hello", stability=-0.1)

        with pytest.raises(ValidationError):
            TranscriptEvent(type="transcript_partial", text="Hello", stability=1.5)


class TestAudioOutEvent:
    """Test AudioOutEvent model."""

    def test_audio_out_event(self):
        """Test audio output event."""
        event = AudioOutEvent(type="audio_out", seq=1, payload_b64="base64data")

        assert event.type == "audio_out"
        assert event.seq == 1
        assert event.payload_b64 == "base64data"

    def test_seq_validation(self):
        """Test sequence number validation (ge=0)."""
        with pytest.raises(ValidationError):
            AudioOutEvent(type="audio_out", seq=-1, payload_b64="data")


class TestWebSocketMessages:
    """Test WebSocket message models."""

    def test_session_started_message(self):
        """Test SessionStartedMessage."""
        msg = SessionStartedMessage(voice_session_id="vs-123")

        assert msg.type == "session_started"
        assert msg.voice_session_id == "vs-123"
        assert msg.audio_format == "pcm16_24k_mono"

    def test_audio_in_message(self):
        """Test AudioInMessage."""
        msg = AudioInMessage(seq=5, payload_b64="audiodata", final=True)

        assert msg.type == "audio_in"
        assert msg.seq == 5
        assert msg.payload_b64 == "audiodata"
        assert msg.final is True

    def test_audio_in_message_defaults(self):
        """Test AudioInMessage default values."""
        msg = AudioInMessage(seq=0, payload_b64="data")

        assert msg.final is False

    def test_transcript_partial_message(self):
        """Test TranscriptPartialMessage."""
        msg = TranscriptPartialMessage(text="partial text", stability=0.5)

        assert msg.type == "transcript_partial"
        assert msg.text == "partial text"
        assert msg.stability == 0.5

    def test_transcript_final_message(self):
        """Test TranscriptFinalMessage."""
        msg = TranscriptFinalMessage(text="final text")

        assert msg.type == "transcript_final"
        assert msg.text == "final text"

    def test_assistant_text_delta_message(self):
        """Test AssistantTextDeltaMessage."""
        msg = AssistantTextDeltaMessage(text="delta")

        assert msg.type == "assistant_text_delta"
        assert msg.text == "delta"

    def test_audio_out_message(self):
        """Test AudioOutMessage."""
        msg = AudioOutMessage(seq=10, payload_b64="audioout")

        assert msg.type == "audio_out"
        assert msg.seq == 10
        assert msg.payload_b64 == "audioout"

    def test_tool_start_message(self):
        """Test ToolStartMessage."""
        msg = ToolStartMessage(name="web_search")

        assert msg.type == "tool_start"
        assert msg.name == "web_search"

    def test_tool_end_message(self):
        """Test ToolEndMessage."""
        msg = ToolEndMessage(name="web_search", success=True)

        assert msg.type == "tool_end"
        assert msg.name == "web_search"
        assert msg.success is True

    def test_interrupt_message(self):
        """Test InterruptMessage."""
        msg = InterruptMessage()

        assert msg.type == "interrupt"

    def test_interrupted_message(self):
        """Test InterruptedMessage."""
        msg = InterruptedMessage()

        assert msg.type == "interrupted"

    def test_turn_complete_message(self):
        """Test TurnCompleteMessage."""
        msg = TurnCompleteMessage(
            tools_used=["web_search"],
            retrieved_chunk_count=5,
            citations=[{"chunk_id": "c1"}],
        )

        assert msg.type == "turn_complete"
        assert msg.tools_used == ["web_search"]
        assert msg.retrieved_chunk_count == 5
        assert msg.citations == [{"chunk_id": "c1"}]

    def test_turn_complete_message_defaults(self):
        """Test TurnCompleteMessage default values."""
        msg = TurnCompleteMessage()

        assert msg.tools_used is None
        assert msg.retrieved_chunk_count is None
        assert msg.citations is None

    def test_heartbeat_message(self):
        """Test HeartbeatMessage."""
        msg = HeartbeatMessage(ts=123.456)

        assert msg.type == "heartbeat"
        assert msg.ts == 123.456

    def test_session_end_message(self):
        """Test SessionEndMessage."""
        msg = SessionEndMessage()

        assert msg.type == "session_end"

    def test_session_closed_message(self):
        """Test SessionClosedMessage."""
        msg = SessionClosedMessage(reason="timeout")

        assert msg.type == "session_closed"
        assert msg.reason == "timeout"

    def test_error_message(self):
        """Test ErrorMessage."""
        msg = ErrorMessage(code="empty_transcript", message="Transcript is empty")

        assert msg.type == "error"
        assert msg.code == "empty_transcript"
        assert msg.message == "Transcript is empty"
