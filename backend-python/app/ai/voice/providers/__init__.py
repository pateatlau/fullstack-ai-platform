"""Voice providers package — concrete STT/TTS adapters."""

from app.ai.voice.providers.openai_voice import OpenAiVoiceAdapter

__all__ = ["OpenAiVoiceAdapter"]
