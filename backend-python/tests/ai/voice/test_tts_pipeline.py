"""Tests for TTS pipeline."""

from collections.abc import AsyncIterable, AsyncIterator

import pytest

from app.ai.voice.config import VoiceConfig
from app.ai.voice.exceptions import TtsError
from app.ai.voice.tts import TtsPipeline

pytestmark = pytest.mark.anyio


class FakeTtsProvider:
    """Fake TTS provider for testing."""

    def __init__(self, audio_data: bytes = b"fake_audio") -> None:
        """Initialize fake provider.

        Args:
            audio_data: Audio data to return.
        """
        self._audio_data = audio_data
        self._call_count = 0
        self._synthesized_texts: list[str] = []

    async def synthesize_stream(
        self, text_chunks: AsyncIterable[str]
    ) -> AsyncIterator[bytes]:
        """Fake synthesize implementation.

        Args:
            text_chunks: Async iterable of text chunks.

        Yields:
            Fake audio data.
        """
        self._call_count += 1
        async for text in text_chunks:
            self._synthesized_texts.append(text)
        yield self._audio_data

    @property
    def call_count(self) -> int:
        """Number of times synthesize_stream was called."""
        return self._call_count

    @property
    def synthesized_texts(self) -> list[str]:
        """List of texts that were synthesized."""
        return self._synthesized_texts


class FailingTtsProvider:
    """Fake TTS provider that always fails."""

    async def synthesize_stream(
        self, text_chunks: AsyncIterable[str]
    ) -> AsyncIterator[bytes]:
        """Fake synthesize that fails.

        Args:
            text_chunks: Async iterable of text chunks.

        Raises:
            TtsError: Always fails.
        """
        async for _ in text_chunks:
            pass
        raise TtsError("Intentional failure")
        yield b""  # Make this an async generator


class EmptyTtsProvider:
    """Fake TTS provider that returns no audio."""

    async def synthesize_stream(
        self, text_chunks: AsyncIterable[str]
    ) -> AsyncIterator[bytes]:
        """Fake synthesize that returns nothing.

        Args:
            text_chunks: Async iterable of text chunks.

        Yields:
            Nothing (empty).
        """
        async for _ in text_chunks:
            pass
        return
        yield  # Make this an async generator


@pytest.fixture
def voice_config() -> VoiceConfig:
    """Voice configuration fixture."""
    return VoiceConfig()


@pytest.fixture
def fake_provider() -> FakeTtsProvider:
    """Fake TTS provider fixture."""
    return FakeTtsProvider()


async def _text_chunk_generator(chunks: list[str]) -> AsyncIterator[str]:
    """Helper to create text chunk generator."""
    for chunk in chunks:
        yield chunk


async def test_pipeline_basic_synthesis(
    fake_provider: FakeTtsProvider, voice_config: VoiceConfig
) -> None:
    """Test basic synthesis flow."""
    pipeline = TtsPipeline(fake_provider, voice_config)

    text_chunks = ["Hello world."]

    audio_chunks = []
    async for audio in pipeline.process(_text_chunk_generator(text_chunks)):
        audio_chunks.append(audio)

    assert len(audio_chunks) == 1
    assert audio_chunks[0] == b"fake_audio"
    assert fake_provider.call_count == 1
    assert len(fake_provider.synthesized_texts) == 1
    assert "Hello world." in fake_provider.synthesized_texts[0]


async def test_pipeline_early_flush_before_sentence_end(
    fake_provider: FakeTtsProvider, voice_config: VoiceConfig
) -> None:
    """First TTS chunk should start before a full sentence boundary arrives."""
    pipeline = TtsPipeline(fake_provider, voice_config)

    long_opening = "A" * 45
    text_chunks = [long_opening, " keeps going without punctuation yet"]

    audio_chunks = []
    async for audio in pipeline.process(_text_chunk_generator(text_chunks)):
        audio_chunks.append(audio)

    assert len(audio_chunks) >= 2
    assert fake_provider.call_count >= 2
    assert len(fake_provider.synthesized_texts[0]) >= voice_config.tts_early_flush_chars


async def test_pipeline_time_flush_before_enough_chars(
    fake_provider: FakeTtsProvider,
) -> None:
    """Time-based flush should start TTS for slow token streams."""
    import asyncio

    config = VoiceConfig(tts_time_flush_ms=50, tts_min_time_flush_chars=8)

    async def slow_text() -> AsyncIterator[str]:
        yield "Hello there "
        await asyncio.sleep(0.08)
        yield "friend"

    pipeline = TtsPipeline(fake_provider, config)
    audio_chunks = []
    async for audio in pipeline.process(slow_text()):
        audio_chunks.append(audio)

    assert len(audio_chunks) >= 2
    assert fake_provider.call_count >= 2
    assert fake_provider.synthesized_texts[0].startswith("Hello")


async def test_pipeline_non_blocking_while_synthesizing(
    voice_config: VoiceConfig,
) -> None:
    """Text should keep buffering while an earlier segment is synthesized."""
    import asyncio

    class SlowFakeTtsProvider:
        def __init__(self) -> None:
            self.synthesized_texts: list[str] = []

        async def synthesize_stream(
            self, text_chunks: AsyncIterable[str]
        ) -> AsyncIterator[bytes]:
            async for text in text_chunks:
                self.synthesized_texts.append(text)
                await asyncio.sleep(0.05)
            yield b"audio"

    provider = SlowFakeTtsProvider()
    pipeline = TtsPipeline(provider, voice_config)

    text_chunks = ["First sentence. ", "Second sentence. ", "Third sentence."]

    audio_chunks = []
    async for audio in pipeline.process(_text_chunk_generator(text_chunks)):
        audio_chunks.append(audio)

    assert len(audio_chunks) == 3
    assert len(provider.synthesized_texts) == 3


async def test_pipeline_sentence_boundary_buffering(
    fake_provider: FakeTtsProvider, voice_config: VoiceConfig
) -> None:
    """Test that text is buffered to sentence boundaries."""
    pipeline = TtsPipeline(fake_provider, voice_config)

    text_chunks = ["Hello ", "world. ", "How ", "are ", "you?"]

    audio_chunks = []
    async for audio in pipeline.process(_text_chunk_generator(text_chunks)):
        audio_chunks.append(audio)

    assert len(audio_chunks) == 2
    assert fake_provider.call_count == 2

    assert "Hello world." in fake_provider.synthesized_texts[0]
    assert "How are you?" in fake_provider.synthesized_texts[1]


async def test_pipeline_flush_on_stream_end(
    fake_provider: FakeTtsProvider, voice_config: VoiceConfig
) -> None:
    """Test that remaining text is flushed when stream ends."""
    pipeline = TtsPipeline(fake_provider, voice_config)

    text_chunks = ["Hello world. ", "Incomplete sentence"]

    audio_chunks = []
    async for audio in pipeline.process(_text_chunk_generator(text_chunks)):
        audio_chunks.append(audio)

    assert len(audio_chunks) == 2
    assert fake_provider.call_count == 2
    assert "Incomplete sentence" in fake_provider.synthesized_texts[1]


async def test_pipeline_max_chunk_size_enforcement(
    fake_provider: FakeTtsProvider, voice_config: VoiceConfig
) -> None:
    """Test that text chunks respect max size limit (4096 chars)."""
    pipeline = TtsPipeline(fake_provider, voice_config)

    long_text = "A" * 5000 + ". "
    text_chunks = [long_text]

    audio_chunks = []
    async for audio in pipeline.process(_text_chunk_generator(text_chunks)):
        audio_chunks.append(audio)

    assert len(audio_chunks) >= 2
    for text in fake_provider.synthesized_texts:
        assert len(text) <= 4096


async def test_pipeline_multiple_sentences(
    fake_provider: FakeTtsProvider, voice_config: VoiceConfig
) -> None:
    """Test handling of multiple sentences in one chunk."""
    pipeline = TtsPipeline(fake_provider, voice_config)

    text_chunks = ["First sentence. Second sentence! Third sentence?"]

    audio_chunks = []
    async for audio in pipeline.process(_text_chunk_generator(text_chunks)):
        audio_chunks.append(audio)

    assert len(audio_chunks) == 1
    assert fake_provider.call_count == 1
    synthesized_text = fake_provider.synthesized_texts[0]
    assert "First sentence." in synthesized_text
    assert "Second sentence!" in synthesized_text
    assert "Third sentence?" in synthesized_text


async def test_pipeline_empty_text_skipped(
    fake_provider: FakeTtsProvider, voice_config: VoiceConfig
) -> None:
    """Test that empty text chunks are skipped."""
    pipeline = TtsPipeline(fake_provider, voice_config)

    text_chunks = ["", "  ", "Hello.", ""]

    audio_chunks = []
    async for audio in pipeline.process(_text_chunk_generator(text_chunks)):
        audio_chunks.append(audio)

    assert len(audio_chunks) == 1
    assert fake_provider.call_count == 1
    assert "Hello." in fake_provider.synthesized_texts[0]


async def test_pipeline_provider_error_propagation(
    voice_config: VoiceConfig,
) -> None:
    """Test that provider errors are wrapped and propagated."""
    provider = FailingTtsProvider()
    pipeline = TtsPipeline(provider, voice_config)

    text_chunks = ["Hello world."]

    with pytest.raises(TtsError):
        async for _ in pipeline.process(_text_chunk_generator(text_chunks)):
            pass


async def test_pipeline_cancel_hook_present(voice_config: VoiceConfig) -> None:
    """Test that cancel hook is present (wired in Phase 6)."""
    fake_provider = FakeTtsProvider()
    pipeline = TtsPipeline(fake_provider, voice_config, cancel_requested=False)

    assert hasattr(pipeline, "_cancel_requested")


async def test_pipeline_cancel_interrupts_synthesis(
    voice_config: VoiceConfig,
) -> None:
    """Test that cancel flag stops synthesis."""
    fake_provider = FakeTtsProvider()
    pipeline = TtsPipeline(fake_provider, voice_config, cancel_requested=True)

    text_chunks = ["Hello world. ", "This should not be synthesized."]

    audio_chunks = []
    async for audio in pipeline.process(_text_chunk_generator(text_chunks)):
        audio_chunks.append(audio)

    assert len(audio_chunks) == 0
    assert fake_provider.call_count == 0


async def test_pipeline_streaming_incremental_synthesis(
    fake_provider: FakeTtsProvider, voice_config: VoiceConfig
) -> None:
    """Test incremental synthesis for streaming text input."""
    pipeline = TtsPipeline(fake_provider, voice_config)

    text_chunks = [
        "First sentence. ",
        "Second sentence. ",
        "Third sentence. ",
        "Final incomplete",
    ]

    audio_chunks = []
    async for audio in pipeline.process(_text_chunk_generator(text_chunks)):
        audio_chunks.append(audio)

    assert len(audio_chunks) == 4
    assert fake_provider.call_count == 4


async def test_pipeline_sentence_splitting_accuracy(
    fake_provider: FakeTtsProvider, voice_config: VoiceConfig
) -> None:
    """Test accurate sentence boundary detection."""
    pipeline = TtsPipeline(fake_provider, voice_config)

    sentences = [
        "Hello. ",
        "How are you? ",
        "I'm fine! ",
    ]

    audio_chunks = []
    async for audio in pipeline.process(_text_chunk_generator(sentences)):
        audio_chunks.append(audio)

    assert len(audio_chunks) == 3
    assert "Hello." in fake_provider.synthesized_texts[0]
    assert "How are you?" in fake_provider.synthesized_texts[1]
    assert "I'm fine!" in fake_provider.synthesized_texts[2]


async def test_pipeline_handles_no_sentence_boundaries(
    fake_provider: FakeTtsProvider, voice_config: VoiceConfig
) -> None:
    """Test handling of text without sentence boundaries."""
    pipeline = TtsPipeline(fake_provider, voice_config)

    text_chunks = ["No punctuation here", " more text", " even more"]

    audio_chunks = []
    async for audio in pipeline.process(_text_chunk_generator(text_chunks)):
        audio_chunks.append(audio)

    assert len(audio_chunks) == 1
    assert fake_provider.call_count == 1
    synthesized_text = fake_provider.synthesized_texts[0]
    assert "No punctuation here more text even more" in synthesized_text


async def test_pipeline_retains_trailing_fragment(
    fake_provider: FakeTtsProvider, voice_config: VoiceConfig
) -> None:
    """Test that trailing fragments after sentence boundaries are retained."""
    pipeline = TtsPipeline(fake_provider, voice_config)

    text_chunks = ["Hello. Wor", "ld more text."]

    audio_chunks = []
    async for audio in pipeline.process(_text_chunk_generator(text_chunks)):
        audio_chunks.append(audio)

    assert len(audio_chunks) == 2
    assert fake_provider.call_count == 2

    assert "Hello." in fake_provider.synthesized_texts[0]
    assert "Wor" not in fake_provider.synthesized_texts[0]

    assert "World more text." in fake_provider.synthesized_texts[1]


async def test_pipeline_multiple_trailing_fragments(
    fake_provider: FakeTtsProvider, voice_config: VoiceConfig
) -> None:
    """Test multiple incomplete fragments across deltas."""
    pipeline = TtsPipeline(fake_provider, voice_config)

    text_chunks = ["First. Sec", "ond. Thi", "rd sentence."]

    audio_chunks = []
    async for audio in pipeline.process(_text_chunk_generator(text_chunks)):
        audio_chunks.append(audio)

    assert len(audio_chunks) == 3
    assert fake_provider.call_count == 3

    assert "First." in fake_provider.synthesized_texts[0]
    assert "Second." in fake_provider.synthesized_texts[1]
    assert "Third sentence." in fake_provider.synthesized_texts[2]
