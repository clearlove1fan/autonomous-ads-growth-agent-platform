from ads_growth_agent.config import Settings
from ads_growth_agent.feedback_review_store_factory import (
    build_configured_feedback_review_store,
    dispose_cached_feedback_review_store_engines,
)
from ads_growth_agent.persistence.feedback_review_store import (
    NoopFeedbackOptimizationReviewStore,
    PostgresFeedbackOptimizationReviewStore,
)


def test_feedback_review_store_factory_defaults_to_noop_store() -> None:
    store = build_configured_feedback_review_store(
        Settings(feedback_review_persistence_backend="none")
    )

    assert isinstance(store, NoopFeedbackOptimizationReviewStore)
    assert store.get_review("missing_review") is None
    reviews = store.list_reviews(event_id="evt_perf_001", limit=10)
    assert reviews.items == []
    assert reviews.count == 0
    assert reviews.event_id == "evt_perf_001"


def test_feedback_review_store_factory_builds_cached_postgres_store(monkeypatch) -> None:
    created: list[tuple[str, dict[str, object]]] = []

    class FakeEngine:
        def dispose(self) -> None:
            pass

    def fake_create_engine(database_url: str, **kwargs: object) -> FakeEngine:
        created.append((database_url, kwargs))
        return FakeEngine()

    monkeypatch.setattr(
        "ads_growth_agent.feedback_review_store_factory.sa.create_engine",
        fake_create_engine,
    )
    dispose_cached_feedback_review_store_engines()

    settings = Settings(
        database_url="postgresql+psycopg://ads_growth:ads_growth@localhost:5432/ads_growth",
        feedback_review_persistence_backend="postgres",
        tenant_id="tenant_a",
    )
    first = build_configured_feedback_review_store(settings)
    second = build_configured_feedback_review_store(settings)

    assert isinstance(first, PostgresFeedbackOptimizationReviewStore)
    assert isinstance(second, PostgresFeedbackOptimizationReviewStore)
    assert created == [
        (
            "postgresql+psycopg://ads_growth:ads_growth@localhost:5432/ads_growth",
            {"pool_pre_ping": True},
        )
    ]

    dispose_cached_feedback_review_store_engines()
