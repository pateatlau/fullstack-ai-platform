"""Tests for STT pipeline."""

import asyncio
from collections.abc import AsyncIterable, AsyncIterator

import pytest

from app.ai.voice.config import VoiceConfig
from app.ai.voice.exceptions import SttError
from app.ai.voice.stt import SttPipeline
from app.schemas.voice import TranscriptEvent

pytestmark = pytest.mark.anyio


class FakeSttProvider:
    """Fake STT provider for testing."""

    def __init__(self, transcript: str = "hello world") -> None:
        """Initialize fake provider.

        Args:
            transcript: Transcript to return.
        """
        self._transcript = transcript
        self._call_count = 0

    async def transcribe_stream(
        self, audio_chunks: AsyncIterable[bytes]
    ) -> AsyncIterator[str]:
        """Fake transcribe implementation.

        Args:
            audio_chunks: Async iterable of audio chunks.

        Yields:
            Fake transcript.
        """
        self._call_count += 1
        async for _ in audio_chunks:
            pass
        yield self._transcript

    @property
    def call_count(self) -> int:
        """Number of times transcribe_stream was called."""
        return self._call_count


class FailingSttProvider:
    """Fake STT provider that always fails."""

    async def transcribe_stream(
        self, audio_chunks: AsyncIterable[bytes]
    ) -> AsyncIterator[str]:
        """Fake transcribe that fails.

        Args:
            audio_chunks: Async iterable of audio chunks.

        Raises:
            SttError: Always fails.
        """
        async for _ in audio_chunks:
            pass
        raise SttError("Intentional failure")
        yield ""  # Make this an async generator


class EmptySttProvider:
    """Fake STT provider that returns empty transcript."""

    async def transcribe_stream(
        self, audio_chunks: AsyncIterable[bytes]
    ) -> AsyncIterator[str]:
        """Fake transcribe that returns nothing.

        Args:
            audio_chunks: Async iterable of audio chunks.

        Yields:
            Nothing (empty).
        """
        async for _ in audio_chunks:
            pass
        return
        yield  # Make this an async generator


@pytest.fixture
def voice_config() -> VoiceConfig:
    """Voice configuration fixture."""
    return VoiceConfig()


@pytest.fixture
def fake_provider() -> FakeSttProvider:
    """Fake STT provider fixture."""
    return FakeSttProvider()


async def _audio_chunk_generator(chunks: list[bytes]) -> AsyncIterator[bytes]:
    """Helper to create audio chunk generator."""
    for chunk in chunks:
        yield chunk


def _create_pcm16_audio(duration_ms: int, sample_rate: int = 24000) -> bytes:
    """Create fake PCM16 mono audio data.

    Args:
        duration_ms: Duration in milliseconds.
        sample_rate: Sample rate in Hz.

    Returns:
        Fake audio bytes.
    """
    samples = int((duration_ms / 1000) * sample_rate)
    return b"\x00\x01" * samples  # Fake audio data


async def test_pipeline_basic_transcription(
    fake_provider: FakeSttProvider, voice_config: VoiceConfig
) -> None:
    """Test basic transcription flow."""
    pipeline = SttPipeline(fake_provider, voice_config)

    audio_data = _create_pcm16_audio(duration_ms=500)
    chunks = [audio_data]

    events = []
    async for event in pipeline.process(_audio_chunk_generator(chunks), final=True):
        events.append(event)

    assert len(events) == 1
    assert isinstance(events[0], TranscriptEvent)
    assert events[0].type == "transcript_final"
    assert events[0].text == "hello world"
    assert fake_provider.call_count == 1


async def test_pipeline_min_audio_duration_check(
    fake_provider: FakeSttProvider, voice_config: VoiceConfig
) -> None:
    """Test that audio below 100ms is rejected."""
    pipeline = SttPipeline(fake_provider, voice_config)

    audio_data = _create_pcm16_audio(duration_ms=50)
    chunks = [audio_data]

    with pytest.raises(SttError, match="below minimum 100ms"):
        async for _ in pipeline.process(_audio_chunk_generator(chunks), final=True):
            pass

    assert fake_provider.call_count == 0


async def test_pipeline_max_utterance_duration_check(
    voice_config: VoiceConfig,
) -> None:
    """Test that utterances exceeding max duration are rejected."""
    config = VoiceConfig(max_utterance_seconds=1)
    provider = FakeSttProvider()
    pipeline = SttPipeline(provider, config)

    async def _slow_generator() -> AsyncIterator[bytes]:
        yield _create_pcm16_audio(duration_ms=500)
        await asyncio.sleep(1.1)
        yield _create_pcm16_audio(duration_ms=500)

    with pytest.raises(SttError, match="exceeds max duration"):
        async for _ in pipeline.process(_slow_generator(), final=True):
            pass


async def test_pipeline_empty_transcript_error(voice_config: VoiceConfig) -> None:
    """Test that empty transcripts raise appropriate error."""
    provider = EmptySttProvider()
    pipeline = SttPipeline(provider, voice_config)

    audio_data = _create_pcm16_audio(duration_ms=500)
    chunks = [audio_data]

    with pytest.raises(SttError, match="Empty transcript") as exc_info:
        async for _ in pipeline.process(_audio_chunk_generator(chunks), final=True):
            pass

    assert exc_info.value.code == "empty_transcript"


async def test_pipeline_provider_error_propagation(voice_config: VoiceConfig) -> None:
    """Test that provider errors are wrapped and propagated."""
    provider = FailingSttProvider()
    pipeline = SttPipeline(provider, voice_config)

    audio_data = _create_pcm16_audio(duration_ms=500)
    chunks = [audio_data]

    with pytest.raises(SttError):
        async for _ in pipeline.process(_audio_chunk_generator(chunks), final=True):
            pass


async def test_pipeline_multiple_chunks(
    fake_provider: FakeSttProvider, voice_config: VoiceConfig
) -> None:
    """Test transcription with multiple audio chunks."""
    pipeline = SttPipeline(fake_provider, voice_config)

    chunk1 = _create_pcm16_audio(duration_ms=150)
    chunk2 = _create_pcm16_audio(duration_ms=150)
    chunk3 = _create_pcm16_audio(duration_ms=200)
    chunks = [chunk1, chunk2, chunk3]

    events = []
    async for event in pipeline.process(_audio_chunk_generator(chunks), final=True):
        events.append(event)

    assert len(events) == 1
    assert events[0].type == "transcript_final"
    assert events[0].text == "hello world"


async def test_pipeline_whitespace_trimming(voice_config: VoiceConfig) -> None:
    """Test that transcripts are trimmed of whitespace."""
    provider = FakeSttProvider(transcript="  hello world  \n")
    pipeline = SttPipeline(provider, voice_config)

    audio_data = _create_pcm16_audio(duration_ms=500)
    chunks = [audio_data]

    events = []
    async for event in pipeline.process(_audio_chunk_generator(chunks), final=True):
        events.append(event)

    assert len(events) == 1
    assert events[0].text == "hello world"


async def test_pipeline_audio_duration_calculation(voice_config: VoiceConfig) -> None:
    """Test audio duration calculation for PCM16 24kHz mono."""
    fake_provider = FakeSttProvider()
    pipeline = SttPipeline(fake_provider, voice_config)

    duration_ms = pipeline._calculate_audio_duration_ms(48000)

    assert abs(duration_ms - 1000.0) < 0.1
