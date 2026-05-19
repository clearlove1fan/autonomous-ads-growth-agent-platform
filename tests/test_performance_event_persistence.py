from ads_growth_agent.config import Settings
from ads_growth_agent.performance_event_store_factory import (
    build_configured_performance_event_store,
    dispose_cached_performance_event_store_engines,
)
from ads_growth_agent.persistence.performance_event_store import (
    NoopCampaignPerformanceEventStore,
    PostgresCampaignPerformanceEventStore,
)


def test_performance_event_store_factory_defaults_to_noop_store() -> None:
    store = build_configured_performance_event_store(
        Settings(performance_event_persistence_backend="none")
    )

    assert isinstance(store, NoopCampaignPerformanceEventStore)
    assert store.get_event("missing_event") is None
    assert store.list_events(advertiser_id="adv_fitness_001", limit=10) == []


def test_performance_event_store_factory_builds_cached_postgres_store(monkeypatch) -> None:
    created: list[tuple[str, dict[str, object]]] = []

    class FakeEngine:
        def dispose(self) -> None:
            pass

    def fake_create_engine(database_url: str, **kwargs: object) -> FakeEngine:
        created.append((database_url, kwargs))
        return FakeEngine()

    monkeypatch.setattr(
        "ads_growth_agent.performance_event_store_factory.sa.create_engine",
        fake_create_engine,
    )
    dispose_cached_performance_event_store_engines()

    settings = Settings(
        database_url="postgresql+psycopg://ads_growth:ads_growth@localhost:5432/ads_growth",
        performance_event_persistence_backend="postgres",
        tenant_id="tenant_a",
    )
    first = build_configured_performance_event_store(settings)
    second = build_configured_performance_event_store(settings)

    assert isinstance(first, PostgresCampaignPerformanceEventStore)
    assert isinstance(second, PostgresCampaignPerformanceEventStore)
    assert created == [
        (
            "postgresql+psycopg://ads_growth:ads_growth@localhost:5432/ads_growth",
            {"pool_pre_ping": True},
        )
    ]

    dispose_cached_performance_event_store_engines()
