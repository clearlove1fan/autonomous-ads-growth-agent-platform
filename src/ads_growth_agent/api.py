import re
import secrets
from collections.abc import Callable
from typing import Annotated, Literal

from fastapi import BackgroundTasks, Depends, FastAPI, Header, HTTPException, Query, Response
from pydantic import BaseModel, ValidationError

from ads_growth_agent import __version__
from ads_growth_agent.advertiser_memory_store_factory import (
    build_configured_advertiser_memory_store,
)
from ads_growth_agent.brief_intake import parse_advertiser_brief
from ads_growth_agent.campaign_draft_store_factory import build_configured_campaign_draft_store
from ads_growth_agent.config import Settings, get_settings
from ads_growth_agent.contracts import (
    AdvertiserBrief,
    AdvertiserBriefIntakeRequest,
    AdvertiserBriefIntakeResponse,
    AdvertiserMemoryDetailResponse,
    AdvertiserMemoryListResponse,
    AdvertiserMemoryType,
    AgentRunDetailResponse,
    CampaignDraftDetailResponse,
    CampaignDraftListResponse,
    CampaignFeedbackActionPlanResponse,
    CampaignFeedbackAnalysis,
    CampaignFeedbackExecutionDryRunListResponse,
    CampaignFeedbackExecutionDryRunResponse,
    CampaignFeedbackExecutionPlanResponse,
    CampaignFeedbackHandoffPackageResponse,
    CampaignFeedbackHandoffRecordListResponse,
    CampaignFeedbackHandoffRecordRequest,
    CampaignFeedbackHandoffRecordResponse,
    CampaignFeedbackLoopChainResponse,
    CampaignFeedbackLoopCommandCenterResponse,
    CampaignFeedbackLoopSummaryResponse,
    CampaignFeedbackLoopTimelineResponse,
    CampaignFeedbackOptimizationDraftResponse,
    CampaignFeedbackOptimizationReviewLineageListResponse,
    CampaignFeedbackOptimizationReviewLineageResponse,
    CampaignFeedbackOptimizationReviewListResponse,
    CampaignFeedbackOptimizationReviewRequest,
    CampaignFeedbackOptimizationReviewResponse,
    CampaignFeedbackOptimizationRevisionDraftResponse,
    CampaignFeedbackOutcomeReportResponse,
    CampaignPerformanceEventDetailResponse,
    CampaignPerformanceEventListResponse,
    CampaignPerformanceEventRequest,
    CampaignPerformanceEventResponse,
    FeedbackHandoffOutcome,
    FeedbackOptimizationReviewDecision,
    GrowthStrategyFromTextRequest,
    GrowthStrategyFromTextResponse,
    GrowthStrategyRequest,
    GrowthStrategyResponse,
    PerformanceEventType,
    StrategyJobAcceptedResponse,
    StrategyJobCancelRequest,
    StrategyJobDetailResponse,
    StrategyJobFromTextResponse,
    StrategyJobListResponse,
    StrategyJobStatus,
)
from ads_growth_agent.feedback import (
    FeedbackRevisionDraftNotRequestedError,
    analyze_campaign_performance_event,
    build_campaign_feedback_action_plan,
    build_campaign_feedback_optimization_draft,
    build_campaign_feedback_optimization_revision_draft,
    build_campaign_feedback_revision_reviewable_draft,
)
from ads_growth_agent.feedback_execution_dry_run import dry_run_feedback_execution_plan
from ads_growth_agent.feedback_execution_plan import (
    FeedbackExecutionPlanNotApprovedError,
    build_feedback_execution_plan,
)
from ads_growth_agent.feedback_execution_store_factory import (
    build_configured_feedback_execution_store,
)
from ads_growth_agent.feedback_handoff_package import build_feedback_handoff_package
from ads_growth_agent.feedback_handoff_record import (
    FeedbackHandoffRecordNotReadyError,
    FeedbackHandoffRecordStepMismatchError,
)
from ads_growth_agent.feedback_handoff_store_factory import (
    build_configured_feedback_handoff_store,
)
from ads_growth_agent.feedback_lineage import (
    build_feedback_optimization_review_lineage,
    list_feedback_optimization_review_lineages,
)
from ads_growth_agent.feedback_loop_chain import build_campaign_feedback_loop_chain
from ads_growth_agent.feedback_loop_command_center import (
    build_campaign_feedback_loop_command_center,
)
from ads_growth_agent.feedback_loop_summary import build_campaign_feedback_loop_summary
from ads_growth_agent.feedback_loop_timeline import build_campaign_feedback_loop_timeline
from ads_growth_agent.feedback_outcome_report import build_campaign_feedback_outcome_report
from ads_growth_agent.feedback_review_store_factory import build_configured_feedback_review_store
from ads_growth_agent.graph import strategy_id_for_brief
from ads_growth_agent.handoff_memory import schedule_or_record_handoff_memory
from ads_growth_agent.health import ReadinessResponse, check_readiness
from ads_growth_agent.idempotency_store_factory import build_configured_idempotency_store
from ads_growth_agent.logging_config import configure_logging
from ads_growth_agent.observability import RunContext, create_run_context
from ads_growth_agent.outbox import enqueue_advertiser_memory_write
from ads_growth_agent.outbox_store_factory import build_configured_outbox_store
from ads_growth_agent.performance_event_store_factory import (
    build_configured_performance_event_store,
)
from ads_growth_agent.persistence.advertiser_memory_store import (
    AdvertiserMemoryConflictError,
    AdvertiserMemoryStore,
    AdvertiserMemoryWriteResult,
)
from ads_growth_agent.persistence.campaign_draft_store import CampaignDraftStore
from ads_growth_agent.persistence.feedback_execution_store import (
    FeedbackExecutionDryRunStatus,
    FeedbackExecutionDryRunStore,
)
from ads_growth_agent.persistence.feedback_handoff_store import FeedbackHandoffRecordStore
from ads_growth_agent.persistence.feedback_review_store import FeedbackOptimizationReviewStore
from ads_growth_agent.persistence.idempotency_store import (
    IdempotencyConflictError,
    IdempotencyStore,
    hash_growth_strategy_request,
)
from ads_growth_agent.persistence.outbox_store import OutboxConflictError, OutboxStore
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
from ads_growth_agent.strategy_job_submission import enqueue_strategy_job
from ads_growth_agent.strategy_job_worker import execute_background_strategy_job


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


def require_api_auth(
    settings: Annotated[Settings, Depends(get_runtime_settings)],
    x_api_key: Annotated[str | None, Header(alias="X-API-Key")] = None,
    authorization: Annotated[str | None, Header()] = None,
) -> None:
    if settings.auth_mode == "none":
        return

    if settings.auth_mode == "api_key":
        expected_key = settings.ads_growth_api_key
        if not expected_key:
            raise HTTPException(
                status_code=503,
                detail={
                    "message": (
                        "API key auth is enabled but ADS_GROWTH_API_KEY is not configured."
                    ),
                    "error_code": "AUTH_NOT_CONFIGURED",
                },
            )

        presented_keys = _presented_api_keys(x_api_key=x_api_key, authorization=authorization)
        if not presented_keys:
            raise HTTPException(
                status_code=401,
                detail={
                    "message": "API authentication is required.",
                    "error_code": "AUTH_REQUIRED",
                },
                headers={"WWW-Authenticate": "Bearer"},
            )

        if not any(
            secrets.compare_digest(presented_key, expected_key)
            for presented_key in presented_keys
        ):
            raise HTTPException(
                status_code=403,
                detail={
                    "message": "API credentials are invalid.",
                    "error_code": "AUTH_FORBIDDEN",
                },
            )
        return

    raise HTTPException(
        status_code=503,
        detail={
            "message": "Configured auth mode is not supported.",
            "error_code": "AUTH_MODE_UNSUPPORTED",
        },
    )


def _presented_api_keys(
    *,
    x_api_key: str | None,
    authorization: str | None,
) -> list[str]:
    keys: list[str] = []
    if x_api_key:
        normalized = x_api_key.strip()
        if normalized:
            keys.append(normalized)
    if authorization:
        scheme, _, token = authorization.strip().partition(" ")
        if scheme.lower() == "bearer" and token.strip():
            keys.append(token.strip())
    return keys


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


def get_runtime_feedback_review_store(
    settings: Annotated[Settings, Depends(get_request_settings)],
) -> FeedbackOptimizationReviewStore:
    return build_configured_feedback_review_store(settings)


def get_runtime_feedback_execution_store(
    settings: Annotated[Settings, Depends(get_request_settings)],
) -> FeedbackExecutionDryRunStore:
    return build_configured_feedback_execution_store(settings)


def get_runtime_feedback_handoff_store(
    settings: Annotated[Settings, Depends(get_request_settings)],
) -> FeedbackHandoffRecordStore:
    return build_configured_feedback_handoff_store(settings)


def get_runtime_advertiser_memory_store(
    settings: Annotated[Settings, Depends(get_request_settings)],
) -> AdvertiserMemoryStore:
    return build_configured_advertiser_memory_store(settings)


def get_runtime_campaign_draft_store(
    settings: Annotated[Settings, Depends(get_request_settings)],
) -> CampaignDraftStore:
    return build_configured_campaign_draft_store(settings)


def get_runtime_outbox_store(
    settings: Annotated[Settings, Depends(get_request_settings)],
) -> OutboxStore:
    return build_configured_outbox_store(settings)


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


@app.post(
    "/growth-strategies",
    response_model=GrowthStrategyResponse,
    dependencies=[Depends(require_api_auth)],
)
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
    "/advertiser-briefs/parse",
    response_model=AdvertiserBriefIntakeResponse,
    dependencies=[Depends(require_api_auth)],
)
def parse_advertiser_brief_text(
    request: AdvertiserBriefIntakeRequest,
    response: Response,
    settings: Annotated[Settings, Depends(get_request_settings)],
) -> AdvertiserBriefIntakeResponse:
    response.headers["X-Tenant-ID"] = settings.tenant_id
    return parse_advertiser_brief(request, settings=settings)


@app.post(
    "/growth-strategies/from-text",
    response_model=GrowthStrategyFromTextResponse,
    dependencies=[Depends(require_api_auth)],
)
def create_growth_strategy_from_text(
    request: GrowthStrategyFromTextRequest,
    response: Response,
    settings: Annotated[Settings, Depends(get_request_settings)],
) -> GrowthStrategyFromTextResponse:
    response.headers["X-Tenant-ID"] = settings.tenant_id
    intake = parse_advertiser_brief(
        AdvertiserBriefIntakeRequest.model_validate(request.model_dump()),
        settings=settings,
    )
    growth_strategy = _generate_growth_strategy_response(
        GrowthStrategyRequest(brief=intake.brief),
        settings=settings,
    )
    response.headers["Run-ID"] = growth_strategy.run_metadata.run_id
    response.headers["Trace-ID"] = growth_strategy.run_metadata.trace_id
    return GrowthStrategyFromTextResponse(
        intake=intake,
        growth_strategy=growth_strategy,
    )


@app.post(
    "/growth-strategies/jobs",
    response_model=StrategyJobAcceptedResponse,
    status_code=202,
    dependencies=[Depends(require_api_auth)],
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
    return _create_strategy_job(
        request,
        response=response,
        background_tasks=background_tasks,
        settings=settings,
        job_store=job_store,
    )


@app.post(
    "/growth-strategies/jobs/from-text",
    response_model=StrategyJobFromTextResponse,
    status_code=202,
    dependencies=[Depends(require_api_auth)],
)
def create_growth_strategy_job_from_text(
    request: GrowthStrategyFromTextRequest,
    response: Response,
    background_tasks: BackgroundTasks,
    settings: Annotated[Settings, Depends(get_request_settings)],
    job_store: Annotated[
        StrategyJobStore,
        Depends(get_runtime_strategy_job_store),
    ],
) -> StrategyJobFromTextResponse:
    intake = parse_advertiser_brief(request, settings=settings)
    job = _create_strategy_job(
        GrowthStrategyRequest(brief=intake.brief),
        response=response,
        background_tasks=background_tasks,
        settings=settings,
        job_store=job_store,
    )
    return StrategyJobFromTextResponse(intake=intake, job=job)


def _create_strategy_job(
    request: GrowthStrategyRequest,
    *,
    response: Response,
    background_tasks: BackgroundTasks,
    settings: Settings,
    job_store: StrategyJobStore,
) -> StrategyJobAcceptedResponse:
    response.headers["X-Tenant-ID"] = settings.tenant_id
    job = enqueue_strategy_job(
        request,
        settings=settings,
        job_store=job_store,
    )
    response.headers["Location"] = job.polling_url
    response.headers["Strategy-Job-ID"] = job.job_id
    response.headers["Run-ID"] = job.run_id
    response.headers["Strategy-Job-Execution-Mode"] = settings.strategy_job_execution_mode
    if settings.strategy_job_execution_mode == "background":
        background_tasks.add_task(
            execute_background_strategy_job,
            job_store,
            job_id=job.job_id,
            settings=settings,
        )
    return job


@app.get(
    "/growth-strategies/jobs",
    response_model=StrategyJobListResponse,
    dependencies=[Depends(require_api_auth)],
)
def list_growth_strategy_jobs(
    response: Response,
    settings: Annotated[Settings, Depends(get_request_settings)],
    job_store: Annotated[
        StrategyJobStore,
        Depends(get_runtime_strategy_job_store),
    ],
    status: Annotated[StrategyJobStatus | None, Query()] = None,
    advertiser_id: Annotated[str | None, Query(min_length=1, max_length=128)] = None,
    run_id: Annotated[str | None, Query(min_length=1, max_length=128)] = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
) -> StrategyJobListResponse:
    response.headers["X-Tenant-ID"] = settings.tenant_id
    jobs = job_store.list_jobs(
        status=status,
        advertiser_id=advertiser_id,
        run_id=run_id,
        limit=limit,
    )
    return StrategyJobListResponse(
        items=jobs,
        count=len(jobs),
        limit=limit,
        status=status,
        advertiser_id=advertiser_id,
        run_id=run_id,
    )


@app.get(
    "/growth-strategies/jobs/{job_id}",
    response_model=StrategyJobDetailResponse,
    dependencies=[Depends(require_api_auth)],
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


@app.get(
    "/campaign-drafts",
    response_model=CampaignDraftListResponse,
    dependencies=[Depends(require_api_auth)],
)
def list_campaign_drafts(
    response: Response,
    settings: Annotated[Settings, Depends(get_request_settings)],
    draft_store: Annotated[
        CampaignDraftStore,
        Depends(get_runtime_campaign_draft_store),
    ],
    advertiser_id: Annotated[str | None, Query(min_length=1, max_length=128)] = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
) -> CampaignDraftListResponse:
    response.headers["X-Tenant-ID"] = settings.tenant_id
    drafts = draft_store.list_drafts(advertiser_id=advertiser_id, limit=limit)
    return CampaignDraftListResponse(
        items=drafts,
        count=len(drafts),
        limit=limit,
        advertiser_id=advertiser_id,
    )


@app.get(
    "/campaign-drafts/{draft_id}",
    response_model=CampaignDraftDetailResponse,
    dependencies=[Depends(require_api_auth)],
)
def get_campaign_draft(
    draft_id: str,
    response: Response,
    settings: Annotated[Settings, Depends(get_request_settings)],
    draft_store: Annotated[
        CampaignDraftStore,
        Depends(get_runtime_campaign_draft_store),
    ],
) -> CampaignDraftDetailResponse:
    response.headers["X-Tenant-ID"] = settings.tenant_id
    draft = draft_store.get_draft(draft_id)
    if draft is None:
        raise HTTPException(
            status_code=404,
            detail={
                "message": "Campaign draft was not found for the effective tenant.",
                "error_code": "CAMPAIGN_DRAFT_NOT_FOUND",
                "draft_id": draft_id,
            },
        )
    return draft


@app.get(
    "/advertisers/{advertiser_id}/memories",
    response_model=AdvertiserMemoryListResponse,
    dependencies=[Depends(require_api_auth)],
)
def list_advertiser_memories(
    advertiser_id: str,
    response: Response,
    settings: Annotated[Settings, Depends(get_request_settings)],
    memory_store: Annotated[
        AdvertiserMemoryStore,
        Depends(get_runtime_advertiser_memory_store),
    ],
    memory_type: Annotated[AdvertiserMemoryType | None, Query()] = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
) -> AdvertiserMemoryListResponse:
    response.headers["X-Tenant-ID"] = settings.tenant_id
    memories = memory_store.list_memories(
        advertiser_id=advertiser_id,
        memory_type=memory_type,
        limit=limit,
    )
    return AdvertiserMemoryListResponse(
        items=memories,
        count=len(memories),
        limit=limit,
        advertiser_id=advertiser_id,
        memory_type=memory_type,
    )


@app.get(
    "/advertisers/{advertiser_id}/memories/{source_id}",
    response_model=AdvertiserMemoryDetailResponse,
    dependencies=[Depends(require_api_auth)],
)
def get_advertiser_memory(
    advertiser_id: str,
    source_id: str,
    response: Response,
    settings: Annotated[Settings, Depends(get_request_settings)],
    memory_store: Annotated[
        AdvertiserMemoryStore,
        Depends(get_runtime_advertiser_memory_store),
    ],
) -> AdvertiserMemoryDetailResponse:
    response.headers["X-Tenant-ID"] = settings.tenant_id
    memory = memory_store.get_memory(
        advertiser_id=advertiser_id,
        source_id=source_id,
    )
    if memory is None:
        raise HTTPException(
            status_code=404,
            detail={
                "message": "Advertiser memory was not found for the effective tenant.",
                "error_code": "ADVERTISER_MEMORY_NOT_FOUND",
                "advertiser_id": advertiser_id,
                "source_id": source_id,
            },
        )
    return memory


@app.post(
    "/growth-strategies/jobs/{job_id}/retry",
    response_model=StrategyJobDetailResponse,
    dependencies=[Depends(require_api_auth)],
)
def retry_growth_strategy_job(
    job_id: str,
    response: Response,
    settings: Annotated[Settings, Depends(get_request_settings)],
    job_store: Annotated[
        StrategyJobStore,
        Depends(get_runtime_strategy_job_store),
    ],
    x_operator_id: Annotated[str | None, Header(alias="X-Operator-ID")] = None,
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
    if job.status != StrategyJobStatus.FAILED:
        raise HTTPException(
            status_code=409,
            detail={
                "message": "Only failed strategy jobs can be retried manually.",
                "error_code": "STRATEGY_JOB_NOT_RETRYABLE",
                "job_id": job_id,
                "status": job.status.value,
            },
        )
    retried = job_store.retry_failed(
        job_id,
        max_attempts=settings.strategy_job_max_attempts,
        requested_by=_operator_id_or_default(x_operator_id, default="api"),
    )
    if retried is None:
        raise HTTPException(
            status_code=409,
            detail={
                "message": "Strategy job could not be retried in its current state.",
                "error_code": "STRATEGY_JOB_NOT_RETRYABLE",
                "job_id": job_id,
            },
        )
    response.headers["Strategy-Job-ID"] = retried.job_id
    response.headers["Strategy-Job-Status"] = retried.status.value
    return retried


@app.post(
    "/growth-strategies/jobs/{job_id}/cancel",
    response_model=StrategyJobDetailResponse,
    dependencies=[Depends(require_api_auth)],
)
def cancel_growth_strategy_job(
    job_id: str,
    response: Response,
    settings: Annotated[Settings, Depends(get_request_settings)],
    job_store: Annotated[
        StrategyJobStore,
        Depends(get_runtime_strategy_job_store),
    ],
    cancel_request: StrategyJobCancelRequest | None = None,
    x_operator_id: Annotated[str | None, Header(alias="X-Operator-ID")] = None,
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
    if job.status not in {StrategyJobStatus.QUEUED, StrategyJobStatus.RUNNING}:
        raise HTTPException(
            status_code=409,
            detail={
                "message": "Only queued or running strategy jobs can be cancelled.",
                "error_code": "STRATEGY_JOB_NOT_CANCELLABLE",
                "job_id": job_id,
                "status": job.status.value,
            },
        )
    cancelled = job_store.cancel(
        job_id,
        requested_by=_operator_id_or_default(x_operator_id, default="api"),
        reason=cancel_request.reason if cancel_request else None,
    )
    if cancelled is None:
        raise HTTPException(
            status_code=409,
            detail={
                "message": "Strategy job could not be cancelled in its current state.",
                "error_code": "STRATEGY_JOB_NOT_CANCELLABLE",
                "job_id": job_id,
            },
        )
    response.headers["Strategy-Job-ID"] = cancelled.job_id
    response.headers["Strategy-Job-Status"] = cancelled.status.value
    return cancelled


def _operator_id_or_default(operator_id: str | None, *, default: str) -> str:
    if operator_id is None:
        return default
    normalized = operator_id.strip()
    return normalized or default


@app.post(
    "/campaign-events/performance",
    response_model=CampaignPerformanceEventResponse,
    dependencies=[Depends(require_api_auth)],
)
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
    outbox_store: Annotated[
        OutboxStore,
        Depends(get_runtime_outbox_store),
    ],
) -> CampaignPerformanceEventResponse:
    response.headers["X-Tenant-ID"] = settings.tenant_id
    response.headers["Performance-Event-ID"] = request.event_id
    request_hash = hash_campaign_performance_event(request)
    existing_event = event_store.get_event(request.event_id)
    if existing_event is not None:
        if existing_event.metadata.get("event_hash") != request_hash:
            _raise_performance_event_conflict(request.event_id)
        memory_result = _schedule_or_record_advertiser_memory(
            settings,
            request,
            existing_event.analysis,
            memory_store=memory_store,
            outbox_store=outbox_store,
        )
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
            advertiser_memory_queued=memory_result.queued,
            advertiser_memory_status=memory_result.status,
            advertiser_memory_source_id=memory_result.source_id,
            analysis=existing_event.analysis,
        )

    analysis = analyze_campaign_performance_event(request)
    try:
        event_store.record_analyzed(request, analysis)
    except PerformanceEventConflictError as exc:
        _raise_performance_event_conflict(exc.event_id)
    memory_result = _schedule_or_record_advertiser_memory(
        settings,
        request,
        analysis,
        memory_store=memory_store,
        outbox_store=outbox_store,
    )
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
        advertiser_memory_queued=memory_result.queued,
        advertiser_memory_status=memory_result.status,
        advertiser_memory_source_id=memory_result.source_id,
        analysis=analysis,
    )


@app.get(
    "/campaign-events/performance",
    response_model=CampaignPerformanceEventListResponse,
    dependencies=[Depends(require_api_auth)],
)
def list_campaign_performance_events(
    response: Response,
    settings: Annotated[Settings, Depends(get_request_settings)],
    event_store: Annotated[
        CampaignPerformanceEventStore,
        Depends(get_runtime_performance_event_store),
    ],
    advertiser_id: Annotated[str | None, Query(min_length=1, max_length=128)] = None,
    run_id: Annotated[str | None, Query(min_length=1, max_length=128)] = None,
    campaign_id: Annotated[str | None, Query(min_length=1, max_length=128)] = None,
    draft_id: Annotated[str | None, Query(min_length=1, max_length=128)] = None,
    event_type: Annotated[PerformanceEventType | None, Query()] = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
) -> CampaignPerformanceEventListResponse:
    response.headers["X-Tenant-ID"] = settings.tenant_id
    events = event_store.list_events(
        advertiser_id=advertiser_id,
        run_id=run_id,
        campaign_id=campaign_id,
        draft_id=draft_id,
        event_type=event_type,
        limit=limit,
    )
    return CampaignPerformanceEventListResponse(
        items=events,
        count=len(events),
        limit=limit,
        advertiser_id=advertiser_id,
        run_id=run_id,
        campaign_id=campaign_id,
        draft_id=draft_id,
        event_type=event_type,
    )


@app.get(
    "/campaign-events/performance/{event_id}",
    response_model=CampaignPerformanceEventDetailResponse,
    dependencies=[Depends(require_api_auth)],
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


@app.get(
    "/campaign-events/performance/{event_id}/action-plan",
    response_model=CampaignFeedbackActionPlanResponse,
    dependencies=[Depends(require_api_auth)],
)
def get_campaign_feedback_action_plan(
    event_id: str,
    response: Response,
    settings: Annotated[Settings, Depends(get_request_settings)],
    event_store: Annotated[
        CampaignPerformanceEventStore,
        Depends(get_runtime_performance_event_store),
    ],
) -> CampaignFeedbackActionPlanResponse:
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
    response.headers["Feedback-ID"] = event.analysis.feedback_id
    return build_campaign_feedback_action_plan(event)


@app.get(
    "/campaign-events/performance/{event_id}/optimization-draft",
    response_model=CampaignFeedbackOptimizationDraftResponse,
    dependencies=[Depends(require_api_auth)],
)
def get_campaign_feedback_optimization_draft(
    event_id: str,
    response: Response,
    settings: Annotated[Settings, Depends(get_request_settings)],
    event_store: Annotated[
        CampaignPerformanceEventStore,
        Depends(get_runtime_performance_event_store),
    ],
) -> CampaignFeedbackOptimizationDraftResponse:
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
    draft = build_campaign_feedback_optimization_draft(event)
    response.headers["Feedback-ID"] = event.analysis.feedback_id
    response.headers["Optimization-Draft-ID"] = draft.optimization_draft_id
    return draft


@app.get(
    "/campaign-events/performance/{event_id}/feedback-loop-summary",
    response_model=CampaignFeedbackLoopSummaryResponse,
    dependencies=[Depends(require_api_auth)],
)
def get_campaign_feedback_loop_summary(
    event_id: str,
    response: Response,
    settings: Annotated[Settings, Depends(get_request_settings)],
    event_store: Annotated[
        CampaignPerformanceEventStore,
        Depends(get_runtime_performance_event_store),
    ],
    review_store: Annotated[
        FeedbackOptimizationReviewStore,
        Depends(get_runtime_feedback_review_store),
    ],
    feedback_execution_store: Annotated[
        FeedbackExecutionDryRunStore,
        Depends(get_runtime_feedback_execution_store),
    ],
    handoff_store: Annotated[
        FeedbackHandoffRecordStore,
        Depends(get_runtime_feedback_handoff_store),
    ],
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
) -> CampaignFeedbackLoopSummaryResponse:
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
    summary = build_campaign_feedback_loop_summary(
        event,
        review_store,
        feedback_execution_store,
        handoff_store,
        review_persistence_enabled=settings.feedback_review_persistence_backend != "none",
        execution_persistence_enabled=(
            settings.feedback_execution_persistence_backend != "none"
        ),
        handoff_persistence_enabled=(
            settings.feedback_execution_persistence_backend != "none"
        ),
        limit=limit,
    )
    response.headers["Feedback-ID"] = event.analysis.feedback_id
    response.headers["Feedback-Loop-Stage"] = summary.current_stage
    response.headers["Feedback-Review-Count"] = str(summary.review_count)
    response.headers["Feedback-Dry-Run-Count"] = str(summary.dry_run_count)
    response.headers["Feedback-Handoff-Record-Count"] = str(summary.handoff_record_count)
    if summary.latest_handoff_outcome is not None:
        response.headers["Feedback-Handoff-Outcome"] = summary.latest_handoff_outcome.value
    return summary


@app.get(
    "/campaign-events/performance/{event_id}/feedback-loop-timeline",
    response_model=CampaignFeedbackLoopTimelineResponse,
    dependencies=[Depends(require_api_auth)],
)
def get_campaign_feedback_loop_timeline(
    event_id: str,
    response: Response,
    settings: Annotated[Settings, Depends(get_request_settings)],
    event_store: Annotated[
        CampaignPerformanceEventStore,
        Depends(get_runtime_performance_event_store),
    ],
    review_store: Annotated[
        FeedbackOptimizationReviewStore,
        Depends(get_runtime_feedback_review_store),
    ],
    feedback_execution_store: Annotated[
        FeedbackExecutionDryRunStore,
        Depends(get_runtime_feedback_execution_store),
    ],
    handoff_store: Annotated[
        FeedbackHandoffRecordStore,
        Depends(get_runtime_feedback_handoff_store),
    ],
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
) -> CampaignFeedbackLoopTimelineResponse:
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
    timeline = build_campaign_feedback_loop_timeline(
        event,
        review_store,
        feedback_execution_store,
        handoff_store,
        review_persistence_enabled=settings.feedback_review_persistence_backend != "none",
        execution_persistence_enabled=(
            settings.feedback_execution_persistence_backend != "none"
        ),
        handoff_persistence_enabled=(
            settings.feedback_execution_persistence_backend != "none"
        ),
        limit=limit,
    )
    response.headers["Feedback-ID"] = event.analysis.feedback_id
    response.headers["Feedback-Loop-Stage"] = timeline.current_stage
    response.headers["Feedback-Timeline-Entry-Count"] = str(timeline.entry_count)
    if timeline.latest_entry_stage is not None:
        response.headers["Feedback-Timeline-Latest-Stage"] = timeline.latest_entry_stage
    return timeline


@app.get(
    "/campaign-events/performance/{event_id}/feedback-loop-command-center",
    response_model=CampaignFeedbackLoopCommandCenterResponse,
    dependencies=[Depends(require_api_auth)],
)
def get_campaign_feedback_loop_command_center(
    event_id: str,
    response: Response,
    settings: Annotated[Settings, Depends(get_request_settings)],
    event_store: Annotated[
        CampaignPerformanceEventStore,
        Depends(get_runtime_performance_event_store),
    ],
    review_store: Annotated[
        FeedbackOptimizationReviewStore,
        Depends(get_runtime_feedback_review_store),
    ],
    feedback_execution_store: Annotated[
        FeedbackExecutionDryRunStore,
        Depends(get_runtime_feedback_execution_store),
    ],
    handoff_store: Annotated[
        FeedbackHandoffRecordStore,
        Depends(get_runtime_feedback_handoff_store),
    ],
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
) -> CampaignFeedbackLoopCommandCenterResponse:
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
    command_center = build_campaign_feedback_loop_command_center(
        event,
        review_store,
        feedback_execution_store,
        handoff_store,
        review_persistence_enabled=settings.feedback_review_persistence_backend != "none",
        execution_persistence_enabled=(
            settings.feedback_execution_persistence_backend != "none"
        ),
        handoff_persistence_enabled=(
            settings.feedback_execution_persistence_backend != "none"
        ),
        outcome_event_store=event_store,
        limit=limit,
    )
    response.headers["Feedback-ID"] = event.analysis.feedback_id
    response.headers["Feedback-Loop-Stage"] = command_center.current_stage
    response.headers["Feedback-Command-Count"] = str(command_center.command_count)
    if command_center.primary_command_id is not None:
        response.headers["Feedback-Primary-Command-ID"] = (
            command_center.primary_command_id
        )
    if command_center.outcome_status is not None:
        response.headers["Feedback-Outcome-Status"] = command_center.outcome_status
    if (
        command_center.outcome_report is not None
        and command_center.outcome_report.followup_event_id is not None
    ):
        response.headers["Feedback-Followup-Event-ID"] = (
            command_center.outcome_report.followup_event_id
        )
    return command_center


@app.get(
    "/campaign-events/performance/{event_id}/feedback-loop-chain",
    response_model=CampaignFeedbackLoopChainResponse,
    dependencies=[Depends(require_api_auth)],
)
def get_campaign_feedback_loop_chain(
    event_id: str,
    response: Response,
    settings: Annotated[Settings, Depends(get_request_settings)],
    event_store: Annotated[
        CampaignPerformanceEventStore,
        Depends(get_runtime_performance_event_store),
    ],
    review_store: Annotated[
        FeedbackOptimizationReviewStore,
        Depends(get_runtime_feedback_review_store),
    ],
    feedback_execution_store: Annotated[
        FeedbackExecutionDryRunStore,
        Depends(get_runtime_feedback_execution_store),
    ],
    handoff_store: Annotated[
        FeedbackHandoffRecordStore,
        Depends(get_runtime_feedback_handoff_store),
    ],
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
) -> CampaignFeedbackLoopChainResponse:
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
    chain = build_campaign_feedback_loop_chain(
        event,
        event_store,
        review_store,
        feedback_execution_store,
        handoff_store,
        review_persistence_enabled=settings.feedback_review_persistence_backend != "none",
        execution_persistence_enabled=(
            settings.feedback_execution_persistence_backend != "none"
        ),
        handoff_persistence_enabled=(
            settings.feedback_execution_persistence_backend != "none"
        ),
        limit=limit,
    )
    response.headers["Feedback-ID"] = event.analysis.feedback_id
    response.headers["Feedback-Loop-Stage"] = chain.baseline_current_stage
    response.headers["Feedback-Chain-Focus"] = chain.recommended_focus
    if chain.outcome_status is not None:
        response.headers["Feedback-Outcome-Status"] = chain.outcome_status
    if chain.followup_event_id is not None:
        response.headers["Feedback-Followup-Event-ID"] = chain.followup_event_id
    if chain.followup_current_stage is not None:
        response.headers["Feedback-Followup-Loop-Stage"] = (
            chain.followup_current_stage
        )
    return chain


@app.get(
    "/campaign-events/performance/{event_id}/feedback-outcome-report",
    response_model=CampaignFeedbackOutcomeReportResponse,
    dependencies=[Depends(require_api_auth)],
)
def get_campaign_feedback_outcome_report(
    event_id: str,
    response: Response,
    settings: Annotated[Settings, Depends(get_request_settings)],
    event_store: Annotated[
        CampaignPerformanceEventStore,
        Depends(get_runtime_performance_event_store),
    ],
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
) -> CampaignFeedbackOutcomeReportResponse:
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
    report = build_campaign_feedback_outcome_report(
        event,
        event_store,
        limit=limit,
    )
    response.headers["Feedback-ID"] = event.analysis.feedback_id
    response.headers["Feedback-Outcome-Status"] = report.outcome_status
    response.headers["Feedback-Outcome-Comparison-Event-Count"] = str(
        report.comparison_event_count
    )
    if report.followup_event_id is not None:
        response.headers["Feedback-Followup-Event-ID"] = report.followup_event_id
    return report


@app.post(
    "/campaign-events/performance/{event_id}/optimization-draft/reviews",
    response_model=CampaignFeedbackOptimizationReviewResponse,
    status_code=201,
    dependencies=[Depends(require_api_auth)],
)
def submit_campaign_feedback_optimization_review(
    event_id: str,
    request: CampaignFeedbackOptimizationReviewRequest,
    response: Response,
    settings: Annotated[Settings, Depends(get_request_settings)],
    event_store: Annotated[
        CampaignPerformanceEventStore,
        Depends(get_runtime_performance_event_store),
    ],
    review_store: Annotated[
        FeedbackOptimizationReviewStore,
        Depends(get_runtime_feedback_review_store),
    ],
) -> CampaignFeedbackOptimizationReviewResponse:
    _require_feedback_review_persistence_enabled(settings)
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

    optimization_draft = build_campaign_feedback_optimization_draft(event)
    try:
        review = review_store.record_review(optimization_draft, request)
    except ValueError as exc:
        raise HTTPException(
            status_code=422,
            detail={
                "message": str(exc),
                "error_code": "FEEDBACK_OPTIMIZATION_REVIEW_INVALID",
            },
        ) from exc

    response.headers["Feedback-Review-ID"] = review.review_id
    response.headers["Optimization-Draft-ID"] = review.optimization_draft_id
    response.headers["Feedback-ID"] = review.feedback_id
    return review


@app.get(
    "/feedback-optimization-reviews",
    response_model=CampaignFeedbackOptimizationReviewListResponse,
    dependencies=[Depends(require_api_auth)],
)
def list_feedback_optimization_reviews(
    response: Response,
    settings: Annotated[Settings, Depends(get_request_settings)],
    review_store: Annotated[
        FeedbackOptimizationReviewStore,
        Depends(get_runtime_feedback_review_store),
    ],
    event_id: Annotated[str | None, Query(min_length=1, max_length=128)] = None,
    advertiser_id: Annotated[str | None, Query(min_length=1, max_length=128)] = None,
    optimization_draft_id: Annotated[str | None, Query(min_length=1, max_length=160)] = None,
    decision: Annotated[FeedbackOptimizationReviewDecision | None, Query()] = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
) -> CampaignFeedbackOptimizationReviewListResponse:
    _require_feedback_review_persistence_enabled(settings)
    response.headers["X-Tenant-ID"] = settings.tenant_id
    return review_store.list_reviews(
        event_id=event_id,
        advertiser_id=advertiser_id,
        optimization_draft_id=optimization_draft_id,
        decision=decision,
        limit=limit,
    )


@app.get(
    "/feedback-optimization-review-lineages",
    response_model=CampaignFeedbackOptimizationReviewLineageListResponse,
    dependencies=[Depends(require_api_auth)],
)
def list_feedback_optimization_review_lineage_api(
    response: Response,
    settings: Annotated[Settings, Depends(get_request_settings)],
    review_store: Annotated[
        FeedbackOptimizationReviewStore,
        Depends(get_runtime_feedback_review_store),
    ],
    feedback_execution_store: Annotated[
        FeedbackExecutionDryRunStore,
        Depends(get_runtime_feedback_execution_store),
    ],
    event_id: Annotated[str | None, Query(min_length=1, max_length=128)] = None,
    advertiser_id: Annotated[str | None, Query(min_length=1, max_length=128)] = None,
    optimization_draft_id: Annotated[str | None, Query(min_length=1, max_length=160)] = None,
    decision: Annotated[FeedbackOptimizationReviewDecision | None, Query()] = None,
    lineage_stage: Annotated[
        Literal["approved", "rejected", "revision_requested", "revision_review"] | None,
        Query(),
    ] = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
) -> CampaignFeedbackOptimizationReviewLineageListResponse:
    _require_feedback_review_persistence_enabled(settings)
    response.headers["X-Tenant-ID"] = settings.tenant_id
    lineages = list_feedback_optimization_review_lineages(
        review_store,
        feedback_execution_store,
        event_id=event_id,
        advertiser_id=advertiser_id,
        optimization_draft_id=optimization_draft_id,
        decision=decision,
        lineage_stage=lineage_stage,
        limit=limit,
    )
    response.headers["Feedback-Lineage-Count"] = str(lineages.count)
    return lineages


@app.get(
    "/feedback-optimization-reviews/{review_id}",
    response_model=CampaignFeedbackOptimizationReviewResponse,
    dependencies=[Depends(require_api_auth)],
)
def get_feedback_optimization_review(
    review_id: str,
    response: Response,
    settings: Annotated[Settings, Depends(get_request_settings)],
    review_store: Annotated[
        FeedbackOptimizationReviewStore,
        Depends(get_runtime_feedback_review_store),
    ],
) -> CampaignFeedbackOptimizationReviewResponse:
    _require_feedback_review_persistence_enabled(settings)
    response.headers["X-Tenant-ID"] = settings.tenant_id
    review = review_store.get_review(review_id)
    if review is None:
        raise HTTPException(
            status_code=404,
            detail={
                "message": "Feedback optimization review was not found for the effective tenant.",
                "error_code": "FEEDBACK_OPTIMIZATION_REVIEW_NOT_FOUND",
                "review_id": review_id,
            },
        )
    response.headers["Feedback-Review-ID"] = review.review_id
    response.headers["Optimization-Draft-ID"] = review.optimization_draft_id
    response.headers["Feedback-ID"] = review.feedback_id
    return review


@app.get(
    "/feedback-optimization-reviews/{review_id}/lineage",
    response_model=CampaignFeedbackOptimizationReviewLineageResponse,
    dependencies=[Depends(require_api_auth)],
)
def get_feedback_optimization_review_lineage(
    review_id: str,
    response: Response,
    settings: Annotated[Settings, Depends(get_request_settings)],
    review_store: Annotated[
        FeedbackOptimizationReviewStore,
        Depends(get_runtime_feedback_review_store),
    ],
    feedback_execution_store: Annotated[
        FeedbackExecutionDryRunStore,
        Depends(get_runtime_feedback_execution_store),
    ],
) -> CampaignFeedbackOptimizationReviewLineageResponse:
    _require_feedback_review_persistence_enabled(settings)
    response.headers["X-Tenant-ID"] = settings.tenant_id
    review = review_store.get_review(review_id)
    if review is None:
        raise HTTPException(
            status_code=404,
            detail={
                "message": "Feedback optimization review was not found for the effective tenant.",
                "error_code": "FEEDBACK_OPTIMIZATION_REVIEW_NOT_FOUND",
                "review_id": review_id,
            },
        )

    try:
        lineage = build_feedback_optimization_review_lineage(
            review,
            review_store,
            feedback_execution_store,
        )
    except ValueError as exc:
        raise HTTPException(
            status_code=422,
            detail={
                "message": str(exc),
                "error_code": "FEEDBACK_OPTIMIZATION_REVIEW_LINEAGE_INVALID",
            },
        ) from exc

    response.headers["Feedback-Review-ID"] = review.review_id
    response.headers["Feedback-Lineage-Source-Review-ID"] = lineage.source_review_id
    response.headers["Feedback-Lineage-Stage"] = lineage.lineage_stage
    response.headers["Feedback-ID"] = review.feedback_id
    return lineage


@app.get(
    "/feedback-optimization-reviews/{review_id}/revision-draft",
    response_model=CampaignFeedbackOptimizationRevisionDraftResponse,
    dependencies=[Depends(require_api_auth)],
)
def get_feedback_optimization_revision_draft(
    review_id: str,
    response: Response,
    settings: Annotated[Settings, Depends(get_request_settings)],
    review_store: Annotated[
        FeedbackOptimizationReviewStore,
        Depends(get_runtime_feedback_review_store),
    ],
) -> CampaignFeedbackOptimizationRevisionDraftResponse:
    _require_feedback_review_persistence_enabled(settings)
    response.headers["X-Tenant-ID"] = settings.tenant_id
    review = review_store.get_review(review_id)
    if review is None:
        raise HTTPException(
            status_code=404,
            detail={
                "message": "Feedback optimization review was not found for the effective tenant.",
                "error_code": "FEEDBACK_OPTIMIZATION_REVIEW_NOT_FOUND",
                "review_id": review_id,
            },
        )
    try:
        revision_draft = build_campaign_feedback_optimization_revision_draft(review)
    except FeedbackRevisionDraftNotRequestedError as exc:
        raise HTTPException(
            status_code=409,
            detail={
                "message": str(exc),
                "error_code": "FEEDBACK_REVISION_DRAFT_NOT_REQUESTED",
                "review_id": exc.review_id,
                "decision": exc.decision.value,
            },
        ) from exc
    except ValueError as exc:
        raise HTTPException(
            status_code=422,
            detail={
                "message": str(exc),
                "error_code": "FEEDBACK_REVISION_DRAFT_INVALID",
            },
        ) from exc

    response.headers["Feedback-Review-ID"] = review.review_id
    response.headers["Feedback-Revision-Draft-ID"] = revision_draft.revision_draft_id
    response.headers["Optimization-Draft-ID"] = review.optimization_draft_id
    response.headers["Feedback-ID"] = review.feedback_id
    return revision_draft


@app.post(
    "/feedback-optimization-reviews/{review_id}/revision-draft/reviews",
    response_model=CampaignFeedbackOptimizationReviewResponse,
    status_code=201,
    dependencies=[Depends(require_api_auth)],
)
def submit_feedback_optimization_revision_review(
    review_id: str,
    request: CampaignFeedbackOptimizationReviewRequest,
    response: Response,
    settings: Annotated[Settings, Depends(get_request_settings)],
    review_store: Annotated[
        FeedbackOptimizationReviewStore,
        Depends(get_runtime_feedback_review_store),
    ],
) -> CampaignFeedbackOptimizationReviewResponse:
    _require_feedback_review_persistence_enabled(settings)
    response.headers["X-Tenant-ID"] = settings.tenant_id
    source_review = review_store.get_review(review_id)
    if source_review is None:
        raise HTTPException(
            status_code=404,
            detail={
                "message": "Feedback optimization review was not found for the effective tenant.",
                "error_code": "FEEDBACK_OPTIMIZATION_REVIEW_NOT_FOUND",
                "review_id": review_id,
            },
        )

    try:
        reviewable_draft = build_campaign_feedback_revision_reviewable_draft(source_review)
        review = review_store.record_review(reviewable_draft, request)
    except FeedbackRevisionDraftNotRequestedError as exc:
        raise HTTPException(
            status_code=409,
            detail={
                "message": str(exc),
                "error_code": "FEEDBACK_REVISION_DRAFT_NOT_REQUESTED",
                "review_id": exc.review_id,
                "decision": exc.decision.value,
            },
        ) from exc
    except ValueError as exc:
        raise HTTPException(
            status_code=422,
            detail={
                "message": str(exc),
                "error_code": "FEEDBACK_REVISION_REVIEW_INVALID",
            },
        ) from exc

    response.headers["Source-Feedback-Review-ID"] = source_review.review_id
    response.headers["Feedback-Review-ID"] = review.review_id
    response.headers["Feedback-Revision-Draft-ID"] = review.optimization_draft_id
    response.headers["Original-Optimization-Draft-ID"] = source_review.optimization_draft_id
    response.headers["Feedback-ID"] = review.feedback_id
    return review


@app.get(
    "/feedback-optimization-reviews/{review_id}/execution-plan",
    response_model=CampaignFeedbackExecutionPlanResponse,
    dependencies=[Depends(require_api_auth)],
)
def get_feedback_execution_plan(
    review_id: str,
    response: Response,
    settings: Annotated[Settings, Depends(get_request_settings)],
    review_store: Annotated[
        FeedbackOptimizationReviewStore,
        Depends(get_runtime_feedback_review_store),
    ],
) -> CampaignFeedbackExecutionPlanResponse:
    _require_feedback_review_persistence_enabled(settings)
    response.headers["X-Tenant-ID"] = settings.tenant_id
    review = review_store.get_review(review_id)
    if review is None:
        raise HTTPException(
            status_code=404,
            detail={
                "message": "Feedback optimization review was not found for the effective tenant.",
                "error_code": "FEEDBACK_OPTIMIZATION_REVIEW_NOT_FOUND",
                "review_id": review_id,
            },
        )
    try:
        execution_plan = build_feedback_execution_plan(review)
    except FeedbackExecutionPlanNotApprovedError as exc:
        raise HTTPException(
            status_code=409,
            detail={
                "message": str(exc),
                "error_code": "FEEDBACK_EXECUTION_PLAN_NOT_APPROVED",
                "review_id": exc.review_id,
                "decision": exc.decision.value,
            },
        ) from exc
    except ValueError as exc:
        raise HTTPException(
            status_code=422,
            detail={
                "message": str(exc),
                "error_code": "FEEDBACK_EXECUTION_PLAN_INVALID",
            },
        ) from exc

    response.headers["Feedback-Review-ID"] = review.review_id
    response.headers["Feedback-Execution-Plan-ID"] = execution_plan.execution_plan_id
    response.headers["Optimization-Draft-ID"] = review.optimization_draft_id
    response.headers["Feedback-ID"] = review.feedback_id
    return execution_plan


@app.get(
    "/feedback-optimization-reviews/{review_id}/handoff-package",
    response_model=CampaignFeedbackHandoffPackageResponse,
    dependencies=[Depends(require_api_auth)],
)
def get_feedback_handoff_package(
    review_id: str,
    response: Response,
    settings: Annotated[Settings, Depends(get_request_settings)],
    review_store: Annotated[
        FeedbackOptimizationReviewStore,
        Depends(get_runtime_feedback_review_store),
    ],
    feedback_execution_store: Annotated[
        FeedbackExecutionDryRunStore,
        Depends(get_runtime_feedback_execution_store),
    ],
) -> CampaignFeedbackHandoffPackageResponse:
    _require_feedback_review_persistence_enabled(settings)
    response.headers["X-Tenant-ID"] = settings.tenant_id
    review = review_store.get_review(review_id)
    if review is None:
        raise HTTPException(
            status_code=404,
            detail={
                "message": "Feedback optimization review was not found for the effective tenant.",
                "error_code": "FEEDBACK_OPTIMIZATION_REVIEW_NOT_FOUND",
                "review_id": review_id,
            },
        )
    try:
        package = build_feedback_handoff_package(review, feedback_execution_store)
    except FeedbackExecutionPlanNotApprovedError as exc:
        raise HTTPException(
            status_code=409,
            detail={
                "message": str(exc),
                "error_code": "FEEDBACK_HANDOFF_PACKAGE_NOT_APPROVED",
                "review_id": exc.review_id,
                "decision": exc.decision.value,
            },
        ) from exc
    except ValueError as exc:
        raise HTTPException(
            status_code=422,
            detail={
                "message": str(exc),
                "error_code": "FEEDBACK_HANDOFF_PACKAGE_INVALID",
            },
        ) from exc

    response.headers["Feedback-Review-ID"] = review.review_id
    response.headers["Feedback-Handoff-Package-ID"] = package.handoff_package_id
    response.headers["Feedback-Handoff-Status"] = package.status
    response.headers["Feedback-Execution-Plan-ID"] = package.execution_plan_id
    return package


@app.post(
    "/feedback-optimization-reviews/{review_id}/handoff-records",
    response_model=CampaignFeedbackHandoffRecordResponse,
    dependencies=[Depends(require_api_auth)],
)
def submit_feedback_handoff_record(
    review_id: str,
    request: CampaignFeedbackHandoffRecordRequest,
    response: Response,
    settings: Annotated[Settings, Depends(get_request_settings)],
    review_store: Annotated[
        FeedbackOptimizationReviewStore,
        Depends(get_runtime_feedback_review_store),
    ],
    feedback_execution_store: Annotated[
        FeedbackExecutionDryRunStore,
        Depends(get_runtime_feedback_execution_store),
    ],
    handoff_store: Annotated[
        FeedbackHandoffRecordStore,
        Depends(get_runtime_feedback_handoff_store),
    ],
    memory_store: Annotated[
        AdvertiserMemoryStore,
        Depends(get_runtime_advertiser_memory_store),
    ],
    outbox_store: Annotated[
        OutboxStore,
        Depends(get_runtime_outbox_store),
    ],
) -> CampaignFeedbackHandoffRecordResponse:
    _require_feedback_review_persistence_enabled(settings)
    _require_feedback_execution_persistence_enabled(settings)
    response.headers["X-Tenant-ID"] = settings.tenant_id
    review = review_store.get_review(review_id)
    if review is None:
        raise HTTPException(
            status_code=404,
            detail={
                "message": "Feedback optimization review was not found for the effective tenant.",
                "error_code": "FEEDBACK_OPTIMIZATION_REVIEW_NOT_FOUND",
                "review_id": review_id,
            },
        )
    try:
        handoff_package = build_feedback_handoff_package(review, feedback_execution_store)
        record = handoff_store.record_handoff(handoff_package, request)
    except FeedbackExecutionPlanNotApprovedError as exc:
        raise HTTPException(
            status_code=409,
            detail={
                "message": str(exc),
                "error_code": "FEEDBACK_HANDOFF_RECORD_NOT_APPROVED",
                "review_id": exc.review_id,
                "decision": exc.decision.value,
            },
        ) from exc
    except FeedbackHandoffRecordNotReadyError as exc:
        raise HTTPException(
            status_code=409,
            detail={
                "message": str(exc),
                "error_code": "FEEDBACK_HANDOFF_RECORD_NOT_READY",
                "handoff_package_id": exc.handoff_package_id,
                "package_status": exc.package_status,
            },
        ) from exc
    except (FeedbackHandoffRecordStepMismatchError, ValueError) as exc:
        raise HTTPException(
            status_code=422,
            detail={
                "message": str(exc),
                "error_code": "FEEDBACK_HANDOFF_RECORD_INVALID",
            },
        ) from exc

    try:
        memory_result = schedule_or_record_handoff_memory(
            settings,
            record,
            memory_store=memory_store,
            outbox_store=outbox_store,
        )
    except OutboxConflictError as exc:
        _raise_handoff_memory_conflict(exc.idempotency_key)
    except AdvertiserMemoryConflictError as exc:
        _raise_handoff_memory_conflict(exc.event_id)

    response.headers["Feedback-Review-ID"] = review.review_id
    response.headers["Feedback-Handoff-Package-ID"] = record.handoff_package_id
    response.headers["Feedback-Handoff-Record-ID"] = record.handoff_record_id
    response.headers["Feedback-Handoff-Outcome"] = record.outcome.value
    _set_advertiser_memory_headers(response, memory_result)
    return record


@app.get(
    "/feedback-handoff-records",
    response_model=CampaignFeedbackHandoffRecordListResponse,
    dependencies=[Depends(require_api_auth)],
)
def list_feedback_handoff_records(
    response: Response,
    settings: Annotated[Settings, Depends(get_request_settings)],
    handoff_store: Annotated[
        FeedbackHandoffRecordStore,
        Depends(get_runtime_feedback_handoff_store),
    ],
    review_id: Annotated[str | None, Query(min_length=1, max_length=160)] = None,
    handoff_package_id: Annotated[str | None, Query(min_length=1, max_length=160)] = None,
    event_id: Annotated[str | None, Query(min_length=1, max_length=128)] = None,
    advertiser_id: Annotated[str | None, Query(min_length=1, max_length=128)] = None,
    outcome: Annotated[FeedbackHandoffOutcome | None, Query()] = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
) -> CampaignFeedbackHandoffRecordListResponse:
    _require_feedback_execution_persistence_enabled(settings)
    response.headers["X-Tenant-ID"] = settings.tenant_id
    return handoff_store.list_handoff_records(
        review_id=review_id,
        handoff_package_id=handoff_package_id,
        event_id=event_id,
        advertiser_id=advertiser_id,
        outcome=outcome,
        limit=limit,
    )


@app.get(
    "/feedback-handoff-records/{handoff_record_id}",
    response_model=CampaignFeedbackHandoffRecordResponse,
    dependencies=[Depends(require_api_auth)],
)
def get_feedback_handoff_record(
    handoff_record_id: str,
    response: Response,
    settings: Annotated[Settings, Depends(get_request_settings)],
    handoff_store: Annotated[
        FeedbackHandoffRecordStore,
        Depends(get_runtime_feedback_handoff_store),
    ],
) -> CampaignFeedbackHandoffRecordResponse:
    _require_feedback_execution_persistence_enabled(settings)
    response.headers["X-Tenant-ID"] = settings.tenant_id
    record = handoff_store.get_handoff_record(handoff_record_id)
    if record is None:
        raise HTTPException(
            status_code=404,
            detail={
                "message": "Feedback handoff record was not found for the effective tenant.",
                "error_code": "FEEDBACK_HANDOFF_RECORD_NOT_FOUND",
                "handoff_record_id": handoff_record_id,
            },
        )
    response.headers["Feedback-Handoff-Record-ID"] = record.handoff_record_id
    response.headers["Feedback-Handoff-Package-ID"] = record.handoff_package_id
    response.headers["Feedback-Review-ID"] = record.review_id
    return record


@app.post(
    "/feedback-optimization-reviews/{review_id}/execution-plan/dry-run",
    response_model=CampaignFeedbackExecutionDryRunResponse,
    dependencies=[Depends(require_api_auth)],
)
def dry_run_feedback_execution_plan_api(
    review_id: str,
    response: Response,
    settings: Annotated[Settings, Depends(get_request_settings)],
    review_store: Annotated[
        FeedbackOptimizationReviewStore,
        Depends(get_runtime_feedback_review_store),
    ],
    feedback_execution_store: Annotated[
        FeedbackExecutionDryRunStore,
        Depends(get_runtime_feedback_execution_store),
    ],
) -> CampaignFeedbackExecutionDryRunResponse:
    _require_feedback_review_persistence_enabled(settings)
    response.headers["X-Tenant-ID"] = settings.tenant_id
    review = review_store.get_review(review_id)
    if review is None:
        raise HTTPException(
            status_code=404,
            detail={
                "message": "Feedback optimization review was not found for the effective tenant.",
                "error_code": "FEEDBACK_OPTIMIZATION_REVIEW_NOT_FOUND",
                "review_id": review_id,
            },
        )
    try:
        execution_plan = build_feedback_execution_plan(review)
    except FeedbackExecutionPlanNotApprovedError as exc:
        raise HTTPException(
            status_code=409,
            detail={
                "message": str(exc),
                "error_code": "FEEDBACK_EXECUTION_PLAN_NOT_APPROVED",
                "review_id": exc.review_id,
                "decision": exc.decision.value,
            },
        ) from exc
    except ValueError as exc:
        raise HTTPException(
            status_code=422,
            detail={
                "message": str(exc),
                "error_code": "FEEDBACK_EXECUTION_PLAN_INVALID",
            },
        ) from exc

    dry_run = dry_run_feedback_execution_plan(execution_plan)
    dry_run = feedback_execution_store.record_dry_run(execution_plan, dry_run)
    response.headers["Feedback-Review-ID"] = review.review_id
    response.headers["Feedback-Execution-Plan-ID"] = execution_plan.execution_plan_id
    response.headers["Feedback-Dry-Run-ID"] = dry_run.dry_run_id
    response.headers["Feedback-Dry-Run-Status"] = (
        "recorded"
        if settings.feedback_execution_persistence_backend != "none"
        else "not_recorded"
    )
    response.headers["Optimization-Draft-ID"] = review.optimization_draft_id
    response.headers["Feedback-ID"] = review.feedback_id
    return dry_run


@app.get(
    "/feedback-execution-dry-runs",
    response_model=CampaignFeedbackExecutionDryRunListResponse,
    dependencies=[Depends(require_api_auth)],
)
def list_feedback_execution_dry_runs(
    response: Response,
    settings: Annotated[Settings, Depends(get_request_settings)],
    feedback_execution_store: Annotated[
        FeedbackExecutionDryRunStore,
        Depends(get_runtime_feedback_execution_store),
    ],
    review_id: Annotated[str | None, Query(min_length=1, max_length=160)] = None,
    execution_plan_id: Annotated[str | None, Query(min_length=1, max_length=160)] = None,
    event_id: Annotated[str | None, Query(min_length=1, max_length=128)] = None,
    advertiser_id: Annotated[str | None, Query(min_length=1, max_length=128)] = None,
    status: Annotated[FeedbackExecutionDryRunStatus | None, Query()] = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
) -> CampaignFeedbackExecutionDryRunListResponse:
    _require_feedback_execution_persistence_enabled(settings)
    response.headers["X-Tenant-ID"] = settings.tenant_id
    return feedback_execution_store.list_dry_runs(
        review_id=review_id,
        execution_plan_id=execution_plan_id,
        event_id=event_id,
        advertiser_id=advertiser_id,
        status=status,
        limit=limit,
    )


@app.get(
    "/feedback-execution-dry-runs/{dry_run_id}",
    response_model=CampaignFeedbackExecutionDryRunResponse,
    dependencies=[Depends(require_api_auth)],
)
def get_feedback_execution_dry_run(
    dry_run_id: str,
    response: Response,
    settings: Annotated[Settings, Depends(get_request_settings)],
    feedback_execution_store: Annotated[
        FeedbackExecutionDryRunStore,
        Depends(get_runtime_feedback_execution_store),
    ],
) -> CampaignFeedbackExecutionDryRunResponse:
    _require_feedback_execution_persistence_enabled(settings)
    response.headers["X-Tenant-ID"] = settings.tenant_id
    dry_run = feedback_execution_store.get_dry_run(dry_run_id)
    if dry_run is None:
        raise HTTPException(
            status_code=404,
            detail={
                "message": "Feedback execution dry run was not found for the effective tenant.",
                "error_code": "FEEDBACK_EXECUTION_DRY_RUN_NOT_FOUND",
                "dry_run_id": dry_run_id,
            },
        )
    response.headers["Feedback-Dry-Run-ID"] = dry_run.dry_run_id
    response.headers["Feedback-Review-ID"] = dry_run.review_id
    response.headers["Feedback-Execution-Plan-ID"] = dry_run.execution_plan_id
    return dry_run


def _raise_performance_event_conflict(event_id: str) -> None:
    raise HTTPException(
        status_code=409,
        detail={
            "message": "Performance event ID was already used with a different payload.",
            "error_code": "PERFORMANCE_EVENT_ID_CONFLICT",
            "event_id": event_id,
        },
    )


def _raise_handoff_memory_conflict(identifier: str) -> None:
    raise HTTPException(
        status_code=409,
        detail={
            "message": "Feedback handoff memory source was already used with a different payload.",
            "error_code": "FEEDBACK_HANDOFF_MEMORY_CONFLICT",
            "identifier": identifier,
        },
    )


def _require_feedback_review_persistence_enabled(settings: Settings) -> None:
    if settings.feedback_review_persistence_backend != "none":
        return
    raise HTTPException(
        status_code=503,
        detail={
            "message": "Feedback optimization review persistence is disabled.",
            "error_code": "FEEDBACK_REVIEW_PERSISTENCE_DISABLED",
        },
    )


def _require_feedback_execution_persistence_enabled(settings: Settings) -> None:
    if settings.feedback_execution_persistence_backend != "none":
        return
    raise HTTPException(
        status_code=503,
        detail={
            "message": "Feedback execution dry-run persistence is disabled.",
            "error_code": "FEEDBACK_EXECUTION_PERSISTENCE_DISABLED",
        },
    )


def _schedule_or_record_advertiser_memory(
    settings: Settings,
    event: CampaignPerformanceEventRequest,
    analysis: CampaignFeedbackAnalysis,
    *,
    memory_store: AdvertiserMemoryStore,
    outbox_store: OutboxStore,
) -> AdvertiserMemoryWriteResult:
    if settings.advertiser_memory_persistence_backend == "none":
        return AdvertiserMemoryWriteResult(persisted=False, status="disabled")
    if settings.outbox_backend == "postgres":
        try:
            return enqueue_advertiser_memory_write(outbox_store, event, analysis)
        except OutboxConflictError:
            _raise_performance_event_conflict(event.event_id)
    try:
        return memory_store.record_feedback_memory(event, analysis)
    except AdvertiserMemoryConflictError as exc:
        _raise_performance_event_conflict(exc.event_id)


def _set_advertiser_memory_headers(
    response: Response,
    result: AdvertiserMemoryWriteResult,
) -> None:
    response.headers["Advertiser-Memory-Status"] = result.status
    if result.source_id is not None:
        response.headers["Advertiser-Memory-Source-ID"] = result.source_id


@app.get(
    "/runs/{run_id}",
    response_model=AgentRunDetailResponse,
    dependencies=[Depends(require_api_auth)],
)
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


@app.post(
    "/runs/{run_id}/resume",
    response_model=GrowthStrategyResponse,
    dependencies=[Depends(require_api_auth)],
)
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


@app.post(
    "/runs/{run_id}/retry",
    response_model=GrowthStrategyResponse,
    dependencies=[Depends(require_api_auth)],
)
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
