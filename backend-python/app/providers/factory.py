from app.ai.observability.tracing.provider_wrapper import TracingLLMProvider
from app.core.config import Settings, get_settings
from app.providers.anthropic_provider import AnthropicProvider
from app.providers.base import LLMProvider
from app.providers.gemini_provider import GeminiProvider
from app.providers.groq_provider import GroqProvider
from app.providers.openai_provider import OpenAIProvider


class UnsupportedProviderError(Exception):
    """Raised when a requested provider name has no registered adapter."""


class ProviderFactory:
    """Single switch point for resolving an `LLMProvider` by name.

    Adding a new provider means adding one adapter class + one branch here.
    """

    @staticmethod
    def get_provider(
        name: str | None = None, settings: Settings | None = None
    ) -> LLMProvider:
        settings = settings or get_settings()
        provider_name = name or settings.llm_provider

        if provider_name == "openai":
            provider: LLMProvider = OpenAIProvider(api_key=settings.openai_api_key)
        elif provider_name == "gemini":
            provider = GeminiProvider(api_key=settings.gemini_api_key)
        elif provider_name == "groq":
            provider = GroqProvider(api_key=settings.groq_api_key)
        elif provider_name == "anthropic":
            provider = AnthropicProvider(api_key=settings.anthropic_api_key)
        else:
            raise UnsupportedProviderError(f"Unsupported provider: {provider_name!r}")

        if settings.observability_enabled:
            return TracingLLMProvider(provider, provider_name)
        return provider
