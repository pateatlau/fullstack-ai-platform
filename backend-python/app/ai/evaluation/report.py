"""Console and JSON reporting for evaluation runs."""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from app.ai.evaluation.datasets import EvalLevel
from app.ai.evaluation.metrics import (
    TARGET_RAG_RESPONSE_MS,
    TARGET_RETRIEVAL_MS,
)

REPORT_SCHEMA_VERSION = 2

_ALL_LEVELS: tuple[EvalLevel, ...] = (
    "prompt",
    "retrieval",
    "e2e",
    "agent",
    "workflow",
    "plugin",
    "hitl",
)


@dataclass(frozen=True)
class EvalRunEnvironment:
    """Runtime prerequisites captured once per evaluation run."""

    agent_runtime_enabled: bool
    workflow_engine_enabled: bool
    plugins_enabled: bool
    hitl_enabled: bool
    postgres_available: bool
    pgvector_available: bool


@dataclass(frozen=True)
class EvalCaseResult:
    """Structured result for a single evaluation case."""

    case_id: str
    level: EvalLevel
    passed: bool
    latency_ms: int
    precision: float | None = None
    recall: float | None = None
    correctness: bool | None = None
    faithfulness: float | None = None
    hallucination: bool | None = None
    retrieved_count: int | None = None
    tool_calls_correct: bool | None = None
    terminal_status: str | None = None
    model: str | None = None
    model_version: str | None = None
    temperature: float | None = None
    seed: int | None = None
    prompt_version: str | None = None
    latency_warning: str | None = None
    error: str | None = None
    skipped: bool = False
    skip_reason: str | None = None


@dataclass
class EvalRunReport:
    """Aggregate report for an evaluation run."""

    dataset_path: str
    settings_snapshot: dict[str, object]
    results: list[EvalCaseResult] = field(default_factory=list)
    skipped_levels: list[str] = field(default_factory=list)
    run_environment: EvalRunEnvironment | None = None
    timestamp: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )
    schema_version: int = REPORT_SCHEMA_VERSION

    @property
    def passed_count(self) -> int:
        return sum(1 for result in self.results if result.passed and not result.skipped)

    @property
    def failed_count(self) -> int:
        return sum(
            1 for result in self.results if not result.passed and not result.skipped
        )

    @property
    def skipped_count(self) -> int:
        return sum(1 for result in self.results if result.skipped)

    def results_for_level(self, level: EvalLevel) -> list[EvalCaseResult]:
        return [result for result in self.results if result.level == level]

    def mean_latency_ms(self, level: EvalLevel) -> float | None:
        latencies = [
            result.latency_ms
            for result in self.results_for_level(level)
            if not result.skipped
        ]
        if not latencies:
            return None
        return sum(latencies) / len(latencies)

    def aggregate_precision(self) -> float | None:
        values = [
            result.precision
            for result in self.results_for_level("retrieval")
            if result.precision is not None and not result.skipped
        ]
        if not values:
            return None
        return sum(values) / len(values)

    def aggregate_recall(self) -> float | None:
        values = [
            result.recall
            for result in self.results_for_level("retrieval")
            if result.recall is not None and not result.skipped
        ]
        if not values:
            return None
        return sum(values) / len(values)

    def all_passed(self) -> bool:
        return not any(
            not result.passed and not result.skipped for result in self.results
        )


def print_console_summary(report: EvalRunReport) -> None:
    """Print a human-readable evaluation summary to stdout."""
    print("Evaluation summary")
    print(f"  Dataset: {report.dataset_path}")
    print(f"  Timestamp: {report.timestamp}")
    print(f"  Passed: {report.passed_count}")
    print(f"  Failed: {report.failed_count}")
    print(f"  Skipped: {report.skipped_count}")

    if report.run_environment is not None:
        env = report.run_environment
        print("\n  Run environment:")
        print(f"    agent_runtime_enabled: {env.agent_runtime_enabled}")
        print(f"    workflow_engine_enabled: {env.workflow_engine_enabled}")
        print(f"    plugins_enabled: {env.plugins_enabled}")
        print(f"    hitl_enabled: {env.hitl_enabled}")
        print(f"    postgres_available: {env.postgres_available}")
        print(f"    pgvector_available: {env.pgvector_available}")

    for level in _ALL_LEVELS:
        level_results = report.results_for_level(level)
        if not level_results:
            continue
        passed = sum(
            1 for result in level_results if result.passed and not result.skipped
        )
        failed = sum(
            1 for result in level_results if not result.passed and not result.skipped
        )
        skipped = sum(1 for result in level_results if result.skipped)
        mean_latency = report.mean_latency_ms(level)
        latency_text = f"{mean_latency:.1f} ms" if mean_latency is not None else "n/a"
        print(f"\n  [{level}] passed={passed} failed={failed} skipped={skipped}")
        print(f"    mean latency: {latency_text}")

        if level == "retrieval":
            precision = report.aggregate_precision()
            recall = report.aggregate_recall()
            if precision is not None:
                print(f"    mean precision: {precision:.3f}")
            if recall is not None:
                print(f"    mean recall: {recall:.3f}")
            print(f"    soft target: {TARGET_RETRIEVAL_MS} ms")

        if level == "e2e":
            print(f"    soft target: {TARGET_RAG_RESPONSE_MS} ms")

        for result in level_results:
            status = "SKIP" if result.skipped else ("PASS" if result.passed else "FAIL")
            detail_parts = [f"latency={result.latency_ms}ms"]
            if result.precision is not None:
                detail_parts.append(f"precision={result.precision:.3f}")
            if result.recall is not None:
                detail_parts.append(f"recall={result.recall:.3f}")
            if result.correctness is not None:
                detail_parts.append(f"correctness={result.correctness}")
            if result.faithfulness is not None:
                detail_parts.append(f"faithfulness={result.faithfulness:.3f}")
            if result.hallucination is not None:
                detail_parts.append(f"hallucination={result.hallucination}")
            if result.tool_calls_correct is not None:
                detail_parts.append(f"tool_calls_correct={result.tool_calls_correct}")
            if result.terminal_status is not None:
                detail_parts.append(f"terminal_status={result.terminal_status}")
            if result.model is not None:
                detail_parts.append(f"model={result.model}")
            if result.temperature is not None:
                detail_parts.append(f"temperature={result.temperature}")
            if result.prompt_version is not None:
                detail_parts.append(f"prompt_version={result.prompt_version}")
            if result.latency_warning:
                detail_parts.append(f"warning={result.latency_warning}")
            if result.error:
                detail_parts.append(f"error={result.error}")
            if result.skip_reason:
                detail_parts.append(f"reason={result.skip_reason}")
            print(f"    - {result.case_id}: {status} ({', '.join(detail_parts)})")

    if report.skipped_levels:
        print("\n  Skipped levels:")
        for reason in report.skipped_levels:
            print(f"    - {reason}")


def write_json_report(report: EvalRunReport, output_path: Path) -> None:
    """Write the full evaluation report to a JSON file."""
    output_path.parent.mkdir(parents=True, exist_ok=True)
    payload = _serialize_report(report)
    output_path.write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def load_json_report(path: Path) -> EvalRunReport:
    """Load an evaluation report from a JSON file."""
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"Report root must be a JSON object: {path}")

    run_environment = None
    env_raw = payload.get("run_environment")
    if isinstance(env_raw, dict):
        run_environment = EvalRunEnvironment(
            agent_runtime_enabled=bool(env_raw.get("agent_runtime_enabled")),
            workflow_engine_enabled=bool(env_raw.get("workflow_engine_enabled")),
            plugins_enabled=bool(env_raw.get("plugins_enabled")),
            hitl_enabled=bool(env_raw.get("hitl_enabled")),
            postgres_available=bool(env_raw.get("postgres_available")),
            pgvector_available=bool(env_raw.get("pgvector_available")),
        )

    results_raw = payload.get("results")
    if not isinstance(results_raw, list):
        raise ValueError(f"Report missing results list: {path}")

    results: list[EvalCaseResult] = []
    for index, item in enumerate(results_raw):
        if not isinstance(item, dict):
            raise ValueError(f"Report result entry must be an object: {path}")
        case_id = _require_result_case_id(item.get("case_id"), path=path, index=index)
        level = _require_result_level(item.get("level"), path=path, index=index)
        results.append(
            EvalCaseResult(
                case_id=case_id,
                level=level,
                passed=bool(item.get("passed", False)),
                latency_ms=int(item.get("latency_ms", 0)),
                precision=_optional_float(item.get("precision")),
                recall=_optional_float(item.get("recall")),
                correctness=_optional_bool(item.get("correctness")),
                faithfulness=_optional_float(item.get("faithfulness")),
                hallucination=_optional_bool(item.get("hallucination")),
                retrieved_count=_optional_int(item.get("retrieved_count")),
                tool_calls_correct=_optional_bool(item.get("tool_calls_correct")),
                terminal_status=_optional_str(item.get("terminal_status")),
                model=_optional_str(item.get("model")),
                model_version=_optional_str(item.get("model_version")),
                temperature=_optional_float(item.get("temperature")),
                seed=_optional_int(item.get("seed")),
                prompt_version=_optional_str(item.get("prompt_version")),
                latency_warning=_optional_str(item.get("latency_warning")),
                error=_optional_str(item.get("error")),
                skipped=bool(item.get("skipped", False)),
                skip_reason=_optional_str(item.get("skip_reason")),
            )
        )

    skipped_levels_raw = payload.get("skipped_levels", [])
    skipped_levels = (
        [str(item) for item in skipped_levels_raw]
        if isinstance(skipped_levels_raw, list)
        else []
    )

    settings_snapshot = payload.get("settings_snapshot", {})
    if not isinstance(settings_snapshot, dict):
        settings_snapshot = {}

    return EvalRunReport(
        dataset_path=str(payload.get("dataset_path", "")),
        settings_snapshot=settings_snapshot,
        results=results,
        skipped_levels=skipped_levels,
        run_environment=run_environment,
        timestamp=str(payload.get("timestamp", "")),
        schema_version=int(payload.get("schema_version", REPORT_SCHEMA_VERSION)),
    )


def _optional_str(value: object) -> str | None:
    return value if isinstance(value, str) else None


_ALLOWED_RESULT_LEVELS: frozenset[EvalLevel] = frozenset(_ALL_LEVELS)


def _require_result_case_id(value: object, *, path: Path, index: int) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"Report result[{index}] missing or invalid case_id: {path}")
    return value


def _require_result_level(value: object, *, path: Path, index: int) -> EvalLevel:
    if value not in _ALLOWED_RESULT_LEVELS:
        raise ValueError(f"Report result[{index}] has invalid level: {path}")
    return value  # type: ignore[return-value]


def _optional_bool(value: object) -> bool | None:
    return value if isinstance(value, bool) else None


def _optional_int(value: object) -> int | None:
    return value if isinstance(value, int) else None


def _optional_float(value: object) -> float | None:
    if isinstance(value, (int, float)):
        return float(value)
    return None


def _serialize_report(report: EvalRunReport) -> dict[str, Any]:
    return {
        "schema_version": report.schema_version,
        "timestamp": report.timestamp,
        "dataset_path": report.dataset_path,
        "settings_snapshot": report.settings_snapshot,
        "run_environment": (
            asdict(report.run_environment)
            if report.run_environment is not None
            else None
        ),
        "summary": {
            "passed": report.passed_count,
            "failed": report.failed_count,
            "skipped": report.skipped_count,
            "mean_latency_ms": {
                level: report.mean_latency_ms(level)
                for level in _ALL_LEVELS
                if report.results_for_level(level)
            },
            "retrieval": {
                "mean_precision": report.aggregate_precision(),
                "mean_recall": report.aggregate_recall(),
            },
        },
        "skipped_levels": report.skipped_levels,
        "results": [asdict(result) for result in report.results],
    }
