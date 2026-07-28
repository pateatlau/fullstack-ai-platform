"""TTS pipeline — streaming synthesis orchestration."""

from collections.abc import AsyncIterable


class TtsPipeline:
    """Consume assistant text → invoke TTS → emit audio chunks.

    Placeholder stub for Phase 3 implementation.
    """

    def __init__(self) -> None:
        """Initialize TTS pipeline (stub)."""
        pass

    async def process(self, text_chunks: AsyncIterable[str]) -> AsyncIterable[bytes]:
        """Process streaming text and emit audio chunks.

        Args:
            text_chunks: Async iterable of text strings to synthesize.

        Yields:
            Audio byte chunks in the configured format.

        Raises:
            NotImplementedError: Phase 1 stub.
        """
        raise NotImplementedError("TtsPipeline.process() — Phase 3 implementation")
        yield  # Make this an async generator
