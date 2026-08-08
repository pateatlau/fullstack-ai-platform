"""Approximate token cost calculator — invoked only from ``SqlUsageStore.record()``."""

from __future__ import annotations

from app.ai.observability.cost.pricing import ModelPricingTable
from app.ai.observability.exceptions import ObservabilityConfigError
from app.core.config import Settings
from app.core.logging import get_logger
from app.providers.base import ProviderUsage

logger = get_logger(__name__)


class CostCalculator:
    """Convert ``ProviderUsage`` into approximate USD cost using a pricing table."""

    def __init__(self, table: ModelPricingTable) -> None:
        self._table = table

    @property
    def pricing_version(self) -> str:
        return self._table.pricing_version

    @property
    def pricing_table(self) -> ModelPricingTable:
        return self._table

    def price(
        self,
        provider: str,
        model: str,
        usage: ProviderUsage | None,
    ) -> tuple[float | None, str | None]:
        if usage is None:
            return None, None
        if usage.prompt_tokens is None or usage.completion_tokens is None:
            return None, None

        entry = self._table.lookup(provider, model)
        if entry is None:
            return None, None

        cost = (usage.prompt_tokens / 1000.0) * entry.input_usd_per_1k + (
            usage.completion_tokens / 1000.0
        ) * entry.output_usd_per_1k
        return cost, self._table.pricing_version


class CostRegistry:
    """Process-wide ``CostCalculator`` accessor (real or unset when disabled)."""

    _calculator: CostCalculator | None = None
    _initialized = False

    @classmethod
    def initialize(cls, settings: Settings) -> None:
        if cls._initialized:
            return

        cls._initialized = True
        if not settings.observability_enabled:
            cls._calculator = None
            return

        try:
            table = ModelPricingTable.load(settings)
        except ObservabilityConfigError:
            raise
        except Exception as exc:
            raise ObservabilityConfigError(
                f"Failed to load model pricing table: {exc}"
            ) from exc

        cls._calculator = CostCalculator(table)

    @classmethod
    def get_calculator(cls) -> CostCalculator | None:
        return cls._calculator

    @classmethod
    def is_enabled(cls) -> bool:
        return cls._calculator is not None

    @classmethod
    def reset_for_tests(cls) -> None:
        cls._initialized = False
        cls._calculator = None
