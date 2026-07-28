"""Voice-specific exceptions."""


class VoiceSessionError(Exception):
    """Base exception for voice session errors."""

    pass


class SttError(Exception):
    """Speech-to-text provider error."""

    pass


class TtsError(Exception):
    """Text-to-speech provider error."""

    pass


class VoiceAuthError(Exception):
    """Voice authentication/authorization error."""

    pass
