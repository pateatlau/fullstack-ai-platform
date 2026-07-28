"""OpenAI voice adapter — Whisper STT + OpenAI TTS implementation."""

import io
import wave
from collections.abc import AsyncIterable, AsyncIterator

from openai import AsyncOpenAI

from app.ai.voice.config import VoiceConfig
from app.ai.voice.exceptions import SttError
from app.core.retry import retry_async


class OpenAiVoiceAdapter:
    """Concrete STT/TTS adapter using OpenAI Whisper and TTS APIs."""

    def __init__(self, api_key: str, config: VoiceConfig) -> None:
        """Initialize OpenAI voice adapter.

        Args:
            api_key: OpenAI API key.
            config: Voice configuration.
        """
        self._client = AsyncOpenAI(api_key=api_key)
        self._config = config

    async def transcribe_stream(
        self, audio_chunks: AsyncIterable[bytes]
    ) -> AsyncIterator[str]:
        """Transcribe streaming audio chunks to text using Whisper API.

        Note: OpenAI Whisper API does not support true streaming transcription.
        This implementation accumulates chunks and transcribes when the stream
        ends or when final chunk is indicated.

        Args:
            audio_chunks: Async iterable of raw audio byte chunks (PCM16 24kHz mono).

        Yields:
            Final transcript string when transcription completes.

        Raises:
            SttError: On transcription failure.
        """
        pcm_buffer = io.BytesIO()

        try:
            async for chunk in audio_chunks:
                pcm_buffer.write(chunk)

            pcm_data = pcm_buffer.getvalue()
            if len(pcm_data) == 0:
                raise SttError("Empty audio buffer for transcription")

            wav_buffer = io.BytesIO()
            with wave.open(wav_buffer, "wb") as wav_file:
                wav_file.setnchannels(1)
                wav_file.setsampwidth(2)
                wav_file.setframerate(self._config.sample_rate_hz)
                wav_file.writeframes(pcm_data)

            wav_buffer.seek(0)
            wav_buffer.name = "audio.wav"

            async def _transcribe() -> str:
                wav_buffer.seek(0)
                response = await self._client.audio.transcriptions.create(
                    model=self._config.stt_model,
                    file=wav_buffer,
                    response_format="text",
                )
                return response

            transcript = await retry_async(_transcribe)

            if not transcript or not transcript.strip():
                raise SttError("Empty transcript from STT provider")

            yield transcript.strip()

        except SttError:
            raise
        except Exception as exc:
            raise SttError(f"STT transcription failed: {exc}") from exc

    async def synthesize_stream(
        self, text_chunks: AsyncIterable[str]
    ) -> AsyncIterator[bytes]:
        """Synthesize streaming text chunks to audio using OpenAI TTS API.

        Note: OpenAI TTS API does not support true streaming synthesis.
        This implementation accumulates text chunks and synthesizes when
        a complete chunk is ready (up to 4096 characters) or stream ends.

        Args:
            text_chunks: Async iterable of text strings to synthesize.

        Yields:
            Audio byte chunks in PCM16 24kHz mono format.

        Raises:
            TtsError: On synthesis failure.
        """
        from app.ai.voice.exceptions import TtsError

        try:
            async for text in text_chunks:
                if not text or not text.strip():
                    continue

                async def _synthesize() -> bytes:
                    response = await self._client.audio.speech.create(
                        model=self._config.tts_model,
                        voice=self._config.tts_voice,
                        input=text.strip(),
                        response_format="pcm",
                    )

                    audio_bytes = b""
                    async for chunk in response.iter_bytes():  # type: ignore[attr-defined]
                        audio_bytes += chunk
                    return audio_bytes

                audio_data = await retry_async(_synthesize)

                if not audio_data:
                    raise TtsError("Empty audio data from TTS provider")

                yield audio_data

        except TtsError:
            raise
        except Exception as exc:
            raise TtsError(f"TTS synthesis failed: {exc}") from exc
