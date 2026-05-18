from uuid import uuid4

from ads_growth_agent.config import Settings
from ads_growth_agent.contracts import GrowthStrategyRequest, StrategyJobAcceptedResponse
from ads_growth_agent.graph import strategy_id_for_brief
from ads_growth_agent.observability import create_run_context
from ads_growth_agent.persistence.strategy_job_store import StrategyJobStore


def enqueue_strategy_job(
    request: GrowthStrategyRequest,
    *,
    settings: Settings,
    job_store: StrategyJobStore,
) -> StrategyJobAcceptedResponse:
    """Queue a strategy-generation job and return its pollable envelope."""
    job_id = f"job_{uuid4().hex[:16]}"
    strategy_id = strategy_id_for_brief(request.brief)
    run_context = create_run_context(strategy_id=strategy_id, settings=settings)
    job = job_store.create_queued(
        request,
        job_id=job_id,
        strategy_id=strategy_id,
        run_id=run_context.run_id,
        trace_id=run_context.trace_id,
        max_attempts=settings.strategy_job_max_attempts,
    )
    return StrategyJobAcceptedResponse(
        job_id=job.job_id,
        status=job.status,
        strategy_id=job.strategy_id,
        advertiser_id=job.advertiser_id,
        objective=job.objective,
        run_id=job.run_id,
        trace_id=job.trace_id,
        polling_url=f"/growth-strategies/jobs/{job.job_id}",
        created_at=job.created_at,
    )
