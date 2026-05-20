from ads_growth_agent.config import Settings
from ads_growth_agent.feedback_execution_store_factory import (
    build_configured_feedback_execution_store,
    dispose_cached_feedback_execution_store_engines,
)
from ads_growth_agent.persistence.feedback_execution_store import (
    NoopFeedbackExecutionDryRunStore,
    PostgresFeedbackExecutionDryRunStore,
)


def test_feedback_execution_store_factory_defaults_to_noop_store() -> None:
    store = build_configured_feedback_execution_store(
        Settings(feedback_execution_persistence_backend="none")
    )

    assert isinstance(store, NoopFeedbackExecutionDryRunStore)
    assert store.get_dry_run("missing_dry_run") is None
    dry_runs = store.list_dry_runs(review_id="feedback_review_001", limit=10)
    assert dry_runs.items == []
    assert dry_runs.count == 0
    assert dry_runs.review_id == "feedback_review_001"


def test_feedback_execution_store_factory_builds_cached_postgres_store(monkeypatch) -> None:
    created: list[tuple[str, dict[str, object]]] = []

    class FakeEngine:
        def dispose(self) -> None:
            pass

    def fake_create_engine(database_url: str, **kwargs: object) -> FakeEngine:
        created.append((database_url, kwargs))
        return FakeEngine()

    monkeypatch.setattr(
        "ads_growth_agent.feedback_execution_store_factory.sa.create_engine",
        fake_create_engine,
    )
    dispose_cached_feedback_execution_store_engines()

    settings = Settings(
        database_url="postgresql+psycopg://ads_growth:ads_growth@localhost:5432/ads_growth",
        feedback_execution_persistence_backend="postgres",
        tenant_id="tenant_a",
    )
    first = build_configured_feedback_execution_store(settings)
    second = build_configured_feedback_execution_store(settings)

    assert isinstance(first, PostgresFeedbackExecutionDryRunStore)
    assert isinstance(second, PostgresFeedbackExecutionDryRunStore)
    assert created == [
        (
            "postgresql+psycopg://ads_growth:ads_growth@localhost:5432/ads_growth",
            {"pool_pre_ping": True},
        )
    ]

    dispose_cached_feedback_execution_store_engines()
