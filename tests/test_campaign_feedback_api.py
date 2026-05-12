from fastapi.testclient import TestClient

from ads_growth_agent import api as api_module
from ads_growth_agent.api import app as api_app
from ads_growth_agent.api import (
    get_runtime_performance_event_store,
    get_runtime_settings,
)
from ads_growth_agent.config import Settings
from ads_growth_agent.contracts import CampaignPerformanceEventDetailResponse
from ads_growth_agent.feedback import analyze_campaign_performance_event


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
    assert payload["persisted"] is True
    assert payload["status"] == "analyzed"
    assert payload["analysis"]["health_status"] == "underperforming"
    assert payload["analysis"]["metrics_summary"]["cpa"] == "50.00"
    assert payload["analysis"]["recommendations"][0]["action_type"] == "adjust_budget"
    assert captured == {"tenant_id": "tenant_api"}
    assert store.records[0][0] == "evt_perf_001"
    assert store.records[0][1].startswith("feedback_")


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


def test_campaign_performance_event_api_rejects_orphan_event() -> None:
    payload = _event_payload()
    payload.pop("run_id")

    response = TestClient(api_app).post("/campaign-events/performance", json=payload)

    assert response.status_code == 422


def test_get_campaign_performance_event_api_returns_tenant_scoped_detail(
    monkeypatch,
) -> None:
    store = CapturingPerformanceEventStore()
    event = api_module.CampaignPerformanceEventRequest.model_validate(_event_payload())
    analysis = analyze_campaign_performance_event(event)
    store.detail = CampaignPerformanceEventDetailResponse(
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
    def __init__(self) -> None:
        self.records: list[tuple[str, str]] = []
        self.detail: CampaignPerformanceEventDetailResponse | None = None
        self.requested_event_ids: list[str] = []

    def record_analyzed(self, event, analysis) -> None:
        self.records.append((event.event_id, analysis.feedback_id))

    def get_event(self, event_id: str) -> CampaignPerformanceEventDetailResponse | None:
        self.requested_event_ids.append(event_id)
        if self.detail is None or self.detail.event_id != event_id:
            return None
        return self.detail


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
