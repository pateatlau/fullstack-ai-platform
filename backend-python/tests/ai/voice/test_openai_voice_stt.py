"""Tests for OpenAI voice adapter STT functionality."""

import wave
from collections.abc import AsyncIterator
from unittest.mock import AsyncMock, patch

import pytest

from app.ai.voice.config import VoiceConfig
from app.ai.voice.exceptions import SttError
from app.ai.voice.providers.openai_voice import OpenAiVoiceAdapter

pytestmark = pytest.mark.anyio


@pytest.fixture
def voice_config() -> VoiceConfig:
    """Voice configuration fixture."""
    return VoiceConfig(stt_model="whisper-1")


@pytest.fixture
def api_key() -> str:
    """Fake API key fixture."""
    return "test-api-key"


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
    return b"\x00\x01" * samples


async def test_transcribe_stream_success(
    api_key: str, voice_config: VoiceConfig
) -> None:
    """Test successful transcription."""
    adapter = OpenAiVoiceAdapter(api_key, voice_config)

    mock_response = "hello world"

    with patch.object(
        adapter._client.audio.transcriptions, "create", new_callable=AsyncMock
    ) as mock_create:
        mock_create.return_value = mock_response

        audio_data = _create_pcm16_audio(duration_ms=500)
        chunks = [audio_data]

        transcripts = []
        async for transcript in adapter.transcribe_stream(
            _audio_chunk_generator(chunks)
        ):
            transcripts.append(transcript)

        assert len(transcripts) == 1
        assert transcripts[0] == "hello world"
        assert mock_create.call_count == 1

        call_kwargs = mock_create.call_args[1]
        assert call_kwargs["model"] == "whisper-1"
        assert call_kwargs["response_format"] == "text"


async def test_transcribe_stream_empty_audio(
    api_key: str, voice_config: VoiceConfig
) -> None:
    """Test that empty audio buffer raises error."""
    adapter = OpenAiVoiceAdapter(api_key, voice_config)

    async def _empty_generator() -> AsyncIterator[bytes]:
        return
        yield  # Make this an async generator

    with pytest.raises(SttError, match="Empty audio buffer"):
        async for _ in adapter.transcribe_stream(_empty_generator()):
            pass


async def test_transcribe_stream_empty_transcript(
    api_key: str, voice_config: VoiceConfig
) -> None:
    """Test that empty transcript from API raises error."""
    adapter = OpenAiVoiceAdapter(api_key, voice_config)

    with patch.object(
        adapter._client.audio.transcriptions, "create", new_callable=AsyncMock
    ) as mock_create:
        mock_create.return_value = ""

        audio_data = _create_pcm16_audio(duration_ms=500)
        chunks = [audio_data]

        with pytest.raises(SttError, match="Empty transcript"):
            async for _ in adapter.transcribe_stream(_audio_chunk_generator(chunks)):
                pass


async def test_transcribe_stream_whitespace_only_transcript(
    api_key: str, voice_config: VoiceConfig
) -> None:
    """Test that whitespace-only transcript raises error."""
    adapter = OpenAiVoiceAdapter(api_key, voice_config)

    with patch.object(
        adapter._client.audio.transcriptions, "create", new_callable=AsyncMock
    ) as mock_create:
        mock_create.return_value = "   \n\t   "

        audio_data = _create_pcm16_audio(duration_ms=500)
        chunks = [audio_data]

        with pytest.raises(SttError, match="Empty transcript"):
            async for _ in adapter.transcribe_stream(_audio_chunk_generator(chunks)):
                pass


async def test_transcribe_stream_api_error(
    api_key: str, voice_config: VoiceConfig
) -> None:
    """Test that API errors are wrapped in SttError."""
    adapter = OpenAiVoiceAdapter(api_key, voice_config)

    with patch.object(
        adapter._client.audio.transcriptions, "create", new_callable=AsyncMock
    ) as mock_create:
        mock_create.side_effect = Exception("API error")

        audio_data = _create_pcm16_audio(duration_ms=500)
        chunks = [audio_data]

        with pytest.raises(SttError, match="STT transcription failed"):
            async for _ in adapter.transcribe_stream(_audio_chunk_generator(chunks)):
                pass


async def test_transcribe_stream_whitespace_trimming(
    api_key: str, voice_config: VoiceConfig
) -> None:
    """Test that transcripts are trimmed of leading/trailing whitespace."""
    adapter = OpenAiVoiceAdapter(api_key, voice_config)

    with patch.object(
        adapter._client.audio.transcriptions, "create", new_callable=AsyncMock
    ) as mock_create:
        mock_create.return_value = "  hello world  \n"

        audio_data = _create_pcm16_audio(duration_ms=500)
        chunks = [audio_data]

        transcripts = []
        async for transcript in adapter.transcribe_stream(
            _audio_chunk_generator(chunks)
        ):
            transcripts.append(transcript)

        assert len(transcripts) == 1
        assert transcripts[0] == "hello world"


async def test_transcribe_stream_multiple_chunks(
    api_key: str, voice_config: VoiceConfig
) -> None:
    """Test transcription with multiple audio chunks."""
    adapter = OpenAiVoiceAdapter(api_key, voice_config)

    with patch.object(
        adapter._client.audio.transcriptions, "create", new_callable=AsyncMock
    ) as mock_create:
        mock_create.return_value = "test transcript"

        chunk1 = _create_pcm16_audio(duration_ms=200)
        chunk2 = _create_pcm16_audio(duration_ms=300)
        chunks = [chunk1, chunk2]

        transcripts = []
        async for transcript in adapter.transcribe_stream(
            _audio_chunk_generator(chunks)
        ):
            transcripts.append(transcript)

        assert len(transcripts) == 1
        assert transcripts[0] == "test transcript"

        call_kwargs = mock_create.call_args[1]
        file_buffer = call_kwargs["file"]

        file_buffer.seek(0)
        with wave.open(file_buffer, "rb") as wav_file:
            assert wav_file.getnchannels() == 1
            assert wav_file.getsampwidth() == 2
            assert wav_file.getframerate() == 24000
            pcm_data = wav_file.readframes(wav_file.getnframes())
            total_expected_bytes = len(chunk1) + len(chunk2)
            assert len(pcm_data) == total_expected_bytes


async def test_transcribe_stream_with_retry(
    api_key: str, voice_config: VoiceConfig
) -> None:
    """Test that retry mechanism is applied."""
    adapter = OpenAiVoiceAdapter(api_key, voice_config)

    call_count = 0

    async def _mock_create_with_retry(*args, **kwargs):
        nonlocal call_count
        call_count += 1
        if call_count < 3:
            import httpx

            raise httpx.TimeoutException("Timeout")
        return "success after retry"

    with patch.object(
        adapter._client.audio.transcriptions, "create", new_callable=AsyncMock
    ) as mock_create:
        mock_create.side_effect = _mock_create_with_retry

        audio_data = _create_pcm16_audio(duration_ms=500)
        chunks = [audio_data]

        transcripts = []
        async for transcript in adapter.transcribe_stream(
            _audio_chunk_generator(chunks)
        ):
            transcripts.append(transcript)

        assert len(transcripts) == 1
        assert transcripts[0] == "success after retry"
        assert call_count == 3


async def test_synthesize_stream_not_implemented(
    api_key: str, voice_config: VoiceConfig
) -> None:
    """Test that TTS is not yet implemented."""
    adapter = OpenAiVoiceAdapter(api_key, voice_config)

    async def _text_generator() -> AsyncIterator[str]:
        yield "hello"

    with pytest.raises(NotImplementedError, match="Phase 3"):
        async for _ in adapter.synthesize_stream(_text_generator()):
            pass
