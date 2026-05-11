from ads_growth_agent.config import Settings
from ads_growth_agent.contracts import AdvertiserBrief, GrowthStrategyRequest
from ads_growth_agent.idempotency_store_factory import (
    build_configured_idempotency_store,
    dispose_cached_idempotency_store_engines,
)
from ads_growth_agent.persistence.idempotency_store import (
    NoopIdempotencyStore,
    PostgresIdempotencyStore,
    hash_growth_strategy_request,
)


def test_idempotency_store_factory_defaults_to_noop_store() -> None:
    store = build_configured_idempotency_store(Settings(idempotency_backend="none"))

    assert isinstance(store, NoopIdempotencyStore)


def test_idempotency_store_factory_builds_cached_postgres_store(monkeypatch) -> None:
    created: list[tuple[str, dict[str, object]]] = []

    class FakeEngine:
        def dispose(self) -> None:
            pass

    def fake_create_engine(database_url: str, **kwargs: object) -> FakeEngine:
        created.append((database_url, kwargs))
        return FakeEngine()

    monkeypatch.setattr(
        "ads_growth_agent.idempotency_store_factory.sa.create_engine",
        fake_create_engine,
    )
    dispose_cached_idempotency_store_engines()

    settings = Settings(
        database_url="postgresql+psycopg://ads_growth:ads_growth@localhost:5432/ads_growth",
        idempotency_backend="postgres",
        tenant_id="tenant_a",
    )
    first = build_configured_idempotency_store(settings)
    second = build_configured_idempotency_store(settings)

    assert isinstance(first, PostgresIdempotencyStore)
    assert isinstance(second, PostgresIdempotencyStore)
    assert created == [
        (
            "postgresql+psycopg://ads_growth:ads_growth@localhost:5432/ads_growth",
            {"pool_pre_ping": True},
        )
    ]

    dispose_cached_idempotency_store_engines()


def test_hash_growth_strategy_request_is_stable_for_same_payload() -> None:
    request = GrowthStrategyRequest(brief=AdvertiserBrief.model_validate(_brief_payload()))

    assert hash_growth_strategy_request(request) == hash_growth_strategy_request(request)


def _brief_payload() -> dict[str, object]:
    return {
        "advertiser_id": "adv_fitness_001",
        "product_name": "FitTrack Pro",
        "product_category": "fitness app",
        "objective": "registrations",
        "budget": "2000.00",
        "currency": "USD",
        "duration_days": 14,
        "target_market": "United States",
        "primary_kpi": "trial registrations",
        "target_cpa": "20.00",
        "brand_voice": "motivational and practical",
        "constraints": ["Avoid unrealistic body transformation claims"],
        "known_audiences": ["Home workout beginners"],
    }
