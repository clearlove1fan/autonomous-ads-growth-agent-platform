from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
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
        max_attempts: int = 3,
    ) -> StrategyJobDetailResponse:
        """Persist a queued strategy-generation job."""

    def mark_running(
        self,
        job_id: str,
        *,
        worker_id: str | None = None,
        lock_seconds: int = 1_800,
    ) -> StrategyJobDetailResponse | None:
        """Mark a queued job as running."""

    def claim_queued(
        self,
        *,
        limit: int,
        worker_id: str,
        lock_seconds: int = 1_800,
    ) -> list[StrategyJobDetailResponse]:
        """Claim queued or stale running jobs for one worker."""

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

    def mark_attempt_failed(
        self,
        job_id: str,
        *,
        error: dict[str, Any],
        retry_delay_seconds: int,
    ) -> StrategyJobDetailResponse | None:
        """Record one failed attempt and retry while attempts remain."""

    def get_job(self, job_id: str) -> StrategyJobDetailResponse | None:
        """Return one strategy job for the configured tenant."""

    def retry_failed(
        self,
        job_id: str,
        *,
        max_attempts: int,
        requested_by: str,
    ) -> StrategyJobDetailResponse | None:
        """Requeue a terminal failed job with a fresh attempt budget."""

    def cancel(
        self,
        job_id: str,
        *,
        requested_by: str,
        reason: str | None,
    ) -> StrategyJobDetailResponse | None:
        """Mark a queued or running strategy job as cancelled."""

    def list_jobs(
        self,
        *,
        status: StrategyJobStatus | None = None,
        advertiser_id: str | None = None,
        limit: int = 50,
    ) -> list[StrategyJobDetailResponse]:
        """Return recent strategy jobs for the configured tenant."""


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
        max_attempts: int = 3,
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
            max_attempts=max_attempts,
            next_attempt_at=now,
            created_at=now,
            updated_at=now,
        )
        with self._lock:
            self._jobs[job_id] = job
        return job

    def mark_running(
        self,
        job_id: str,
        *,
        worker_id: str | None = None,
        lock_seconds: int = 1_800,
    ) -> StrategyJobDetailResponse | None:
        now = datetime.now(UTC)
        with self._lock:
            job = self._jobs.get(job_id)
            if job is None or job.status != StrategyJobStatus.QUEUED:
                return None
            updated = job.model_copy(
                update={
                    "status": StrategyJobStatus.RUNNING,
                    "attempt_count": job.attempt_count + 1,
                    "next_attempt_at": None,
                    "locked_by": worker_id or "memory_background",
                    "locked_until": now + timedelta(seconds=lock_seconds),
                    "updated_at": now,
                }
            )
            self._jobs[job_id] = updated
            return updated

    def claim_queued(
        self,
        *,
        limit: int,
        worker_id: str,
        lock_seconds: int = 1_800,
    ) -> list[StrategyJobDetailResponse]:
        now = datetime.now(UTC)
        claimed: list[StrategyJobDetailResponse] = []
        with self._lock:
            candidates = sorted(
                (
                    job
                    for job in self._jobs.values()
                    if job.attempt_count < job.max_attempts
                    and (
                        (
                            job.status == StrategyJobStatus.QUEUED
                            and (
                                job.next_attempt_at is None
                                or job.next_attempt_at <= now
                            )
                        )
                        or (
                            job.status == StrategyJobStatus.RUNNING
                            and job.locked_until is not None
                            and job.locked_until <= now
                        )
                    )
                ),
                key=lambda job: (job.created_at, job.job_id),
            )
            for job in candidates[:limit]:
                updated = job.model_copy(
                    update={
                        "status": StrategyJobStatus.RUNNING,
                        "attempt_count": job.attempt_count + 1,
                        "next_attempt_at": None,
                        "locked_by": worker_id,
                        "locked_until": now + timedelta(seconds=lock_seconds),
                        "updated_at": now,
                    }
                )
                self._jobs[job.job_id] = updated
                claimed.append(updated)
        return claimed

    def mark_completed(
        self,
        job_id: str,
        response: GrowthStrategyResponse,
    ) -> StrategyJobDetailResponse | None:
        return self._update(
            job_id,
            status=StrategyJobStatus.COMPLETED,
            result=response,
            next_attempt_at=None,
            locked_by=None,
            locked_until=None,
            completed_at=datetime.now(UTC),
            expected_status=StrategyJobStatus.RUNNING,
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
            next_attempt_at=None,
            locked_by=None,
            locked_until=None,
            completed_at=datetime.now(UTC),
            expected_status=StrategyJobStatus.RUNNING,
        )

    def mark_attempt_failed(
        self,
        job_id: str,
        *,
        error: dict[str, Any],
        retry_delay_seconds: int,
    ) -> StrategyJobDetailResponse | None:
        now = datetime.now(UTC)
        with self._lock:
            job = self._jobs.get(job_id)
            if job is None or job.status != StrategyJobStatus.RUNNING:
                return None
            has_attempts_remaining = job.attempt_count < job.max_attempts
            updated = job.model_copy(
                update={
                    "status": (
                        StrategyJobStatus.QUEUED
                        if has_attempts_remaining
                        else StrategyJobStatus.FAILED
                    ),
                    "result": None,
                    "error": error,
                    "next_attempt_at": (
                        now + timedelta(seconds=retry_delay_seconds)
                        if has_attempts_remaining
                        else None
                    ),
                    "locked_by": None,
                    "locked_until": None,
                    "updated_at": now,
                    "completed_at": None if has_attempts_remaining else now,
                }
            )
            self._jobs[job_id] = updated
            return updated

    def get_job(self, job_id: str) -> StrategyJobDetailResponse | None:
        with self._lock:
            return self._jobs.get(job_id)

    def retry_failed(
        self,
        job_id: str,
        *,
        max_attempts: int,
        requested_by: str,
    ) -> StrategyJobDetailResponse | None:
        now = datetime.now(UTC)
        with self._lock:
            job = self._jobs.get(job_id)
            if job is None or job.status != StrategyJobStatus.FAILED:
                return None
            metadata = _manual_retry_metadata(
                job.metadata,
                previous_error=job.error,
                requested_by=requested_by,
                requested_at=now,
            )
            updated = job.model_copy(
                update={
                    "status": StrategyJobStatus.QUEUED,
                    "result": None,
                    "error": None,
                    "metadata": metadata,
                    "attempt_count": 0,
                    "max_attempts": max_attempts,
                    "next_attempt_at": now,
                    "locked_by": None,
                    "locked_until": None,
                    "completed_at": None,
                    "updated_at": now,
                }
            )
            self._jobs[job_id] = updated
            return updated

    def cancel(
        self,
        job_id: str,
        *,
        requested_by: str,
        reason: str | None,
    ) -> StrategyJobDetailResponse | None:
        now = datetime.now(UTC)
        with self._lock:
            job = self._jobs.get(job_id)
            if job is None or job.status not in {
                StrategyJobStatus.QUEUED,
                StrategyJobStatus.RUNNING,
            }:
                return None
            metadata = _manual_cancel_metadata(
                job.metadata,
                previous_error=job.error,
                requested_by=requested_by,
                requested_at=now,
                reason=reason,
                from_status=job.status,
            )
            updated = job.model_copy(
                update={
                    "status": StrategyJobStatus.CANCELLED,
                    "result": None,
                    "error": _manual_cancel_error(
                        requested_by=requested_by,
                        reason=reason,
                        from_status=job.status,
                    ),
                    "metadata": metadata,
                    "next_attempt_at": None,
                    "locked_by": None,
                    "locked_until": None,
                    "completed_at": now,
                    "updated_at": now,
                }
            )
            self._jobs[job_id] = updated
            return updated

    def list_jobs(
        self,
        *,
        status: StrategyJobStatus | None = None,
        advertiser_id: str | None = None,
        limit: int = 50,
    ) -> list[StrategyJobDetailResponse]:
        with self._lock:
            jobs = [
                job
                for job in self._jobs.values()
                if (status is None or job.status == status)
                and (advertiser_id is None or job.advertiser_id == advertiser_id)
            ]
        return sorted(jobs, key=lambda job: (job.updated_at, job.job_id), reverse=True)[
            :limit
        ]

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
        next_attempt_at: datetime | None = None,
        locked_by: str | None = None,
        locked_until: datetime | None = None,
        completed_at: datetime | None = None,
        expected_status: StrategyJobStatus | None = None,
    ) -> StrategyJobDetailResponse | None:
        with self._lock:
            job = self._jobs.get(job_id)
            if job is None:
                return None
            if expected_status is not None and job.status != expected_status:
                return None
            updated = job.model_copy(
                update={
                    "status": status,
                    "result": result if result is not None else job.result,
                    "error": error if error is not None else job.error,
                    "next_attempt_at": next_attempt_at,
                    "locked_by": locked_by,
                    "locked_until": locked_until,
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
        max_attempts: int = 3,
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
                max_attempts=max_attempts,
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
                        "attempt_count": values["attempt_count"],
                        "max_attempts": values["max_attempts"],
                        "next_attempt_at": values["next_attempt_at"],
                        "locked_by": values["locked_by"],
                        "locked_until": values["locked_until"],
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

    def mark_running(
        self,
        job_id: str,
        *,
        worker_id: str | None = None,
        lock_seconds: int = 1_800,
    ) -> StrategyJobDetailResponse | None:
        locked_until = datetime.now(UTC) + timedelta(seconds=lock_seconds)
        with _transaction(self._bind) as connection:
            row = _fetch_job_row_for_update(connection, job_id, tenant_id=self._tenant_id)
            if row is None or row["status"] != StrategyJobStatus.QUEUED.value:
                return None
            connection.execute(
                strategy_jobs.update()
                .where(strategy_jobs.c.tenant_id == self._tenant_id)
                .where(strategy_jobs.c.job_id == job_id)
                .values(
                    status=StrategyJobStatus.RUNNING.value,
                    response_json=None,
                    error_json=None,
                    attempt_count=row["attempt_count"] + 1,
                    next_attempt_at=None,
                    locked_by=worker_id or "api_background",
                    locked_until=locked_until,
                    updated_at=sa.func.now(),
                    completed_at=None,
                )
            )
            return _fetch_job(connection, job_id, tenant_id=self._tenant_id)

    def claim_queued(
        self,
        *,
        limit: int,
        worker_id: str,
        lock_seconds: int = 1_800,
    ) -> list[StrategyJobDetailResponse]:
        now = datetime.now(UTC)
        locked_until = now + timedelta(seconds=lock_seconds)
        with _transaction(self._bind) as connection:
            rows = connection.execute(
                sa.select(strategy_jobs)
                .where(strategy_jobs.c.tenant_id == self._tenant_id)
                .where(strategy_jobs.c.attempt_count < strategy_jobs.c.max_attempts)
                .where(
                    sa.or_(
                        sa.and_(
                            strategy_jobs.c.status == StrategyJobStatus.QUEUED.value,
                            sa.or_(
                                strategy_jobs.c.next_attempt_at.is_(None),
                                strategy_jobs.c.next_attempt_at <= now,
                            ),
                        ),
                        sa.and_(
                            strategy_jobs.c.status == StrategyJobStatus.RUNNING.value,
                            strategy_jobs.c.locked_until <= now,
                        ),
                    )
                )
                .order_by(strategy_jobs.c.created_at, strategy_jobs.c.job_id)
                .limit(limit)
                .with_for_update(skip_locked=True)
            ).mappings().all()
            claimed: list[StrategyJobDetailResponse] = []
            for row in rows:
                connection.execute(
                    strategy_jobs.update()
                    .where(strategy_jobs.c.tenant_id == self._tenant_id)
                    .where(strategy_jobs.c.job_id == row["job_id"])
                    .values(
                        status=StrategyJobStatus.RUNNING.value,
                        response_json=None,
                        error_json=None,
                        attempt_count=row["attempt_count"] + 1,
                        next_attempt_at=None,
                        locked_by=worker_id,
                        locked_until=locked_until,
                        updated_at=sa.func.now(),
                        completed_at=None,
                    )
                )
                updated = _fetch_job(connection, row["job_id"], tenant_id=self._tenant_id)
                if updated is not None:
                    claimed.append(updated)
        return claimed

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

    def mark_attempt_failed(
        self,
        job_id: str,
        *,
        error: dict[str, Any],
        retry_delay_seconds: int,
    ) -> StrategyJobDetailResponse | None:
        now = datetime.now(UTC)
        with _transaction(self._bind) as connection:
            row = _fetch_job_row_for_update(connection, job_id, tenant_id=self._tenant_id)
            if row is None or row["status"] != StrategyJobStatus.RUNNING.value:
                return None
            has_attempts_remaining = row["attempt_count"] < row["max_attempts"]
            connection.execute(
                strategy_jobs.update()
                .where(strategy_jobs.c.tenant_id == self._tenant_id)
                .where(strategy_jobs.c.job_id == job_id)
                .values(
                    status=(
                        StrategyJobStatus.QUEUED.value
                        if has_attempts_remaining
                        else StrategyJobStatus.FAILED.value
                    ),
                    response_json=None,
                    error_json=error,
                    next_attempt_at=(
                        now + timedelta(seconds=retry_delay_seconds)
                        if has_attempts_remaining
                        else None
                    ),
                    locked_by=None,
                    locked_until=None,
                    updated_at=sa.func.now(),
                    completed_at=sa.func.now() if not has_attempts_remaining else None,
                )
            )
            return _fetch_job(connection, job_id, tenant_id=self._tenant_id)

    def get_job(self, job_id: str) -> StrategyJobDetailResponse | None:
        with _connection(self._bind) as connection:
            return _fetch_job(connection, job_id, tenant_id=self._tenant_id)

    def retry_failed(
        self,
        job_id: str,
        *,
        max_attempts: int,
        requested_by: str,
    ) -> StrategyJobDetailResponse | None:
        now = datetime.now(UTC)
        with _transaction(self._bind) as connection:
            row = _fetch_job_row_for_update(connection, job_id, tenant_id=self._tenant_id)
            if row is None or row["status"] != StrategyJobStatus.FAILED.value:
                return None
            metadata = _manual_retry_metadata(
                dict(row["metadata"] or {}),
                previous_error=(
                    dict(row["error_json"]) if row["error_json"] is not None else None
                ),
                requested_by=requested_by,
                requested_at=now,
            )
            connection.execute(
                strategy_jobs.update()
                .where(strategy_jobs.c.tenant_id == self._tenant_id)
                .where(strategy_jobs.c.job_id == job_id)
                .values(
                    status=StrategyJobStatus.QUEUED.value,
                    response_json=None,
                    error_json=None,
                    metadata=metadata,
                    attempt_count=0,
                    max_attempts=max_attempts,
                    next_attempt_at=now,
                    locked_by=None,
                    locked_until=None,
                    completed_at=None,
                    updated_at=sa.func.now(),
                )
            )
            return _fetch_job(connection, job_id, tenant_id=self._tenant_id)

    def cancel(
        self,
        job_id: str,
        *,
        requested_by: str,
        reason: str | None,
    ) -> StrategyJobDetailResponse | None:
        now = datetime.now(UTC)
        with _transaction(self._bind) as connection:
            row = _fetch_job_row_for_update(connection, job_id, tenant_id=self._tenant_id)
            if row is None or row["status"] not in {
                StrategyJobStatus.QUEUED.value,
                StrategyJobStatus.RUNNING.value,
            }:
                return None
            from_status = StrategyJobStatus(row["status"])
            metadata = _manual_cancel_metadata(
                dict(row["metadata"] or {}),
                previous_error=(
                    dict(row["error_json"]) if row["error_json"] is not None else None
                ),
                requested_by=requested_by,
                requested_at=now,
                reason=reason,
                from_status=from_status,
            )
            connection.execute(
                strategy_jobs.update()
                .where(strategy_jobs.c.tenant_id == self._tenant_id)
                .where(strategy_jobs.c.job_id == job_id)
                .values(
                    status=StrategyJobStatus.CANCELLED.value,
                    response_json=None,
                    error_json=_manual_cancel_error(
                        requested_by=requested_by,
                        reason=reason,
                        from_status=from_status,
                    ),
                    metadata=metadata,
                    next_attempt_at=None,
                    locked_by=None,
                    locked_until=None,
                    completed_at=now,
                    updated_at=sa.func.now(),
                )
            )
            return _fetch_job(connection, job_id, tenant_id=self._tenant_id)

    def list_jobs(
        self,
        *,
        status: StrategyJobStatus | None = None,
        advertiser_id: str | None = None,
        limit: int = 50,
    ) -> list[StrategyJobDetailResponse]:
        with _connection(self._bind) as connection:
            stmt = sa.select(strategy_jobs).where(
                strategy_jobs.c.tenant_id == self._tenant_id
            )
            if status is not None:
                stmt = stmt.where(strategy_jobs.c.status == status.value)
            if advertiser_id is not None:
                stmt = stmt.where(strategy_jobs.c.advertiser_id == advertiser_id)
            rows = (
                connection.execute(
                    stmt.order_by(
                        strategy_jobs.c.updated_at.desc(),
                        strategy_jobs.c.job_id.desc(),
                    ).limit(limit)
                )
                .mappings()
                .all()
            )
        return [_row_to_job(row) for row in rows]

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
                .where(strategy_jobs.c.status == StrategyJobStatus.RUNNING.value)
                .values(
                    status=status.value,
                    response_json=response_json,
                    error_json=error_json,
                    next_attempt_at=None,
                    locked_by=None,
                    locked_until=None,
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
    max_attempts: int,
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
        "attempt_count": 0,
        "max_attempts": max_attempts,
        "next_attempt_at": datetime.now(UTC),
        "locked_by": None,
        "locked_until": None,
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
    return _row_to_job(row)


def _row_to_job(row) -> StrategyJobDetailResponse:
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
        attempt_count=row["attempt_count"],
        max_attempts=row["max_attempts"],
        next_attempt_at=row["next_attempt_at"],
        locked_by=row["locked_by"],
        locked_until=row["locked_until"],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
        completed_at=row["completed_at"],
    )


def _manual_retry_metadata(
    metadata: dict[str, Any],
    *,
    previous_error: dict[str, Any] | None,
    requested_by: str,
    requested_at: datetime,
) -> dict[str, Any]:
    retry_count = int(metadata.get("manual_retry_count") or 0) + 1
    return {
        **metadata,
        "manual_retry_count": retry_count,
        "last_manual_retry_by": requested_by,
        "last_manual_retry_at": requested_at.isoformat(),
        "previous_error": previous_error,
    }


def _manual_cancel_metadata(
    metadata: dict[str, Any],
    *,
    previous_error: dict[str, Any] | None,
    requested_by: str,
    requested_at: datetime,
    reason: str | None,
    from_status: StrategyJobStatus,
) -> dict[str, Any]:
    return {
        **metadata,
        "cancelled_by": requested_by,
        "cancelled_at": requested_at.isoformat(),
        "cancel_reason": reason,
        "cancelled_from_status": from_status.value,
        "cancelled_previous_error": previous_error,
    }


def _manual_cancel_error(
    *,
    requested_by: str,
    reason: str | None,
    from_status: StrategyJobStatus,
) -> dict[str, Any]:
    return {
        "message": "Strategy job was cancelled by an operator.",
        "error_code": "STRATEGY_JOB_CANCELLED",
        "cancelled_by": requested_by,
        "cancel_reason": reason,
        "cancelled_from_status": from_status.value,
    }


def _fetch_job_row_for_update(
    connection: Connection,
    job_id: str,
    *,
    tenant_id: str,
):
    return connection.execute(
        sa.select(strategy_jobs)
        .where(strategy_jobs.c.tenant_id == tenant_id)
        .where(strategy_jobs.c.job_id == job_id)
        .with_for_update()
    ).mappings().one_or_none()
