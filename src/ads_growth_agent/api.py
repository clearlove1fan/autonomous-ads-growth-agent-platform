import re
from typing import Annotated

from fastapi import Depends, FastAPI, Header, HTTPException, Response
from pydantic import BaseModel

from ads_growth_agent import __version__
from ads_growth_agent.config import Settings, get_settings
from ads_growth_agent.contracts import (
    AgentRunDetailResponse,
    GrowthStrategyRequest,
    GrowthStrategyResponse,
)
from ads_growth_agent.idempotency_store_factory import build_configured_idempotency_store
from ads_growth_agent.logging_config import configure_logging
from ads_growth_agent.persistence.idempotency_store import (
    IdempotencyConflictError,
    IdempotencyStore,
    hash_growth_strategy_request,
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
) -> GrowthStrategyResponse:
    try:
        return generate_growth_strategy(request.brief, settings=settings)
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


def _run_id_from_http_error(exc: HTTPException) -> str | None:
    if not isinstance(exc.detail, dict):
        return None
    run_metadata = exc.detail.get("run_metadata")
    if not isinstance(run_metadata, dict):
        return None
    run_id = run_metadata.get("run_id")
    return run_id if isinstance(run_id, str) else None
