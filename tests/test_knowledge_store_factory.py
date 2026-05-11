from ads_growth_agent.config import Settings
from ads_growth_agent.knowledge import InMemoryKnowledgeStore
from ads_growth_agent.knowledge_store_factory import (
    build_configured_knowledge_store,
    dispose_cached_knowledge_store_engines,
)
from ads_growth_agent.persistence.knowledge_store import PostgresKnowledgeStore


def test_knowledge_store_factory_defaults_to_in_memory_store() -> None:
    store = build_configured_knowledge_store(Settings(knowledge_store_backend="memory"))

    assert isinstance(store, InMemoryKnowledgeStore)


def test_knowledge_store_factory_builds_cached_postgres_store(monkeypatch) -> None:
    created: list[tuple[str, dict[str, object]]] = []

    class FakeEngine:
        def dispose(self) -> None:
            pass

    def fake_create_engine(database_url: str, **kwargs: object) -> FakeEngine:
        created.append((database_url, kwargs))
        return FakeEngine()

    monkeypatch.setattr(
        "ads_growth_agent.knowledge_store_factory.sa.create_engine",
        fake_create_engine,
    )
    dispose_cached_knowledge_store_engines()

    settings = Settings(
        database_url="postgresql+psycopg://ads_growth:ads_growth@localhost:5432/ads_growth",
        knowledge_store_backend="postgres",
        tenant_id="tenant_a",
    )
    first = build_configured_knowledge_store(settings)
    second = build_configured_knowledge_store(settings)

    assert isinstance(first, PostgresKnowledgeStore)
    assert isinstance(second, PostgresKnowledgeStore)
    assert created == [
        (
            "postgresql+psycopg://ads_growth:ads_growth@localhost:5432/ads_growth",
            {"pool_pre_ping": True},
        )
    ]

    dispose_cached_knowledge_store_engines()
