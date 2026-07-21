"""Provider capability lookup for V1.1 feature gating."""

from __future__ import annotations

from dataclasses import dataclass

from app.schemas.chat import ProviderName

ALL_PROVIDERS: tuple[ProviderName, ...] = ("openai", "gemini", "groq", "anthropic")


@dataclass(frozen=True)
class ProviderCapabilities:
    supports_streaming: bool
    supports_tool_calling: bool
    supports_json_mode: bool
    supports_reasoning: bool
    supports_image_input: bool
    supports_image_output: bool
    supports_audio: bool
    supports_embeddings: bool


_CAPABILITIES: dict[ProviderName, ProviderCapabilities] = {
    "openai": ProviderCapabilities(
        supports_streaming=True,
        supports_tool_calling=True,
        supports_json_mode=False,
        supports_reasoning=False,
        supports_image_input=False,
        supports_image_output=False,
        supports_audio=False,
        supports_embeddings=False,
    ),
    "gemini": ProviderCapabilities(
        supports_streaming=True,
        supports_tool_calling=True,
        supports_json_mode=False,
        supports_reasoning=False,
        supports_image_input=False,
        supports_image_output=False,
        supports_audio=False,
        supports_embeddings=False,
    ),
    "groq": ProviderCapabilities(
        supports_streaming=True,
        supports_tool_calling=True,
        supports_json_mode=False,
        supports_reasoning=False,
        supports_image_input=False,
        supports_image_output=False,
        supports_audio=False,
        supports_embeddings=False,
    ),
    "anthropic": ProviderCapabilities(
        supports_streaming=True,
        supports_tool_calling=True,
        supports_json_mode=False,
        supports_reasoning=False,
        supports_image_input=False,
        supports_image_output=False,
        supports_audio=False,
        supports_embeddings=False,
    ),
}


def get_capabilities(provider: ProviderName) -> ProviderCapabilities:
    """Return capability flags for a supported LLM provider."""
    return _CAPABILITIES[provider]


def capabilities_by_provider() -> dict[str, dict[str, bool]]:
    """Serialize all provider capabilities for health/config responses."""
    return {
        name: {
            "supports_streaming": caps.supports_streaming,
            "supports_tool_calling": caps.supports_tool_calling,
            "supports_json_mode": caps.supports_json_mode,
            "supports_reasoning": caps.supports_reasoning,
            "supports_image_input": caps.supports_image_input,
            "supports_image_output": caps.supports_image_output,
            "supports_audio": caps.supports_audio,
            "supports_embeddings": caps.supports_embeddings,
        }
        for name, caps in _CAPABILITIES.items()
    }
