import hashlib
import json
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from typing import Any, Literal, Protocol
from uuid import NAMESPACE_URL, uuid5

import sqlalchemy as sa
from pydantic import BaseModel, Field
from sqlalchemy.engine import Connection, Engine

from ads_growth_agent.persistence.partitioning import partition_bucket
from ads_growth_agent.persistence.run_store import DEFAULT_TENANT_ID
from ads_growth_agent.persistence.schema import outbox_events

OutboxEventStatus = Literal["pending", "processing", "completed", "failed"]


class OutboxConflictError(Exception):
    def __init__(self, idempotency_key: str) -> None:
        super().__init__(f"Outbox idempotency key was reused: {idempotency_key}")
        self.idempotency_key = idempotency_key


class OutboxEventRecord(BaseModel):
    outbox_event_id: str = Field(min_length=1, max_length=160)
    event_type: str = Field(min_length=1, max_length=160)
    aggregate_type: str = Field(min_length=1, max_length=120)
    aggregate_id: str = Field(min_length=1, max_length=160)
    idempotency_key: str = Field(min_length=1, max_length=240)
    status: OutboxEventStatus
    payload: dict[str, Any]
    result_json: dict[str, Any] | None = None
    error_json: dict[str, Any] | None = None
    attempt_count: int = Field(ge=0)
    max_attempts: int = Field(gt=0)
    metadata: dict[str, Any] = Field(default_factory=dict)
    next_attempt_at: datetime | None = None
    locked_by: str | None = Field(default=None, min_length=1, max_length=160)
    locked_until: datetime | None = None
    created_at: datetime
    updated_at: datetime
    completed_at: datetime | None = None


class OutboxStore(Protocol):
    def enqueue(
        self,
        *,
        event_type: str,
        aggregate_type: str,
        aggregate_id: str,
        idempotency_key: str,
        payload: dict[str, Any],
        metadata: dict[str, Any] | None = None,
        partition_key: str | None = None,
        partition_date: datetime | None = None,
        max_attempts: int = 3,
    ) -> OutboxEventRecord:
        """Persist a pending event or return the existing idempotent event."""

    def claim_pending(
        self,
        *,
        limit: int,
        worker_id: str,
        lock_seconds: int = 60,
    ) -> list[OutboxEventRecord]:
        """Claim pending work items for one worker."""

    def mark_completed(
        self,
        outbox_event_id: str,
        *,
        result: dict[str, Any] | None = None,
    ) -> OutboxEventRecord | None:
        """Mark a claimed event completed."""

    def mark_failed(
        self,
        outbox_event_id: str,
        *,
        error: dict[str, Any],
        retry_delay_seconds: int = 5,
    ) -> OutboxEventRecord | None:
        """Record a processing error and retry while attempts remain."""

    def get_event(self, outbox_event_id: str) -> OutboxEventRecord | None:
        """Fetch one tenant-scoped outbox event."""

    def list_events(
        self,
        *,
        status: OutboxEventStatus | None = None,
        event_type: str | None = None,
        aggregate_type: str | None = None,
        aggregate_id: str | None = None,
        limit: int = 50,
    ) -> list[OutboxEventRecord]:
        """List tenant-scoped outbox events for operator inspection."""

    def retry_failed(
        self,
        outbox_event_id: str,
        *,
        max_attempts: int | None = None,
        requested_by: str = "operator",
    ) -> OutboxEventRecord | None:
        """Requeue a terminal failed event for manual replay."""


class NoopOutboxStore:
    def enqueue(
        self,
        *,
        event_type: str,
        aggregate_type: str,
        aggregate_id: str,
        idempotency_key: str,
        payload: dict[str, Any],
        metadata: dict[str, Any] | None = None,
        partition_key: str | None = None,
        partition_date: datetime | None = None,
        max_attempts: int = 3,
    ) -> OutboxEventRecord:
        now = datetime.now(UTC)
        return OutboxEventRecord(
            outbox_event_id=outbox_event_id_for_idempotency_key(idempotency_key),
            event_type=event_type,
            aggregate_type=aggregate_type,
            aggregate_id=aggregate_id,
            idempotency_key=idempotency_key,
            status="failed",
            payload=payload,
            error_json={"message": "outbox backend is disabled"},
            attempt_count=0,
            max_attempts=max_attempts,
            metadata=metadata or {},
            next_attempt_at=None,
            created_at=now,
            updated_at=now,
        )

    def claim_pending(
        self,
        *,
        limit: int,
        worker_id: str,
        lock_seconds: int = 60,
    ) -> list[OutboxEventRecord]:
        return []

    def mark_completed(
        self,
        outbox_event_id: str,
        *,
        result: dict[str, Any] | None = None,
    ) -> OutboxEventRecord | None:
        return None

    def mark_failed(
        self,
        outbox_event_id: str,
        *,
        error: dict[str, Any],
        retry_delay_seconds: int = 5,
    ) -> OutboxEventRecord | None:
        return None

    def get_event(self, outbox_event_id: str) -> OutboxEventRecord | None:
        return None

    def list_events(
        self,
        *,
        status: OutboxEventStatus | None = None,
        event_type: str | None = None,
        aggregate_type: str | None = None,
        aggregate_id: str | None = None,
        limit: int = 50,
    ) -> list[OutboxEventRecord]:
        return []

    def retry_failed(
        self,
        outbox_event_id: str,
        *,
        max_attempts: int | None = None,
        requested_by: str = "operator",
    ) -> OutboxEventRecord | None:
        return None


class PostgresOutboxStore:
    def __init__(self, bind: Engine | Connection, *, tenant_id: str = DEFAULT_TENANT_ID) -> None:
        self._bind = bind
        self._tenant_id = tenant_id

    def enqueue(
        self,
        *,
        event_type: str,
        aggregate_type: str,
        aggregate_id: str,
        idempotency_key: str,
        payload: dict[str, Any],
        metadata: dict[str, Any] | None = None,
        partition_key: str | None = None,
        partition_date: datetime | None = None,
        max_attempts: int = 3,
    ) -> OutboxEventRecord:
        payload_hash = hash_outbox_payload(payload)
        event_metadata = {**(metadata or {}), "payload_hash": payload_hash}
        outbox_event_id = outbox_event_id_for_idempotency_key(idempotency_key)
        with _transaction(self._bind) as connection:
            existing = _fetch_by_idempotency_key_for_update(
                connection,
                idempotency_key,
                tenant_id=self._tenant_id,
            )
            if existing is not None:
                existing_metadata = dict(existing["metadata"] or {})
                if existing_metadata.get("payload_hash") != payload_hash:
                    raise OutboxConflictError(idempotency_key)
                return _row_to_record(existing)

            values = {
                "tenant_id": self._tenant_id,
                "outbox_event_id": outbox_event_id,
                "event_type": event_type,
                "aggregate_type": aggregate_type,
                "aggregate_id": aggregate_id,
                "idempotency_key": idempotency_key,
                "status": "pending",
                "payload": payload,
                "result_json": None,
                "error_json": None,
                "attempt_count": 0,
                "max_attempts": max_attempts,
                "next_attempt_at": datetime.now(UTC),
                "locked_by": None,
                "locked_until": None,
                "completed_at": None,
                "metadata": event_metadata,
                "partition_key": partition_key or aggregate_id,
                "partition_bucket": partition_bucket(partition_key or aggregate_id),
                "partition_date": (partition_date or datetime.now(UTC)).date(),
            }
            connection.execute(outbox_events.insert().values(values))
            row = _fetch_by_idempotency_key_for_update(
                connection,
                idempotency_key,
                tenant_id=self._tenant_id,
            )

        if row is None:
            raise RuntimeError(f"outbox event was not persisted: {outbox_event_id}")
        return _row_to_record(row)

    def claim_pending(
        self,
        *,
        limit: int,
        worker_id: str,
        lock_seconds: int = 60,
    ) -> list[OutboxEventRecord]:
        now = datetime.now(UTC)
        locked_until = now + timedelta(seconds=lock_seconds)
        with _transaction(self._bind) as connection:
            rows = connection.execute(
                sa.select(outbox_events)
                .where(outbox_events.c.tenant_id == self._tenant_id)
                .where(outbox_events.c.attempt_count < outbox_events.c.max_attempts)
                .where(
                    sa.or_(
                        sa.and_(
                            outbox_events.c.status == "pending",
                            sa.or_(
                                outbox_events.c.next_attempt_at.is_(None),
                                outbox_events.c.next_attempt_at <= now,
                            ),
                        ),
                        sa.and_(
                            outbox_events.c.status == "processing",
                            outbox_events.c.locked_until <= now,
                        ),
                    )
                )
                .order_by(outbox_events.c.created_at, outbox_events.c.outbox_event_id)
                .limit(limit)
                .with_for_update(skip_locked=True)
            ).mappings().all()
            claimed: list[OutboxEventRecord] = []
            for row in rows:
                connection.execute(
                    outbox_events.update()
                    .where(outbox_events.c.tenant_id == self._tenant_id)
                    .where(outbox_events.c.outbox_event_id == row["outbox_event_id"])
                    .values(
                        status="processing",
                        attempt_count=row["attempt_count"] + 1,
                        locked_by=worker_id,
                        locked_until=locked_until,
                        updated_at=sa.func.now(),
                    )
                )
                updated = _fetch_by_outbox_event_id(
                    connection,
                    row["outbox_event_id"],
                    tenant_id=self._tenant_id,
                )
                if updated is not None:
                    claimed.append(_row_to_record(updated))
        return claimed

    def mark_completed(
        self,
        outbox_event_id: str,
        *,
        result: dict[str, Any] | None = None,
    ) -> OutboxEventRecord | None:
        with _transaction(self._bind) as connection:
            connection.execute(
                outbox_events.update()
                .where(outbox_events.c.tenant_id == self._tenant_id)
                .where(outbox_events.c.outbox_event_id == outbox_event_id)
                .values(
                    status="completed",
                    result_json=result,
                    error_json=None,
                    locked_by=None,
                    locked_until=None,
                    completed_at=sa.func.now(),
                    updated_at=sa.func.now(),
                )
            )
            row = _fetch_by_outbox_event_id(
                connection,
                outbox_event_id,
                tenant_id=self._tenant_id,
            )
        return _row_to_record(row) if row is not None else None

    def mark_failed(
        self,
        outbox_event_id: str,
        *,
        error: dict[str, Any],
        retry_delay_seconds: int = 5,
    ) -> OutboxEventRecord | None:
        with _transaction(self._bind) as connection:
            row = _fetch_by_outbox_event_id_for_update(
                connection,
                outbox_event_id,
                tenant_id=self._tenant_id,
            )
            if row is None:
                return None
            has_attempts_remaining = row["attempt_count"] < row["max_attempts"]
            connection.execute(
                outbox_events.update()
                .where(outbox_events.c.tenant_id == self._tenant_id)
                .where(outbox_events.c.outbox_event_id == outbox_event_id)
                .values(
                    status="pending" if has_attempts_remaining else "failed",
                    error_json=error,
                    locked_by=None,
                    locked_until=None,
                    next_attempt_at=(
                        datetime.now(UTC) + timedelta(seconds=retry_delay_seconds)
                        if has_attempts_remaining
                        else None
                    ),
                    completed_at=sa.func.now() if not has_attempts_remaining else None,
                    updated_at=sa.func.now(),
                )
            )
            updated = _fetch_by_outbox_event_id(
                connection,
                outbox_event_id,
                tenant_id=self._tenant_id,
            )
        return _row_to_record(updated) if updated is not None else None

    def get_event(self, outbox_event_id: str) -> OutboxEventRecord | None:
        with _transaction(self._bind) as connection:
            row = _fetch_by_outbox_event_id(
                connection,
                outbox_event_id,
                tenant_id=self._tenant_id,
            )
        return _row_to_record(row) if row is not None else None

    def list_events(
        self,
        *,
        status: OutboxEventStatus | None = None,
        event_type: str | None = None,
        aggregate_type: str | None = None,
        aggregate_id: str | None = None,
        limit: int = 50,
    ) -> list[OutboxEventRecord]:
        statement = sa.select(outbox_events).where(outbox_events.c.tenant_id == self._tenant_id)
        if status is not None:
            statement = statement.where(outbox_events.c.status == status)
        if event_type is not None:
            statement = statement.where(outbox_events.c.event_type == event_type)
        if aggregate_type is not None:
            statement = statement.where(outbox_events.c.aggregate_type == aggregate_type)
        if aggregate_id is not None:
            statement = statement.where(outbox_events.c.aggregate_id == aggregate_id)

        statement = statement.order_by(
            outbox_events.c.created_at.desc(),
            outbox_events.c.outbox_event_id.desc(),
        ).limit(limit)
        with _transaction(self._bind) as connection:
            rows = connection.execute(statement).mappings().all()
        return [_row_to_record(row) for row in rows]

    def retry_failed(
        self,
        outbox_event_id: str,
        *,
        max_attempts: int | None = None,
        requested_by: str = "operator",
    ) -> OutboxEventRecord | None:
        now = datetime.now(UTC)
        with _transaction(self._bind) as connection:
            row = _fetch_by_outbox_event_id_for_update(
                connection,
                outbox_event_id,
                tenant_id=self._tenant_id,
            )
            if row is None or row["status"] != "failed":
                return None

            metadata = dict(row["metadata"] or {})
            previous_error = row["error_json"]
            if previous_error is not None:
                metadata["previous_error"] = dict(previous_error)
            metadata["manual_retry_count"] = int(metadata.get("manual_retry_count") or 0) + 1
            metadata["last_manual_retry_by"] = requested_by
            metadata["last_manual_retry_at"] = now.isoformat()

            connection.execute(
                outbox_events.update()
                .where(outbox_events.c.tenant_id == self._tenant_id)
                .where(outbox_events.c.outbox_event_id == outbox_event_id)
                .values(
                    status="pending",
                    error_json=None,
                    attempt_count=0,
                    max_attempts=max_attempts or row["max_attempts"],
                    next_attempt_at=now,
                    locked_by=None,
                    locked_until=None,
                    completed_at=None,
                    metadata=metadata,
                    updated_at=sa.func.now(),
                )
            )
            updated = _fetch_by_outbox_event_id(
                connection,
                outbox_event_id,
                tenant_id=self._tenant_id,
            )
        return _row_to_record(updated) if updated is not None else None


@contextmanager
def _transaction(bind: Engine | Connection) -> Iterator[Connection]:
    if isinstance(bind, Engine):
        with bind.begin() as connection:
            yield connection
    else:
        yield bind


def outbox_event_id_for_idempotency_key(idempotency_key: str) -> str:
    fingerprint = uuid5(NAMESPACE_URL, idempotency_key).hex[:20]
    return f"outbox_{fingerprint}"


def hash_outbox_payload(payload: dict[str, Any]) -> str:
    normalized = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


def _fetch_by_idempotency_key_for_update(
    connection: Connection,
    idempotency_key: str,
    *,
    tenant_id: str,
):
    return connection.execute(
        sa.select(outbox_events)
        .where(outbox_events.c.tenant_id == tenant_id)
        .where(outbox_events.c.idempotency_key == idempotency_key)
        .with_for_update()
    ).mappings().one_or_none()


def _fetch_by_outbox_event_id_for_update(
    connection: Connection,
    outbox_event_id: str,
    *,
    tenant_id: str,
):
    return connection.execute(
        sa.select(outbox_events)
        .where(outbox_events.c.tenant_id == tenant_id)
        .where(outbox_events.c.outbox_event_id == outbox_event_id)
        .with_for_update()
    ).mappings().one_or_none()


def _fetch_by_outbox_event_id(
    connection: Connection,
    outbox_event_id: str,
    *,
    tenant_id: str,
):
    return connection.execute(
        sa.select(outbox_events)
        .where(outbox_events.c.tenant_id == tenant_id)
        .where(outbox_events.c.outbox_event_id == outbox_event_id)
    ).mappings().one_or_none()


def _row_to_record(row) -> OutboxEventRecord:
    return OutboxEventRecord(
        outbox_event_id=row["outbox_event_id"],
        event_type=row["event_type"],
        aggregate_type=row["aggregate_type"],
        aggregate_id=row["aggregate_id"],
        idempotency_key=row["idempotency_key"],
        status=row["status"],
        payload=dict(row["payload"] or {}),
        result_json=dict(row["result_json"]) if row["result_json"] is not None else None,
        error_json=dict(row["error_json"]) if row["error_json"] is not None else None,
        attempt_count=row["attempt_count"],
        max_attempts=row["max_attempts"],
        metadata=dict(row["metadata"] or {}),
        next_attempt_at=row["next_attempt_at"],
        locked_by=row["locked_by"],
        locked_until=row["locked_until"],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
        completed_at=row["completed_at"],
    )
