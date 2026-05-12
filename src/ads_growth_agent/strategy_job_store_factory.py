from threading import Lock

import sqlalchemy as sa
from sqlalchemy.engine import Engine

from ads_growth_agent.config import Settings
from ads_growth_agent.persistence.strategy_job_store import (
    InMemoryStrategyJobStore,
    PostgresStrategyJobStore,
    StrategyJobStore,
)

_ENGINE_LOCK = Lock()
_ENGINES_BY_URL: dict[str, Engine] = {}
_MEMORY_JOB_STORE = InMemoryStrategyJobStore()


def build_configured_strategy_job_store(settings: Settings) -> StrategyJobStore:
    if settings.strategy_job_backend == "memory":
        return _MEMORY_JOB_STORE
    if settings.strategy_job_backend == "postgres":
        return PostgresStrategyJobStore(
            _engine_for_url(settings.database_url),
            tenant_id=settings.tenant_id,
        )
    raise ValueError(f"Unsupported strategy job backend: {settings.strategy_job_backend}")


def clear_memory_strategy_job_store() -> None:
    _MEMORY_JOB_STORE.clear()


def dispose_cached_strategy_job_store_engines() -> None:
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
