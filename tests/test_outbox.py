import json
from datetime import UTC, datetime

from fastapi.testclient import TestClient
from typer.testing import CliRunner

from ads_growth_agent.api import (
    app as api_app,
)
from ads_growth_agent.api import (
    get_runtime_advertiser_memory_store,
    get_runtime_outbox_store,
    get_runtime_settings,
)
from ads_growth_agent.cli import app as cli_app
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


def test_outbox_api_lists_gets_retries_and_processes_events() -> None:
    event = _event_request()
    analysis = analyze_campaign_performance_event(event)
    failed_record = _outbox_record(
        outbox_event_id="outbox_failed",
        status="failed",
        payload={"bad": "payload"},
        error_json={"message": "schema mismatch"},
    )
    processable_record = _outbox_record(
        outbox_event_id="outbox_process",
        payload={
            "event": event.model_dump(mode="json"),
            "analysis": analysis.model_dump(mode="json"),
        },
    )
    outbox_store = FakeOutboxStore(
        events=[failed_record],
        claimed=[processable_record],
    )
    memory_store = FakeAdvertiserMemoryStore()
    api_app.dependency_overrides[get_runtime_settings] = lambda: Settings(
        outbox_backend="postgres"
    )
    api_app.dependency_overrides[get_runtime_outbox_store] = lambda: outbox_store
    api_app.dependency_overrides[get_runtime_advertiser_memory_store] = lambda: memory_store
    try:
        client = TestClient(api_app)
        listed = client.get(
            "/outbox/events",
            params={"status": "failed", "limit": "10"},
            headers={"X-Tenant-ID": "tenant_outbox_api"},
        )
        detail = client.get(
            "/outbox/events/outbox_failed",
            headers={"X-Tenant-ID": "tenant_outbox_api"},
        )
        retried = client.post(
            "/outbox/events/outbox_failed/retry",
            headers={
                "X-Tenant-ID": "tenant_outbox_api",
                "X-Operator-ID": "operator_api",
            },
        )
        processed = client.post(
            "/outbox/process",
            params={"limit": "5"},
            headers={
                "X-Tenant-ID": "tenant_outbox_api",
                "X-Worker-ID": "worker_api",
            },
        )
    finally:
        api_app.dependency_overrides.clear()

    listed_payload = listed.json()
    detail_payload = detail.json()
    retried_payload = retried.json()
    processed_payload = processed.json()
    assert listed.status_code == 200
    assert listed.headers["x-tenant-id"] == "tenant_outbox_api"
    assert listed.headers["outbox-event-count"] == "1"
    assert listed_payload["count"] == 1
    assert listed_payload["status"] == "failed"
    assert listed_payload["items"][0]["outbox_event_id"] == "outbox_failed"
    assert detail.status_code == 200
    assert detail.headers["outbox-event-status"] == "failed"
    assert detail_payload["error_json"]["message"] == "schema mismatch"
    assert retried.status_code == 200
    assert retried.headers["outbox-event-status"] == "pending"
    assert retried_payload["status"] == "pending"
    assert retried_payload["attempt_count"] == 0
    assert retried_payload["error_json"] is None
    assert retried_payload["metadata"]["manual_retry_count"] == 1
    assert retried_payload["metadata"]["last_manual_retry_by"] == "operator_api"
    assert retried_payload["metadata"]["previous_error"]["message"] == "schema mismatch"
    assert processed.status_code == 200
    assert processed.headers["outbox-claimed"] == "1"
    assert processed_payload["worker_id"] == "worker_api"
    assert processed_payload["completed"] == 1
    assert memory_store.records == [("evt_perf_001", analysis.feedback_id)]


def test_outbox_api_rejects_retry_for_active_event() -> None:
    pending_record = _outbox_record(
        outbox_event_id="outbox_pending",
        status="pending",
        payload={"queued": True},
    )
    outbox_store = FakeOutboxStore(events=[pending_record])
    api_app.dependency_overrides[get_runtime_settings] = lambda: Settings(
        outbox_backend="postgres"
    )
    api_app.dependency_overrides[get_runtime_outbox_store] = lambda: outbox_store
    try:
        response = TestClient(api_app).post("/outbox/events/outbox_pending/retry")
    finally:
        api_app.dependency_overrides.clear()

    assert response.status_code == 409
    assert response.json()["detail"]["error_code"] == "OUTBOX_EVENT_NOT_RETRYABLE"
    assert response.json()["detail"]["status"] == "pending"


def test_outbox_cli_lists_gets_and_retries_events(monkeypatch) -> None:
    failed_record = _outbox_record(
        outbox_event_id="outbox_cli_failed",
        status="failed",
        payload={"bad": "payload"},
        error_json={"message": "transient memory write failure"},
    )
    outbox_store = FakeOutboxStore(events=[failed_record])
    monkeypatch.setattr(
        "ads_growth_agent.cli.get_settings",
        lambda: Settings(outbox_backend="postgres"),
    )
    monkeypatch.setattr(
        "ads_growth_agent.cli.build_configured_outbox_store",
        lambda settings: outbox_store,
    )

    listed = CliRunner().invoke(cli_app, ["list-outbox-events", "--status", "failed"])
    detail = CliRunner().invoke(cli_app, ["get-outbox-event", "outbox_cli_failed"])
    retried = CliRunner().invoke(
        cli_app,
        [
            "retry-outbox-event",
            "outbox_cli_failed",
            "--requested-by",
            "operator_cli",
        ],
    )

    listed_payload = json.loads(listed.stdout)
    detail_payload = json.loads(detail.stdout)
    retried_payload = json.loads(retried.stdout)
    assert listed.exit_code == 0
    assert listed_payload["count"] == 1
    assert listed_payload["items"][0]["status"] == "failed"
    assert detail.exit_code == 0
    assert detail_payload["outbox_event_id"] == "outbox_cli_failed"
    assert retried.exit_code == 0
    assert retried_payload["status"] == "pending"
    assert retried_payload["metadata"]["last_manual_retry_by"] == "operator_cli"


class FakeOutboxStore:
    def __init__(
        self,
        *,
        status: str = "pending",
        claimed: list[OutboxEventRecord] | None = None,
        events: list[OutboxEventRecord] | None = None,
    ) -> None:
        self.status = status
        self.claimed = claimed or []
        self.events = {event.outbox_event_id: event for event in events or []}
        self.enqueued: list[dict[str, object]] = []
        self.completed: list[str] = []
        self.failed: list[str] = []

    def enqueue(self, **kwargs) -> OutboxEventRecord:
        self.enqueued.append(kwargs)
        record = _outbox_record(
            status=self.status,
            payload=kwargs["payload"],
            metadata=kwargs.get("metadata") or {},
        )
        self.events[record.outbox_event_id] = record
        return record

    def claim_pending(self, *, limit: int, worker_id: str, lock_seconds: int = 60):
        return self.claimed[:limit]

    def mark_completed(self, outbox_event_id: str, *, result=None):
        self.completed.append(outbox_event_id)
        return None

    def mark_failed(self, outbox_event_id: str, *, error, retry_delay_seconds: int = 5):
        self.failed.append(outbox_event_id)
        return None

    def get_event(self, outbox_event_id: str):
        return self.events.get(outbox_event_id)

    def list_events(
        self,
        *,
        status=None,
        event_type=None,
        aggregate_type=None,
        aggregate_id=None,
        limit: int = 50,
    ):
        records = list(self.events.values())
        if status is not None:
            records = [record for record in records if record.status == status]
        if event_type is not None:
            records = [record for record in records if record.event_type == event_type]
        if aggregate_type is not None:
            records = [
                record for record in records if record.aggregate_type == aggregate_type
            ]
        if aggregate_id is not None:
            records = [record for record in records if record.aggregate_id == aggregate_id]
        return records[:limit]

    def retry_failed(self, outbox_event_id: str, *, max_attempts=None, requested_by="operator"):
        record = self.events.get(outbox_event_id)
        if record is None or record.status != "failed":
            return None
        metadata = dict(record.metadata)
        if record.error_json is not None:
            metadata["previous_error"] = record.error_json
        metadata["manual_retry_count"] = int(metadata.get("manual_retry_count") or 0) + 1
        metadata["last_manual_retry_by"] = requested_by
        retried = record.model_copy(
            update={
                "status": "pending",
                "attempt_count": 0,
                "max_attempts": max_attempts or record.max_attempts,
                "error_json": None,
                "metadata": metadata,
                "next_attempt_at": datetime.now(UTC),
                "locked_by": None,
                "locked_until": None,
                "completed_at": None,
            }
        )
        self.events[outbox_event_id] = retried
        return retried


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
    outbox_event_id: str = "outbox_test",
    status: str = "pending",
    payload: dict,
    event_type: str = CAMPAIGN_PERFORMANCE_ANALYZED_EVENT,
    aggregate_type: str = "campaign_performance_event",
    aggregate_id: str = "evt_perf_001",
    metadata: dict[str, object] | None = None,
    error_json: dict[str, object] | None = None,
) -> OutboxEventRecord:
    now = datetime.now(UTC)
    return OutboxEventRecord(
        outbox_event_id=outbox_event_id,
        event_type=event_type,
        aggregate_type=aggregate_type,
        aggregate_id=aggregate_id,
        idempotency_key="advertiser-memory:adv_fitness_001:evt_perf_001:v1",
        status=status,
        payload=payload,
        error_json=error_json,
        attempt_count=0,
        max_attempts=3,
        metadata=metadata or {},
        next_attempt_at=now if status == "pending" else None,
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
