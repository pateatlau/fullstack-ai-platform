"""CLI entry point for the evaluation framework."""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.pool import NullPool

from app.ai.evaluation.datasets import EvalLevel, filter_cases, load_dataset
from app.ai.evaluation.report import (
    EvalRunEnvironment,
    EvalRunReport,
    print_console_summary,
    write_json_report,
)
from app.ai.evaluation.runners import (
    AgentEvalRunner,
    EndToEndEvalRunner,
    PromptEvalRunner,
    RetrievalEvalRunner,
    WorkflowEvalRunner,
    pgvector_available,
    postgres_available,
)
from app.ai.prompts.manager import create_prompt_manager
from app.core.config import Settings, get_settings

DEFAULT_DATASET = Path("tests/data/evaluation/sample.yaml")
DEFAULT_OUTPUT = Path(".eval/eval-report.json")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Run prompt, retrieval, e2e, agent, and workflow eval."
    )
    parser.add_argument(
        "--level",
        choices=["prompt", "retrieval", "e2e", "agent", "workflow", "all"],
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
        "agent_runtime_enabled": settings.agent_runtime_enabled,
        "workflow_engine_enabled": settings.workflow_engine_enabled,
    }


def _levels_to_run(level: str) -> set[EvalLevel]:
    if level == "all":
        return {"prompt", "retrieval", "e2e", "agent", "workflow"}
    return {level}  # type: ignore[return-value]


def _all_level_prerequisite_error(
    *,
    settings: Settings,
    postgres_ok: bool,
    pgvector_ok: bool,
) -> str | None:
    if not settings.agent_runtime_enabled:
        return (
            "--level all requires AGENT_RUNTIME_ENABLED=true "
            "(target --level agent to skip when disabled)."
        )
    if not settings.workflow_engine_enabled:
        return (
            "--level all requires WORKFLOW_ENGINE_ENABLED=true "
            "(target --level workflow to skip when disabled)."
        )
    if not postgres_ok:
        return "--level all requires Postgres (run from backend-python with DB up)."
    if not pgvector_ok:
        return "--level all requires the pgvector extension."
    return None


async def _probe_postgres(
    settings: Settings,
) -> tuple[bool, bool, AsyncSession | None, AsyncEngine | None]:
    """Return postgres/pgvector availability and an open session when Postgres is up."""
    engine = create_async_engine(settings.database_url, poolclass=NullPool)
    session: AsyncSession | None = None
    try:
        factory = async_sessionmaker(engine, expire_on_commit=False)
        session = factory()
        if not await postgres_available(session):
            await session.close()
            await engine.dispose()
            return False, False, None, None
        pgvector_ok = await pgvector_available(session)
        return True, pgvector_ok, session, engine
    except Exception:
        if session is not None:
            await session.close()
        await engine.dispose()
        return False, False, None, None


async def _dispose_db_resources(
    session: AsyncSession | None,
    engine: AsyncEngine | None,
    *,
    rollback: bool = False,
) -> None:
    if session is not None:
        if rollback:
            await session.rollback()
        await session.close()
    if engine is not None:
        await engine.dispose()


async def _run_with_session(
    *,
    settings: Settings,
    dataset_path: Path,
    levels: set[EvalLevel],
    use_judge: bool,
    level_arg: str,
) -> tuple[EvalRunReport, int | None]:
    dataset = load_dataset(dataset_path)
    prompt_manager = create_prompt_manager()
    report = EvalRunReport(
        dataset_path=str(dataset_path),
        settings_snapshot=_settings_snapshot(settings),
    )

    postgres_ok = False
    pgvector_ok = False
    session: AsyncSession | None = None
    engine: AsyncEngine | None = None

    db_levels = levels & {"retrieval", "e2e", "workflow"}
    if db_levels:
        postgres_ok, pgvector_ok, session, engine = await _probe_postgres(settings)

    report.run_environment = EvalRunEnvironment(
        agent_runtime_enabled=settings.agent_runtime_enabled,
        workflow_engine_enabled=settings.workflow_engine_enabled,
        postgres_available=postgres_ok,
        pgvector_available=pgvector_ok,
    )

    if level_arg == "all":
        prerequisite_error = _all_level_prerequisite_error(
            settings=settings,
            postgres_ok=postgres_ok,
            pgvector_ok=pgvector_ok,
        )
        if prerequisite_error is not None:
            print(prerequisite_error, file=sys.stderr)
            await _dispose_db_resources(session, engine)
            return report, 2

    if "prompt" in levels:
        runner = PromptEvalRunner(prompt_manager=prompt_manager)
        for case in filter_cases(dataset, "prompt"):
            report.results.append(runner.run_case(case))

    if "agent" in levels:
        agent_runner = AgentEvalRunner(
            settings=settings,
            prompt_manager=prompt_manager,
        )
        for case in filter_cases(dataset, "agent"):
            report.results.append(await agent_runner.run_case(case))

    if not db_levels:
        return report, None

    if not postgres_ok:
        for level in sorted(db_levels):
            for case in filter_cases(dataset, level):  # type: ignore[arg-type]
                report.results.append(
                    _skipped_result(
                        case_id=case.id,
                        level=level,  # type: ignore[arg-type]
                        reason="Postgres not available (run from backend-python with DB up)",
                    )
                )
        report.skipped_levels.append(
            f"{'/'.join(sorted(db_levels))} skipped — Postgres unavailable"
        )
        return report, None

    if session is None or engine is None:
        return report, None

    if levels & {"retrieval", "e2e"} and not pgvector_ok:
        for level in sorted(levels & {"retrieval", "e2e"}):
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
    else:
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

    if "workflow" in levels:
        if not pgvector_ok:
            for case in filter_cases(dataset, "workflow"):
                report.results.append(
                    _skipped_result(
                        case_id=case.id,
                        level="workflow",
                        reason="pgvector extension not available",
                    )
                )
            report.skipped_levels.append(
                "workflow skipped — pgvector extension not installed"
            )
        else:
            workflow_runner = WorkflowEvalRunner(session=session, settings=settings)
            for case in filter_cases(dataset, "workflow"):
                report.results.append(await workflow_runner.run_case(case))

    await _dispose_db_resources(session, engine, rollback=True)
    return report, None


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
    report, early_exit = await _run_with_session(
        settings=settings,
        dataset_path=args.dataset,
        levels=levels,
        use_judge=args.use_judge,
        level_arg=args.level,
    )
    if early_exit is not None:
        return early_exit
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
