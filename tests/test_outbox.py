from datetime import UTC, datetime

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
from ads_growth_agent.outbox import (
    ADVERTISER_MEMORY_RETRIEVED_EVENT,
    CAMPAIGN_PERFORMANCE_ANALYZED_EVENT,
    FEEDBACK_HANDOFF_RECORDED_EVENT,
    enqueue_advertiser_memory_retrieved,
    enqueue_advertiser_memory_write,
    enqueue_handoff_memory_write,
    process_outbox_events,
)
from ads_growth_agent.outbox_store_factory import (
    build_configured_outbox_store,
    dispose_cached_outbox_store_engines,
)
from ads_growth_agent.persistence.advertiser_memory_store import (
    AdvertiserMemoryUsageResult,
    AdvertiserMemoryWriteResult,
)
from ads_growth_agent.persistence.outbox_store import (
    NoopOutboxStore,
    OutboxEventRecord,
    PostgresOutboxStore,
)


def test_outbox_store_factory_defaults_to_noop_store() -> None:
    store = build_configured_outbox_store(Settings(outbox_backend="none"))

    assert isinstance(store, NoopOutboxStore)


def test_outbox_store_factory_builds_cached_postgres_store(monkeypatch) -> None:
    created: list[tuple[str, dict[str, object]]] = []

    class FakeEngine:
        def dispose(self) -> None:
            pass

    def fake_create_engine(database_url: str, **kwargs: object) -> FakeEngine:
        created.append((database_url, kwargs))
        return FakeEngine()

    monkeypatch.setattr(
        "ads_growth_agent.outbox_store_factory.sa.create_engine",
        fake_create_engine,
    )
    dispose_cached_outbox_store_engines()

    settings = Settings(
        database_url="postgresql+psycopg://ads_growth:ads_growth@localhost:5432/ads_growth",
        outbox_backend="postgres",
        tenant_id="tenant_a",
    )
    first = build_configured_outbox_store(settings)
    second = build_configured_outbox_store(settings)

    assert isinstance(first, PostgresOutboxStore)
    assert isinstance(second, PostgresOutboxStore)
    assert created == [
        (
            "postgresql+psycopg://ads_growth:ads_growth@localhost:5432/ads_growth",
            {"pool_pre_ping": True},
        )
    ]

    dispose_cached_outbox_store_engines()


def test_enqueue_advertiser_memory_write_returns_queued_result() -> None:
    event = _event_request()
    analysis = analyze_campaign_performance_event(event)
    outbox_store = FakeOutboxStore(status="pending")

    result = enqueue_advertiser_memory_write(outbox_store, event, analysis)

    assert result.persisted is False
    assert result.queued is True
    assert result.status == "queued"
    assert result.source_id is not None
    assert outbox_store.enqueued[0]["event_type"] == CAMPAIGN_PERFORMANCE_ANALYZED_EVENT
    assert outbox_store.enqueued[0]["payload"]["event"]["event_id"] == "evt_perf_001"
    assert outbox_store.enqueued[0]["payload"]["analysis"]["event_id"] == "evt_perf_001"


def test_enqueue_handoff_memory_write_returns_queued_result() -> None:
    handoff_record = _handoff_record()
    outbox_store = FakeOutboxStore(status="pending")

    result = enqueue_handoff_memory_write(outbox_store, handoff_record)

    assert result.persisted is False
    assert result.queued is True
    assert result.status == "queued"
    assert result.source_id is not None
    assert outbox_store.enqueued[0]["event_type"] == FEEDBACK_HANDOFF_RECORDED_EVENT
    assert outbox_store.enqueued[0]["aggregate_type"] == "feedback_handoff_record"
    assert outbox_store.enqueued[0]["payload"]["handoff_record"]["handoff_record_id"] == (
        "feedback_handoff_record_outbox_test"
    )
    assert outbox_store.enqueued[0]["metadata"]["handoff_outcome"] == "blocked"


def test_enqueue_advertiser_memory_retrieved_records_usage_event() -> None:
    outbox_store = FakeOutboxStore(status="pending")

    record = enqueue_advertiser_memory_retrieved(
        outbox_store,
        source_id="memory:adv_fitness_001:profile:v1",
        advertiser_id="adv_fitness_001",
        run_id="run_usage_001",
        query="fitness registrations",
        relevance=0.8,
    )

    assert record.status == "pending"
    assert outbox_store.enqueued[0]["event_type"] == ADVERTISER_MEMORY_RETRIEVED_EVENT
    assert outbox_store.enqueued[0]["aggregate_type"] == "advertiser_memory"
    assert outbox_store.enqueued[0]["payload"]["source_id"] == (
        "memory:adv_fitness_001:profile:v1"
    )
    assert outbox_store.enqueued[0]["payload"]["run_id"] == "run_usage_001"


def test_process_outbox_events_writes_advertiser_memory_and_marks_completed() -> None:
    event = _event_request()
    analysis = analyze_campaign_performance_event(event)
    record = _outbox_record(
        payload={
            "event": event.model_dump(mode="json"),
            "analysis": analysis.model_dump(mode="json"),
        }
    )
    outbox_store = FakeOutboxStore(claimed=[record])
    memory_store = FakeAdvertiserMemoryStore()

    report = process_outbox_events(
        outbox_store,
        memory_store,
        limit=10,
        worker_id="worker_unit",
    )

    assert report.worker_id == "worker_unit"
    assert report.claimed == 1
    assert report.completed == 1
    assert report.failed == 0
    assert memory_store.records == [("evt_perf_001", analysis.feedback_id)]
    assert outbox_store.completed == [record.outbox_event_id]
    assert outbox_store.failed == []


def test_process_outbox_events_records_memory_usage_and_marks_completed() -> None:
    record = _outbox_record(
        event_type=ADVERTISER_MEMORY_RETRIEVED_EVENT,
        aggregate_type="advertiser_memory",
        aggregate_id="memory:adv_fitness_001:profile:v1",
        payload={
            "source_id": "memory:adv_fitness_001:profile:v1",
            "advertiser_id": "adv_fitness_001",
            "run_id": "run_usage_001",
            "query": "fitness registrations",
            "relevance": 0.8,
            "retrieved_at": "2026-05-13T01:30:00+00:00",
        },
    )
    outbox_store = FakeOutboxStore(claimed=[record])
    memory_store = FakeAdvertiserMemoryStore()

    report = process_outbox_events(
        outbox_store,
        memory_store,
        limit=10,
        worker_id="worker_unit",
    )

    assert report.claimed == 1
    assert report.completed == 1
    assert report.failed == 0
    assert memory_store.usage_records == ["memory:adv_fitness_001:profile:v1"]
    assert outbox_store.completed == [record.outbox_event_id]


def test_process_outbox_events_writes_handoff_memory_and_marks_completed() -> None:
    handoff_record = _handoff_record()
    record = _outbox_record(
        event_type=FEEDBACK_HANDOFF_RECORDED_EVENT,
        aggregate_type="feedback_handoff_record",
        aggregate_id=handoff_record.handoff_record_id,
        payload={"handoff_record": handoff_record.model_dump(mode="json")},
    )
    outbox_store = FakeOutboxStore(claimed=[record])
    memory_store = FakeAdvertiserMemoryStore()

    report = process_outbox_events(
        outbox_store,
        memory_store,
        limit=10,
        worker_id="worker_unit",
    )

    assert report.claimed == 1
    assert report.completed == 1
    assert report.failed == 0
    assert memory_store.handoff_records == ["feedback_handoff_record_outbox_test"]
    assert outbox_store.completed == [record.outbox_event_id]


def test_process_outbox_events_marks_invalid_payload_failed() -> None:
    record = _outbox_record(payload={"event": {"bad": "payload"}, "analysis": {}})
    outbox_store = FakeOutboxStore(claimed=[record])
    memory_store = FakeAdvertiserMemoryStore()

    report = process_outbox_events(
        outbox_store,
        memory_store,
        limit=10,
        worker_id="worker_unit",
    )

    assert report.claimed == 1
    assert report.completed == 0
    assert report.failed == 1
    assert memory_store.records == []
    assert outbox_store.completed == []
    assert outbox_store.failed == [record.outbox_event_id]


class FakeOutboxStore:
    def __init__(
        self,
        *,
        status: str = "pending",
        claimed: list[OutboxEventRecord] | None = None,
    ) -> None:
        self.status = status
        self.claimed = claimed or []
        self.enqueued: list[dict[str, object]] = []
        self.completed: list[str] = []
        self.failed: list[str] = []

    def enqueue(self, **kwargs) -> OutboxEventRecord:
        self.enqueued.append(kwargs)
        return _outbox_record(
            status=self.status,
            payload=kwargs["payload"],
            metadata=kwargs.get("metadata") or {},
        )

    def claim_pending(self, *, limit: int, worker_id: str, lock_seconds: int = 60):
        return self.claimed[:limit]

    def mark_completed(self, outbox_event_id: str, *, result=None):
        self.completed.append(outbox_event_id)
        return None

    def mark_failed(self, outbox_event_id: str, *, error, retry_delay_seconds: int = 5):
        self.failed.append(outbox_event_id)
        return None


class FakeAdvertiserMemoryStore:
    def __init__(self) -> None:
        self.records: list[tuple[str, str]] = []
        self.handoff_records: list[str] = []
        self.usage_records: list[str] = []

    def record_feedback_memory(self, event, analysis) -> AdvertiserMemoryWriteResult:
        self.records.append((event.event_id, analysis.feedback_id))
        return AdvertiserMemoryWriteResult(
            persisted=True,
            status="recorded",
            source_id="memory:performance:test:v1",
            memory_type="historical_performance",
        )

    def record_handoff_memory(self, record) -> AdvertiserMemoryWriteResult:
        self.handoff_records.append(record.handoff_record_id)
        return AdvertiserMemoryWriteResult(
            persisted=True,
            status="recorded",
            source_id="memory:handoff:test:v1",
            memory_type="historical_performance",
        )

    def record_retrieval_usage(self, *, source_id: str, retrieved_at=None):
        self.usage_records.append(source_id)
        return AdvertiserMemoryUsageResult(
            recorded=True,
            source_id=source_id,
            usage_count=1,
            last_used_at=retrieved_at,
        )


def _outbox_record(
    *,
    status: str = "pending",
    payload: dict,
    event_type: str = CAMPAIGN_PERFORMANCE_ANALYZED_EVENT,
    aggregate_type: str = "campaign_performance_event",
    aggregate_id: str = "evt_perf_001",
    metadata: dict[str, object] | None = None,
) -> OutboxEventRecord:
    now = datetime.now(UTC)
    return OutboxEventRecord(
        outbox_event_id="outbox_test",
        event_type=event_type,
        aggregate_type=aggregate_type,
        aggregate_id=aggregate_id,
        idempotency_key="advertiser-memory:adv_fitness_001:evt_perf_001:v1",
        status=status,
        payload=payload,
        attempt_count=0,
        max_attempts=3,
        metadata=metadata or {},
        created_at=now,
        updated_at=now,
    )


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
        review_id="feedback_review_outbox_test",
    )
    handoff_package = build_feedback_handoff_package(review)
    return build_feedback_handoff_record(
        handoff_package,
        CampaignFeedbackHandoffRecordRequest(
            outcome=FeedbackHandoffOutcome.BLOCKED,
            operator_id="operator_test",
            notes="Blocked by missing manual validation.",
        ),
        handoff_record_id="feedback_handoff_record_outbox_test",
    )
