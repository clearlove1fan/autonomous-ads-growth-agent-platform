from threading import Lock

import sqlalchemy as sa
from sqlalchemy.engine import Engine

from ads_growth_agent.config import Settings
from ads_growth_agent.persistence.idempotency_store import (
    IdempotencyStore,
    NoopIdempotencyStore,
    PostgresIdempotencyStore,
)

_ENGINE_LOCK = Lock()
_ENGINES_BY_URL: dict[str, Engine] = {}


def build_configured_idempotency_store(settings: Settings) -> IdempotencyStore:
    if settings.idempotency_backend == "none":
        return NoopIdempotencyStore()
    if settings.idempotency_backend == "postgres":
        return PostgresIdempotencyStore(
            _engine_for_url(settings.database_url),
            tenant_id=settings.tenant_id,
        )
    raise ValueError(f"Unsupported idempotency backend: {settings.idempotency_backend}")


def dispose_cached_idempotency_store_engines() -> None:
    with _ENGINE_LOCK:
        engines = list(_ENGINES_BY_URL.values())
        _ENGINES_BY_URL.clear()

    for engine in engines:
        engine.dispose()


def _engine_for_url(database_url: str) -> Engine:
    with _ENGINE_LOCK:
        engine = _ENGINES_BY_URL.get(database_url)
        if engine is None:
            engine = sa.create_engine(database_url, pool_pre_ping=True)
            _ENGINES_BY_URL[database_url] = engine
        return engine
