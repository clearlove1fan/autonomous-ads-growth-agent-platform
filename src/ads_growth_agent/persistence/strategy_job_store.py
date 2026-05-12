from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from threading import Lock
from typing import Any, Protocol

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.engine import Connection, Engine

from ads_growth_agent.contracts import (
    AdvertiserBrief,
    GrowthStrategyRequest,
    GrowthStrategyResponse,
    StrategyJobDetailResponse,
    StrategyJobStatus,
)
from ads_growth_agent.persistence.identity import upsert_tenant_and_advertiser
from ads_growth_agent.persistence.partitioning import partition_bucket
from ads_growth_agent.persistence.run_store import DEFAULT_TENANT_ID
from ads_growth_agent.persistence.schema import strategy_jobs


class StrategyJobStore(Protocol):
    def create_queued(
        self,
        request: GrowthStrategyRequest,
        *,
        job_id: str,
        strategy_id: str,
        run_id: str,
        trace_id: str,
    ) -> StrategyJobDetailResponse:
        """Persist a queued strategy-generation job."""

    def mark_running(self, job_id: str) -> StrategyJobDetailResponse | None:
        """Mark a queued job as running."""

    def mark_completed(
        self,
        job_id: str,
        response: GrowthStrategyResponse,
    ) -> StrategyJobDetailResponse | None:
        """Persist a completed job result."""

    def mark_failed(
        self,
        job_id: str,
        *,
        error: dict[str, Any],
    ) -> StrategyJobDetailResponse | None:
        """Persist a failed job result."""

    def get_job(self, job_id: str) -> StrategyJobDetailResponse | None:
        """Return one strategy job for the configured tenant."""


class InMemoryStrategyJobStore:
    def __init__(self) -> None:
        self._lock = Lock()
        self._jobs: dict[str, StrategyJobDetailResponse] = {}

    def create_queued(
        self,
        request: GrowthStrategyRequest,
        *,
        job_id: str,
        strategy_id: str,
        run_id: str,
        trace_id: str,
    ) -> StrategyJobDetailResponse:
        now = datetime.now(UTC)
        job = StrategyJobDetailResponse(
            job_id=job_id,
            status=StrategyJobStatus.QUEUED,
            strategy_id=strategy_id,
            advertiser_id=request.brief.advertiser_id,
            objective=request.brief.objective,
            run_id=run_id,
            trace_id=trace_id,
            request=request,
            metadata={"strategy_job_backend": "memory"},
            created_at=now,
            updated_at=now,
        )
        with self._lock:
            self._jobs[job_id] = job
        return job

    def mark_running(self, job_id: str) -> StrategyJobDetailResponse | None:
        return self._update(job_id, status=StrategyJobStatus.RUNNING)

    def mark_completed(
        self,
        job_id: str,
        response: GrowthStrategyResponse,
    ) -> StrategyJobDetailResponse | None:
        return self._update(
            job_id,
            status=StrategyJobStatus.COMPLETED,
            result=response,
            completed_at=datetime.now(UTC),
        )

    def mark_failed(
        self,
        job_id: str,
        *,
        error: dict[str, Any],
    ) -> StrategyJobDetailResponse | None:
        return self._update(
            job_id,
            status=StrategyJobStatus.FAILED,
            error=error,
            completed_at=datetime.now(UTC),
        )

    def get_job(self, job_id: str) -> StrategyJobDetailResponse | None:
        with self._lock:
            return self._jobs.get(job_id)

    def clear(self) -> None:
        with self._lock:
            self._jobs.clear()

    def _update(
        self,
        job_id: str,
        *,
        status: StrategyJobStatus,
        result: GrowthStrategyResponse | None = None,
        error: dict[str, Any] | None = None,
        completed_at: datetime | None = None,
    ) -> StrategyJobDetailResponse | None:
        with self._lock:
            job = self._jobs.get(job_id)
            if job is None:
                return None
            updated = job.model_copy(
                update={
                    "status": status,
                    "result": result if result is not None else job.result,
                    "error": error if error is not None else job.error,
                    "updated_at": datetime.now(UTC),
                    "completed_at": completed_at if completed_at is not None else job.completed_at,
                }
            )
            self._jobs[job_id] = updated
            return updated


class PostgresStrategyJobStore:
    def __init__(self, bind: Engine | Connection, *, tenant_id: str = DEFAULT_TENANT_ID) -> None:
        self._bind = bind
        self._tenant_id = tenant_id

    def create_queued(
        self,
        request: GrowthStrategyRequest,
        *,
        job_id: str,
        strategy_id: str,
        run_id: str,
        trace_id: str,
    ) -> StrategyJobDetailResponse:
        with _transaction(self._bind) as connection:
            upsert_tenant_and_advertiser(
                connection,
                request.brief,
                tenant_id=self._tenant_id,
                upserted_by="strategy_job_store",
            )
            values = _job_values(
                request.brief,
                request,
                tenant_id=self._tenant_id,
                job_id=job_id,
                strategy_id=strategy_id,
                run_id=run_id,
                trace_id=trace_id,
                status=StrategyJobStatus.QUEUED,
                response_json=None,
                error_json=None,
                completed_at=None,
            )
            stmt = (
                pg_insert(strategy_jobs)
                .values(values)
                .on_conflict_do_update(
                    index_elements=[strategy_jobs.c.tenant_id, strategy_jobs.c.job_id],
                    set_={
                        "status": values["status"],
                        "request_json": values["request_json"],
                        "response_json": values["response_json"],
                        "error_json": values["error_json"],
                        "run_id": values["run_id"],
                        "trace_id": values["trace_id"],
                        "metadata": values["metadata"],
                        "updated_at": sa.func.now(),
                        "completed_at": values["completed_at"],
                    },
                )
            )
            connection.execute(stmt)
            job = _fetch_job(connection, job_id, tenant_id=self._tenant_id)

        if job is None:
            raise RuntimeError(f"strategy job was not persisted: {job_id}")
        return job

    def mark_running(self, job_id: str) -> StrategyJobDetailResponse | None:
        return self._mark_terminal_or_running(
            job_id,
            status=StrategyJobStatus.RUNNING,
            response_json=None,
            error_json=None,
            completed_at=None,
        )

    def mark_completed(
        self,
        job_id: str,
        response: GrowthStrategyResponse,
    ) -> StrategyJobDetailResponse | None:
        return self._mark_terminal_or_running(
            job_id,
            status=StrategyJobStatus.COMPLETED,
            response_json=response.model_dump(mode="json"),
            error_json=None,
            completed_at=sa.func.now(),
        )

    def mark_failed(
        self,
        job_id: str,
        *,
        error: dict[str, Any],
    ) -> StrategyJobDetailResponse | None:
        return self._mark_terminal_or_running(
            job_id,
            status=StrategyJobStatus.FAILED,
            response_json=None,
            error_json=error,
            completed_at=sa.func.now(),
        )

    def get_job(self, job_id: str) -> StrategyJobDetailResponse | None:
        with _connection(self._bind) as connection:
            return _fetch_job(connection, job_id, tenant_id=self._tenant_id)

    def _mark_terminal_or_running(
        self,
        job_id: str,
        *,
        status: StrategyJobStatus,
        response_json: dict[str, Any] | None,
        error_json: dict[str, Any] | None,
        completed_at: Any,
    ) -> StrategyJobDetailResponse | None:
        with _transaction(self._bind) as connection:
            result = connection.execute(
                strategy_jobs.update()
                .where(strategy_jobs.c.tenant_id == self._tenant_id)
                .where(strategy_jobs.c.job_id == job_id)
                .values(
                    status=status.value,
                    response_json=response_json,
                    error_json=error_json,
                    updated_at=sa.func.now(),
                    completed_at=completed_at,
                )
            )
            if result.rowcount == 0:
                return None
            return _fetch_job(connection, job_id, tenant_id=self._tenant_id)


@contextmanager
def _transaction(bind: Engine | Connection) -> Iterator[Connection]:
    if isinstance(bind, Engine):
        with bind.begin() as connection:
            yield connection
    else:
        yield bind


@contextmanager
def _connection(bind: Engine | Connection) -> Iterator[Connection]:
    if isinstance(bind, Engine):
        with bind.connect() as connection:
            yield connection
    else:
        yield bind


def _job_values(
    brief: AdvertiserBrief,
    request: GrowthStrategyRequest,
    *,
    tenant_id: str,
    job_id: str,
    strategy_id: str,
    run_id: str,
    trace_id: str,
    status: StrategyJobStatus,
    response_json: dict[str, Any] | None,
    error_json: dict[str, Any] | None,
    completed_at: Any,
) -> dict[str, Any]:
    return {
        "tenant_id": tenant_id,
        "job_id": job_id,
        "strategy_id": strategy_id,
        "advertiser_id": brief.advertiser_id,
        "objective": brief.objective.value,
        "status": status.value,
        "run_id": run_id,
        "trace_id": trace_id,
        "request_json": request.model_dump(mode="json"),
        "response_json": response_json,
        "error_json": error_json,
        "metadata": {"strategy_job_backend": "postgres"},
        "partition_key": job_id,
        "partition_bucket": partition_bucket(job_id),
        "completed_at": completed_at,
    }


def _fetch_job(
    connection: Connection,
    job_id: str,
    *,
    tenant_id: str,
) -> StrategyJobDetailResponse | None:
    row = connection.execute(
        sa.select(strategy_jobs)
        .where(strategy_jobs.c.tenant_id == tenant_id)
        .where(strategy_jobs.c.job_id == job_id)
    ).mappings().one_or_none()
    if row is None:
        return None
    return StrategyJobDetailResponse(
        job_id=row["job_id"],
        status=row["status"],
        strategy_id=row["strategy_id"],
        advertiser_id=row["advertiser_id"],
        objective=row["objective"],
        run_id=row["run_id"],
        trace_id=row["trace_id"],
        request=GrowthStrategyRequest.model_validate(row["request_json"]),
        result=GrowthStrategyResponse.model_validate(row["response_json"])
        if row["response_json"] is not None
        else None,
        error=dict(row["error_json"]) if row["error_json"] else None,
        metadata=dict(row["metadata"] or {}),
        created_at=row["created_at"],
        updated_at=row["updated_at"],
        completed_at=row["completed_at"],
    )
