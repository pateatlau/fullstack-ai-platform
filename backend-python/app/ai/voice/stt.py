"""STT pipeline — streaming transcription orchestration."""

from collections.abc import AsyncIterable


class SttPipeline:
    """Buffer/chunk audio → invoke STT → emit transcript events.

    Placeholder stub for Phase 2 implementation.
    """

    def __init__(self) -> None:
        """Initialize STT pipeline (stub)."""
        pass

    async def process(self, audio_chunks: AsyncIterable[bytes]) -> AsyncIterable[str]:
        """Process streaming audio and emit transcript events.

        Args:
            audio_chunks: Async iterable of raw audio byte chunks.

        Yields:
            Partial or final transcript strings.

        Raises:
            NotImplementedError: Phase 1 stub.
        """
        raise NotImplementedError("SttPipeline.process() — Phase 2 implementation")
        yield  # Make this an async generator
