"""Voice provider protocols and session interface."""

from collections.abc import AsyncIterable, AsyncIterator
from typing import Protocol


class SttProvider(Protocol):
    """Protocol for streaming speech-to-text providers."""

    def transcribe_stream(
        self, audio_chunks: AsyncIterable[bytes]
    ) -> AsyncIterator[str]:
        """Transcribe streaming audio chunks to text.

        Args:
            audio_chunks: Async iterable of raw audio byte chunks.

        Returns:
            Async iterator of partial or final transcript strings.

        Raises:
            SttError: On transcription failure.
        """
        ...


class TtsProvider(Protocol):
    """Protocol for streaming text-to-speech providers."""

    def synthesize_stream(
        self, text_chunks: AsyncIterable[str]
    ) -> AsyncIterator[bytes]:
        """Synthesize streaming text chunks to audio.

        Args:
            text_chunks: Async iterable of text strings to synthesize.

        Returns:
            Async iterator of audio byte chunks in the configured format (PCM16 24kHz mono).

        Raises:
            TtsError: On synthesis failure.
        """
        ...


class VoiceSession(Protocol):
    """Protocol for voice session handles."""

    @property
    def voice_session_id(self) -> str:
        """Unique voice session identifier."""
        ...

    @property
    def session_id(self) -> str:
        """Associated chat session identifier."""
        ...

    @property
    def user_id(self) -> str:
        """Owner user identifier."""
        ...

    @property
    def is_active(self) -> bool:
        """Whether the session is currently active."""
        ...
