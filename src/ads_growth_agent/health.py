from time import perf_counter
from typing import Literal

import httpx
import sqlalchemy as sa
from pydantic import BaseModel, Field

from ads_growth_agent import __version__
from ads_growth_agent.config import Settings

DependencyStatus = Literal["ok", "failed", "skipped"]
ReadinessStatus = Literal["ok", "not_ready"]


class DependencyCheck(BaseModel):
    name: str = Field(min_length=1)
    status: DependencyStatus
    required: bool
    latency_ms: int | None = None
    detail: str | None = None


class ReadinessResponse(BaseModel):
    status: ReadinessStatus
    service: str
    version: str
    environment: str
    dependencies: list[DependencyCheck]


def check_readiness(settings: Settings) -> ReadinessResponse:
    dependencies = [
        _check_postgres(settings) if _requires_postgres(settings) else _skipped("postgres"),
        _check_litellm(settings) if _requires_litellm(settings) else _skipped("litellm"),
    ]
    status: ReadinessStatus = (
        "ok"
        if all(item.status == "ok" or not item.required for item in dependencies)
        else "not_ready"
    )
    return ReadinessResponse(
        status=status,
        service="ads-growth-agent",
        version=__version__,
        environment=settings.ads_growth_env,
        dependencies=dependencies,
    )


def _requires_postgres(settings: Settings) -> bool:
    return any(
        [
            settings.knowledge_store_backend == "postgres",
            settings.run_persistence_backend == "postgres",
            settings.campaign_draft_persistence_backend == "postgres",
            settings.performance_event_persistence_backend == "postgres",
            settings.advertiser_memory_persistence_backend == "postgres",
            settings.outbox_backend == "postgres",
            settings.memory_usage_tracking_backend == "outbox",
            settings.idempotency_backend == "postgres",
            settings.strategy_job_backend == "postgres",
            settings.graph_checkpointer_backend == "postgres",
        ]
    )


def _requires_litellm(settings: Settings) -> bool:
    return (
        settings.use_llm_brief_intake
        or settings.use_llm_planner
        or settings.use_llm_critic
    )


def _check_postgres(settings: Settings) -> DependencyCheck:
    started = perf_counter()
    try:
        engine = sa.create_engine(
            settings.database_url,
            pool_pre_ping=True,
            connect_args={"connect_timeout": settings.dependency_check_timeout_seconds},
        )
        try:
            with engine.connect() as connection:
                connection.execute(sa.text("select 1"))
        finally:
            engine.dispose()
    except Exception as exc:
        return DependencyCheck(
            name="postgres",
            status="failed",
            required=True,
            latency_ms=_elapsed_ms(started),
            detail=f"{type(exc).__name__}: {exc}",
        )

    return DependencyCheck(
        name="postgres",
        status="ok",
        required=True,
        latency_ms=_elapsed_ms(started),
    )


def _check_litellm(settings: Settings) -> DependencyCheck:
    started = perf_counter()
    url = _join_url(settings.litellm_base_url, settings.litellm_health_path)
    headers = {"Authorization": f"Bearer {settings.litellm_api_key}"}
    try:
        response = httpx.get(
            url,
            headers=headers,
            timeout=settings.dependency_check_timeout_seconds,
        )
        response.raise_for_status()
    except Exception as exc:
        return DependencyCheck(
            name="litellm",
            status="failed",
            required=True,
            latency_ms=_elapsed_ms(started),
            detail=f"{type(exc).__name__}: {exc}",
        )

    return DependencyCheck(
        name="litellm",
        status="ok",
        required=True,
        latency_ms=_elapsed_ms(started),
    )


def _skipped(name: str) -> DependencyCheck:
    return DependencyCheck(
        name=name,
        status="skipped",
        required=False,
        detail="not required by current configuration",
    )


def _join_url(base_url: str, path: str) -> str:
    return f"{base_url.rstrip('/')}/{path.lstrip('/')}"


def _elapsed_ms(started: float) -> int:
    return max(0, int((perf_counter() - started) * 1000))
