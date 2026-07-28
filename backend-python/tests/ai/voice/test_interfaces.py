"""Tests for voice provider interfaces."""

from collections.abc import AsyncIterable, AsyncIterator

import pytest

from app.ai.voice.interfaces import SttProvider, TtsProvider, VoiceSession


class FakeSttProvider:
    """Fake STT provider for testing Protocol conformance."""

    async def transcribe_stream(
        self, audio_chunks: AsyncIterable[bytes]
    ) -> AsyncIterator[str]:
        """Fake transcription that yields mock transcripts."""
        async for chunk in audio_chunks:
            if chunk:
                yield f"transcribed:{len(chunk)}"


class FakeTtsProvider:
    """Fake TTS provider for testing Protocol conformance."""

    async def synthesize_stream(
        self, text_chunks: AsyncIterable[str]
    ) -> AsyncIterator[bytes]:
        """Fake synthesis that yields mock audio."""
        async for text in text_chunks:
            if text:
                yield text.encode("utf-8")


class FakeVoiceSession:
    """Fake voice session for testing Protocol conformance."""

    def __init__(
        self,
        voice_session_id: str,
        session_id: str,
        user_id: str,
        is_active: bool = True,
    ):
        self._voice_session_id = voice_session_id
        self._session_id = session_id
        self._user_id = user_id
        self._is_active = is_active

    @property
    def voice_session_id(self) -> str:
        return self._voice_session_id

    @property
    def session_id(self) -> str:
        return self._session_id

    @property
    def user_id(self) -> str:
        return self._user_id

    @property
    def is_active(self) -> bool:
        return self._is_active


class TestSttProvider:
    """Test SttProvider protocol conformance."""

    @pytest.mark.anyio
    async def test_fake_stt_provider_protocol(self):
        """Test that FakeSttProvider conforms to SttProvider protocol."""
        provider: SttProvider = FakeSttProvider()

        async def audio_generator():
            yield b"audio_chunk_1"
            yield b"audio_chunk_2"

        transcripts = []
        async for transcript in provider.transcribe_stream(audio_generator()):
            transcripts.append(transcript)

        assert len(transcripts) == 2
        assert transcripts[0] == "transcribed:13"
        assert transcripts[1] == "transcribed:13"


class TestTtsProvider:
    """Test TtsProvider protocol conformance."""

    @pytest.mark.anyio
    async def test_fake_tts_provider_protocol(self):
        """Test that FakeTtsProvider conforms to TtsProvider protocol."""
        provider: TtsProvider = FakeTtsProvider()

        async def text_generator():
            yield "Hello"
            yield "World"

        audio_chunks = []
        async for chunk in provider.synthesize_stream(text_generator()):
            audio_chunks.append(chunk)

        assert len(audio_chunks) == 2
        assert audio_chunks[0] == b"Hello"
        assert audio_chunks[1] == b"World"


class TestVoiceSession:
    """Test VoiceSession protocol conformance."""

    def test_fake_voice_session_protocol(self):
        """Test that FakeVoiceSession conforms to VoiceSession protocol."""
        session: VoiceSession = FakeVoiceSession(
            voice_session_id="vs-123",
            session_id="s-456",
            user_id="u-789",
            is_active=True,
        )

        assert session.voice_session_id == "vs-123"
        assert session.session_id == "s-456"
        assert session.user_id == "u-789"
        assert session.is_active is True

    def test_fake_voice_session_inactive(self):
        """Test inactive voice session."""
        session: VoiceSession = FakeVoiceSession(
            voice_session_id="vs-999",
            session_id="s-999",
            user_id="u-999",
            is_active=False,
        )

        assert session.is_active is False
