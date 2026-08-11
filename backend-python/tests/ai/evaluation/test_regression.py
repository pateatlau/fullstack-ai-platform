"""Tests for RegressionChecker and regression CLI integration."""

from __future__ import annotations

import json
from collections.abc import Iterator
from pathlib import Path

import pytest

from app.ai.evaluation.cli import _levels_to_run, run_eval
from app.ai.evaluation.datasets import load_dataset
from app.ai.evaluation.regression import RegressionChecker, RegressionResult
from app.ai.evaluation.report import (
    EvalCaseResult,
    EvalRunEnvironment,
    EvalRunReport,
    load_json_report,
    write_json_report,
)
from app.core.config import get_settings

DATASET = Path("tests/data/evaluation/sample.yaml")


@pytest.fixture(autouse=True)
def _clear_settings_cache() -> Iterator[None]:
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def _environment(
    *,
    agent_runtime_enabled: bool = True,
    workflow_engine_enabled: bool = True,
    plugins_enabled: bool = False,
    postgres_available: bool = True,
    pgvector_available: bool = True,
) -> EvalRunEnvironment:
    return EvalRunEnvironment(
        agent_runtime_enabled=agent_runtime_enabled,
        workflow_engine_enabled=workflow_engine_enabled,
        plugins_enabled=plugins_enabled,
        postgres_available=postgres_available,
        pgvector_available=pgvector_available,
    )


def _case_result(
    case_id: str,
    level: str,
    *,
    passed: bool = True,
    latency_ms: int = 10,
    skipped: bool = False,
    model: str | None = None,
    prompt_version: str | None = None,
    temperature: float | None = None,
) -> EvalCaseResult:
    return EvalCaseResult(
        case_id=case_id,
        level=level,  # type: ignore[arg-type]
        passed=passed,
        latency_ms=latency_ms,
        skipped=skipped,
        model=model,
        prompt_version=prompt_version,
        temperature=temperature,
    )


def _report(
    *results: EvalCaseResult, env: EvalRunEnvironment | None = None
) -> EvalRunReport:
    return EvalRunReport(
        dataset_path=str(DATASET),
        settings_snapshot={},
        results=list(results),
        run_environment=env or _environment(),
    )


def test_regression_checker_no_regression() -> None:
    baseline = _report(
        _case_result("prompt_a", "prompt"),
        _case_result("agent_a", "agent"),
    )
    current = _report(
        _case_result("prompt_a", "prompt"),
        _case_result("agent_a", "agent"),
    )

    result = RegressionChecker.compare(
        current,
        baseline,
        pass_rate_tolerance_pct=5.0,
        latency_tolerance_pct=20.0,
        latency_floor_ms=10.0,
    )

    assert result.has_regression is False
    assert result.environment_mismatch is False
    assert result.baseline_invalid is False
    assert result.hard_regressions == []
    assert result.soft_regressions == []


def test_regression_checker_environment_mismatch() -> None:
    baseline = _report(
        _case_result("agent_a", "agent"),
        env=_environment(agent_runtime_enabled=True),
    )
    current = _report(
        _case_result("agent_a", "agent"),
        env=_environment(agent_runtime_enabled=False),
    )

    result = RegressionChecker.compare(
        current,
        baseline,
        pass_rate_tolerance_pct=5.0,
        latency_tolerance_pct=20.0,
        latency_floor_ms=10.0,
    )

    assert result.environment_mismatch is True
    assert result.environment_diff_fields
    assert "agent_runtime_enabled" in result.environment_diff_fields[0]
    assert result.hard_regressions == []
    assert result.soft_regressions == []


def test_regression_checker_ignores_db_env_for_prompt_only_runs() -> None:
    baseline = _report(
        _case_result("prompt_a", "prompt"),
        env=_environment(postgres_available=True, pgvector_available=True),
    )
    current = _report(
        _case_result("prompt_a", "prompt"),
        env=_environment(postgres_available=False, pgvector_available=False),
    )

    result = RegressionChecker.compare(
        current,
        baseline,
        pass_rate_tolerance_pct=5.0,
        latency_tolerance_pct=20.0,
        latency_floor_ms=10.0,
    )

    assert result.environment_mismatch is False
    assert result.hard_regressions == []


def test_regression_checker_rejects_skipped_agent_workflow_baseline() -> None:
    baseline = _report(
        _case_result("agent_a", "agent", skipped=True),
        env=_environment(),
    )
    current = _report(_case_result("agent_a", "agent"))

    result = RegressionChecker.compare(
        current,
        baseline,
        pass_rate_tolerance_pct=5.0,
        latency_tolerance_pct=20.0,
        latency_floor_ms=10.0,
    )

    assert result.baseline_invalid is True
    assert any("agent_a" in reason for reason in result.baseline_invalid_reasons)
    assert result.hard_regressions == []


def test_regression_checker_hard_regression() -> None:
    baseline = _report(
        _case_result(
            "prompt_a",
            "prompt",
            model="gpt-4o-mini",
            prompt_version="1",
            temperature=0.7,
        )
    )
    current = _report(
        _case_result(
            "prompt_a",
            "prompt",
            passed=False,
            model="gpt-4o",
            prompt_version="2",
            temperature=0.2,
        )
    )

    result = RegressionChecker.compare(
        current,
        baseline,
        pass_rate_tolerance_pct=5.0,
        latency_tolerance_pct=20.0,
        latency_floor_ms=10.0,
    )

    assert len(result.hard_regressions) == 1
    hard = result.hard_regressions[0]
    assert hard.case_id == "prompt_a"
    assert hard.baseline.model == "gpt-4o-mini"
    assert hard.baseline.prompt_version == "1"
    assert hard.current.model == "gpt-4o"
    assert hard.current.prompt_version == "2"


def test_regression_checker_pass_rate_regression() -> None:
    baseline = _report(
        _case_result("p1", "prompt"),
        _case_result("p2", "prompt"),
    )
    current = _report(
        _case_result("p1", "prompt"),
        _case_result("p2", "prompt", passed=False),
    )

    result = RegressionChecker.compare(
        current,
        baseline,
        pass_rate_tolerance_pct=5.0,
        latency_tolerance_pct=20.0,
        latency_floor_ms=10.0,
    )

    assert len(result.hard_regressions) == 1
    assert any(item.kind == "pass_rate" for item in result.soft_regressions)


def test_regression_checker_latency_regression() -> None:
    baseline = _report(_case_result("prompt_a", "prompt", latency_ms=100))
    current = _report(_case_result("prompt_a", "prompt", latency_ms=150))

    result = RegressionChecker.compare(
        current,
        baseline,
        pass_rate_tolerance_pct=5.0,
        latency_tolerance_pct=20.0,
        latency_floor_ms=10.0,
    )

    assert result.hard_regressions == []
    assert len(result.soft_regressions) == 1
    soft = result.soft_regressions[0]
    assert soft.kind == "latency"
    assert soft.level == "prompt"
    assert soft.current_value == pytest.approx(150.0)
    assert soft.baseline_value == pytest.approx(100.0)


def test_regression_checker_ignores_latency_pct_spike_below_absolute_floor() -> None:
    baseline = _report(_case_result("prompt_a", "prompt", latency_ms=10))
    current = _report(_case_result("prompt_a", "prompt", latency_ms=13))

    result = RegressionChecker.compare(
        current,
        baseline,
        pass_rate_tolerance_pct=5.0,
        latency_tolerance_pct=20.0,
        latency_floor_ms=10.0,
    )

    assert result.soft_regressions == []


def test_regression_checker_skips_latency_check_for_sub_ms_baseline() -> None:
    baseline = _report(_case_result("prompt_a", "prompt", latency_ms=0))
    current = _report(_case_result("prompt_a", "prompt", latency_ms=50))

    result = RegressionChecker.compare(
        current,
        baseline,
        pass_rate_tolerance_pct=5.0,
        latency_tolerance_pct=20.0,
        latency_floor_ms=10.0,
    )

    assert result.soft_regressions == []


def test_regression_result_json_serializable() -> None:
    result = RegressionResult(
        environment_mismatch=True,
        environment_diff_fields=["postgres_available: baseline=True, current=False"],
    )

    payload = json.loads(json.dumps(result.to_dict()))

    assert payload["environment_mismatch"] is True
    assert payload["environment_diff_fields"]


def test_load_json_report_round_trip(tmp_path: Path) -> None:
    report = _report(
        _case_result(
            "agent_a",
            "agent",
            model="gpt-4o-mini",
            temperature=0.3,
        )
    )
    path = tmp_path / "report.json"
    write_json_report(report, path)

    loaded = load_json_report(path)

    assert loaded.schema_version == report.schema_version
    assert loaded.results[0].model == "gpt-4o-mini"
    assert loaded.run_environment is not None
    assert loaded.run_environment.postgres_available is True


def test_load_json_report_rejects_missing_case_id(tmp_path: Path) -> None:
    path = tmp_path / "bad-report.json"
    path.write_text(
        json.dumps({"results": [{"level": "prompt", "passed": True}]}),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="missing or invalid case_id"):
        load_json_report(path)


def test_load_json_report_rejects_invalid_level(tmp_path: Path) -> None:
    path = tmp_path / "bad-report.json"
    path.write_text(
        json.dumps({"results": [{"case_id": "x", "level": "unknown"}]}),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="invalid level"):
        load_json_report(path)


def test_load_json_report_preserves_passed_and_latency_defaults(tmp_path: Path) -> None:
    path = tmp_path / "minimal-report.json"
    path.write_text(
        json.dumps({"results": [{"case_id": "prompt_a", "level": "prompt"}]}),
        encoding="utf-8",
    )

    loaded = load_json_report(path)

    assert len(loaded.results) == 1
    assert loaded.results[0].passed is False
    assert loaded.results[0].latency_ms == 0


def _args(**overrides: object):
    from argparse import Namespace

    base = {
        "level": "all",
        "dataset": DATASET,
        "output": Path(".eval/test-regression-report.json"),
        "use_judge": False,
        "check_regression": None,
        "regression_output": Path(".eval/test-regression-result.json"),
        "update_baseline": None,
    }
    base.update(overrides)
    return Namespace(**base)


@pytest.mark.anyio
async def test_cli_update_baseline_refuses_without_level_all(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(Path(__file__).resolve().parents[3])

    exit_code = await run_eval(
        _args(level="prompt", update_baseline=tmp_path / "baseline.json")
    )

    assert exit_code == 2
    assert not (tmp_path / "baseline.json").exists()


@pytest.mark.anyio
async def test_cli_check_regression_missing_baseline_returns_2(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.chdir(Path(__file__).resolve().parents[3])

    missing_baseline = tmp_path / "missing-baseline.json"
    output = tmp_path / "current.json"

    exit_code = await run_eval(
        _args(
            level="prompt",
            output=output,
            check_regression=missing_baseline,
        )
    )

    assert exit_code == 2
    assert "Baseline report not found" in capsys.readouterr().err


@pytest.mark.anyio
async def test_cli_check_regression_detects_hard_regression(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(Path(__file__).resolve().parents[3])
    monkeypatch.setenv("AGENT_RUNTIME_ENABLED", "true")
    monkeypatch.setenv("WORKFLOW_ENGINE_ENABLED", "true")

    baseline_path = tmp_path / "baseline.json"
    # Case absent from a prompt-only run → deterministic hard regression, not
    # environment mismatch (match offline prompt run_environment: no DB probe).
    baseline = _report(
        _case_result("baseline_only_prompt_case", "prompt", passed=True),
        env=_environment(postgres_available=False, pgvector_available=False),
    )
    write_json_report(baseline, baseline_path)

    output = tmp_path / "current.json"

    exit_code = await run_eval(
        _args(
            level="prompt",
            output=output,
            check_regression=baseline_path,
        )
    )

    assert exit_code == 1
    payload = json.loads(output.read_text(encoding="utf-8"))
    assert payload["summary"]["passed"] >= 1


@pytest.mark.anyio
async def test_cli_check_regression_writes_json_artifact(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(Path(__file__).resolve().parents[3])

    baseline_path = tmp_path / "baseline.json"
    baseline = _report(
        _case_result("baseline_only_prompt_case", "prompt", passed=True),
        env=_environment(),
    )
    write_json_report(baseline, baseline_path)

    regression_output = tmp_path / "regression-result.json"

    exit_code = await run_eval(
        _args(
            level="prompt",
            output=tmp_path / "current.json",
            check_regression=baseline_path,
            regression_output=regression_output,
        )
    )

    assert exit_code == 1
    payload = json.loads(regression_output.read_text(encoding="utf-8"))
    assert payload["hard_regressions"]
    assert payload["hard_regressions"][0]["case_id"] == "baseline_only_prompt_case"


@pytest.mark.anyio
async def test_cli_update_baseline_writes_file_when_eligible(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(Path(__file__).resolve().parents[3])
    monkeypatch.setenv("AGENT_RUNTIME_ENABLED", "true")
    monkeypatch.setenv("WORKFLOW_ENGINE_ENABLED", "true")

    baseline_path = tmp_path / "baseline.json"
    output = tmp_path / "current.json"

    exit_code = await run_eval(
        _args(level="all", output=output, update_baseline=baseline_path)
    )

    if exit_code == 2:
        pytest.skip("Postgres/pgvector prerequisites unavailable for baseline update")

    if exit_code == 1:
        pytest.skip("Eval run had failing cases; baseline update requires all passing")

    assert baseline_path.is_file()
    payload = json.loads(baseline_path.read_text(encoding="utf-8"))
    assert payload["schema_version"] == 2
    assert payload["run_environment"]["agent_runtime_enabled"] is True
    all_level_cases = [
        case
        for case in load_dataset(DATASET).cases
        if case.level in _levels_to_run("all")
    ]
    assert len(payload["results"]) == len(all_level_cases)
    assert not any(
        item["skipped"]
        for item in payload["results"]
        if item["level"] in {"agent", "workflow"}
    )


def test_dataset_loads_all_new_cases() -> None:
    dataset = load_dataset(
        Path(__file__).resolve().parents[2] / "data" / "evaluation" / "sample.yaml"
    )

    case_ids = {case.id for case in dataset.cases}
    expected = {
        "rag_answer_renders",
        "chat_summarize_system_snapshot",
        "rag_query_rewrite_snapshot",
        "chat_default_system_snapshot",
        "agent_planner_snapshot",
        "retrieval_finds_fixture",
        "empty_corpus_retrieval",
        "retrieval_markdown_fixture",
        "e2e_fixture_answer",
        "e2e_markdown_exact_match",
        "agent_echo_tool",
        "workflow_echo_complete",
        "workflow_sequential_tasks",
        "workflow_conditional_router",
        "workflow_approval_pause",
        "plugin_echo_tool_ping",
        "plugin_echo_prompt_greeting",
        "plugin_echo_workflow_node",
    }
    assert case_ids == expected
