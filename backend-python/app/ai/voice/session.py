"""Voice session manager — lifecycle, chat attach, heartbeat."""


class VoiceSessionManager:
    """Create/attach/teardown voice sessions; heartbeat; timeout.

    Placeholder stub for Phase 5 implementation.
    """

    def __init__(self) -> None:
        """Initialize voice session manager (stub)."""
        pass

    async def create(self, session_id: str, user_id: str) -> str:
        """Create a new voice session attached to a chat session.

        Args:
            session_id: Associated chat session ID.
            user_id: Owner user identifier.

        Returns:
            Voice session ID.

        Raises:
            NotImplementedError: Phase 1 stub.
        """
        raise NotImplementedError(
            "VoiceSessionManager.create() — Phase 5 implementation"
        )

    async def teardown(self, voice_session_id: str) -> None:
        """Idempotent teardown; release provider resources.

        Args:
            voice_session_id: Voice session identifier.

        Raises:
            NotImplementedError: Phase 1 stub.
        """
        raise NotImplementedError(
            "VoiceSessionManager.teardown() — Phase 5 implementation"
        )
