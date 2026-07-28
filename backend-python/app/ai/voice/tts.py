"""TTS pipeline — streaming synthesis orchestration."""

import re
from collections.abc import AsyncIterable, AsyncIterator

from app.ai.voice.config import VoiceConfig
from app.ai.voice.exceptions import TtsError
from app.ai.voice.interfaces import TtsProvider


class TtsPipeline:
    """Consume assistant text → invoke TTS → emit audio chunks.

    Buffers text to sentence boundaries when possible, respecting the
    max chunk size limit. Provides a cancel hook for interrupt handling.
    """

    # Sentence boundary markers (period, exclamation, question mark)
    # followed by whitespace or end of string
    _SENTENCE_BOUNDARY_PATTERN = re.compile(r"[.!?](?:\s+|$)")

    def __init__(
        self, provider: TtsProvider, config: VoiceConfig, cancel_requested: bool = False
    ) -> None:
        """Initialize TTS pipeline.

        Args:
            provider: TTS provider implementing the TtsProvider protocol.
            config: Voice configuration.
            cancel_requested: Placeholder for Phase 6 interrupt hook; prefer
                :meth:`request_cancel` on a live pipeline instance.
        """
        self._provider = provider
        self._config = config
        self._max_chunk_chars = 4096
        self._cancel_requested = cancel_requested

    def request_cancel(self) -> None:
        """Signal the pipeline to stop synthesis (Phase 6 interrupt hook)."""
        self._cancel_requested = True

    def _split_into_sentences(self, text: str) -> list[str]:
        """Split text at sentence boundaries.

        Args:
            text: Input text to split.

        Returns:
            List of sentence fragments (including punctuation).
        """
        if not text:
            return []

        sentences = []
        current_pos = 0

        for match in self._SENTENCE_BOUNDARY_PATTERN.finditer(text):
            sentence_end = match.end()
            sentence = text[current_pos:sentence_end].strip()
            if sentence:
                sentences.append(sentence)
            current_pos = sentence_end

        remaining = text[current_pos:].strip()
        if remaining:
            sentences.append(remaining)

        return sentences

    def _buffer_to_chunks(self, text_buffer: str) -> list[str]:
        """Buffer text into synthesis chunks respecting max size.

        Splits at sentence boundaries when possible, otherwise splits
        at the max character limit.

        Args:
            text_buffer: Accumulated text to chunk.

        Returns:
            List of text chunks ready for synthesis.
        """
        if not text_buffer:
            return []

        if len(text_buffer) <= self._max_chunk_chars:
            return [text_buffer]

        sentences = self._split_into_sentences(text_buffer)
        chunks = []
        current_chunk = ""

        for sentence in sentences:
            if len(sentence) > self._max_chunk_chars:
                if current_chunk:
                    chunks.append(current_chunk)
                    current_chunk = ""

                for i in range(0, len(sentence), self._max_chunk_chars):
                    chunks.append(sentence[i : i + self._max_chunk_chars])
            elif len(current_chunk) + len(sentence) + 1 > self._max_chunk_chars:
                if current_chunk:
                    chunks.append(current_chunk)
                current_chunk = sentence
            else:
                if current_chunk:
                    current_chunk += " " + sentence
                else:
                    current_chunk = sentence

        if current_chunk:
            chunks.append(current_chunk)

        return chunks

    def _flush_to_last_sentence(self, text_buffer: str) -> tuple[str, str]:
        """Flush text up to the last sentence boundary, retaining trailing fragment.

        Args:
            text_buffer: Accumulated text buffer.

        Returns:
            Tuple of (text_to_flush, remaining_fragment).
        """
        matches = list(self._SENTENCE_BOUNDARY_PATTERN.finditer(text_buffer))
        if not matches:
            return "", text_buffer

        last_match = matches[-1]
        flush_text = text_buffer[: last_match.end()].strip()
        remaining = text_buffer[last_match.end() :].strip()
        return flush_text, remaining

    async def _synthesize_chunks(self, text: str) -> AsyncIterator[bytes]:
        """Helper to synthesize text chunks through the provider.

        Args:
            text: Text to synthesize.

        Yields:
            Audio byte chunks.
        """
        chunks_to_synthesize = self._buffer_to_chunks(text)

        for chunk in chunks_to_synthesize:
            if self._cancel_requested:
                break

            async def _chunk_generator() -> AsyncIterator[str]:
                yield chunk

            async for audio_chunk in self._provider.synthesize_stream(
                _chunk_generator()
            ):
                if self._cancel_requested:
                    break
                yield audio_chunk

    async def process(self, text_chunks: AsyncIterable[str]) -> AsyncIterator[bytes]:
        """Process streaming text and emit audio chunks.

        Buffers incoming text to sentence boundaries, then invokes TTS
        provider. Supports cancellation via the cancel hook (wired in Phase 6).

        Args:
            text_chunks: Async iterable of text strings to synthesize.

        Yields:
            Audio byte chunks in the configured format (PCM16 24kHz mono).

        Raises:
            TtsError: On synthesis failure.
        """
        text_buffer = ""

        try:
            async for text_delta in text_chunks:
                if self._cancel_requested:
                    break

                if not text_delta:
                    continue

                text_buffer += text_delta

                if self._SENTENCE_BOUNDARY_PATTERN.search(text_buffer):
                    flush_text, text_buffer = self._flush_to_last_sentence(text_buffer)

                    if flush_text:
                        async for audio_chunk in self._synthesize_chunks(flush_text):
                            yield audio_chunk

            if text_buffer and not self._cancel_requested:
                async for audio_chunk in self._synthesize_chunks(text_buffer):
                    yield audio_chunk

        except TtsError:
            raise
        except Exception as exc:
            raise TtsError(f"TTS pipeline processing failed: {exc}") from exc
