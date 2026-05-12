import re
from typing import Annotated

from fastapi import Depends, FastAPI, Header, HTTPException, Response
from pydantic import BaseModel, ValidationError

from ads_growth_agent import __version__
from ads_growth_agent.config import Settings, get_settings
from ads_growth_agent.contracts import (
    AdvertiserBrief,
    AgentRunDetailResponse,
    CampaignPerformanceEventDetailResponse,
    CampaignPerformanceEventRequest,
    CampaignPerformanceEventResponse,
    GrowthStrategyRequest,
    GrowthStrategyResponse,
)
from ads_growth_agent.feedback import analyze_campaign_performance_event
from ads_growth_agent.graph import strategy_id_for_brief
from ads_growth_agent.idempotency_store_factory import build_configured_idempotency_store
from ads_growth_agent.logging_config import configure_logging
from ads_growth_agent.observability import RunContext, create_run_context
from ads_growth_agent.performance_event_store_factory import (
    build_configured_performance_event_store,
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
from ads_growth_agent.run_store_factory import build_configured_run_read_store
from ads_growth_agent.strategy import StrategyGenerationError, generate_growth_strategy


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


@app.get("/health", response_model=HealthResponse)
def health() -> HealthResponse:
    settings = get_runtime_settings()
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


@app.post("/campaign-events/performance", response_model=CampaignPerformanceEventResponse)
def ingest_campaign_performance_event(
    request: CampaignPerformanceEventRequest,
    response: Response,
    settings: Annotated[Settings, Depends(get_request_settings)],
    event_store: Annotated[
        CampaignPerformanceEventStore,
        Depends(get_runtime_performance_event_store),
    ],
) -> CampaignPerformanceEventResponse:
    response.headers["X-Tenant-ID"] = settings.tenant_id
    response.headers["Performance-Event-ID"] = request.event_id
    request_hash = hash_campaign_performance_event(request)
    existing_event = event_store.get_event(request.event_id)
    if existing_event is not None:
        if existing_event.metadata.get("event_hash") != request_hash:
            _raise_performance_event_conflict(request.event_id)
        response.headers["Feedback-ID"] = existing_event.analysis.feedback_id
        response.headers["Performance-Event-Status"] = "replayed"
        return CampaignPerformanceEventResponse(
            event_id=existing_event.event_id,
            advertiser_id=existing_event.advertiser_id,
            run_id=existing_event.run_id,
            status="analyzed",
            persisted=True,
            analysis=existing_event.analysis,
        )

    analysis = analyze_campaign_performance_event(request)
    try:
        event_store.record_analyzed(request, analysis)
    except PerformanceEventConflictError as exc:
        _raise_performance_event_conflict(exc.event_id)
    response.headers["Feedback-ID"] = analysis.feedback_id
    response.headers["Performance-Event-Status"] = "created"
    return CampaignPerformanceEventResponse(
        event_id=request.event_id,
        advertiser_id=request.advertiser_id,
        run_id=request.run_id,
        status="analyzed",
        persisted=settings.performance_event_persistence_backend != "none",
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
