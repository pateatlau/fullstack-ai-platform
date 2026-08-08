"""ModelPricingTable and CostCalculator tests."""

from __future__ import annotations

from pathlib import Path

import pytest

from app.ai.observability.cost.calculator import CostCalculator, CostRegistry
from app.ai.observability.cost.pricing import ModelPricingTable
from app.ai.observability.exceptions import ObservabilityConfigError
from app.core.config import Settings
from app.providers.base import ProviderUsage

_BACKEND_ROOT = Path(__file__).resolve().parents[3]
_CANONICAL_PRICING = _BACKEND_ROOT / "config" / "model_pricing.yaml"


def _write_pricing(tmp_path: Path, content: str) -> Path:
    path = tmp_path / "model_pricing.yaml"
    path.write_text(content, encoding="utf-8")
    return path


def test_model_pricing_table_loads_valid_file() -> None:
    settings = Settings(
        openai_api_key="test-key",
        observability_cost_pricing_version="2026-08",
    )
    table = ModelPricingTable.load(settings, pricing_file=_CANONICAL_PRICING)

    assert table.pricing_version == "2026-08"
    entry = table.lookup("openai", "gpt-4o-mini")
    assert entry is not None
    assert entry.input_usd_per_1k > 0
    assert entry.output_usd_per_1k > 0


def test_model_pricing_table_rejects_duplicate_keys(tmp_path: Path) -> None:
    pricing_file = _write_pricing(
        tmp_path,
        """
pricing_version: '2026-08'
models:
  - provider: openai
    model: gpt-4o-mini
    input_usd_per_1k: 0.001
    output_usd_per_1k: 0.002
  - provider: openai
    model: gpt-4o-mini
    input_usd_per_1k: 0.003
    output_usd_per_1k: 0.004
""",
    )
    settings = Settings(
        openai_api_key="test-key",
        observability_cost_pricing_version="2026-08",
    )

    with pytest.raises(ObservabilityConfigError, match="Duplicate pricing entry"):
        ModelPricingTable.load(settings, pricing_file=pricing_file)


def test_model_pricing_table_rejects_negative_rate(tmp_path: Path) -> None:
    pricing_file = _write_pricing(
        tmp_path,
        """
pricing_version: '2026-08'
models:
  - provider: openai
    model: gpt-4o-mini
    input_usd_per_1k: -0.001
    output_usd_per_1k: 0.002
""",
    )
    settings = Settings(
        openai_api_key="test-key",
        observability_cost_pricing_version="2026-08",
    )

    with pytest.raises(ObservabilityConfigError, match="must be >= 0"):
        ModelPricingTable.load(settings, pricing_file=pricing_file)


def test_model_pricing_table_rejects_version_mismatch(tmp_path: Path) -> None:
    pricing_file = _write_pricing(
        tmp_path,
        """
pricing_version: '2026-09'
models:
  - provider: openai
    model: gpt-4o-mini
    input_usd_per_1k: 0.001
    output_usd_per_1k: 0.002
""",
    )
    settings = Settings(
        openai_api_key="test-key",
        observability_cost_pricing_version="2026-08",
    )

    with pytest.raises(ObservabilityConfigError, match="version mismatch"):
        ModelPricingTable.load(settings, pricing_file=pricing_file)


def test_cost_calculator_prices_known_model() -> None:
    settings = Settings(
        openai_api_key="test-key",
        observability_cost_pricing_version="2026-08",
    )
    table = ModelPricingTable.load(settings, pricing_file=_CANONICAL_PRICING)
    calculator = CostCalculator(table)

    cost, version = calculator.price(
        "openai",
        "gpt-4o-mini",
        ProviderUsage(prompt_tokens=1000, completion_tokens=500, total_tokens=1500),
    )

    assert cost is not None
    assert cost > 0
    assert version == "2026-08"


def test_cost_calculator_unknown_model_returns_none() -> None:
    settings = Settings(
        openai_api_key="test-key",
        observability_cost_pricing_version="2026-08",
    )
    table = ModelPricingTable.load(settings, pricing_file=_CANONICAL_PRICING)
    calculator = CostCalculator(table)

    cost, version = calculator.price(
        "openai",
        "unknown-model",
        ProviderUsage(prompt_tokens=100, completion_tokens=50, total_tokens=150),
    )

    assert cost is None
    assert version is None


def test_cost_calculator_unknown_provider_returns_none() -> None:
    settings = Settings(
        openai_api_key="test-key",
        observability_cost_pricing_version="2026-08",
    )
    table = ModelPricingTable.load(settings, pricing_file=_CANONICAL_PRICING)
    calculator = CostCalculator(table)

    cost, version = calculator.price(
        "unknown-provider",
        "gpt-4o-mini",
        ProviderUsage(prompt_tokens=100, completion_tokens=50, total_tokens=150),
    )

    assert cost is None
    assert version is None


def test_cost_calculator_missing_usage_fields_returns_none() -> None:
    settings = Settings(
        openai_api_key="test-key",
        observability_cost_pricing_version="2026-08",
    )
    table = ModelPricingTable.load(settings, pricing_file=_CANONICAL_PRICING)
    calculator = CostCalculator(table)

    cost, version = calculator.price(
        "openai",
        "gpt-4o-mini",
        ProviderUsage(prompt_tokens=100, completion_tokens=None, total_tokens=100),
    )

    assert cost is None
    assert version is None


def test_cost_registry_skips_when_observability_disabled() -> None:
    CostRegistry.reset_for_tests()
    settings = Settings(openai_api_key="test-key", observability_enabled=False)
    CostRegistry.initialize(settings)

    assert CostRegistry.get_calculator() is None

    CostRegistry.reset_for_tests()
