"""CLI entry point for the evaluation framework."""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.ai.evaluation.datasets import EvalLevel, filter_cases, load_dataset
from app.ai.evaluation.report import (
    EvalRunReport,
    print_console_summary,
    write_json_report,
)
from app.ai.evaluation.runners import (
    EndToEndEvalRunner,
    PromptEvalRunner,
    RetrievalEvalRunner,
    pgvector_available,
)
from app.ai.prompts.manager import create_prompt_manager
from app.core.config import Settings, get_settings

DEFAULT_DATASET = Path("tests/data/evaluation/sample.yaml")
DEFAULT_OUTPUT = Path(".eval/eval-report.json")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run prompt, retrieval, and e2e eval.")
    parser.add_argument(
        "--level",
        choices=["prompt", "retrieval", "e2e", "all"],
        default="all",
        help="Evaluation level to run (default: all).",
    )
    parser.add_argument(
        "--dataset",
        type=Path,
        default=DEFAULT_DATASET,
        help="Path to YAML/JSON eval dataset.",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=DEFAULT_OUTPUT,
        help="JSON report output path.",
    )
    parser.add_argument(
        "--use-judge",
        action="store_true",
        help="Enable LLM-as-judge for e2e faithfulness/hallucination checks.",
    )
    return parser


def _settings_snapshot(settings: Settings) -> dict[str, object]:
    return {
        "llm_provider": settings.llm_provider,
        "embedding_provider": settings.embedding_provider,
        "embedding_model": settings.embedding_model,
        "embedding_dimensions": settings.embedding_dimensions,
        "chunk_size": settings.chunk_size,
        "chunk_overlap": settings.chunk_overlap,
        "rag_top_k": settings.rag_top_k,
        "rag_context_max_chars": settings.rag_context_max_chars,
        "default_temperature": settings.default_temperature,
    }


def _levels_to_run(level: str) -> set[EvalLevel]:
    if level == "all":
        return {"prompt", "retrieval", "e2e"}
    return {level}  # type: ignore[return-value]


async def _run_with_session(
    *,
    settings: Settings,
    dataset_path: Path,
    levels: set[EvalLevel],
    use_judge: bool,
) -> EvalRunReport:
    dataset = load_dataset(dataset_path)
    prompt_manager = create_prompt_manager()
    report = EvalRunReport(
        dataset_path=str(dataset_path),
        settings_snapshot=_settings_snapshot(settings),
    )

    if "prompt" in levels:
        runner = PromptEvalRunner(prompt_manager=prompt_manager)
        for case in filter_cases(dataset, "prompt"):
            report.results.append(runner.run_case(case))

    db_levels = levels & {"retrieval", "e2e"}
    if not db_levels:
        return report

    engine = create_async_engine(settings.database_url, poolclass=NullPool)
    try:
        async with engine.connect() as conn:
            await conn.execute(text("SELECT 1"))
    except Exception as exc:
        for level in sorted(db_levels):
            for case in filter_cases(dataset, level):  # type: ignore[arg-type]
                report.results.append(
                    _skipped_result(
                        case_id=case.id,
                        level=level,  # type: ignore[arg-type]
                        reason=f"Postgres not available: {exc}",
                    )
                )
        report.skipped_levels.append(
            "retrieval/e2e skipped — Postgres unavailable (run from backend-python with DB up)"
        )
        await engine.dispose()
        return report

    factory = async_sessionmaker(engine, expire_on_commit=False)
    session: AsyncSession = factory()
    try:
        if not await pgvector_available(session):
            for level in sorted(db_levels):
                for case in filter_cases(dataset, level):  # type: ignore[arg-type]
                    report.results.append(
                        _skipped_result(
                            case_id=case.id,
                            level=level,  # type: ignore[arg-type]
                            reason="pgvector extension not available",
                        )
                    )
            report.skipped_levels.append(
                "retrieval/e2e skipped — pgvector extension not installed"
            )
            return report

        if "retrieval" in levels:
            retrieval_runner = RetrievalEvalRunner(session=session, settings=settings)
            for case in filter_cases(dataset, "retrieval"):
                report.results.append(await retrieval_runner.run_case(case))

        if "e2e" in levels:
            e2e_runner = EndToEndEvalRunner(
                session=session,
                settings=settings,
                prompt_manager=prompt_manager,
                use_judge=use_judge,
            )
            for case in filter_cases(dataset, "e2e"):
                report.results.append(await e2e_runner.run_case(case))
    finally:
        await session.rollback()
        await session.close()
        await engine.dispose()

    return report


def _skipped_result(*, case_id: str, level: EvalLevel, reason: str):
    from app.ai.evaluation.report import EvalCaseResult

    return EvalCaseResult(
        case_id=case_id,
        level=level,
        passed=False,
        latency_ms=0,
        skipped=True,
        skip_reason=reason,
    )


async def run_eval(args: argparse.Namespace) -> int:
    get_settings.cache_clear()
    settings = get_settings()
    levels = _levels_to_run(args.level)
    report = await _run_with_session(
        settings=settings,
        dataset_path=args.dataset,
        levels=levels,
        use_judge=args.use_judge,
    )
    print_console_summary(report)
    write_json_report(report, args.output)
    print(f"\nJSON report written to: {args.output}")
    return 0 if report.all_passed() else 1


def main(argv: list[str] | None = None) -> None:
    parser = build_parser()
    args = parser.parse_args(argv)
    exit_code = asyncio.run(run_eval(args))
    sys.exit(exit_code)


if __name__ == "__main__":
    main()
