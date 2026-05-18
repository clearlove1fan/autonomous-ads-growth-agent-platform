from fastapi.testclient import TestClient

from ads_growth_agent import api as api_module
from ads_growth_agent.api import app as api_app
from ads_growth_agent.api import (
    get_runtime_advertiser_memory_store,
    get_runtime_outbox_store,
    get_runtime_performance_event_store,
    get_runtime_settings,
)
from ads_growth_agent.config import Settings
from ads_growth_agent.contracts import CampaignPerformanceEventDetailResponse
from ads_growth_agent.feedback import analyze_campaign_performance_event
from ads_growth_agent.persistence.advertiser_memory_store import (
    AdvertiserMemoryWriteResult,
)
from ads_growth_agent.persistence.outbox_store import OutboxEventRecord
from ads_growth_agent.persistence.performance_event_store import (
    PerformanceEventConflictError,
    hash_campaign_performance_event,
)


def test_campaign_performance_event_api_returns_feedback_analysis(monkeypatch) -> None:
    store = CapturingPerformanceEventStore()
    captured: dict[str, str] = {}

    def fake_build_configured_performance_event_store(
        settings: Settings,
    ) -> CapturingPerformanceEventStore:
        captured["tenant_id"] = settings.tenant_id
        return store

    monkeypatch.setattr(
        api_module,
        "build_configured_performance_event_store",
        fake_build_configured_performance_event_store,
    )
    api_app.dependency_overrides[get_runtime_settings] = lambda: Settings(
        performance_event_persistence_backend="postgres",
        tenant_id="process_default",
    )
    try:
        response = TestClient(api_app).post(
            "/campaign-events/performance",
            json=_event_payload(),
            headers={"X-Tenant-ID": "tenant_api"},
        )
    finally:
        api_app.dependency_overrides.clear()

    payload = response.json()
    assert response.status_code == 200
    assert response.headers["x-tenant-id"] == "tenant_api"
    assert response.headers["performance-event-id"] == "evt_perf_001"
    assert response.headers["feedback-id"].startswith("feedback_")
    assert response.headers["performance-event-status"] == "created"
    assert response.headers["advertiser-memory-status"] == "disabled"
    assert payload["persisted"] is True
    assert payload["advertiser_memory_persisted"] is False
    assert payload["advertiser_memory_queued"] is False
    assert payload["advertiser_memory_status"] == "disabled"
    assert payload["advertiser_memory_source_id"] is None
    assert payload["status"] == "analyzed"
    assert payload["analysis"]["health_status"] == "underperforming"
    assert payload["analysis"]["metrics_summary"]["cpa"] == "50.00"
    assert payload["analysis"]["recommendations"][0]["action_type"] == "adjust_budget"
    assert captured == {"tenant_id": "tenant_api"}
    assert store.records[0][0] == "evt_perf_001"
    assert store.records[0][1].startswith("feedback_")
    assert store.requested_event_ids == ["evt_perf_001"]


def test_campaign_performance_event_api_uses_strategy_context() -> None:
    api_app.dependency_overrides[get_runtime_settings] = lambda: Settings(
        performance_event_persistence_backend="none"
    )
    try:
        response = TestClient(api_app).post(
            "/campaign-events/performance",
            json=_event_payload_with_strategy_context(),
        )
    finally:
        api_app.dependency_overrides.clear()

    payload = response.json()
    assert response.status_code == 200
    assert payload["analysis"]["strategy_id"] == "strategy_001"
    assert payload["analysis"]["draft_id"] == "draft_fittrack"
    assert payload["analysis"]["matched_strategy_rules"][0]["rule_id"] == (
        "strategy_001:rule:cpa_guardrail"
    )
    assert payload["analysis"]["recommendations"][0]["params"]["strategy_id"] == (
        "strategy_001"
    )
    assert payload["analysis"]["recommendations"][0]["params"][
        "matched_strategy_rule_ids"
    ] == ["strategy_001:rule:cpa_guardrail"]


def test_campaign_performance_event_api_uses_noop_persistence_by_default() -> None:
    api_app.dependency_overrides[get_runtime_settings] = lambda: Settings(
        performance_event_persistence_backend="none"
    )
    api_app.dependency_overrides[
        get_runtime_performance_event_store
    ] = lambda: CapturingPerformanceEventStore()
    try:
        response = TestClient(api_app).post(
            "/campaign-events/performance",
            json=_event_payload(),
        )
    finally:
        api_app.dependency_overrides.clear()

    assert response.status_code == 200
    assert response.json()["persisted"] is False
    assert response.headers["performance-event-status"] == "created"
    assert response.headers["advertiser-memory-status"] == "disabled"


def test_campaign_performance_event_api_rejects_orphan_event() -> None:
    payload = _event_payload()
    payload.pop("run_id")

    response = TestClient(api_app).post("/campaign-events/performance", json=payload)

    assert response.status_code == 422


def test_campaign_performance_event_api_replays_existing_event() -> None:
    store = CapturingPerformanceEventStore()
    event = api_module.CampaignPerformanceEventRequest.model_validate(_event_payload())
    analysis = analyze_campaign_performance_event(event)
    store.detail = _event_detail(
        event,
        metadata={"event_hash": hash_campaign_performance_event(event)},
    )
    api_app.dependency_overrides[get_runtime_settings] = lambda: Settings(
        performance_event_persistence_backend="postgres"
    )
    api_app.dependency_overrides[get_runtime_performance_event_store] = lambda: store
    try:
        response = TestClient(api_app).post(
            "/campaign-events/performance",
            json=_event_payload(),
        )
    finally:
        api_app.dependency_overrides.clear()

    payload = response.json()
    assert response.status_code == 200
    assert response.headers["performance-event-status"] == "replayed"
    assert response.headers["feedback-id"] == analysis.feedback_id
    assert response.headers["advertiser-memory-status"] == "disabled"
    assert payload["analysis"]["feedback_id"] == analysis.feedback_id
    assert store.records == []
    assert store.requested_event_ids == ["evt_perf_001"]


def test_campaign_performance_event_api_records_advertiser_memory() -> None:
    event_store = CapturingPerformanceEventStore()
    memory_store = CapturingAdvertiserMemoryStore(
        AdvertiserMemoryWriteResult(
            persisted=True,
            status="recorded",
            source_id="memory:performance:test:v1",
            memory_type="historical_performance",
        )
    )
    api_app.dependency_overrides[get_runtime_settings] = lambda: Settings(
        performance_event_persistence_backend="postgres",
        advertiser_memory_persistence_backend="postgres",
    )
    api_app.dependency_overrides[get_runtime_performance_event_store] = (
        lambda: event_store
    )
    api_app.dependency_overrides[get_runtime_advertiser_memory_store] = (
        lambda: memory_store
    )
    try:
        response = TestClient(api_app).post(
            "/campaign-events/performance",
            json=_event_payload(),
        )
    finally:
        api_app.dependency_overrides.clear()

    payload = response.json()
    assert response.status_code == 200
    assert response.headers["advertiser-memory-status"] == "recorded"
    assert response.headers["advertiser-memory-source-id"] == "memory:performance:test:v1"
    assert payload["advertiser_memory_persisted"] is True
    assert payload["advertiser_memory_queued"] is False
    assert payload["advertiser_memory_status"] == "recorded"
    assert payload["advertiser_memory_source_id"] == "memory:performance:test:v1"
    assert memory_store.records == [("evt_perf_001", payload["analysis"]["feedback_id"])]


def test_campaign_performance_event_api_queues_advertiser_memory_when_outbox_enabled() -> None:
    event_store = CapturingPerformanceEventStore()
    outbox_store = CapturingOutboxStore()
    memory_store = CapturingAdvertiserMemoryStore(
        AdvertiserMemoryWriteResult(
            persisted=True,
            source_id="memory:performance:direct:v1",
            memory_type="historical_performance",
            status="recorded",
        )
    )
    api_app.dependency_overrides[get_runtime_settings] = lambda: Settings(
        performance_event_persistence_backend="postgres",
        advertiser_memory_persistence_backend="postgres",
        outbox_backend="postgres",
    )
    api_app.dependency_overrides[get_runtime_performance_event_store] = (
        lambda: event_store
    )
    api_app.dependency_overrides[get_runtime_advertiser_memory_store] = (
        lambda: memory_store
    )
    api_app.dependency_overrides[get_runtime_outbox_store] = lambda: outbox_store
    try:
        response = TestClient(api_app).post(
            "/campaign-events/performance",
            json=_event_payload(),
        )
    finally:
        api_app.dependency_overrides.clear()

    payload = response.json()
    assert response.status_code == 200
    assert response.headers["advertiser-memory-status"] == "queued"
    assert response.headers["advertiser-memory-source-id"].startswith("memory:performance:")
    assert payload["advertiser_memory_persisted"] is False
    assert payload["advertiser_memory_queued"] is True
    assert payload["advertiser_memory_status"] == "queued"
    assert payload["advertiser_memory_source_id"].startswith("memory:performance:")
    assert outbox_store.enqueued[0]["event_type"] == "campaign_performance_analyzed"
    assert outbox_store.enqueued[0]["payload"]["event"]["event_id"] == "evt_perf_001"
    assert memory_store.records == []


def test_campaign_performance_event_api_rejects_event_id_payload_conflict() -> None:
    store = CapturingPerformanceEventStore()
    event = api_module.CampaignPerformanceEventRequest.model_validate(_event_payload())
    store.detail = _event_detail(event, metadata={"event_hash": "different_hash"})
    api_app.dependency_overrides[get_runtime_settings] = lambda: Settings(
        performance_event_persistence_backend="postgres"
    )
    api_app.dependency_overrides[get_runtime_performance_event_store] = lambda: store
    try:
        response = TestClient(api_app).post(
            "/campaign-events/performance",
            json=_event_payload(),
        )
    finally:
        api_app.dependency_overrides.clear()

    assert response.status_code == 409
    assert response.json()["detail"]["error_code"] == "PERFORMANCE_EVENT_ID_CONFLICT"
    assert store.records == []
    assert store.requested_event_ids == ["evt_perf_001"]


def test_campaign_performance_event_api_maps_store_conflict_to_409() -> None:
    store = CapturingPerformanceEventStore(
        record_error=PerformanceEventConflictError("evt_perf_001")
    )
    api_app.dependency_overrides[get_runtime_settings] = lambda: Settings(
        performance_event_persistence_backend="postgres"
    )
    api_app.dependency_overrides[get_runtime_performance_event_store] = lambda: store
    try:
        response = TestClient(api_app).post(
            "/campaign-events/performance",
            json=_event_payload(),
        )
    finally:
        api_app.dependency_overrides.clear()

    assert response.status_code == 409
    assert response.json()["detail"] == {
        "message": "Performance event ID was already used with a different payload.",
        "error_code": "PERFORMANCE_EVENT_ID_CONFLICT",
        "event_id": "evt_perf_001",
    }


def test_get_campaign_performance_event_api_returns_tenant_scoped_detail(
    monkeypatch,
) -> None:
    store = CapturingPerformanceEventStore()
    event = api_module.CampaignPerformanceEventRequest.model_validate(_event_payload())
    store.detail = _event_detail(event)
    captured: dict[str, str] = {}

    def fake_build_configured_performance_event_store(
        settings: Settings,
    ) -> CapturingPerformanceEventStore:
        captured["tenant_id"] = settings.tenant_id
        return store

    monkeypatch.setattr(
        api_module,
        "build_configured_performance_event_store",
        fake_build_configured_performance_event_store,
    )
    api_app.dependency_overrides[get_runtime_settings] = lambda: Settings(
        performance_event_persistence_backend="postgres",
        tenant_id="process_default",
    )
    try:
        response = TestClient(api_app).get(
            f"/campaign-events/performance/{event.event_id}",
            headers={"X-Tenant-ID": "tenant_api"},
        )
    finally:
        api_app.dependency_overrides.clear()

    payload = response.json()
    assert response.status_code == 200
    assert response.headers["x-tenant-id"] == "tenant_api"
    assert captured == {"tenant_id": "tenant_api"}
    assert store.requested_event_ids == [event.event_id]
    assert payload["event_id"] == event.event_id
    assert payload["analysis"]["health_status"] == "underperforming"
    assert payload["metrics"]["spend"] == "1000.00"


def test_get_campaign_performance_event_api_returns_404_when_missing() -> None:
    store = CapturingPerformanceEventStore()
    api_app.dependency_overrides[get_runtime_settings] = lambda: Settings(
        performance_event_persistence_backend="postgres"
    )
    api_app.dependency_overrides[get_runtime_performance_event_store] = lambda: store
    try:
        response = TestClient(api_app).get("/campaign-events/performance/missing_event")
    finally:
        api_app.dependency_overrides.clear()

    assert response.status_code == 404
    assert response.json()["detail"]["error_code"] == "PERFORMANCE_EVENT_NOT_FOUND"
    assert store.requested_event_ids == ["missing_event"]


class CapturingPerformanceEventStore:
    def __init__(self, *, record_error: Exception | None = None) -> None:
        self.records: list[tuple[str, str]] = []
        self.detail: CampaignPerformanceEventDetailResponse | None = None
        self.requested_event_ids: list[str] = []
        self._record_error = record_error

    def record_analyzed(self, event, analysis) -> None:
        if self._record_error is not None:
            raise self._record_error
        self.records.append((event.event_id, analysis.feedback_id))

    def get_event(self, event_id: str) -> CampaignPerformanceEventDetailResponse | None:
        self.requested_event_ids.append(event_id)
        if self.detail is None or self.detail.event_id != event_id:
            return None
        return self.detail


class CapturingAdvertiserMemoryStore:
    def __init__(self, result: AdvertiserMemoryWriteResult) -> None:
        self.result = result
        self.records: list[tuple[str, str]] = []

    def record_feedback_memory(self, event, analysis) -> AdvertiserMemoryWriteResult:
        self.records.append((event.event_id, analysis.feedback_id))
        return self.result


class CapturingOutboxStore:
    def __init__(self) -> None:
        self.enqueued: list[dict] = []

    def enqueue(self, **kwargs) -> OutboxEventRecord:
        from datetime import UTC, datetime

        now = datetime.now(UTC)
        self.enqueued.append(kwargs)
        return OutboxEventRecord(
            outbox_event_id="outbox_api_test",
            event_type=kwargs["event_type"],
            aggregate_type=kwargs["aggregate_type"],
            aggregate_id=kwargs["aggregate_id"],
            idempotency_key=kwargs["idempotency_key"],
            status="pending",
            payload=kwargs["payload"],
            attempt_count=0,
            max_attempts=3,
            metadata=kwargs.get("metadata") or {},
            created_at=now,
            updated_at=now,
        )

    def claim_pending(self, *, limit: int, worker_id: str, lock_seconds: int = 60):
        return []

    def mark_completed(self, outbox_event_id: str, *, result=None):
        return None

    def mark_failed(self, outbox_event_id: str, *, error, retry_delay_seconds: int = 5):
        return None


def _event_detail(
    event,
    *,
    metadata: dict[str, object] | None = None,
) -> CampaignPerformanceEventDetailResponse:
    analysis = analyze_campaign_performance_event(event)
    return CampaignPerformanceEventDetailResponse(
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
        metadata=metadata or {"performance_event_persistence": "postgres"},
        analysis=analysis,
        created_at=analysis.created_at,
        updated_at=analysis.created_at,
    )


def _event_payload() -> dict[str, object]:
    return {
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


def _event_payload_with_strategy_context() -> dict[str, object]:
    payload = _event_payload()
    payload.pop("target_cpa")
    payload["draft_id"] = "draft_fittrack"
    payload["strategy_context"] = {
        "strategy_id": "strategy_001",
        "draft_id": "draft_fittrack",
        "target_cpa": "20.00",
        "optimization_rules": [
            {
                "rule_id": "strategy_001:rule:cpa_guardrail",
                "trigger_metric": "cost_per_result",
                "condition": "Observed CPA exceeds target by more than 20%.",
                "recommended_action": "Shift budget toward the best converting lane.",
                "owner_role": "budget_optimizer",
                "priority": 1,
                "rationale": "CPA is the primary efficiency guardrail.",
            },
            {
                "rule_id": "strategy_001:rule:creative_learning",
                "trigger_metric": "creative_cell_conversions",
                "condition": "One creative angle wins.",
                "recommended_action": "Generate close variants of the winning hook.",
                "owner_role": "creative_strategist",
                "priority": 2,
                "rationale": "Creative learning should happen before broad scaling.",
            },
        ],
    }
    return payload
