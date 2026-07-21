"""Tests for evaluation dataset loading and validation."""

from __future__ import annotations

from pathlib import Path

import pytest

from app.ai.evaluation.datasets import EvalDatasetError, load_dataset

DATA_DIR = Path(__file__).resolve().parent / "data" / "evaluation"


def test_load_valid_sample_yaml() -> None:
    dataset = load_dataset(DATA_DIR / "sample.yaml")

    assert dataset.path.name == "sample.yaml"
    assert len(dataset.cases) == 5
    levels = {case.level for case in dataset.cases}
    assert levels == {"prompt", "retrieval", "e2e"}


def test_load_invalid_yaml_missing_cases(tmp_path: Path) -> None:
    path = tmp_path / "invalid.yaml"
    path.write_text("name: broken\n", encoding="utf-8")

    with pytest.raises(EvalDatasetError, match="cases"):
        load_dataset(path)


def test_load_invalid_prompt_case_missing_assertions(tmp_path: Path) -> None:
    path = tmp_path / "bad_prompt.yaml"
    path.write_text(
        """
cases:
  - id: bad_prompt
    level: prompt
    prompt_category: chat
    prompt_name: summarize_system
    prompt_version: '1'
    prompt_variables: {}
""".strip(),
        encoding="utf-8",
    )

    with pytest.raises(EvalDatasetError, match="expected_render"):
        load_dataset(path)


def test_load_invalid_level(tmp_path: Path) -> None:
    path = tmp_path / "bad_level.yaml"
    path.write_text(
        """
cases:
  - id: x
    level: unknown
""".strip(),
        encoding="utf-8",
    )

    with pytest.raises(EvalDatasetError, match="level"):
        load_dataset(path)


def test_load_malformed_yaml_raises_eval_dataset_error(tmp_path: Path) -> None:
    path = tmp_path / "malformed.yaml"
    path.write_text("cases:\n  - id: [unclosed\n", encoding="utf-8")

    with pytest.raises(EvalDatasetError, match="Failed to parse YAML dataset"):
        load_dataset(path)


def test_load_malformed_json_raises_eval_dataset_error(tmp_path: Path) -> None:
    path = tmp_path / "malformed.json"
    path.write_text('{"cases": [', encoding="utf-8")

    with pytest.raises(EvalDatasetError, match="Failed to parse JSON dataset"):
        load_dataset(path)
