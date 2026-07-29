"""TTS pipeline — streaming synthesis orchestration."""

from __future__ import annotations

import asyncio
import re
import time
from collections.abc import AsyncIterable, AsyncIterator

from app.ai.voice.config import VoiceConfig
from app.ai.voice.exceptions import TtsError
from app.ai.voice.interfaces import TtsProvider


class TtsPipeline:
    """Consume assistant text → invoke TTS → emit audio chunks.

    Buffers text to sentence boundaries when possible, with optional early
    character and time-based flushes for lower time-to-first-audio. Audio is
    yielded as soon as each segment is synthesised so playback can start while
    the LLM is still streaming (the chat bridge already decouples SSE from this
    coroutine via a text queue).
    """

    _SENTENCE_BOUNDARY_PATTERN = re.compile(r"[.!?](?:\s+|$)")

    def __init__(
        self, provider: TtsProvider, config: VoiceConfig, cancel_requested: bool = False
    ) -> None:
        self._provider = provider
        self._config = config
        self._max_chunk_chars = 4096
        self._early_flush_chars = config.tts_early_flush_chars
        self._time_flush_ms = config.tts_time_flush_ms
        self._min_time_flush_chars = config.tts_min_time_flush_chars
        self._cancel_requested = cancel_requested

    def request_cancel(self) -> None:
        self._cancel_requested = True

    def _split_into_sentences(self, text: str) -> list[str]:
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
        matches = list(self._SENTENCE_BOUNDARY_PATTERN.finditer(text_buffer))
        if not matches:
            return "", text_buffer

        last_match = matches[-1]
        flush_text = text_buffer[: last_match.end()].strip()
        remaining = text_buffer[last_match.end() :].strip()
        return flush_text, remaining

    def _take_early_flush_prefix(self, text_buffer: str) -> tuple[str, str]:
        if len(text_buffer) < self._early_flush_chars:
            return "", text_buffer

        split_at = text_buffer.rfind(" ", 0, self._early_flush_chars + 1)
        if split_at <= 0:
            split_at = self._early_flush_chars

        flush_text = text_buffer[:split_at].strip()
        remaining = text_buffer[split_at:].lstrip()
        return flush_text, remaining

    def _take_time_flush_prefix(self, text_buffer: str) -> tuple[str, str]:
        if len(text_buffer) < self._min_time_flush_chars:
            return "", text_buffer

        split_at = text_buffer.rfind(" ", 0, len(text_buffer))
        if split_at <= 0:
            split_at = len(text_buffer)

        flush_text = text_buffer[:split_at].strip()
        remaining = text_buffer[split_at:].lstrip()
        return flush_text, remaining

    async def _synthesize_and_yield(self, text: str) -> AsyncIterator[bytes]:
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
        text_buffer = ""
        first_text_at: float | None = None
        early_flush_done = False
        next_chunk_task: asyncio.Task[str] | None = None

        async def flush_buffer(flush_text: str) -> AsyncIterator[bytes]:
            if self._cancel_requested or not flush_text.strip():
                return
            async for audio_chunk in self._synthesize_and_yield(flush_text.strip()):
                yield audio_chunk

        async def try_flushes(*, allow_time: bool) -> AsyncIterator[bytes]:
            nonlocal text_buffer, early_flush_done, first_text_at

            while not self._cancel_requested:
                flushed = False
                flush_text = ""

                if self._SENTENCE_BOUNDARY_PATTERN.search(text_buffer):
                    flush_text, text_buffer = self._flush_to_last_sentence(text_buffer)
                    if flush_text:
                        early_flush_done = True
                        flushed = True
                elif (
                    not early_flush_done and len(text_buffer) >= self._early_flush_chars
                ):
                    flush_text, text_buffer = self._take_early_flush_prefix(text_buffer)
                    if flush_text:
                        early_flush_done = True
                        flushed = True
                elif allow_time and first_text_at is not None:
                    elapsed_ms = (time.monotonic() - first_text_at) * 1000
                    if elapsed_ms >= self._time_flush_ms:
                        flush_text, text_buffer = self._take_time_flush_prefix(
                            text_buffer
                        )
                        if flush_text:
                            first_text_at = time.monotonic()
                            flushed = True

                if not flushed:
                    break

                async for audio_chunk in flush_buffer(flush_text):
                    yield audio_chunk

            if not text_buffer.strip():
                first_text_at = None

        try:
            chunk_iter = text_chunks.__aiter__()

            async def _read_next_chunk() -> str:
                return await chunk_iter.__anext__()

            while not self._cancel_requested:
                timeout_seconds: float | None = None
                if first_text_at is not None and text_buffer:
                    elapsed_ms = (time.monotonic() - first_text_at) * 1000
                    remaining_ms = self._time_flush_ms - elapsed_ms
                    if remaining_ms <= 0:
                        timeout_seconds = 0.001
                    else:
                        timeout_seconds = remaining_ms / 1000

                if next_chunk_task is None:
                    next_chunk_task = asyncio.create_task(_read_next_chunk())

                if timeout_seconds is not None:
                    sleep_task = asyncio.create_task(asyncio.sleep(timeout_seconds))
                    done, _pending = await asyncio.wait(
                        {next_chunk_task, sleep_task},
                        return_when=asyncio.FIRST_COMPLETED,
                    )
                    if next_chunk_task not in done:
                        sleep_task.cancel()
                        await asyncio.gather(sleep_task, return_exceptions=True)
                        async for audio_chunk in try_flushes(allow_time=True):
                            yield audio_chunk
                        continue
                    sleep_task.cancel()
                    await asyncio.gather(sleep_task, return_exceptions=True)

                try:
                    text_delta = await next_chunk_task
                except StopAsyncIteration:
                    break
                finally:
                    next_chunk_task = None

                if not text_delta:
                    continue

                if first_text_at is None:
                    first_text_at = time.monotonic()

                text_buffer += text_delta
                async for audio_chunk in try_flushes(allow_time=False):
                    yield audio_chunk

            if text_buffer.strip() and not self._cancel_requested:
                async for audio_chunk in flush_buffer(text_buffer.strip()):
                    yield audio_chunk

        except TtsError:
            raise
        except Exception as exc:
            raise TtsError(f"TTS pipeline processing failed: {exc}") from exc
        finally:
            if next_chunk_task is not None and not next_chunk_task.done():
                next_chunk_task.cancel()
                await asyncio.gather(next_chunk_task, return_exceptions=True)
