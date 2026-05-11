import json
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from hashlib import sha256
from typing import Any, Literal, Protocol

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.engine import Connection, Engine, RowMapping

from ads_growth_agent.contracts import GrowthStrategyRequest
from ads_growth_agent.persistence.partitioning import partition_bucket
from ads_growth_agent.persistence.schema import idempotency_keys

DEFAULT_TENANT_ID = "default"
IdempotencyStartStatus = Literal["started", "replayed"]


class IdempotencyConflictError(Exception):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


@dataclass(frozen=True)
class IdempotencyStart:
    status: IdempotencyStartStatus
    response_json: dict[str, Any] | None = None


class IdempotencyStore(Protocol):
    def begin(
        self,
        key: str,
        request_hash: str,
        *,
        ttl_seconds: int,
    ) -> IdempotencyStart:
        """Claim a key or return a replay result."""

    def mark_completed(
        self,
        key: str,
        request_hash: str,
        *,
        run_id: str | None,
        response_json: dict[str, Any],
        ttl_seconds: int,
    ) -> None:
        """Store the successful response for replay."""

    def mark_failed(
        self,
        key: str,
        request_hash: str,
        *,
        run_id: str | None,
        error_json: dict[str, Any],
        ttl_seconds: int,
    ) -> None:
        """Store a failed idempotency attempt."""


class NoopIdempotencyStore:
    def begin(
        self,
        key: str,
        request_hash: str,
        *,
        ttl_seconds: int,
    ) -> IdempotencyStart:
        return IdempotencyStart(status="started")

    def mark_completed(
        self,
        key: str,
        request_hash: str,
        *,
        run_id: str | None,
        response_json: dict[str, Any],
        ttl_seconds: int,
    ) -> None:
        return None

    def mark_failed(
        self,
        key: str,
        request_hash: str,
        *,
        run_id: str | None,
        error_json: dict[str, Any],
        ttl_seconds: int,
    ) -> None:
        return None


class PostgresIdempotencyStore:
    def __init__(self, bind: Engine | Connection, *, tenant_id: str = DEFAULT_TENANT_ID) -> None:
        self._bind = bind
        self._tenant_id = tenant_id

    def begin(
        self,
        key: str,
        request_hash: str,
        *,
        ttl_seconds: int,
    ) -> IdempotencyStart:
        expires_at = _expires_at(ttl_seconds)
        with _transaction(self._bind) as connection:
            _delete_expired_key(connection, key, tenant_id=self._tenant_id)
            inserted_key = connection.execute(
                pg_insert(idempotency_keys)
                .values(
                    tenant_id=self._tenant_id,
                    idempotency_key=key,
                    request_hash=request_hash,
                    status="in_progress",
                    response_json=None,
                    expires_at=expires_at,
                    partition_key=key,
                    partition_bucket=partition_bucket(key),
                )
                .on_conflict_do_nothing(
                    index_elements=[
                        idempotency_keys.c.tenant_id,
                        idempotency_keys.c.idempotency_key,
                    ]
                )
                .returning(idempotency_keys.c.idempotency_key)
            ).scalar_one_or_none()
            if inserted_key is not None:
                return IdempotencyStart(status="started")

            row = _select_key_for_update(connection, key, tenant_id=self._tenant_id)
            if row is None:
                raise IdempotencyConflictError(
                    "IDEMPOTENCY_KEY_RACE",
                    "Idempotency key could not be claimed. Please retry.",
                )
            return _start_from_existing_row(row, request_hash)

    def mark_completed(
        self,
        key: str,
        request_hash: str,
        *,
        run_id: str | None,
        response_json: dict[str, Any],
        ttl_seconds: int,
    ) -> None:
        with _transaction(self._bind) as connection:
            _update_existing_key(
                connection,
                key,
                request_hash,
                tenant_id=self._tenant_id,
                status="completed",
                run_id=run_id,
                response_json=response_json,
                ttl_seconds=ttl_seconds,
            )

    def mark_failed(
        self,
        key: str,
        request_hash: str,
        *,
        run_id: str | None,
        error_json: dict[str, Any],
        ttl_seconds: int,
    ) -> None:
        with _transaction(self._bind) as connection:
            _update_existing_key(
                connection,
                key,
                request_hash,
                tenant_id=self._tenant_id,
                status="failed",
                run_id=run_id,
                response_json=error_json,
                ttl_seconds=ttl_seconds,
            )


def hash_growth_strategy_request(request: GrowthStrategyRequest) -> str:
    payload = json.dumps(
        request.model_dump(mode="json"),
        sort_keys=True,
        separators=(",", ":"),
    )
    return sha256(payload.encode("utf-8")).hexdigest()


@contextmanager
def _transaction(bind: Engine | Connection) -> Iterator[Connection]:
    if isinstance(bind, Engine):
        with bind.begin() as connection:
            yield connection
    else:
        yield bind


def _delete_expired_key(connection: Connection, key: str, *, tenant_id: str) -> None:
    connection.execute(
        idempotency_keys.delete()
        .where(idempotency_keys.c.tenant_id == tenant_id)
        .where(idempotency_keys.c.idempotency_key == key)
        .where(idempotency_keys.c.expires_at < datetime.now(UTC))
    )


def _select_key_for_update(
    connection: Connection,
    key: str,
    *,
    tenant_id: str,
) -> RowMapping | None:
    return connection.execute(
        sa.select(idempotency_keys)
        .where(idempotency_keys.c.tenant_id == tenant_id)
        .where(idempotency_keys.c.idempotency_key == key)
        .with_for_update()
    ).mappings().one_or_none()


def _start_from_existing_row(
    row: RowMapping,
    request_hash: str,
) -> IdempotencyStart:
    if row["request_hash"] != request_hash:
        raise IdempotencyConflictError(
            "IDEMPOTENCY_KEY_REUSED",
            "Idempotency key was already used with a different request body.",
        )
    if row["status"] == "completed" and row["response_json"] is not None:
        return IdempotencyStart(status="replayed", response_json=row["response_json"])
    if row["status"] == "in_progress":
        raise IdempotencyConflictError(
            "IDEMPOTENCY_IN_PROGRESS",
            "A request with this idempotency key is already in progress.",
        )
    if row["status"] == "failed":
        raise IdempotencyConflictError(
            "IDEMPOTENCY_PREVIOUS_FAILURE",
            "A previous request with this idempotency key failed. Use a new key to retry.",
        )
    raise IdempotencyConflictError(
        "IDEMPOTENCY_INVALID_STATE",
        f"Idempotency key is in an unsupported state: {row['status']}",
    )


def _update_existing_key(
    connection: Connection,
    key: str,
    request_hash: str,
    *,
    tenant_id: str,
    status: Literal["completed", "failed"],
    run_id: str | None,
    response_json: dict[str, Any],
    ttl_seconds: int,
) -> None:
    connection.execute(
        idempotency_keys.update()
        .where(idempotency_keys.c.tenant_id == tenant_id)
        .where(idempotency_keys.c.idempotency_key == key)
        .where(idempotency_keys.c.request_hash == request_hash)
        .values(
            status=status,
            run_id=run_id,
            response_json=response_json,
            expires_at=_expires_at(ttl_seconds),
            partition_key=key,
            partition_bucket=partition_bucket(key),
            updated_at=sa.func.now(),
        )
    )


def _expires_at(ttl_seconds: int) -> datetime:
    return datetime.now(UTC) + timedelta(seconds=max(1, ttl_seconds))
