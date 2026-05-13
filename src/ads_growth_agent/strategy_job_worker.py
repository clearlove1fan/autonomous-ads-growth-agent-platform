from uuid import uuid4

from pydantic import BaseModel, Field

from ads_growth_agent.config import Settings
from ads_growth_agent.contracts import StrategyJobDetailResponse
from ads_growth_agent.observability import create_run_context
from ads_growth_agent.persistence.strategy_job_store import StrategyJobStore
from ads_growth_agent.strategy import StrategyGenerationError, generate_growth_strategy
from ads_growth_agent.strategy_job_store_factory import build_configured_strategy_job_store


class StrategyJobWorkerReport(BaseModel):
    worker_id: str = Field(min_length=1, max_length=160)
    claimed: int = Field(ge=0)
    completed: int = Field(ge=0)
    failed: int = Field(ge=0)
    job_ids: list[str] = Field(default_factory=list)
    failures: list[dict] = Field(default_factory=list)


def process_configured_strategy_jobs(
    settings: Settings,
    *,
    limit: int,
    worker_id: str | None = None,
    lock_seconds: int = 1_800,
) -> StrategyJobWorkerReport:
    return process_strategy_jobs(
        build_configured_strategy_job_store(settings),
        settings=settings,
        limit=limit,
        worker_id=worker_id,
        lock_seconds=lock_seconds,
    )


def process_strategy_jobs(
    job_store: StrategyJobStore,
    *,
    settings: Settings,
    limit: int,
    worker_id: str | None = None,
    lock_seconds: int = 1_800,
) -> StrategyJobWorkerReport:
    effective_worker_id = worker_id or f"strategy_worker_{uuid4().hex[:12]}"
    claimed_jobs = job_store.claim_queued(
        limit=limit,
        worker_id=effective_worker_id,
        lock_seconds=lock_seconds,
    )
    completed = 0
    failures: list[dict] = []
    for job in claimed_jobs:
        result = execute_claimed_strategy_job(job_store, job, settings=settings)
        if result:
            completed += 1
        else:
            failures.append({"job_id": job.job_id, "status": "failed"})

    return StrategyJobWorkerReport(
        worker_id=effective_worker_id,
        claimed=len(claimed_jobs),
        completed=completed,
        failed=len(claimed_jobs) - completed,
        job_ids=[job.job_id for job in claimed_jobs],
        failures=failures,
    )


def execute_background_strategy_job(
    job_store: StrategyJobStore,
    *,
    job_id: str,
    settings: Settings,
    worker_id: str = "api_background",
    lock_seconds: int = 1_800,
) -> None:
    job = job_store.mark_running(
        job_id,
        worker_id=worker_id,
        lock_seconds=lock_seconds,
    )
    if job is None:
        return
    execute_claimed_strategy_job(job_store, job, settings=settings)


def execute_claimed_strategy_job(
    job_store: StrategyJobStore,
    job: StrategyJobDetailResponse,
    *,
    settings: Settings,
) -> bool:
    run_context = create_run_context(
        run_id=job.run_id,
        strategy_id=job.strategy_id,
        trace_id=job.trace_id,
        settings=settings,
    )
    try:
        growth_response = generate_growth_strategy(
            job.request.brief,
            settings=settings,
            run_context=run_context,
        )
    except StrategyGenerationError as exc:
        job_store.mark_failed(job.job_id, error=_job_error_from_strategy_error(exc))
        return False
    except Exception as exc:
        job_store.mark_failed(job.job_id, error=_job_error_from_exception(exc))
        return False

    job_store.mark_completed(job.job_id, growth_response)
    return True


def _job_error_from_strategy_error(exc: StrategyGenerationError) -> dict:
    error = exc.tool_result.error
    return {
        "message": str(exc),
        "error_code": error.code if error else "STRATEGY_GENERATION_FAILED",
        "tool_name": exc.tool_result.tool_name,
        "run_metadata": (
            exc.run_metadata.model_dump(mode="json") if exc.run_metadata else None
        ),
    }


def _job_error_from_exception(exc: Exception) -> dict:
    return {
        "message": "Strategy job execution failed with an unexpected error.",
        "error_code": "STRATEGY_JOB_EXECUTION_FAILED",
        "exception_type": type(exc).__name__,
        "detail": str(exc),
    }
