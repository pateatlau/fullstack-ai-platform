"""Interrupt controller — barge-in / cancel semantics."""


class InterruptController:
    """On barge-in: cancel TTS task + upstream stream.

    Placeholder stub for Phase 6 implementation.
    """

    def __init__(self) -> None:
        """Initialize interrupt controller (stub)."""
        pass

    async def cancel_all(self, voice_session_id: str) -> None:
        """Cancel TTS task + upstream LLM/agent stream.

        Args:
            voice_session_id: Voice session identifier.

        Raises:
            NotImplementedError: Phase 1 stub.
        """
        raise NotImplementedError(
            "InterruptController.cancel_all() — Phase 6 implementation"
        )
