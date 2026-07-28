"""Tests for OpenAI voice adapter TTS functionality."""

from collections.abc import AsyncIterator
from contextlib import AbstractContextManager
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from app.ai.voice.config import VoiceConfig
from app.ai.voice.exceptions import TtsError
from app.ai.voice.providers.openai_voice import OpenAiVoiceAdapter

pytestmark = pytest.mark.anyio


@pytest.fixture
def voice_config() -> VoiceConfig:
    """Voice configuration fixture."""
    return VoiceConfig(tts_model="tts-1", tts_voice="alloy")


@pytest.fixture
def api_key() -> str:
    """Fake API key fixture."""
    return "test-api-key"


async def _text_chunk_generator(chunks: list[str]) -> AsyncIterator[str]:
    """Helper to create text chunk generator."""
    for chunk in chunks:
        yield chunk


def _speech_stream(*audio_frames: bytes) -> MagicMock:
    """Stub streaming speech context manager emitting *audio_frames*."""

    async def _iter_bytes() -> AsyncIterator[bytes]:
        for frame in audio_frames:
            yield frame

    response = MagicMock()
    response.iter_bytes = _iter_bytes

    manager = MagicMock()
    manager.__aenter__ = AsyncMock(return_value=response)
    manager.__aexit__ = AsyncMock(return_value=False)
    return manager


def _failing_speech_stream(error: Exception) -> MagicMock:
    """Stub streaming speech context manager that fails when opened."""
    manager = MagicMock()
    manager.__aenter__ = AsyncMock(side_effect=error)
    manager.__aexit__ = AsyncMock(return_value=False)
    return manager


def _patch_speech_create(
    adapter: OpenAiVoiceAdapter,
) -> AbstractContextManager[MagicMock]:
    """Patch the streaming speech endpoint used by the adapter."""
    return patch.object(adapter._client.audio.speech.with_streaming_response, "create")


async def _collect_audio(
    adapter: OpenAiVoiceAdapter, text_chunks: list[str]
) -> list[bytes]:
    """Drain ``synthesize_stream`` into a list of audio frames."""
    return [
        audio
        async for audio in adapter.synthesize_stream(_text_chunk_generator(text_chunks))
    ]


async def test_synthesize_stream_success(
    api_key: str, voice_config: VoiceConfig
) -> None:
    """Test successful synthesis."""
    adapter = OpenAiVoiceAdapter(api_key, voice_config)
    mock_audio_data = b"\x00\x01" * 1000

    with _patch_speech_create(adapter) as mock_create:
        mock_create.return_value = _speech_stream(mock_audio_data)

        audio_chunks = await _collect_audio(adapter, ["Hello world"])

        assert audio_chunks == [mock_audio_data]
        assert mock_create.call_count == 1

        call_kwargs = mock_create.call_args[1]
        assert call_kwargs["model"] == "tts-1"
        assert call_kwargs["voice"] == "alloy"
        assert call_kwargs["input"] == "Hello world"
        assert call_kwargs["response_format"] == "pcm"


async def test_synthesize_stream_forwards_frames_as_they_arrive(
    api_key: str, voice_config: VoiceConfig
) -> None:
    """Test that response frames are forwarded without being buffered whole."""
    adapter = OpenAiVoiceAdapter(api_key, voice_config)
    frames = [b"\x00\x01" * 10, b"\x00\x02" * 10, b"\x00\x03" * 10]

    with _patch_speech_create(adapter) as mock_create:
        mock_create.return_value = _speech_stream(*frames)

        audio_chunks = await _collect_audio(adapter, ["Test streaming"])

        assert audio_chunks == frames


async def test_synthesize_stream_realigns_split_pcm_samples(
    api_key: str, voice_config: VoiceConfig
) -> None:
    """Test that a sample split across response frames is not corrupted."""
    adapter = OpenAiVoiceAdapter(api_key, voice_config)

    with _patch_speech_create(adapter) as mock_create:
        mock_create.return_value = _speech_stream(b"\x01\x02\x03", b"\x04\x05\x06")

        audio_chunks = await _collect_audio(adapter, ["Odd sized frames"])

        assert audio_chunks == [b"\x01\x02", b"\x03\x04\x05\x06"]
        assert b"".join(audio_chunks) == b"\x01\x02\x03\x04\x05\x06"


async def test_synthesize_stream_empty_text_skipped(
    api_key: str, voice_config: VoiceConfig
) -> None:
    """Test that empty text is skipped."""
    adapter = OpenAiVoiceAdapter(api_key, voice_config)

    audio_chunks = await _collect_audio(adapter, ["", "  ", "   \n\t  "])

    assert audio_chunks == []


async def test_synthesize_stream_whitespace_trimming(
    api_key: str, voice_config: VoiceConfig
) -> None:
    """Test that text is trimmed before synthesis."""
    adapter = OpenAiVoiceAdapter(api_key, voice_config)

    with _patch_speech_create(adapter) as mock_create:
        mock_create.return_value = _speech_stream(b"\x00\x01" * 100)

        audio_chunks = await _collect_audio(adapter, ["  Hello world  \n"])

        assert len(audio_chunks) == 1
        assert mock_create.call_args[1]["input"] == "Hello world"


async def test_synthesize_stream_multiple_chunks(
    api_key: str, voice_config: VoiceConfig
) -> None:
    """Test synthesis with multiple text chunks."""
    adapter = OpenAiVoiceAdapter(api_key, voice_config)
    mock_audio_data_1 = b"\x00\x01" * 100
    mock_audio_data_2 = b"\x00\x02" * 100

    with _patch_speech_create(adapter) as mock_create:
        mock_create.side_effect = [
            _speech_stream(mock_audio_data_1),
            _speech_stream(mock_audio_data_2),
        ]

        audio_chunks = await _collect_audio(adapter, ["First text", "Second text"])

        assert audio_chunks == [mock_audio_data_1, mock_audio_data_2]
        assert mock_create.call_count == 2


async def test_synthesize_stream_empty_audio_error(
    api_key: str, voice_config: VoiceConfig
) -> None:
    """Test that empty audio from API raises error."""
    adapter = OpenAiVoiceAdapter(api_key, voice_config)

    with _patch_speech_create(adapter) as mock_create:
        mock_create.return_value = _speech_stream()

        with pytest.raises(TtsError, match="Empty audio data"):
            await _collect_audio(adapter, ["Hello world"])


async def test_synthesize_stream_api_error(
    api_key: str, voice_config: VoiceConfig
) -> None:
    """Test that API errors are wrapped in TtsError."""
    adapter = OpenAiVoiceAdapter(api_key, voice_config)

    with _patch_speech_create(adapter) as mock_create:
        mock_create.side_effect = Exception("API error")

        with pytest.raises(TtsError, match="TTS synthesis failed"):
            await _collect_audio(adapter, ["Hello world"])


async def test_synthesize_stream_with_retry(
    api_key: str, voice_config: VoiceConfig
) -> None:
    """Test that retry mechanism is applied."""
    adapter = OpenAiVoiceAdapter(api_key, voice_config)

    with _patch_speech_create(adapter) as mock_create:
        mock_create.side_effect = [
            _failing_speech_stream(httpx.TimeoutException("Timeout")),
            _failing_speech_stream(httpx.TimeoutException("Timeout")),
            _speech_stream(b"retried_audio!"),
        ]

        audio_chunks = await _collect_audio(adapter, ["Hello world"])

        assert audio_chunks == [b"retried_audio!"]
        assert mock_create.call_count == 3


async def test_synthesize_stream_uses_config_model_and_voice(
    api_key: str,
) -> None:
    """Test that configured TTS model and voice are used."""
    custom_config = VoiceConfig(tts_model="tts-1-hd", tts_voice="nova")
    adapter = OpenAiVoiceAdapter(api_key, custom_config)

    with _patch_speech_create(adapter) as mock_create:
        mock_create.return_value = _speech_stream(b"\x00\x01" * 100)

        await _collect_audio(adapter, ["Test"])

        call_kwargs = mock_create.call_args[1]
        assert call_kwargs["model"] == "tts-1-hd"
        assert call_kwargs["voice"] == "nova"
