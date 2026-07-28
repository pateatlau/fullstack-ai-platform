"""Tests for OpenAI voice adapter TTS functionality."""

from collections.abc import AsyncIterator
from unittest.mock import AsyncMock, MagicMock, patch

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


async def test_synthesize_stream_success(
    api_key: str, voice_config: VoiceConfig
) -> None:
    """Test successful synthesis."""
    adapter = OpenAiVoiceAdapter(api_key, voice_config)

    mock_audio_data = b"\x00\x01" * 1000

    mock_response = MagicMock()

    async def mock_iter_bytes():
        yield mock_audio_data

    mock_response.iter_bytes = mock_iter_bytes

    with patch.object(
        adapter._client.audio.speech, "create", new_callable=AsyncMock
    ) as mock_create:
        mock_create.return_value = mock_response

        text_chunks = ["Hello world"]

        audio_chunks = []
        async for audio in adapter.synthesize_stream(
            _text_chunk_generator(text_chunks)
        ):
            audio_chunks.append(audio)

        assert len(audio_chunks) == 1
        assert audio_chunks[0] == mock_audio_data
        assert mock_create.call_count == 1

        call_kwargs = mock_create.call_args[1]
        assert call_kwargs["model"] == "tts-1"
        assert call_kwargs["voice"] == "alloy"
        assert call_kwargs["input"] == "Hello world"
        assert call_kwargs["response_format"] == "pcm"


async def test_synthesize_stream_empty_text_skipped(
    api_key: str, voice_config: VoiceConfig
) -> None:
    """Test that empty text is skipped."""
    adapter = OpenAiVoiceAdapter(api_key, voice_config)

    text_chunks = ["", "  ", "   \n\t  "]

    audio_chunks = []
    async for audio in adapter.synthesize_stream(_text_chunk_generator(text_chunks)):
        audio_chunks.append(audio)

    assert len(audio_chunks) == 0


async def test_synthesize_stream_whitespace_trimming(
    api_key: str, voice_config: VoiceConfig
) -> None:
    """Test that text is trimmed before synthesis."""
    adapter = OpenAiVoiceAdapter(api_key, voice_config)

    mock_audio_data = b"\x00\x01" * 100

    mock_response = MagicMock()

    async def mock_iter_bytes():
        yield mock_audio_data

    mock_response.iter_bytes = mock_iter_bytes

    with patch.object(
        adapter._client.audio.speech, "create", new_callable=AsyncMock
    ) as mock_create:
        mock_create.return_value = mock_response

        text_chunks = ["  Hello world  \n"]

        audio_chunks = []
        async for audio in adapter.synthesize_stream(
            _text_chunk_generator(text_chunks)
        ):
            audio_chunks.append(audio)

        assert len(audio_chunks) == 1

        call_kwargs = mock_create.call_args[1]
        assert call_kwargs["input"] == "Hello world"


async def test_synthesize_stream_multiple_chunks(
    api_key: str, voice_config: VoiceConfig
) -> None:
    """Test synthesis with multiple text chunks."""
    adapter = OpenAiVoiceAdapter(api_key, voice_config)

    mock_audio_data_1 = b"\x00\x01" * 100
    mock_audio_data_2 = b"\x00\x02" * 100

    call_count = 0

    async def mock_create_side_effect(*args, **kwargs):
        nonlocal call_count
        call_count += 1

        mock_response = MagicMock()

        if call_count == 1:

            async def mock_iter_bytes_1():
                yield mock_audio_data_1

            mock_response.iter_bytes = mock_iter_bytes_1
        else:

            async def mock_iter_bytes_2():
                yield mock_audio_data_2

            mock_response.iter_bytes = mock_iter_bytes_2

        return mock_response

    with patch.object(
        adapter._client.audio.speech, "create", new_callable=AsyncMock
    ) as mock_create:
        mock_create.side_effect = mock_create_side_effect

        text_chunks = ["First text", "Second text"]

        audio_chunks = []
        async for audio in adapter.synthesize_stream(
            _text_chunk_generator(text_chunks)
        ):
            audio_chunks.append(audio)

        assert len(audio_chunks) == 2
        assert audio_chunks[0] == mock_audio_data_1
        assert audio_chunks[1] == mock_audio_data_2
        assert call_count == 2


async def test_synthesize_stream_empty_audio_error(
    api_key: str, voice_config: VoiceConfig
) -> None:
    """Test that empty audio from API raises error."""
    adapter = OpenAiVoiceAdapter(api_key, voice_config)

    mock_response = MagicMock()

    async def mock_iter_bytes():
        return
        yield  # Make this an async generator

    mock_response.iter_bytes = mock_iter_bytes

    with patch.object(
        adapter._client.audio.speech, "create", new_callable=AsyncMock
    ) as mock_create:
        mock_create.return_value = mock_response

        text_chunks = ["Hello world"]

        with pytest.raises(TtsError, match="Empty audio data"):
            async for _ in adapter.synthesize_stream(
                _text_chunk_generator(text_chunks)
            ):
                pass


async def test_synthesize_stream_api_error(
    api_key: str, voice_config: VoiceConfig
) -> None:
    """Test that API errors are wrapped in TtsError."""
    adapter = OpenAiVoiceAdapter(api_key, voice_config)

    with patch.object(
        adapter._client.audio.speech, "create", new_callable=AsyncMock
    ) as mock_create:
        mock_create.side_effect = Exception("API error")

        text_chunks = ["Hello world"]

        with pytest.raises(TtsError, match="TTS synthesis failed"):
            async for _ in adapter.synthesize_stream(
                _text_chunk_generator(text_chunks)
            ):
                pass


async def test_synthesize_stream_with_retry(
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

        mock_response = MagicMock()

        async def mock_iter_bytes():
            yield b"success_audio"

        mock_response.iter_bytes = mock_iter_bytes
        return mock_response

    with patch.object(
        adapter._client.audio.speech, "create", new_callable=AsyncMock
    ) as mock_create:
        mock_create.side_effect = _mock_create_with_retry

        text_chunks = ["Hello world"]

        audio_chunks = []
        async for audio in adapter.synthesize_stream(
            _text_chunk_generator(text_chunks)
        ):
            audio_chunks.append(audio)

        assert len(audio_chunks) == 1
        assert audio_chunks[0] == b"success_audio"
        assert call_count == 3


async def test_synthesize_stream_response_format_pcm(
    api_key: str, voice_config: VoiceConfig
) -> None:
    """Test that response format is set to PCM."""
    adapter = OpenAiVoiceAdapter(api_key, voice_config)

    mock_audio_data = b"\x00\x01" * 100

    mock_response = MagicMock()

    async def mock_iter_bytes():
        yield mock_audio_data

    mock_response.iter_bytes = mock_iter_bytes

    with patch.object(
        adapter._client.audio.speech, "create", new_callable=AsyncMock
    ) as mock_create:
        mock_create.return_value = mock_response

        text_chunks = ["Test audio"]

        audio_chunks = []
        async for audio in adapter.synthesize_stream(
            _text_chunk_generator(text_chunks)
        ):
            audio_chunks.append(audio)

        call_kwargs = mock_create.call_args[1]
        assert call_kwargs["response_format"] == "pcm"


async def test_synthesize_stream_uses_config_model_and_voice(
    api_key: str,
) -> None:
    """Test that configured TTS model and voice are used."""
    custom_config = VoiceConfig(tts_model="tts-1-hd", tts_voice="nova")
    adapter = OpenAiVoiceAdapter(api_key, custom_config)

    mock_audio_data = b"\x00\x01" * 100

    mock_response = MagicMock()

    async def mock_iter_bytes():
        yield mock_audio_data

    mock_response.iter_bytes = mock_iter_bytes

    with patch.object(
        adapter._client.audio.speech, "create", new_callable=AsyncMock
    ) as mock_create:
        mock_create.return_value = mock_response

        text_chunks = ["Test"]

        audio_chunks = []
        async for audio in adapter.synthesize_stream(
            _text_chunk_generator(text_chunks)
        ):
            audio_chunks.append(audio)

        call_kwargs = mock_create.call_args[1]
        assert call_kwargs["model"] == "tts-1-hd"
        assert call_kwargs["voice"] == "nova"


async def test_synthesize_stream_handles_streaming_response(
    api_key: str, voice_config: VoiceConfig
) -> None:
    """Test handling of streaming audio response from API."""
    adapter = OpenAiVoiceAdapter(api_key, voice_config)

    mock_audio_chunk_1 = b"\x00\x01" * 50
    mock_audio_chunk_2 = b"\x00\x02" * 50
    mock_audio_chunk_3 = b"\x00\x03" * 50

    mock_response = MagicMock()

    async def mock_iter_bytes():
        yield mock_audio_chunk_1
        yield mock_audio_chunk_2
        yield mock_audio_chunk_3

    mock_response.iter_bytes = mock_iter_bytes

    with patch.object(
        adapter._client.audio.speech, "create", new_callable=AsyncMock
    ) as mock_create:
        mock_create.return_value = mock_response

        text_chunks = ["Test streaming"]

        audio_chunks = []
        async for audio in adapter.synthesize_stream(
            _text_chunk_generator(text_chunks)
        ):
            audio_chunks.append(audio)

        assert len(audio_chunks) == 1
        expected_audio = mock_audio_chunk_1 + mock_audio_chunk_2 + mock_audio_chunk_3
        assert audio_chunks[0] == expected_audio
