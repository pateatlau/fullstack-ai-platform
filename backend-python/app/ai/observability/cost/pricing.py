"""Model pricing table loader — git-tracked ``config/model_pricing.yaml``."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from app.ai.observability.exceptions import ObservabilityConfigError
from app.core.config import Settings

_BACKEND_ROOT = Path(__file__).resolve().parents[4]


@dataclass(frozen=True)
class ModelPricingEntry:
    provider: str
    model: str
    input_usd_per_1k: float
    output_usd_per_1k: float


class ModelPricingTable:
    """Version-locked per-(provider, model) token rates loaded at startup."""

    def __init__(
        self,
        *,
        pricing_version: str,
        entries: dict[tuple[str, str], ModelPricingEntry],
    ) -> None:
        self._pricing_version = pricing_version
        self._entries = entries

    @property
    def pricing_version(self) -> str:
        return self._pricing_version

    @property
    def model_registry(self) -> frozenset[str]:
        return frozenset(entry.model for entry in self._entries.values())

    def lookup(self, provider: str, model: str) -> ModelPricingEntry | None:
        return self._entries.get((provider, model))

    @classmethod
    def load(
        cls, settings: Settings, *, pricing_file: Path | None = None
    ) -> ModelPricingTable:
        path = pricing_file or (
            _BACKEND_ROOT / settings.observability_cost_pricing_file
        )
        if not path.is_file():
            raise ObservabilityConfigError(f"Model pricing file not found: {path}")

        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
        if not isinstance(raw, dict):
            raise ObservabilityConfigError(
                "Model pricing file must contain a YAML mapping at the top level."
            )

        file_version = raw.get("pricing_version")
        if not isinstance(file_version, str) or not file_version.strip():
            raise ObservabilityConfigError(
                "Model pricing file requires a non-empty pricing_version."
            )
        if file_version != settings.observability_cost_pricing_version:
            raise ObservabilityConfigError(
                "Model pricing version mismatch: "
                f"file has {file_version!r}, "
                f"settings.observability_cost_pricing_version is "
                f"{settings.observability_cost_pricing_version!r}."
            )

        models_raw = raw.get("models")
        if not isinstance(models_raw, list) or not models_raw:
            raise ObservabilityConfigError(
                "Model pricing file requires a non-empty models list."
            )

        entries: dict[tuple[str, str], ModelPricingEntry] = {}
        for index, item in enumerate(models_raw):
            entry = cls._parse_entry(item, index=index)
            key = (entry.provider, entry.model)
            if key in entries:
                raise ObservabilityConfigError(
                    f"Duplicate pricing entry for provider={entry.provider!r}, "
                    f"model={entry.model!r}."
                )
            entries[key] = entry

        return cls(pricing_version=file_version, entries=entries)

    @staticmethod
    def _parse_entry(raw: Any, *, index: int) -> ModelPricingEntry:
        if not isinstance(raw, dict):
            raise ObservabilityConfigError(f"models[{index}] must be a mapping.")

        provider = raw.get("provider")
        model = raw.get("model")
        input_rate = raw.get("input_usd_per_1k")
        output_rate = raw.get("output_usd_per_1k")

        if not isinstance(provider, str) or not provider.strip():
            raise ObservabilityConfigError(
                f"models[{index}].provider must be a non-empty string."
            )
        if not isinstance(model, str) or not model.strip():
            raise ObservabilityConfigError(
                f"models[{index}].model must be a non-empty string."
            )

        validated_input_rate = input_rate
        validated_output_rate = output_rate
        for field_name, value in (
            ("input_usd_per_1k", validated_input_rate),
            ("output_usd_per_1k", validated_output_rate),
        ):
            if not isinstance(value, (int, float)) or isinstance(value, bool):
                raise ObservabilityConfigError(
                    f"models[{index}].{field_name} must be a finite number >= 0."
                )
            if value < 0:
                raise ObservabilityConfigError(
                    f"models[{index}].{field_name} must be >= 0."
                )

        assert isinstance(validated_input_rate, (int, float))
        assert isinstance(validated_output_rate, (int, float))

        return ModelPricingEntry(
            provider=provider.strip(),
            model=model.strip(),
            input_usd_per_1k=float(validated_input_rate),
            output_usd_per_1k=float(validated_output_rate),
        )
