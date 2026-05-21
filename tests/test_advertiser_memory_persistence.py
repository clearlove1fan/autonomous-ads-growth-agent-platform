from ads_growth_agent.advertiser_memory_store_factory import (
    build_configured_advertiser_memory_store,
    dispose_cached_advertiser_memory_store_engines,
)
from ads_growth_agent.config import Settings
from ads_growth_agent.contracts import (
    CampaignFeedbackHandoffRecordRequest,
    CampaignFeedbackOptimizationReviewRequest,
    CampaignPerformanceEventDetailResponse,
    FeedbackHandoffOutcome,
    FeedbackOptimizationReviewDecision,
)
from ads_growth_agent.feedback import (
    analyze_campaign_performance_event,
    build_campaign_feedback_optimization_draft,
    build_campaign_feedback_optimization_review,
)
from ads_growth_agent.feedback_handoff_package import build_feedback_handoff_package
from ads_growth_agent.feedback_handoff_record import build_feedback_handoff_record
from ads_growth_agent.persistence.advertiser_memory_store import (
    NoopAdvertiserMemoryStore,
    PostgresAdvertiserMemoryStore,
    feedback_memory_source_id,
    handoff_memory_source_id,
)


def test_advertiser_memory_store_factory_defaults_to_noop_store() -> None:
    store = build_configured_advertiser_memory_store(
        Settings(advertiser_memory_persistence_backend="none")
    )

    assert isinstance(store, NoopAdvertiserMemoryStore)
    assert (
        store.get_memory(
            advertiser_id="adv_fitness_001",
            source_id="memory:performance:test:v1",
        )
        is None
    )
    assert store.list_memories(advertiser_id="adv_fitness_001", limit=10) == []


def test_advertiser_memory_store_factory_builds_cached_postgres_store(monkeypatch) -> None:
    created: list[tuple[str, dict[str, object]]] = []

    class FakeEngine:
        def dispose(self) -> None:
            pass

    def fake_create_engine(database_url: str, **kwargs: object) -> FakeEngine:
        created.append((database_url, kwargs))
        return FakeEngine()

    monkeypatch.setattr(
        "ads_growth_agent.advertiser_memory_store_factory.sa.create_engine",
        fake_create_engine,
    )
    dispose_cached_advertiser_memory_store_engines()

    settings = Settings(
        database_url="postgresql+psycopg://ads_growth:ads_growth@localhost:5432/ads_growth",
        advertiser_memory_persistence_backend="postgres",
        tenant_id="tenant_a",
    )
    first = build_configured_advertiser_memory_store(settings)
    second = build_configured_advertiser_memory_store(settings)

    assert isinstance(first, PostgresAdvertiserMemoryStore)
    assert isinstance(second, PostgresAdvertiserMemoryStore)
    assert created == [
        (
            "postgresql+psycopg://ads_growth:ads_growth@localhost:5432/ads_growth",
            {"pool_pre_ping": True},
        )
    ]

    dispose_cached_advertiser_memory_store_engines()


def test_feedback_memory_source_id_is_stable_and_short() -> None:
    event = _event_request()

    source_id = feedback_memory_source_id(event)

    assert source_id == feedback_memory_source_id(event)
    assert source_id.startswith("memory:performance:")
    assert len(source_id) <= 160


def test_handoff_memory_source_id_is_stable_and_short() -> None:
    record = _handoff_record()

    source_id = handoff_memory_source_id(record)

    assert source_id == handoff_memory_source_id(record)
    assert source_id.startswith("memory:handoff:")
    assert len(source_id) <= 160


def test_noop_memory_store_reports_handoff_memory_disabled() -> None:
    store = NoopAdvertiserMemoryStore()

    result = store.record_handoff_memory(_handoff_record())

    assert result.persisted is False
    assert result.queued is False
    assert result.status == "disabled"


def _event_request():
    from ads_growth_agent.contracts import CampaignPerformanceEventRequest

    return CampaignPerformanceEventRequest.model_validate(
        {
            "event_id": "evt_perf_001",
            "advertiser_id": "adv_fitness_001",
            "run_id": "run_001",
            "objective": "registrations",
            "event_type": "performance_snapshot",
            "occurred_at": "2026-05-12T12:00:00Z",
            "metrics": {
                "impressions": 10000,
                "clicks": 500,
                "spend": "1000.00",
                "conversions": 20,
            },
            "target_cpa": "20.00",
            "attribution_window_days": 7,
        }
    )


def _handoff_record():
    event = _event_request()
    analysis = analyze_campaign_performance_event(event)
    detail = CampaignPerformanceEventDetailResponse(
        event_id=event.event_id,
        advertiser_id=event.advertiser_id,
        run_id=event.run_id,
        campaign_id=event.campaign_id,
        draft_id=event.draft_id,
        objective=event.objective,
        event_type=event.event_type,
        occurred_at=event.occurred_at,
        metrics=event.metrics,
        status="analyzed",
        metadata={"performance_event_persistence": "postgres"},
        analysis=analysis,
        created_at=analysis.created_at,
        updated_at=analysis.created_at,
    )
    optimization_draft = build_campaign_feedback_optimization_draft(detail)
    review = build_campaign_feedback_optimization_review(
        optimization_draft,
        CampaignFeedbackOptimizationReviewRequest(
            decision=FeedbackOptimizationReviewDecision.APPROVED,
            reviewer_id="operator_test",
            selected_change_ids=[optimization_draft.changes[0].change_id],
        ),
        review_id="feedback_review_memory_test",
    )
    handoff_package = build_feedback_handoff_package(review)
    return build_feedback_handoff_record(
        handoff_package,
        CampaignFeedbackHandoffRecordRequest(
            outcome=FeedbackHandoffOutcome.BLOCKED,
            operator_id="operator_test",
            notes="Blocked by missing manual validation.",
        ),
        handoff_record_id="feedback_handoff_record_memory_test",
    )
