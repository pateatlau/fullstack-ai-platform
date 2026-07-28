"""Voice configuration models."""

from __future__ import annotations

from typing import TYPE_CHECKING

from pydantic import BaseModel, ConfigDict, Field

if TYPE_CHECKING:
    from app.core.config import Settings


class VoiceConfig(BaseModel):
    """Voice pipeline configuration."""

    model_config = ConfigDict(frozen=True)

    provider: str = "openai"
    stt_model: str = "whisper-1"
    tts_model: str = "tts-1"
    tts_voice: str = "alloy"

    sample_rate_hz: int = Field(default=24000, ge=1)
    audio_encoding: str = "pcm16"
    max_chunk_bytes: int = Field(default=4096, ge=1)

    session_timeout_seconds: int = Field(default=300, ge=1)
    heartbeat_interval_seconds: int = Field(default=30, ge=1)
    max_utterance_seconds: int = Field(default=60, ge=1)

    @classmethod
    def from_settings(cls, settings: Settings) -> VoiceConfig:
        """Build voice pipeline config from application settings."""
        return cls(
            provider=settings.voice_provider,
            stt_model=settings.voice_stt_model,
            tts_model=settings.voice_tts_model,
            tts_voice=settings.voice_tts_voice,
            sample_rate_hz=settings.voice_sample_rate_hz,
            audio_encoding=settings.voice_audio_encoding,
            max_chunk_bytes=settings.voice_max_chunk_bytes,
            session_timeout_seconds=settings.voice_session_timeout_seconds,
            heartbeat_interval_seconds=settings.voice_heartbeat_interval_seconds,
            max_utterance_seconds=settings.voice_max_utterance_seconds,
        )
