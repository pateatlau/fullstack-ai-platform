"""Voice-specific exceptions."""


class VoiceSessionError(Exception):
    """Base exception for voice session errors."""

    def __init__(self, message: str, code: str | None = None) -> None:
        """Initialize voice session error.

        Args:
            message: Error message.
            code: Optional error code for client classification.
        """
        super().__init__(message)
        self.code = code


class SttError(Exception):
    """Speech-to-text provider error."""

    def __init__(self, message: str, code: str | None = None) -> None:
        """Initialize STT error.

        Args:
            message: Error message.
            code: Optional error code for client classification.
        """
        super().__init__(message)
        self.code = code


class TtsError(Exception):
    """Text-to-speech provider error."""

    pass


class VoiceAuthError(Exception):
    """Voice authentication/authorization error."""

    pass
