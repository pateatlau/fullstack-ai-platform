"""CLI integration smoke tests for the evaluation framework."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.ai.evaluation.cli import run_eval
from app.core.config import Settings, get_settings

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
    assert payload["schema_version"] == 2
    assert payload["run_environment"] is not None


@pytest.mark.anyio
async def test_cli_level_agent_runs_when_flag_on(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    output = tmp_path / "agent-report.json"
    monkeypatch.chdir(Path(__file__).resolve().parents[1])
    get_settings.cache_clear()
    monkeypatch.setenv("AGENT_RUNTIME_ENABLED", "true")

    exit_code = await run_eval(_args(level="agent", output=output))

    payload = json.loads(output.read_text(encoding="utf-8"))
    assert len(payload["results"]) == 1
    assert payload["results"][0]["level"] == "agent"
    assert exit_code in {0, 1}


@pytest.mark.anyio
async def test_cli_level_agent_skips_when_flag_off(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    output = tmp_path / "agent-skip-report.json"
    monkeypatch.chdir(Path(__file__).resolve().parents[1])
    get_settings.cache_clear()
    monkeypatch.setenv("AGENT_RUNTIME_ENABLED", "false")

    exit_code = await run_eval(_args(level="agent", output=output))

    payload = json.loads(output.read_text(encoding="utf-8"))
    assert payload["results"][0]["skipped"] is True
    assert exit_code == 0


@pytest.mark.anyio
async def test_cli_level_all_hard_fails_without_prerequisites(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    output = tmp_path / "all-prereq-report.json"
    monkeypatch.chdir(Path(__file__).resolve().parents[1])
    get_settings.cache_clear()
    disabled_settings = Settings(
        openai_api_key="test-key",
        agent_runtime_enabled=False,
        workflow_engine_enabled=False,
    )

    def fake_get_settings() -> Settings:
        return disabled_settings

    fake_get_settings.cache_clear = lambda: None  # type: ignore[attr-defined]
    monkeypatch.setattr("app.ai.evaluation.cli.get_settings", fake_get_settings)

    exit_code = await run_eval(_args(level="all", output=output))

    assert exit_code == 2
    assert not output.exists()


@pytest.mark.anyio
async def test_cli_level_all_smoke(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    output = tmp_path / "all-report.json"
    monkeypatch.chdir(Path(__file__).resolve().parents[1])
    get_settings.cache_clear()
    monkeypatch.setenv("AGENT_RUNTIME_ENABLED", "true")
    monkeypatch.setenv("WORKFLOW_ENGINE_ENABLED", "true")

    exit_code = await run_eval(_args(level="all", output=output))

    if exit_code == 2:
        pytest.skip("Postgres/pgvector prerequisites unavailable for --level all")

    payload = json.loads(output.read_text(encoding="utf-8"))
    assert "results" in payload
    assert len(payload["results"]) == 7
    assert payload["run_environment"] is not None
    assert exit_code in {0, 1}
