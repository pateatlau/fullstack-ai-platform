"""Voice stream bridge — audio chunk framing; WS message codec."""


class VoiceStreamBridge:
    """Encode/decode WebSocket JSON frames; bridge audio ↔ pipelines.

    Placeholder stub for Phase 4 implementation.
    """

    def __init__(self) -> None:
        """Initialize voice stream bridge (stub)."""
        pass

    def encode_message(self, message: dict) -> str:
        """Encode a message to JSON for WebSocket send.

        Args:
            message: Message dict to encode.

        Returns:
            JSON string.

        Raises:
            NotImplementedError: Phase 1 stub.
        """
        raise NotImplementedError(
            "VoiceStreamBridge.encode_message() — Phase 4 implementation"
        )

    def decode_message(self, data: str) -> dict:
        """Decode a JSON WebSocket message.

        Args:
            data: JSON string from WebSocket.

        Returns:
            Parsed message dict.

        Raises:
            NotImplementedError: Phase 1 stub.
        """
        raise NotImplementedError(
            "VoiceStreamBridge.decode_message() — Phase 4 implementation"
        )
