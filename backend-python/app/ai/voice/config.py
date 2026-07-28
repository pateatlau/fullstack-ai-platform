"""Voice configuration models."""

from pydantic import BaseModel, ConfigDict, Field


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
