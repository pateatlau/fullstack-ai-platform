"""CLI integration smoke tests for the evaluation framework."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.ai.evaluation.cli import run_eval
from app.core.config import get_settings

DATASET = Path("tests/data/evaluation/sample.yaml")


def _args(**overrides: object):
    from argparse import Namespace

    base = {
        "level": "prompt",
        "dataset": DATASET,
        "output": Path(".eval/test-eval-report.json"),
        "use_judge": False,
    }
    base.update(overrides)
    return Namespace(**base)


@pytest.mark.anyio
async def test_cli_level_prompt_runs_offline(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    output = tmp_path / "prompt-report.json"
    monkeypatch.chdir(Path(__file__).resolve().parents[1])
    get_settings.cache_clear()

    exit_code = await run_eval(_args(level="prompt", output=output))

    assert exit_code == 0
    payload = json.loads(output.read_text(encoding="utf-8"))
    assert payload["summary"]["passed"] >= 2
    assert payload["schema_version"] == 1


@pytest.mark.anyio
async def test_cli_level_all_smoke(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    output = tmp_path / "all-report.json"
    monkeypatch.chdir(Path(__file__).resolve().parents[1])
    get_settings.cache_clear()

    exit_code = await run_eval(_args(level="all", output=output))

    payload = json.loads(output.read_text(encoding="utf-8"))
    assert "results" in payload
    assert len(payload["results"]) == 5
    # Exit code depends on DB availability; skipped DB cases must not fail the run.
    assert exit_code in {0, 1}
