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

REPORT_SCHEMA_VERSION = 1


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

    for level in ("prompt", "retrieval", "e2e"):
        level_results = report.results_for_level(level)  # type: ignore[arg-type]
        if not level_results:
            continue
        passed = sum(
            1 for result in level_results if result.passed and not result.skipped
        )
        failed = sum(
            1 for result in level_results if not result.passed and not result.skipped
        )
        skipped = sum(1 for result in level_results if result.skipped)
        mean_latency = report.mean_latency_ms(level)  # type: ignore[arg-type]
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


def _serialize_report(report: EvalRunReport) -> dict[str, Any]:
    return {
        "schema_version": report.schema_version,
        "timestamp": report.timestamp,
        "dataset_path": report.dataset_path,
        "settings_snapshot": report.settings_snapshot,
        "summary": {
            "passed": report.passed_count,
            "failed": report.failed_count,
            "skipped": report.skipped_count,
            "mean_latency_ms": {
                level: report.mean_latency_ms(level)  # type: ignore[arg-type]
                for level in ("prompt", "retrieval", "e2e")
                if report.results_for_level(level)  # type: ignore[arg-type]
            },
            "retrieval": {
                "mean_precision": report.aggregate_precision(),
                "mean_recall": report.aggregate_recall(),
            },
        },
        "skipped_levels": report.skipped_levels,
        "results": [asdict(result) for result in report.results],
    }
