"""Voice interfaces package — speech-to-text, text-to-speech, and voice sessions.

Public API exports per Part I § Public APIs.
"""

from app.ai.voice.config import VoiceConfig
from app.ai.voice.exceptions import (
    SttError,
    TtsError,
    VoiceAuthError,
    VoiceSessionError,
)
from app.ai.voice.interfaces import SttProvider, TtsProvider, VoiceSession
from app.ai.voice.interrupt import InterruptController
from app.ai.voice.session import VoiceSessionManager
from app.ai.voice.streaming import VoiceStreamBridge

__all__ = [
    "SttProvider",
    "TtsProvider",
    "VoiceSession",
    "VoiceConfig",
    "VoiceSessionError",
    "SttError",
    "TtsError",
    "VoiceAuthError",
    "VoiceSessionManager",
    "VoiceStreamBridge",
    "InterruptController",
]
