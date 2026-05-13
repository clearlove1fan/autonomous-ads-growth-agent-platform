import re
from collections.abc import Callable
from typing import Annotated
from uuid import uuid4

from fastapi import BackgroundTasks, Depends, FastAPI, Header, HTTPException, Response
from pydantic import BaseModel, ValidationError

from ads_growth_agent import __version__
from ads_growth_agent.advertiser_memory_store_factory import (
    build_configured_advertiser_memory_store,
)
from ads_growth_agent.config import Settings, get_settings
from ads_growth_agent.contracts import (
    AdvertiserBrief,
    AgentRunDetailResponse,
    CampaignPerformanceEventDetailResponse,
    CampaignPerformanceEventRequest,
    CampaignPerformanceEventResponse,
    GrowthStrategyRequest,
    GrowthStrategyResponse,
    StrategyJobAcceptedResponse,
    StrategyJobDetailResponse,
)
from ads_growth_agent.feedback import analyze_campaign_performance_event
from ads_growth_agent.graph import strategy_id_for_brief
from ads_growth_agent.health import ReadinessResponse, check_readiness
from ads_growth_agent.idempotency_store_factory import build_configured_idempotency_store
from ads_growth_agent.logging_config import configure_logging
from ads_growth_agent.observability import RunContext, create_run_context
from ads_growth_agent.performance_event_store_factory import (
    build_configured_performance_event_store,
)
from ads_growth_agent.persistence.advertiser_memory_store import (
    AdvertiserMemoryConflictError,
    AdvertiserMemoryStore,
    AdvertiserMemoryWriteResult,
)
from ads_growth_agent.persistence.idempotency_store import (
    IdempotencyConflictError,
    IdempotencyStore,
    hash_growth_strategy_request,
)
from ads_growth_agent.persistence.performance_event_store import (
    CampaignPerformanceEventStore,
    PerformanceEventConflictError,
    hash_campaign_performance_event,
)
from ads_growth_agent.persistence.run_read_store import AgentRunReadStore
from ads_growth_agent.persistence.strategy_job_store import StrategyJobStore
from ads_growth_agent.run_store_factory import build_configured_run_read_store
from ads_growth_agent.strategy import StrategyGenerationError, generate_growth_strategy
from ads_growth_agent.strategy_job_store_factory import build_configured_strategy_job_store


class HealthResponse(BaseModel):
    status: str
    service: str
    version: str
    environment: str


TENANT_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,127}$")

app = FastAPI(
    title="Autonomous Ads Growth Agent Platform",
    version=__version__,
    description="AI agent platform for advertiser growth automation.",
)
configure_logging()


def get_runtime_settings() -> Settings:
    return get_settings()


def get_runtime_readiness_checker() -> Callable[[Settings], ReadinessResponse]:
    return check_readiness


def get_request_settings(
    settings: Annotated[Settings, Depends(get_runtime_settings)],
    x_tenant_id: Annotated[str | None, Header(alias="X-Tenant-ID")] = None,
) -> Settings:
    if x_tenant_id is None:
        return settings

    tenant_id = x_tenant_id.strip()
    if not TENANT_ID_PATTERN.fullmatch(tenant_id):
        raise HTTPException(
            status_code=400,
            detail={
                "message": (
                    "X-Tenant-ID must be 1-128 characters and contain only "
                    "letters, numbers, underscores, or hyphens."
                ),
                "error_code": "INVALID_TENANT_ID",
            },
        )

    return settings.model_copy(update={"tenant_id": tenant_id})


def get_runtime_idempotency_store(
    settings: Annotated[Settings, Depends(get_request_settings)],
) -> IdempotencyStore:
    return build_configured_idempotency_store(settings)


def get_runtime_run_read_store(
    settings: Annotated[Settings, Depends(get_request_settings)],
) -> AgentRunReadStore:
    return build_configured_run_read_store(settings)


def get_runtime_performance_event_store(
    settings: Annotated[Settings, Depends(get_request_settings)],
) -> CampaignPerformanceEventStore:
    return build_configured_performance_event_store(settings)


def get_runtime_advertiser_memory_store(
    settings: Annotated[Settings, Depends(get_request_settings)],
) -> AdvertiserMemoryStore:
    return build_configured_advertiser_memory_store(settings)


def get_runtime_strategy_job_store(
    settings: Annotated[Settings, Depends(get_request_settings)],
) -> StrategyJobStore:
    return build_configured_strategy_job_store(settings)


@app.get("/health/live", response_model=HealthResponse)
def health_live(
    settings: Annotated[Settings, Depends(get_runtime_settings)],
) -> HealthResponse:
    return _health_response(settings)


@app.get("/health", response_model=HealthResponse)
def health(
    settings: Annotated[Settings, Depends(get_runtime_settings)],
) -> HealthResponse:
    return _health_response(settings)


@app.get("/health/ready", response_model=ReadinessResponse)
def health_ready(
    response: Response,
    settings: Annotated[Settings, Depends(get_runtime_settings)],
    readiness_checker: Annotated[
        Callable[[Settings], ReadinessResponse],
        Depends(get_runtime_readiness_checker),
    ],
) -> ReadinessResponse:
    readiness = readiness_checker(settings)
    if readiness.status != "ok":
        response.status_code = 503
    return readiness


def _health_response(settings: Settings) -> HealthResponse:
    return HealthResponse(
        status="ok",
        service="ads-growth-agent",
        version=__version__,
        environment=settings.ads_growth_env,
    )


@app.post("/growth-strategies", response_model=GrowthStrategyResponse)
def create_growth_strategy(
    request: GrowthStrategyRequest,
    response: Response,
    settings: Annotated[Settings, Depends(get_request_settings)],
    idempotency_store: Annotated[
        IdempotencyStore,
        Depends(get_runtime_idempotency_store),
    ],
    idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
) -> GrowthStrategyResponse:
    response.headers["X-Tenant-ID"] = settings.tenant_id

    if idempotency_key and settings.idempotency_backend != "none":
        return _create_growth_strategy_with_idempotency(
            request,
            response=response,
            settings=settings,
            idempotency_store=idempotency_store,
            idempotency_key=idempotency_key,
        )

    return _generate_growth_strategy_response(request, settings=settings)


@app.post(
    "/growth-strategies/jobs",
    response_model=StrategyJobAcceptedResponse,
    status_code=202,
)
def create_growth_strategy_job(
    request: GrowthStrategyRequest,
    response: Response,
    background_tasks: BackgroundTasks,
    settings: Annotated[Settings, Depends(get_request_settings)],
    job_store: Annotated[
        StrategyJobStore,
        Depends(get_runtime_strategy_job_store),
    ],
) -> StrategyJobAcceptedResponse:
    response.headers["X-Tenant-ID"] = settings.tenant_id
    job_id = f"job_{uuid4().hex[:16]}"
    strategy_id = strategy_id_for_brief(request.brief)
    run_context = create_run_context(strategy_id=strategy_id, settings=settings)
    job = job_store.create_queued(
        request,
        job_id=job_id,
        strategy_id=strategy_id,
        run_id=run_context.run_id,
        trace_id=run_context.trace_id,
    )
    polling_url = f"/growth-strategies/jobs/{job.job_id}"
    response.headers["Location"] = polling_url
    response.headers["Strategy-Job-ID"] = job.job_id
    response.headers["Run-ID"] = job.run_id
    background_tasks.add_task(
        _execute_growth_strategy_job,
        job.job_id,
        request,
        settings,
        run_context,
        job_store,
    )
    return StrategyJobAcceptedResponse(
        job_id=job.job_id,
        status=job.status,
        strategy_id=job.strategy_id,
        advertiser_id=job.advertiser_id,
        objective=job.objective,
        run_id=job.run_id,
        trace_id=job.trace_id,
        polling_url=polling_url,
        created_at=job.created_at,
    )


@app.get(
    "/growth-strategies/jobs/{job_id}",
    response_model=StrategyJobDetailResponse,
)
def get_growth_strategy_job(
    job_id: str,
    response: Response,
    settings: Annotated[Settings, Depends(get_request_settings)],
    job_store: Annotated[
        StrategyJobStore,
        Depends(get_runtime_strategy_job_store),
    ],
) -> StrategyJobDetailResponse:
    response.headers["X-Tenant-ID"] = settings.tenant_id
    job = job_store.get_job(job_id)
    if job is None:
        raise HTTPException(
            status_code=404,
            detail={
                "message": "Strategy job was not found for the effective tenant.",
                "error_code": "STRATEGY_JOB_NOT_FOUND",
                "job_id": job_id,
            },
        )
    return job


@app.post("/campaign-events/performance", response_model=CampaignPerformanceEventResponse)
def ingest_campaign_performance_event(
    request: CampaignPerformanceEventRequest,
    response: Response,
    settings: Annotated[Settings, Depends(get_request_settings)],
    event_store: Annotated[
        CampaignPerformanceEventStore,
        Depends(get_runtime_performance_event_store),
    ],
    memory_store: Annotated[
        AdvertiserMemoryStore,
        Depends(get_runtime_advertiser_memory_store),
    ],
) -> CampaignPerformanceEventResponse:
    response.headers["X-Tenant-ID"] = settings.tenant_id
    response.headers["Performance-Event-ID"] = request.event_id
    request_hash = hash_campaign_performance_event(request)
    existing_event = event_store.get_event(request.event_id)
    if existing_event is not None:
        if existing_event.metadata.get("event_hash") != request_hash:
            _raise_performance_event_conflict(request.event_id)
        try:
            memory_result = memory_store.record_feedback_memory(
                request,
                existing_event.analysis,
            )
        except AdvertiserMemoryConflictError as exc:
            _raise_performance_event_conflict(exc.event_id)
        response.headers["Feedback-ID"] = existing_event.analysis.feedback_id
        response.headers["Performance-Event-Status"] = "replayed"
        _set_advertiser_memory_headers(response, memory_result)
        return CampaignPerformanceEventResponse(
            event_id=existing_event.event_id,
            advertiser_id=existing_event.advertiser_id,
            run_id=existing_event.run_id,
            status="analyzed",
            persisted=True,
            advertiser_memory_persisted=memory_result.persisted,
            advertiser_memory_source_id=memory_result.source_id,
            analysis=existing_event.analysis,
        )

    analysis = analyze_campaign_performance_event(request)
    try:
        event_store.record_analyzed(request, analysis)
    except PerformanceEventConflictError as exc:
        _raise_performance_event_conflict(exc.event_id)
    try:
        memory_result = memory_store.record_feedback_memory(request, analysis)
    except AdvertiserMemoryConflictError as exc:
        _raise_performance_event_conflict(exc.event_id)
    response.headers["Feedback-ID"] = analysis.feedback_id
    response.headers["Performance-Event-Status"] = "created"
    _set_advertiser_memory_headers(response, memory_result)
    return CampaignPerformanceEventResponse(
        event_id=request.event_id,
        advertiser_id=request.advertiser_id,
        run_id=request.run_id,
        status="analyzed",
        persisted=settings.performance_event_persistence_backend != "none",
        advertiser_memory_persisted=memory_result.persisted,
        advertiser_memory_source_id=memory_result.source_id,
        analysis=analysis,
    )


@app.get(
    "/campaign-events/performance/{event_id}",
    response_model=CampaignPerformanceEventDetailResponse,
)
def get_campaign_performance_event(
    event_id: str,
    response: Response,
    settings: Annotated[Settings, Depends(get_request_settings)],
    event_store: Annotated[
        CampaignPerformanceEventStore,
        Depends(get_runtime_performance_event_store),
    ],
) -> CampaignPerformanceEventDetailResponse:
    response.headers["X-Tenant-ID"] = settings.tenant_id
    event = event_store.get_event(event_id)
    if event is None:
        raise HTTPException(
            status_code=404,
            detail={
                "message": "Campaign performance event was not found for the effective tenant.",
                "error_code": "PERFORMANCE_EVENT_NOT_FOUND",
                "event_id": event_id,
            },
        )
    return event


def _raise_performance_event_conflict(event_id: str) -> None:
    raise HTTPException(
        status_code=409,
        detail={
            "message": "Performance event ID was already used with a different payload.",
            "error_code": "PERFORMANCE_EVENT_ID_CONFLICT",
            "event_id": event_id,
        },
    )


def _set_advertiser_memory_headers(
    response: Response,
    result: AdvertiserMemoryWriteResult,
) -> None:
    response.headers["Advertiser-Memory-Status"] = (
        "recorded" if result.persisted else "disabled"
    )
    if result.source_id is not None:
        response.headers["Advertiser-Memory-Source-ID"] = result.source_id


@app.get("/runs/{run_id}", response_model=AgentRunDetailResponse)
def get_agent_run(
    run_id: str,
    response: Response,
    settings: Annotated[Settings, Depends(get_request_settings)],
    run_read_store: Annotated[
        AgentRunReadStore,
        Depends(get_runtime_run_read_store),
    ],
) -> AgentRunDetailResponse:
    response.headers["X-Tenant-ID"] = settings.tenant_id
    run = run_read_store.get_run(run_id)
    if run is None:
        raise HTTPException(
            status_code=404,
            detail={
                "message": "Run was not found for the effective tenant.",
                "error_code": "RUN_NOT_FOUND",
                "run_id": run_id,
            },
        )
    return run


@app.post("/runs/{run_id}/resume", response_model=GrowthStrategyResponse)
def resume_agent_run(
    run_id: str,
    response: Response,
    settings: Annotated[Settings, Depends(get_request_settings)],
    run_read_store: Annotated[
        AgentRunReadStore,
        Depends(get_runtime_run_read_store),
    ],
) -> GrowthStrategyResponse:
    response.headers["X-Tenant-ID"] = settings.tenant_id
    response.headers["Resumed-Run-ID"] = run_id
    response.headers["Resume-Mode"] = (
        "postgres-checkpoint"
        if settings.graph_checkpointer_backend == "postgres"
        else "same-run-replay"
    )
    original_run = run_read_store.get_run(run_id)
    if original_run is None:
        raise HTTPException(
            status_code=404,
            detail={
                "message": "Run was not found for the effective tenant.",
                "error_code": "RUN_NOT_FOUND",
                "run_id": run_id,
            },
        )
    if original_run.status == "completed":
        raise HTTPException(
            status_code=409,
            detail={
                "message": "Completed runs cannot be resumed.",
                "error_code": "RUN_NOT_RESUMABLE",
                "run_id": run_id,
                "status": original_run.status,
            },
        )

    brief = _brief_from_run_metadata(original_run)
    if strategy_id_for_brief(brief) != original_run.strategy_id:
        raise HTTPException(
            status_code=409,
            detail={
                "message": "Stored run brief does not match the original strategy identity.",
                "error_code": "RUN_BRIEF_MISMATCH",
                "run_id": run_id,
                "strategy_id": original_run.strategy_id,
            },
        )

    run_context = create_run_context(
        run_id=original_run.run_id,
        strategy_id=original_run.strategy_id,
        trace_id=original_run.trace_id,
        settings=settings,
    )
    return _generate_growth_strategy_response(
        GrowthStrategyRequest(brief=brief),
        settings=settings,
        run_context=run_context,
    )


@app.post("/runs/{run_id}/retry", response_model=GrowthStrategyResponse)
def retry_agent_run(
    run_id: str,
    request: GrowthStrategyRequest,
    response: Response,
    settings: Annotated[Settings, Depends(get_request_settings)],
    run_read_store: Annotated[
        AgentRunReadStore,
        Depends(get_runtime_run_read_store),
    ],
) -> GrowthStrategyResponse:
    response.headers["X-Tenant-ID"] = settings.tenant_id
    response.headers["Retried-Run-ID"] = run_id
    original_run = run_read_store.get_run(run_id)
    if original_run is None:
        raise HTTPException(
            status_code=404,
            detail={
                "message": "Run was not found for the effective tenant.",
                "error_code": "RUN_NOT_FOUND",
                "run_id": run_id,
            },
        )
    if original_run.status != "failed":
        raise HTTPException(
            status_code=409,
            detail={
                "message": "Only failed runs can be retried.",
                "error_code": "RUN_NOT_RETRYABLE",
                "run_id": run_id,
                "status": original_run.status,
            },
        )
    if (
        request.brief.advertiser_id != original_run.advertiser_id
        or request.brief.objective != original_run.objective
    ):
        raise HTTPException(
            status_code=409,
            detail={
                "message": "Retry brief must match the original run advertiser and objective.",
                "error_code": "RETRY_BRIEF_MISMATCH",
                "run_id": run_id,
                "advertiser_id": original_run.advertiser_id,
                "objective": original_run.objective.value,
            },
        )

    return _generate_growth_strategy_response(request, settings=settings)


def _create_growth_strategy_with_idempotency(
    request: GrowthStrategyRequest,
    *,
    response: Response,
    settings: Settings,
    idempotency_store: IdempotencyStore,
    idempotency_key: str,
) -> GrowthStrategyResponse:
    request_hash = hash_growth_strategy_request(request)
    try:
        start = idempotency_store.begin(
            idempotency_key,
            request_hash,
            ttl_seconds=settings.idempotency_ttl_seconds,
        )
    except IdempotencyConflictError as exc:
        raise HTTPException(
            status_code=409,
            detail={"message": exc.message, "error_code": exc.code},
        ) from exc

    if start.status == "replayed":
        response.headers["Idempotency-Status"] = "replayed"
        return GrowthStrategyResponse.model_validate(start.response_json)

    try:
        growth_response = _generate_growth_strategy_response(request, settings=settings)
    except HTTPException as exc:
        idempotency_store.mark_failed(
            idempotency_key,
            request_hash,
            run_id=(
                _run_id_from_http_error(exc)
                if settings.run_persistence_backend == "postgres"
                else None
            ),
            error_json={"detail": exc.detail},
            ttl_seconds=settings.idempotency_ttl_seconds,
        )
        raise

    idempotency_store.mark_completed(
        idempotency_key,
        request_hash,
        run_id=(
            growth_response.run_metadata.run_id
            if settings.run_persistence_backend == "postgres"
            else None
        ),
        response_json=growth_response.model_dump(mode="json"),
        ttl_seconds=settings.idempotency_ttl_seconds,
    )
    response.headers["Idempotency-Status"] = "created"
    return growth_response


def _generate_growth_strategy_response(
    request: GrowthStrategyRequest,
    *,
    settings: Settings,
    run_context: RunContext | None = None,
) -> GrowthStrategyResponse:
    try:
        if run_context is None:
            return generate_growth_strategy(request.brief, settings=settings)
        return generate_growth_strategy(
            request.brief,
            settings=settings,
            run_context=run_context,
        )
    except StrategyGenerationError as exc:
        error = exc.tool_result.error
        raise HTTPException(
            status_code=502,
            detail={
                "message": str(exc),
                "tool_name": exc.tool_result.tool_name,
                "error_code": error.code if error else "TOOL_FAILURE",
                "run_metadata": (
                    exc.run_metadata.model_dump(mode="json") if exc.run_metadata else None
                ),
            },
        ) from exc


def _execute_growth_strategy_job(
    job_id: str,
    request: GrowthStrategyRequest,
    settings: Settings,
    run_context: RunContext,
    job_store: StrategyJobStore,
) -> None:
    job_store.mark_running(job_id)
    try:
        growth_response = _generate_growth_strategy_response(
            request,
            settings=settings,
            run_context=run_context,
        )
    except HTTPException as exc:
        job_store.mark_failed(job_id, error=_job_error_from_http_exception(exc))
        return
    except Exception as exc:
        job_store.mark_failed(
            job_id,
            error={
                "message": "Strategy job execution failed with an unexpected error.",
                "error_code": "STRATEGY_JOB_EXECUTION_FAILED",
                "exception_type": type(exc).__name__,
                "detail": str(exc),
            },
        )
        return

    job_store.mark_completed(job_id, growth_response)


def _job_error_from_http_exception(exc: HTTPException) -> dict:
    return {
        "message": "Strategy job execution failed.",
        "error_code": "STRATEGY_JOB_EXECUTION_FAILED",
        "status_code": exc.status_code,
        "detail": exc.detail,
    }


def _brief_from_run_metadata(run: AgentRunDetailResponse) -> AdvertiserBrief:
    brief_json = run.metadata.get("advertiser_brief")
    if not isinstance(brief_json, dict):
        raise HTTPException(
            status_code=409,
            detail={
                "message": "Run does not contain a stored advertiser brief.",
                "error_code": "RUN_BRIEF_NOT_AVAILABLE",
                "run_id": run.run_id,
            },
        )
    try:
        return AdvertiserBrief.model_validate(brief_json)
    except ValidationError as exc:
        raise HTTPException(
            status_code=409,
            detail={
                "message": "Stored advertiser brief is no longer valid.",
                "error_code": "RUN_BRIEF_NOT_AVAILABLE",
                "run_id": run.run_id,
            },
        ) from exc


def _run_id_from_http_error(exc: HTTPException) -> str | None:
    if not isinstance(exc.detail, dict):
        return None
    run_metadata = exc.detail.get("run_metadata")
    if not isinstance(run_metadata, dict):
        return None
    run_id = run_metadata.get("run_id")
    return run_id if isinstance(run_id, str) else None
