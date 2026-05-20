import json

from fastapi.testclient import TestClient
from typer.testing import CliRunner

from ads_growth_agent import api as api_module
from ads_growth_agent.api import app as api_app
from ads_growth_agent.api import (
    get_runtime_advertiser_memory_store,
    get_runtime_feedback_review_store,
    get_runtime_outbox_store,
    get_runtime_performance_event_store,
    get_runtime_settings,
)
from ads_growth_agent.cli import app as cli_app
from ads_growth_agent.config import Settings
from ads_growth_agent.contracts import (
    CampaignFeedbackOptimizationReviewListResponse,
    CampaignFeedbackOptimizationReviewRequest,
    CampaignFeedbackOptimizationReviewResponse,
    CampaignPerformanceEventDetailResponse,
    FeedbackOptimizationReviewDecision,
    PerformanceEventType,
)
from ads_growth_agent.feedback import (
    analyze_campaign_performance_event,
    build_campaign_feedback_optimization_draft,
    build_campaign_feedback_optimization_review,
)
from ads_growth_agent.persistence.advertiser_memory_store import (
    AdvertiserMemoryWriteResult,
)
from ads_growth_agent.persistence.outbox_store import OutboxEventRecord
from ads_growth_agent.persistence.performance_event_store import (
    PerformanceEventConflictError,
    hash_campaign_performance_event,
)

PerformanceEventListRequest = tuple[
    str | None,
    str | None,
    str | None,
    str | None,
    PerformanceEventType | None,
    int,
]
FeedbackReviewListRequest = tuple[
    str | None,
    str | None,
    str | None,
    FeedbackOptimizationReviewDecision | None,
    int,
]


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


def test_get_campaign_feedback_action_plan_api_returns_tenant_scoped_plan(
    monkeypatch,
) -> None:
    store = CapturingPerformanceEventStore()
    event = api_module.CampaignPerformanceEventRequest.model_validate(
        _event_payload_with_strategy_context()
    )
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
            f"/campaign-events/performance/{event.event_id}/action-plan",
            headers={"X-Tenant-ID": "tenant_api"},
        )
    finally:
        api_app.dependency_overrides.clear()

    payload = response.json()
    assert response.status_code == 200
    assert response.headers["x-tenant-id"] == "tenant_api"
    assert response.headers["feedback-id"] == store.detail.analysis.feedback_id
    assert captured == {"tenant_id": "tenant_api"}
    assert store.requested_event_ids == [event.event_id]
    assert payload["event_id"] == event.event_id
    assert payload["strategy_id"] == "strategy_001"
    assert payload["draft_id"] == "draft_fittrack"
    assert payload["health_status"] == "underperforming"
    assert payload["steps"][0]["action_type"] == "adjust_budget"
    assert payload["steps"][0]["owner_role"] == "budget_optimizer"
    assert payload["steps"][0]["tool_name"] == "optimize_budget"
    assert payload["steps"][0]["status"] == "draft_recommendation"
    assert payload["steps"][0]["matched_strategy_rule_ids"] == [
        "strategy_001:rule:cpa_guardrail"
    ]


def test_get_campaign_feedback_action_plan_api_returns_404_when_missing() -> None:
    store = CapturingPerformanceEventStore()
    api_app.dependency_overrides[get_runtime_settings] = lambda: Settings(
        performance_event_persistence_backend="postgres"
    )
    api_app.dependency_overrides[get_runtime_performance_event_store] = lambda: store
    try:
        response = TestClient(api_app).get(
            "/campaign-events/performance/missing_event/action-plan"
        )
    finally:
        api_app.dependency_overrides.clear()

    assert response.status_code == 404
    assert response.json()["detail"]["error_code"] == "PERFORMANCE_EVENT_NOT_FOUND"
    assert store.requested_event_ids == ["missing_event"]


def test_get_campaign_feedback_optimization_draft_api_returns_tenant_scoped_draft(
    monkeypatch,
) -> None:
    store = CapturingPerformanceEventStore()
    event = api_module.CampaignPerformanceEventRequest.model_validate(
        _event_payload_with_strategy_context()
    )
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
            f"/campaign-events/performance/{event.event_id}/optimization-draft",
            headers={"X-Tenant-ID": "tenant_api"},
        )
    finally:
        api_app.dependency_overrides.clear()

    payload = response.json()
    assert response.status_code == 200
    assert response.headers["x-tenant-id"] == "tenant_api"
    assert response.headers["feedback-id"] == store.detail.analysis.feedback_id
    assert response.headers["optimization-draft-id"].startswith("optimization_draft_")
    assert captured == {"tenant_id": "tenant_api"}
    assert store.requested_event_ids == [event.event_id]
    assert payload["event_id"] == event.event_id
    assert payload["base_draft_id"] == "draft_fittrack"
    assert payload["strategy_id"] == "strategy_001"
    assert payload["status"] == "draft"
    assert payload["requires_human_approval"] is True
    assert payload["changes"][0]["change_type"] == "budget"
    assert payload["changes"][0]["status"] == "draft_change"
    assert payload["changes"][0]["params"]["budget_guardrail"].startswith("Do not")


def test_get_campaign_feedback_optimization_draft_api_returns_404_when_missing() -> None:
    store = CapturingPerformanceEventStore()
    api_app.dependency_overrides[get_runtime_settings] = lambda: Settings(
        performance_event_persistence_backend="postgres"
    )
    api_app.dependency_overrides[get_runtime_performance_event_store] = lambda: store
    try:
        response = TestClient(api_app).get(
            "/campaign-events/performance/missing_event/optimization-draft"
        )
    finally:
        api_app.dependency_overrides.clear()

    assert response.status_code == 404
    assert response.json()["detail"]["error_code"] == "PERFORMANCE_EVENT_NOT_FOUND"
    assert store.requested_event_ids == ["missing_event"]


def test_submit_campaign_feedback_optimization_review_api_records_review() -> None:
    event = api_module.CampaignPerformanceEventRequest.model_validate(
        _event_payload_with_strategy_context()
    )
    detail = _event_detail(event)
    event_store = CapturingPerformanceEventStore(details=[detail])
    review_store = CapturingFeedbackOptimizationReviewStore()
    optimization_draft = build_campaign_feedback_optimization_draft(detail)

    api_app.dependency_overrides[get_runtime_settings] = lambda: Settings(
        performance_event_persistence_backend="postgres",
        feedback_review_persistence_backend="postgres",
    )
    api_app.dependency_overrides[get_runtime_performance_event_store] = lambda: event_store
    api_app.dependency_overrides[get_runtime_feedback_review_store] = lambda: review_store
    try:
        response = TestClient(api_app).post(
            f"/campaign-events/performance/{event.event_id}/optimization-draft/reviews",
            json={
                "decision": "approved",
                "reviewer_id": "operator_001",
                "notes": "Approve budget and creative changes.",
                "selected_change_ids": [optimization_draft.changes[0].change_id],
            },
            headers={"X-Tenant-ID": "tenant_api"},
        )
    finally:
        api_app.dependency_overrides.clear()

    payload = response.json()
    assert response.status_code == 201
    assert response.headers["x-tenant-id"] == "tenant_api"
    assert response.headers["feedback-review-id"].startswith("feedback_review_")
    assert response.headers["optimization-draft-id"] == optimization_draft.optimization_draft_id
    assert payload["decision"] == "approved"
    assert payload["reviewer_id"] == "operator_001"
    assert payload["selected_change_ids"] == [optimization_draft.changes[0].change_id]
    assert payload["optimization_draft"]["event_id"] == event.event_id
    assert event_store.requested_event_ids == [event.event_id]
    assert review_store.recorded_requests[0].decision == (
        FeedbackOptimizationReviewDecision.APPROVED
    )


def test_submit_campaign_feedback_optimization_review_api_requires_persistence() -> None:
    event = api_module.CampaignPerformanceEventRequest.model_validate(_event_payload())
    detail = _event_detail(event)
    api_app.dependency_overrides[get_runtime_settings] = lambda: Settings(
        feedback_review_persistence_backend="none"
    )
    api_app.dependency_overrides[
        get_runtime_performance_event_store
    ] = lambda: CapturingPerformanceEventStore(details=[detail])
    try:
        response = TestClient(api_app).post(
            f"/campaign-events/performance/{event.event_id}/optimization-draft/reviews",
            json={"decision": "approved", "reviewer_id": "operator_001"},
        )
    finally:
        api_app.dependency_overrides.clear()

    assert response.status_code == 503
    assert response.json()["detail"]["error_code"] == "FEEDBACK_REVIEW_PERSISTENCE_DISABLED"


def test_submit_campaign_feedback_optimization_review_api_rejects_unknown_change_id() -> None:
    event = api_module.CampaignPerformanceEventRequest.model_validate(_event_payload())
    detail = _event_detail(event)
    api_app.dependency_overrides[get_runtime_settings] = lambda: Settings(
        performance_event_persistence_backend="postgres",
        feedback_review_persistence_backend="postgres",
    )
    api_app.dependency_overrides[
        get_runtime_performance_event_store
    ] = lambda: CapturingPerformanceEventStore(details=[detail])
    api_app.dependency_overrides[
        get_runtime_feedback_review_store
    ] = lambda: CapturingFeedbackOptimizationReviewStore()
    try:
        response = TestClient(api_app).post(
            f"/campaign-events/performance/{event.event_id}/optimization-draft/reviews",
            json={
                "decision": "needs_revision",
                "reviewer_id": "operator_001",
                "selected_change_ids": ["unknown_change"],
            },
        )
    finally:
        api_app.dependency_overrides.clear()

    assert response.status_code == 422
    assert response.json()["detail"]["error_code"] == "FEEDBACK_OPTIMIZATION_REVIEW_INVALID"


def test_get_and_list_feedback_optimization_review_api_returns_persisted_reviews() -> None:
    event = api_module.CampaignPerformanceEventRequest.model_validate(
        _event_payload_with_strategy_context()
    )
    detail = _event_detail(event)
    optimization_draft = build_campaign_feedback_optimization_draft(detail)
    review = build_campaign_feedback_optimization_review(
        optimization_draft,
        CampaignFeedbackOptimizationReviewRequest(
            decision=FeedbackOptimizationReviewDecision.REJECTED,
            reviewer_id="operator_001",
            notes="Revise creative before budget shift.",
        ),
        review_id="feedback_review_api_001",
    )
    review_store = CapturingFeedbackOptimizationReviewStore(reviews=[review])
    api_app.dependency_overrides[get_runtime_settings] = lambda: Settings(
        feedback_review_persistence_backend="postgres"
    )
    api_app.dependency_overrides[get_runtime_feedback_review_store] = lambda: review_store
    try:
        get_response = TestClient(api_app).get(
            f"/feedback-optimization-reviews/{review.review_id}"
        )
        list_response = TestClient(api_app).get(
            "/feedback-optimization-reviews",
            params={
                "event_id": event.event_id,
                "advertiser_id": event.advertiser_id,
                "optimization_draft_id": optimization_draft.optimization_draft_id,
                "decision": "rejected",
                "limit": "5",
            },
        )
    finally:
        api_app.dependency_overrides.clear()

    assert get_response.status_code == 200
    assert get_response.headers["feedback-review-id"] == review.review_id
    assert get_response.json()["decision"] == "rejected"
    assert list_response.status_code == 200
    list_payload = list_response.json()
    assert list_payload["count"] == 1
    assert list_payload["limit"] == 5
    assert list_payload["event_id"] == event.event_id
    assert list_payload["decision"] == "rejected"
    assert list_payload["items"][0]["review_id"] == review.review_id
    assert review_store.list_requests == [
        (
            event.event_id,
            event.advertiser_id,
            optimization_draft.optimization_draft_id,
            FeedbackOptimizationReviewDecision.REJECTED,
            5,
        )
    ]


def test_get_feedback_optimization_review_api_returns_404_when_missing() -> None:
    api_app.dependency_overrides[get_runtime_settings] = lambda: Settings(
        feedback_review_persistence_backend="postgres"
    )
    api_app.dependency_overrides[
        get_runtime_feedback_review_store
    ] = lambda: CapturingFeedbackOptimizationReviewStore()
    try:
        response = TestClient(api_app).get(
            "/feedback-optimization-reviews/feedback_review_missing"
        )
    finally:
        api_app.dependency_overrides.clear()

    assert response.status_code == 404
    assert response.json()["detail"]["error_code"] == "FEEDBACK_OPTIMIZATION_REVIEW_NOT_FOUND"


def test_get_feedback_execution_plan_api_returns_dry_run_plan() -> None:
    event = api_module.CampaignPerformanceEventRequest.model_validate(
        _event_payload_with_strategy_context()
    )
    detail = _event_detail(event)
    optimization_draft = build_campaign_feedback_optimization_draft(detail)
    review = build_campaign_feedback_optimization_review(
        optimization_draft,
        CampaignFeedbackOptimizationReviewRequest(
            decision=FeedbackOptimizationReviewDecision.APPROVED,
            reviewer_id="operator_001",
            selected_change_ids=[optimization_draft.changes[0].change_id],
        ),
        review_id="feedback_review_execution_api_001",
    )
    review_store = CapturingFeedbackOptimizationReviewStore(reviews=[review])
    api_app.dependency_overrides[get_runtime_settings] = lambda: Settings(
        feedback_review_persistence_backend="postgres"
    )
    api_app.dependency_overrides[get_runtime_feedback_review_store] = lambda: review_store
    try:
        response = TestClient(api_app).get(
            f"/feedback-optimization-reviews/{review.review_id}/execution-plan",
            headers={"X-Tenant-ID": "tenant_api"},
        )
    finally:
        api_app.dependency_overrides.clear()

    payload = response.json()
    assert response.status_code == 200
    assert response.headers["x-tenant-id"] == "tenant_api"
    assert response.headers["feedback-review-id"] == review.review_id
    assert response.headers["feedback-execution-plan-id"].startswith(
        "feedback_execution_plan_"
    )
    assert payload["review_id"] == review.review_id
    assert payload["execution_mode"] == "dry_run"
    assert payload["steps"][0]["tool_intent"]["tool_name"] == "draft_budget_reallocation"
    assert payload["steps"][0]["tool_intent"]["params"]["dry_run"] is True


def test_dry_run_feedback_execution_plan_api_validates_draft_tools() -> None:
    event = api_module.CampaignPerformanceEventRequest.model_validate(
        _event_payload_with_strategy_context()
    )
    detail = _event_detail(event)
    optimization_draft = build_campaign_feedback_optimization_draft(detail)
    review = build_campaign_feedback_optimization_review(
        optimization_draft,
        CampaignFeedbackOptimizationReviewRequest(
            decision=FeedbackOptimizationReviewDecision.APPROVED,
            reviewer_id="operator_001",
            selected_change_ids=[optimization_draft.changes[0].change_id],
        ),
        review_id="feedback_review_dry_run_api_001",
    )
    review_store = CapturingFeedbackOptimizationReviewStore(reviews=[review])
    api_app.dependency_overrides[get_runtime_settings] = lambda: Settings(
        feedback_review_persistence_backend="postgres"
    )
    api_app.dependency_overrides[get_runtime_feedback_review_store] = lambda: review_store
    try:
        response = TestClient(api_app).post(
            f"/feedback-optimization-reviews/{review.review_id}/execution-plan/dry-run",
            headers={"X-Tenant-ID": "tenant_api"},
        )
    finally:
        api_app.dependency_overrides.clear()

    payload = response.json()
    assert response.status_code == 200
    assert response.headers["feedback-dry-run-id"].startswith("feedback_dry_run_")
    assert payload["status"] == "passed"
    assert payload["validated_step_count"] == 1
    assert payload["blocked_step_count"] == 0
    assert payload["step_results"][0]["tool_result"]["success"] is True
    assert payload["step_results"][0]["tool_result"]["payload"]["mutation_performed"] is False


def test_get_feedback_execution_plan_api_rejects_non_approved_review() -> None:
    event = api_module.CampaignPerformanceEventRequest.model_validate(
        _event_payload_with_strategy_context()
    )
    detail = _event_detail(event)
    optimization_draft = build_campaign_feedback_optimization_draft(detail)
    review = build_campaign_feedback_optimization_review(
        optimization_draft,
        CampaignFeedbackOptimizationReviewRequest(
            decision=FeedbackOptimizationReviewDecision.NEEDS_REVISION,
            reviewer_id="operator_001",
        ),
        review_id="feedback_review_execution_api_blocked",
    )
    review_store = CapturingFeedbackOptimizationReviewStore(reviews=[review])
    api_app.dependency_overrides[get_runtime_settings] = lambda: Settings(
        feedback_review_persistence_backend="postgres"
    )
    api_app.dependency_overrides[get_runtime_feedback_review_store] = lambda: review_store
    try:
        response = TestClient(api_app).get(
            f"/feedback-optimization-reviews/{review.review_id}/execution-plan"
        )
    finally:
        api_app.dependency_overrides.clear()

    assert response.status_code == 409
    assert response.json()["detail"]["error_code"] == "FEEDBACK_EXECUTION_PLAN_NOT_APPROVED"
    assert response.json()["detail"]["decision"] == "needs_revision"


def test_list_campaign_performance_events_api_filters_recent_events(
    monkeypatch,
) -> None:
    event = api_module.CampaignPerformanceEventRequest.model_validate(
        _event_payload_with_strategy_context()
    )
    detail = _event_detail(event)
    store = CapturingPerformanceEventStore(details=[detail])
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
            "/campaign-events/performance",
            params={
                "advertiser_id": event.advertiser_id,
                "run_id": event.run_id,
                "campaign_id": event.campaign_id,
                "draft_id": event.draft_id,
                "event_type": "performance_snapshot",
                "limit": "5",
            },
            headers={"X-Tenant-ID": "tenant_api"},
        )
    finally:
        api_app.dependency_overrides.clear()

    payload = response.json()
    assert response.status_code == 200
    assert response.headers["x-tenant-id"] == "tenant_api"
    assert captured == {"tenant_id": "tenant_api"}
    assert payload["count"] == 1
    assert payload["limit"] == 5
    assert payload["advertiser_id"] == event.advertiser_id
    assert payload["run_id"] == event.run_id
    assert payload["draft_id"] == event.draft_id
    assert payload["event_type"] == "performance_snapshot"
    assert payload["items"][0]["event_id"] == event.event_id
    assert store.list_requests == [
        (
            event.advertiser_id,
            event.run_id,
            event.campaign_id,
            event.draft_id,
            PerformanceEventType.PERFORMANCE_SNAPSHOT,
            5,
        )
    ]


def test_get_performance_event_cli_returns_detail(monkeypatch) -> None:
    event = api_module.CampaignPerformanceEventRequest.model_validate(_event_payload())
    detail = _event_detail(event)
    store = CapturingPerformanceEventStore(details=[detail])

    monkeypatch.setattr(
        "ads_growth_agent.cli.get_settings",
        lambda: Settings(tenant_id="tenant_cli"),
    )
    monkeypatch.setattr(
        "ads_growth_agent.cli.build_configured_performance_event_store",
        lambda settings: store,
    )

    result = CliRunner().invoke(cli_app, ["get-performance-event", event.event_id])

    assert result.exit_code == 0
    payload = result.stdout
    assert event.event_id in payload
    assert "underperforming" in payload


def test_get_performance_event_cli_reports_missing_event(monkeypatch) -> None:
    monkeypatch.setattr(
        "ads_growth_agent.cli.get_settings",
        lambda: Settings(tenant_id="tenant_cli"),
    )
    monkeypatch.setattr(
        "ads_growth_agent.cli.build_configured_performance_event_store",
        lambda settings: CapturingPerformanceEventStore(),
    )

    result = CliRunner().invoke(cli_app, ["get-performance-event", "missing_event"])

    assert result.exit_code == 1
    assert "Performance event not found: missing_event" in result.stderr


def test_get_feedback_action_plan_cli_returns_plan(monkeypatch) -> None:
    event = api_module.CampaignPerformanceEventRequest.model_validate(
        _event_payload_with_strategy_context()
    )
    detail = _event_detail(event)
    store = CapturingPerformanceEventStore(details=[detail])

    monkeypatch.setattr(
        "ads_growth_agent.cli.get_settings",
        lambda: Settings(tenant_id="tenant_cli"),
    )
    monkeypatch.setattr(
        "ads_growth_agent.cli.build_configured_performance_event_store",
        lambda settings: store,
    )

    result = CliRunner().invoke(cli_app, ["get-feedback-action-plan", event.event_id])

    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["event_id"] == event.event_id
    assert payload["strategy_id"] == "strategy_001"
    assert payload["steps"][0]["action_type"] == "adjust_budget"
    assert payload["steps"][0]["requires_human_approval"] is True


def test_get_feedback_action_plan_cli_reports_missing_event(monkeypatch) -> None:
    monkeypatch.setattr(
        "ads_growth_agent.cli.get_settings",
        lambda: Settings(tenant_id="tenant_cli"),
    )
    monkeypatch.setattr(
        "ads_growth_agent.cli.build_configured_performance_event_store",
        lambda settings: CapturingPerformanceEventStore(),
    )

    result = CliRunner().invoke(cli_app, ["get-feedback-action-plan", "missing_event"])

    assert result.exit_code == 1
    assert "Performance event not found: missing_event" in result.stderr


def test_get_feedback_optimization_draft_cli_returns_draft(monkeypatch) -> None:
    event = api_module.CampaignPerformanceEventRequest.model_validate(
        _event_payload_with_strategy_context()
    )
    detail = _event_detail(event)
    store = CapturingPerformanceEventStore(details=[detail])

    monkeypatch.setattr(
        "ads_growth_agent.cli.get_settings",
        lambda: Settings(tenant_id="tenant_cli"),
    )
    monkeypatch.setattr(
        "ads_growth_agent.cli.build_configured_performance_event_store",
        lambda settings: store,
    )

    result = CliRunner().invoke(
        cli_app,
        ["get-feedback-optimization-draft", event.event_id],
    )

    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["event_id"] == event.event_id
    assert payload["status"] == "draft"
    assert payload["changes"][0]["change_type"] == "budget"
    assert payload["changes"][0]["requires_human_approval"] is True


def test_get_feedback_optimization_draft_cli_reports_missing_event(monkeypatch) -> None:
    monkeypatch.setattr(
        "ads_growth_agent.cli.get_settings",
        lambda: Settings(tenant_id="tenant_cli"),
    )
    monkeypatch.setattr(
        "ads_growth_agent.cli.build_configured_performance_event_store",
        lambda settings: CapturingPerformanceEventStore(),
    )

    result = CliRunner().invoke(
        cli_app,
        ["get-feedback-optimization-draft", "missing_event"],
    )

    assert result.exit_code == 1
    assert "Performance event not found: missing_event" in result.stderr


def test_submit_feedback_optimization_review_cli_records_review(monkeypatch) -> None:
    event = api_module.CampaignPerformanceEventRequest.model_validate(
        _event_payload_with_strategy_context()
    )
    detail = _event_detail(event)
    event_store = CapturingPerformanceEventStore(details=[detail])
    review_store = CapturingFeedbackOptimizationReviewStore()
    optimization_draft = build_campaign_feedback_optimization_draft(detail)

    monkeypatch.setattr(
        "ads_growth_agent.cli.get_settings",
        lambda: Settings(
            tenant_id="tenant_cli",
            feedback_review_persistence_backend="postgres",
        ),
    )
    monkeypatch.setattr(
        "ads_growth_agent.cli.build_configured_performance_event_store",
        lambda settings: event_store,
    )
    monkeypatch.setattr(
        "ads_growth_agent.cli.build_configured_feedback_review_store",
        lambda settings: review_store,
    )

    result = CliRunner().invoke(
        cli_app,
        [
            "submit-feedback-optimization-review",
            event.event_id,
            "--decision",
            "approved",
            "--reviewer-id",
            "operator_001",
            "--notes",
            "Approve first change.",
            "--selected-change-id",
            optimization_draft.changes[0].change_id,
        ],
    )

    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["event_id"] == event.event_id
    assert payload["decision"] == "approved"
    assert payload["selected_change_ids"] == [optimization_draft.changes[0].change_id]
    assert review_store.recorded_requests[0].reviewer_id == "operator_001"


def test_submit_feedback_optimization_review_cli_requires_persistence(monkeypatch) -> None:
    monkeypatch.setattr(
        "ads_growth_agent.cli.get_settings",
        lambda: Settings(feedback_review_persistence_backend="none"),
    )

    result = CliRunner().invoke(
        cli_app,
        [
            "submit-feedback-optimization-review",
            "evt_perf_001",
            "--decision",
            "approved",
            "--reviewer-id",
            "operator_001",
        ],
    )

    assert result.exit_code == 2
    assert "Feedback optimization review persistence is disabled." in result.stderr


def test_get_and_list_feedback_optimization_review_cli_returns_reviews(monkeypatch) -> None:
    event = api_module.CampaignPerformanceEventRequest.model_validate(
        _event_payload_with_strategy_context()
    )
    detail = _event_detail(event)
    optimization_draft = build_campaign_feedback_optimization_draft(detail)
    review = build_campaign_feedback_optimization_review(
        optimization_draft,
        CampaignFeedbackOptimizationReviewRequest(
            decision=FeedbackOptimizationReviewDecision.NEEDS_REVISION,
            reviewer_id="operator_001",
        ),
        review_id="feedback_review_cli_001",
    )
    review_store = CapturingFeedbackOptimizationReviewStore(reviews=[review])

    monkeypatch.setattr(
        "ads_growth_agent.cli.get_settings",
        lambda: Settings(
            tenant_id="tenant_cli",
            feedback_review_persistence_backend="postgres",
        ),
    )
    monkeypatch.setattr(
        "ads_growth_agent.cli.build_configured_feedback_review_store",
        lambda settings: review_store,
    )

    get_result = CliRunner().invoke(
        cli_app,
        ["get-feedback-optimization-review", review.review_id],
    )
    list_result = CliRunner().invoke(
        cli_app,
        [
            "list-feedback-optimization-reviews",
            "--event-id",
            event.event_id,
            "--advertiser-id",
            event.advertiser_id,
            "--optimization-draft-id",
            optimization_draft.optimization_draft_id,
            "--decision",
            "needs_revision",
            "--limit",
            "5",
        ],
    )

    assert get_result.exit_code == 0
    assert json.loads(get_result.stdout)["review_id"] == review.review_id
    assert list_result.exit_code == 0
    list_payload = json.loads(list_result.stdout)
    assert list_payload["count"] == 1
    assert list_payload["items"][0]["decision"] == "needs_revision"


def test_get_feedback_execution_plan_cli_returns_dry_run_plan(monkeypatch) -> None:
    event = api_module.CampaignPerformanceEventRequest.model_validate(
        _event_payload_with_strategy_context()
    )
    detail = _event_detail(event)
    optimization_draft = build_campaign_feedback_optimization_draft(detail)
    review = build_campaign_feedback_optimization_review(
        optimization_draft,
        CampaignFeedbackOptimizationReviewRequest(
            decision=FeedbackOptimizationReviewDecision.APPROVED,
            reviewer_id="operator_001",
            selected_change_ids=[optimization_draft.changes[0].change_id],
        ),
        review_id="feedback_review_execution_cli_001",
    )
    review_store = CapturingFeedbackOptimizationReviewStore(reviews=[review])

    monkeypatch.setattr(
        "ads_growth_agent.cli.get_settings",
        lambda: Settings(
            tenant_id="tenant_cli",
            feedback_review_persistence_backend="postgres",
        ),
    )
    monkeypatch.setattr(
        "ads_growth_agent.cli.build_configured_feedback_review_store",
        lambda settings: review_store,
    )

    result = CliRunner().invoke(
        cli_app,
        ["get-feedback-execution-plan", review.review_id],
    )

    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["review_id"] == review.review_id
    assert payload["execution_mode"] == "dry_run"
    assert payload["steps"][0]["tool_intent"]["tool_name"] == "draft_budget_reallocation"


def test_dry_run_feedback_execution_plan_cli_validates_draft_tools(monkeypatch) -> None:
    event = api_module.CampaignPerformanceEventRequest.model_validate(
        _event_payload_with_strategy_context()
    )
    detail = _event_detail(event)
    optimization_draft = build_campaign_feedback_optimization_draft(detail)
    review = build_campaign_feedback_optimization_review(
        optimization_draft,
        CampaignFeedbackOptimizationReviewRequest(
            decision=FeedbackOptimizationReviewDecision.APPROVED,
            reviewer_id="operator_001",
            selected_change_ids=[optimization_draft.changes[0].change_id],
        ),
        review_id="feedback_review_dry_run_cli_001",
    )
    review_store = CapturingFeedbackOptimizationReviewStore(reviews=[review])

    monkeypatch.setattr(
        "ads_growth_agent.cli.get_settings",
        lambda: Settings(feedback_review_persistence_backend="postgres"),
    )
    monkeypatch.setattr(
        "ads_growth_agent.cli.build_configured_feedback_review_store",
        lambda settings: review_store,
    )

    result = CliRunner().invoke(
        cli_app,
        ["dry-run-feedback-execution-plan", review.review_id],
    )

    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["status"] == "passed"
    assert payload["step_results"][0]["tool_result"]["success"] is True
    assert payload["step_results"][0]["tool_result"]["payload"]["dry_run"] is True


def test_get_feedback_execution_plan_cli_rejects_non_approved_review(monkeypatch) -> None:
    event = api_module.CampaignPerformanceEventRequest.model_validate(
        _event_payload_with_strategy_context()
    )
    detail = _event_detail(event)
    optimization_draft = build_campaign_feedback_optimization_draft(detail)
    review = build_campaign_feedback_optimization_review(
        optimization_draft,
        CampaignFeedbackOptimizationReviewRequest(
            decision=FeedbackOptimizationReviewDecision.REJECTED,
            reviewer_id="operator_001",
        ),
        review_id="feedback_review_execution_cli_blocked",
    )
    review_store = CapturingFeedbackOptimizationReviewStore(reviews=[review])

    monkeypatch.setattr(
        "ads_growth_agent.cli.get_settings",
        lambda: Settings(feedback_review_persistence_backend="postgres"),
    )
    monkeypatch.setattr(
        "ads_growth_agent.cli.build_configured_feedback_review_store",
        lambda settings: review_store,
    )

    result = CliRunner().invoke(
        cli_app,
        ["get-feedback-execution-plan", review.review_id],
    )

    assert result.exit_code == 1
    assert "must be approved" in result.stderr


def test_list_feedback_optimization_reviews_cli_rejects_invalid_decision(monkeypatch) -> None:
    monkeypatch.setattr(
        "ads_growth_agent.cli.get_settings",
        lambda: Settings(feedback_review_persistence_backend="postgres"),
    )

    result = CliRunner().invoke(
        cli_app,
        ["list-feedback-optimization-reviews", "--decision", "unknown"],
    )

    assert result.exit_code == 2
    assert "Invalid feedback review decision" in result.stderr


def test_list_performance_events_cli_filters_recent_events(monkeypatch) -> None:
    event = api_module.CampaignPerformanceEventRequest.model_validate(
        _event_payload_with_strategy_context()
    )
    detail = _event_detail(event)
    store = CapturingPerformanceEventStore(details=[detail])

    monkeypatch.setattr(
        "ads_growth_agent.cli.get_settings",
        lambda: Settings(tenant_id="tenant_cli"),
    )
    monkeypatch.setattr(
        "ads_growth_agent.cli.build_configured_performance_event_store",
        lambda settings: store,
    )

    result = CliRunner().invoke(
        cli_app,
        [
            "list-performance-events",
            "--advertiser-id",
            event.advertiser_id,
            "--run-id",
            event.run_id,
            "--campaign-id",
            event.campaign_id,
            "--draft-id",
            event.draft_id,
            "--event-type",
            "performance_snapshot",
            "--limit",
            "5",
        ],
    )

    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["count"] == 1
    assert payload["items"][0]["event_id"] == event.event_id
    assert payload["event_type"] == "performance_snapshot"
    assert store.list_requests == [
        (
            event.advertiser_id,
            event.run_id,
            event.campaign_id,
            event.draft_id,
            PerformanceEventType.PERFORMANCE_SNAPSHOT,
            5,
        )
    ]


def test_list_performance_events_cli_rejects_invalid_event_type(monkeypatch) -> None:
    monkeypatch.setattr(
        "ads_growth_agent.cli.get_settings",
        lambda: Settings(tenant_id="tenant_cli"),
    )

    result = CliRunner().invoke(
        cli_app,
        [
            "list-performance-events",
            "--event-type",
            "unknown",
        ],
    )

    assert result.exit_code == 2
    assert "Invalid performance event type" in result.stderr


class CapturingPerformanceEventStore:
    def __init__(
        self,
        *,
        record_error: Exception | None = None,
        details: list[CampaignPerformanceEventDetailResponse] | None = None,
    ) -> None:
        self.records: list[tuple[str, str]] = []
        self.details = details or []
        self.detail: CampaignPerformanceEventDetailResponse | None = (
            self.details[0] if len(self.details) == 1 else None
        )
        self.requested_event_ids: list[str] = []
        self.list_requests: list[PerformanceEventListRequest] = []
        self._record_error = record_error

    def record_analyzed(self, event, analysis) -> None:
        if self._record_error is not None:
            raise self._record_error
        self.records.append((event.event_id, analysis.feedback_id))

    def get_event(self, event_id: str) -> CampaignPerformanceEventDetailResponse | None:
        self.requested_event_ids.append(event_id)
        details = self.details or ([self.detail] if self.detail is not None else [])
        for detail in details:
            if detail.event_id == event_id:
                return detail
        return None

    def list_events(
        self,
        *,
        advertiser_id: str | None = None,
        run_id: str | None = None,
        campaign_id: str | None = None,
        draft_id: str | None = None,
        event_type: PerformanceEventType | None = None,
        limit: int = 50,
    ) -> list[CampaignPerformanceEventDetailResponse]:
        self.list_requests.append(
            (advertiser_id, run_id, campaign_id, draft_id, event_type, limit)
        )
        details = self.details or ([self.detail] if self.detail is not None else [])
        return [
            detail
            for detail in details
            if (advertiser_id is None or detail.advertiser_id == advertiser_id)
            and (run_id is None or detail.run_id == run_id)
            and (campaign_id is None or detail.campaign_id == campaign_id)
            and (draft_id is None or detail.draft_id == draft_id)
            and (event_type is None or detail.event_type == event_type)
        ][:limit]


class CapturingFeedbackOptimizationReviewStore:
    def __init__(
        self,
        reviews: list[CampaignFeedbackOptimizationReviewResponse] | None = None,
    ) -> None:
        self.reviews = reviews or []
        self.recorded_requests: list[CampaignFeedbackOptimizationReviewRequest] = []
        self.recorded_draft_ids: list[str] = []
        self.requested_review_ids: list[str] = []
        self.list_requests: list[FeedbackReviewListRequest] = []

    def record_review(
        self,
        optimization_draft,
        request: CampaignFeedbackOptimizationReviewRequest,
    ) -> CampaignFeedbackOptimizationReviewResponse:
        self.recorded_requests.append(request)
        self.recorded_draft_ids.append(optimization_draft.optimization_draft_id)
        review = build_campaign_feedback_optimization_review(optimization_draft, request)
        self.reviews.append(review)
        return review

    def get_review(self, review_id: str) -> CampaignFeedbackOptimizationReviewResponse | None:
        self.requested_review_ids.append(review_id)
        for review in self.reviews:
            if review.review_id == review_id:
                return review
        return None

    def list_reviews(
        self,
        *,
        event_id: str | None = None,
        advertiser_id: str | None = None,
        optimization_draft_id: str | None = None,
        decision: FeedbackOptimizationReviewDecision | None = None,
        limit: int = 50,
    ) -> CampaignFeedbackOptimizationReviewListResponse:
        self.list_requests.append(
            (event_id, advertiser_id, optimization_draft_id, decision, limit)
        )
        items = [
            review
            for review in self.reviews
            if (event_id is None or review.event_id == event_id)
            and (advertiser_id is None or review.advertiser_id == advertiser_id)
            and (
                optimization_draft_id is None
                or review.optimization_draft_id == optimization_draft_id
            )
            and (decision is None or review.decision == decision)
        ][:limit]
        return CampaignFeedbackOptimizationReviewListResponse(
            items=items,
            count=len(items),
            limit=limit,
            event_id=event_id,
            advertiser_id=advertiser_id,
            optimization_draft_id=optimization_draft_id,
            decision=decision,
        )


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
    payload["campaign_id"] = "cmp_fittrack"
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
