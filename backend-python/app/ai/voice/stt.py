"""STT pipeline — streaming transcription orchestration."""

from collections.abc import AsyncIterable, AsyncIterator

from app.ai.voice.config import VoiceConfig
from app.ai.voice.exceptions import SttError
from app.ai.voice.interfaces import SttProvider
from app.schemas.voice import TranscriptEvent


class SttPipeline:
    """Buffer/chunk audio → invoke STT → emit transcript events."""

    def __init__(self, provider: SttProvider, config: VoiceConfig) -> None:
        """Initialize STT pipeline.

        Args:
            provider: STT provider implementing the SttProvider protocol.
            config: Voice configuration.
        """
        self._provider = provider
        self._config = config
        self._sample_rate = config.sample_rate_hz
        self._max_utterance_seconds = config.max_utterance_seconds
        self._cancel_requested = False

    def request_cancel(self) -> None:
        """Signal the pipeline to stop transcription (Phase 6 interrupt hook)."""
        self._cancel_requested = True

    def _calculate_audio_duration_ms(self, audio_bytes: int) -> float:
        """Calculate audio duration in milliseconds from byte count.

        Assumes PCM16 mono format: 2 bytes per sample.

        Args:
            audio_bytes: Number of audio bytes.

        Returns:
            Duration in milliseconds.
        """
        bytes_per_sample = 2  # PCM16
        samples = audio_bytes / bytes_per_sample
        duration_seconds = samples / self._sample_rate
        return duration_seconds * 1000

    async def process(
        self, audio_chunks: AsyncIterable[bytes], final: bool = False
    ) -> AsyncIterator[TranscriptEvent]:
        """Process streaming audio and emit transcript events.

        Args:
            audio_chunks: Async iterable of raw audio byte chunks (PCM16 24kHz mono).
            final: Whether this is the final chunk (utterance complete).

        Yields:
            TranscriptEvent with partial or final transcripts.

        Raises:
            SttError: On transcription failure or policy violations.
        """
        buffer = bytearray()
        max_utterance_ms = self._max_utterance_seconds * 1000

        try:
            async for chunk in audio_chunks:
                if self._cancel_requested:
                    return

                buffer.extend(chunk)

                audio_duration_ms = self._calculate_audio_duration_ms(len(buffer))
                if audio_duration_ms > max_utterance_ms:
                    raise SttError(
                        f"Audio duration ({audio_duration_ms:.1f}ms) exceeds max utterance {self._max_utterance_seconds}s"
                    )

            audio_duration_ms = self._calculate_audio_duration_ms(len(buffer))

            if self._cancel_requested:
                return

            if audio_duration_ms < 100:
                raise SttError(
                    f"Audio duration ({audio_duration_ms:.1f}ms) below minimum 100ms"
                )

            async def _chunk_generator() -> AsyncIterator[bytes]:
                yield bytes(buffer)

            transcript_generator = self._provider.transcribe_stream(_chunk_generator())

            has_transcript = False
            async for transcript_text in transcript_generator:
                if self._cancel_requested:
                    return

                if transcript_text and transcript_text.strip():
                    has_transcript = True
                    yield TranscriptEvent(
                        type="transcript_final", text=transcript_text.strip()
                    )

            if not has_transcript:
                raise SttError(
                    "Empty transcript from STT provider", code="empty_transcript"
                )

        except SttError:
            raise
        except Exception as exc:
            raise SttError(f"STT pipeline processing failed: {exc}") from exc
