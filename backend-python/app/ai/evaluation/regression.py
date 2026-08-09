"""Regression detection against a git-tracked evaluation baseline."""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Literal

from app.ai.evaluation.datasets import EvalLevel
from app.ai.evaluation.report import (
    EvalCaseResult,
    EvalRunEnvironment,
    EvalRunReport,
    load_json_report,
)

_ALL_LEVELS: tuple[EvalLevel, ...] = (
    "prompt",
    "retrieval",
    "e2e",
    "agent",
    "workflow",
)

_ENVIRONMENT_FIELDS: tuple[str, ...] = (
    "agent_runtime_enabled",
    "workflow_engine_enabled",
    "postgres_available",
    "pgvector_available",
)


@dataclass(frozen=True)
class ReproducibilitySnapshot:
    """Per-case metadata explaining what changed between runs."""

    model: str | None = None
    model_version: str | None = None
    temperature: float | None = None
    seed: int | None = None
    prompt_version: str | None = None


@dataclass(frozen=True)
class HardRegressionCase:
    """A case that passed in the baseline but fails in the current run."""

    case_id: str
    level: EvalLevel
    baseline: ReproducibilitySnapshot
    current: ReproducibilitySnapshot


@dataclass(frozen=True)
class SoftRegressionFinding:
    """Per-level pass-rate or latency regression beyond tolerance."""

    level: EvalLevel
    kind: Literal["pass_rate", "latency"]
    baseline_value: float
    current_value: float
    tolerance_pct: float


@dataclass
class RegressionResult:
    """Outcome of comparing a current eval run against a baseline."""

    environment_mismatch: bool = False
    environment_diff_fields: list[str] = field(default_factory=list)
    baseline_invalid: bool = False
    baseline_invalid_reasons: list[str] = field(default_factory=list)
    hard_regressions: list[HardRegressionCase] = field(default_factory=list)
    soft_regressions: list[SoftRegressionFinding] = field(default_factory=list)

    @property
    def has_regression(self) -> bool:
        return (
            self.environment_mismatch
            or self.baseline_invalid
            or bool(self.hard_regressions)
            or bool(self.soft_regressions)
        )

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class RegressionChecker:
    """Pure comparison of current and baseline ``EvalRunReport`` instances."""

    @staticmethod
    def compare(
        current: EvalRunReport,
        baseline: EvalRunReport,
        *,
        pass_rate_tolerance_pct: float,
        latency_tolerance_pct: float,
    ) -> RegressionResult:
        baseline_invalid_reasons = _baseline_invalid_reasons(baseline)
        environment_diff_fields = _environment_diff_fields(
            current.run_environment,
            baseline.run_environment,
        )

        result = RegressionResult(
            environment_mismatch=bool(environment_diff_fields),
            environment_diff_fields=environment_diff_fields,
            baseline_invalid=bool(baseline_invalid_reasons),
            baseline_invalid_reasons=baseline_invalid_reasons,
        )

        if result.baseline_invalid or result.environment_mismatch:
            return result

        result.hard_regressions = _detect_hard_regressions(current, baseline)
        result.soft_regressions = _detect_soft_regressions(
            current,
            baseline,
            pass_rate_tolerance_pct=pass_rate_tolerance_pct,
            latency_tolerance_pct=latency_tolerance_pct,
        )
        return result


def load_baseline_report(path: Path) -> EvalRunReport:
    """Load a baseline report from disk."""
    if not path.is_file():
        raise FileNotFoundError(f"Baseline report not found: {path}")
    return load_json_report(path)


def print_regression_summary(result: RegressionResult) -> None:
    """Print a human-readable regression summary to stdout."""
    print("\nRegression check")
    if result.baseline_invalid:
        print("  Baseline invalid — cannot compare:")
        for reason in result.baseline_invalid_reasons:
            print(f"    - {reason}")
        return

    if result.environment_mismatch:
        print("  Environment mismatch — cannot compare metrics:")
        for field_name in result.environment_diff_fields:
            print(f"    - {field_name}")
        return

    if not result.hard_regressions and not result.soft_regressions:
        print("  No regressions detected.")
        return

    if result.hard_regressions:
        print(f"  Hard regressions ({len(result.hard_regressions)}):")
        for item in result.hard_regressions:
            print(f"    - {item.case_id} [{item.level}]")
            print(f"        baseline: {_format_snapshot(item.baseline)}")
            print(f"        current:  {_format_snapshot(item.current)}")

    if result.soft_regressions:
        print(f"  Soft regressions ({len(result.soft_regressions)}):")
        for item in result.soft_regressions:
            print(
                f"    - [{item.level}] {item.kind}: "
                f"baseline={item.baseline_value:.2f}, "
                f"current={item.current_value:.2f}, "
                f"tolerance={item.tolerance_pct:.1f}%"
            )


def write_regression_result(result: RegressionResult, output_path: Path) -> None:
    """Write regression findings to JSON for CI artifacts."""
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        json.dumps(result.to_dict(), indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def _baseline_invalid_reasons(baseline: EvalRunReport) -> list[str]:
    reasons: list[str] = []
    env = baseline.run_environment
    if env is None:
        reasons.append("baseline missing run_environment")
    else:
        if not env.agent_runtime_enabled:
            reasons.append("baseline run_environment.agent_runtime_enabled=false")
        if not env.workflow_engine_enabled:
            reasons.append("baseline run_environment.workflow_engine_enabled=false")
        if not env.postgres_available:
            reasons.append("baseline run_environment.postgres_available=false")
        if not env.pgvector_available:
            reasons.append("baseline run_environment.pgvector_available=false")

    for case_result in baseline.results:
        if case_result.level in {"agent", "workflow"} and case_result.skipped:
            reason = case_result.skip_reason or "skipped"
            reasons.append(f"baseline case '{case_result.case_id}' skipped ({reason})")
    return reasons


def _environment_diff_fields(
    current: EvalRunEnvironment | None,
    baseline: EvalRunEnvironment | None,
) -> list[str]:
    if current is None or baseline is None:
        missing = []
        if current is None:
            missing.append("current.run_environment missing")
        if baseline is None:
            missing.append("baseline.run_environment missing")
        return missing

    diffs: list[str] = []
    for field_name in _ENVIRONMENT_FIELDS:
        current_value = getattr(current, field_name)
        baseline_value = getattr(baseline, field_name)
        if current_value != baseline_value:
            diffs.append(
                f"{field_name}: baseline={baseline_value}, current={current_value}"
            )
    return diffs


def _detect_hard_regressions(
    current: EvalRunReport,
    baseline: EvalRunReport,
) -> list[HardRegressionCase]:
    current_by_id = _index_results(current)
    findings: list[HardRegressionCase] = []

    for baseline_result in baseline.results:
        if baseline_result.skipped or not baseline_result.passed:
            continue
        current_result = current_by_id.get(baseline_result.case_id)
        if (
            current_result is None
            or current_result.skipped
            or not current_result.passed
        ):
            current_snapshot = (
                _snapshot_from_result(current_result)
                if current_result is not None
                else ReproducibilitySnapshot()
            )
            findings.append(
                HardRegressionCase(
                    case_id=baseline_result.case_id,
                    level=baseline_result.level,
                    baseline=_snapshot_from_result(baseline_result),
                    current=current_snapshot,
                )
            )
    return findings


def _detect_soft_regressions(
    current: EvalRunReport,
    baseline: EvalRunReport,
    *,
    pass_rate_tolerance_pct: float,
    latency_tolerance_pct: float,
) -> list[SoftRegressionFinding]:
    findings: list[SoftRegressionFinding] = []

    for level in _ALL_LEVELS:
        baseline_rate = _level_pass_rate(baseline, level)
        current_rate = _level_pass_rate(current, level)
        if baseline_rate is not None and current_rate is not None:
            drop = baseline_rate - current_rate
            if drop > pass_rate_tolerance_pct:
                findings.append(
                    SoftRegressionFinding(
                        level=level,
                        kind="pass_rate",
                        baseline_value=baseline_rate,
                        current_value=current_rate,
                        tolerance_pct=pass_rate_tolerance_pct,
                    )
                )

        baseline_latency = baseline.mean_latency_ms(level)
        current_latency = current.mean_latency_ms(level)
        if (
            baseline_latency is not None
            and current_latency is not None
            and baseline_latency > 0
        ):
            increase_pct = (
                (current_latency - baseline_latency) / baseline_latency
            ) * 100.0
            if increase_pct > latency_tolerance_pct:
                findings.append(
                    SoftRegressionFinding(
                        level=level,
                        kind="latency",
                        baseline_value=baseline_latency,
                        current_value=current_latency,
                        tolerance_pct=latency_tolerance_pct,
                    )
                )
    return findings


def _level_pass_rate(report: EvalRunReport, level: EvalLevel) -> float | None:
    results = [
        result for result in report.results_for_level(level) if not result.skipped
    ]
    if not results:
        return None
    passed = sum(1 for result in results if result.passed)
    return (passed / len(results)) * 100.0


def _index_results(report: EvalRunReport) -> dict[str, EvalCaseResult]:
    return {result.case_id: result for result in report.results}


def _snapshot_from_result(result: EvalCaseResult) -> ReproducibilitySnapshot:
    return ReproducibilitySnapshot(
        model=result.model,
        model_version=result.model_version,
        temperature=result.temperature,
        seed=result.seed,
        prompt_version=result.prompt_version,
    )


def _format_snapshot(snapshot: ReproducibilitySnapshot) -> str:
    parts: list[str] = []
    if snapshot.model is not None:
        parts.append(f"model={snapshot.model}")
    if snapshot.model_version is not None:
        parts.append(f"model_version={snapshot.model_version}")
    if snapshot.temperature is not None:
        parts.append(f"temperature={snapshot.temperature}")
    if snapshot.seed is not None:
        parts.append(f"seed={snapshot.seed}")
    if snapshot.prompt_version is not None:
        parts.append(f"prompt_version={snapshot.prompt_version}")
    return ", ".join(parts) if parts else "(no reproducibility metadata)"
