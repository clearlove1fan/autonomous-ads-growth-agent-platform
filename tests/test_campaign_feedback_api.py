import json

from fastapi.testclient import TestClient
from typer.testing import CliRunner

from ads_growth_agent import api as api_module
from ads_growth_agent.api import app as api_app
from ads_growth_agent.api import (
    get_runtime_advertiser_memory_store,
    get_runtime_feedback_execution_store,
    get_runtime_feedback_handoff_store,
    get_runtime_feedback_review_store,
    get_runtime_outbox_store,
    get_runtime_performance_event_store,
    get_runtime_settings,
)
from ads_growth_agent.cli import app as cli_app
from ads_growth_agent.config import Settings
from ads_growth_agent.contracts import (
    CampaignFeedbackExecutionDryRunListResponse,
    CampaignFeedbackExecutionDryRunResponse,
    CampaignFeedbackHandoffRecordListResponse,
    CampaignFeedbackHandoffRecordRequest,
    CampaignFeedbackHandoffRecordResponse,
    CampaignFeedbackOptimizationReviewListResponse,
    CampaignFeedbackOptimizationReviewRequest,
    CampaignFeedbackOptimizationReviewResponse,
    CampaignPerformanceEventDetailResponse,
    FeedbackHandoffOutcome,
    FeedbackOptimizationReviewDecision,
    PerformanceEventType,
)
from ads_growth_agent.feedback import (
    analyze_campaign_performance_event,
    build_campaign_feedback_optimization_draft,
    build_campaign_feedback_optimization_review,
    build_campaign_feedback_revision_reviewable_draft,
)
from ads_growth_agent.feedback_handoff_record import build_feedback_handoff_record
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
FeedbackExecutionDryRunListRequest = tuple[
    str | None,
    str | None,
    str | None,
    str | None,
    str | None,
    int,
]
FeedbackHandoffRecordListRequest = tuple[
    str | None,
    str | None,
    str | None,
    str | None,
    FeedbackHandoffOutcome | None,
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


def test_get_campaign_feedback_loop_summary_api_returns_operator_status() -> None:
    event = api_module.CampaignPerformanceEventRequest.model_validate(
        _event_payload_with_strategy_context()
    )
    detail = _event_detail(event)
    optimization_draft = build_campaign_feedback_optimization_draft(detail)
    source_review = build_campaign_feedback_optimization_review(
        optimization_draft,
        CampaignFeedbackOptimizationReviewRequest(
            decision=FeedbackOptimizationReviewDecision.NEEDS_REVISION,
            reviewer_id="operator_001",
            selected_change_ids=[optimization_draft.changes[0].change_id],
        ),
        review_id="feedback_review_summary_api_source",
    )
    revision_draft = build_campaign_feedback_revision_reviewable_draft(source_review)
    revision_review = build_campaign_feedback_optimization_review(
        revision_draft,
        CampaignFeedbackOptimizationReviewRequest(
            decision=FeedbackOptimizationReviewDecision.APPROVED,
            reviewer_id="operator_002",
            selected_change_ids=[revision_draft.changes[0].change_id],
        ),
        review_id="feedback_review_summary_api_revision",
    )
    event_store = CapturingPerformanceEventStore(details=[detail])
    review_store = CapturingFeedbackOptimizationReviewStore(
        reviews=[source_review, revision_review]
    )
    execution_store = CapturingFeedbackExecutionDryRunStore()
    handoff_store = CapturingFeedbackHandoffRecordStore()
    execution_plan = api_module.build_feedback_execution_plan(revision_review)
    completed_step_id = execution_plan.steps[0].step_id
    api_app.dependency_overrides[get_runtime_settings] = lambda: Settings(
        performance_event_persistence_backend="postgres",
        feedback_review_persistence_backend="postgres",
        feedback_execution_persistence_backend="postgres",
    )
    api_app.dependency_overrides[get_runtime_performance_event_store] = lambda: event_store
    api_app.dependency_overrides[get_runtime_feedback_review_store] = lambda: review_store
    api_app.dependency_overrides[get_runtime_feedback_execution_store] = (
        lambda: execution_store
    )
    api_app.dependency_overrides[get_runtime_feedback_handoff_store] = (
        lambda: handoff_store
    )
    try:
        client = TestClient(api_app)
        dry_run_response = client.post(
            f"/feedback-optimization-reviews/{revision_review.review_id}/execution-plan/dry-run",
            headers={"X-Tenant-ID": "tenant_api"},
        )
        handoff_response = client.post(
            f"/feedback-optimization-reviews/{revision_review.review_id}/handoff-records",
            json={
                "outcome": "applied",
                "operator_id": "operator_003",
                "completed_step_ids": [completed_step_id],
            },
            headers={"X-Tenant-ID": "tenant_api"},
        )
        summary_response = client.get(
            f"/campaign-events/performance/{event.event_id}/feedback-loop-summary",
            params={"limit": "10"},
            headers={"X-Tenant-ID": "tenant_api"},
        )
        timeline_response = client.get(
            f"/campaign-events/performance/{event.event_id}/feedback-loop-timeline",
            params={"limit": "20"},
            headers={"X-Tenant-ID": "tenant_api"},
        )
        command_center_response = client.get(
            f"/campaign-events/performance/{event.event_id}/feedback-loop-command-center",
            params={"limit": "20"},
            headers={"X-Tenant-ID": "tenant_api"},
        )
    finally:
        api_app.dependency_overrides.clear()

    payload = summary_response.json()
    timeline_payload = timeline_response.json()
    command_center_payload = command_center_response.json()
    assert dry_run_response.status_code == 200
    assert handoff_response.status_code == 200
    assert summary_response.status_code == 200
    assert timeline_response.status_code == 200
    assert command_center_response.status_code == 200
    assert summary_response.headers["feedback-loop-stage"] == "handoff_applied"
    assert summary_response.headers["feedback-review-count"] == "2"
    assert summary_response.headers["feedback-dry-run-count"] == "1"
    assert summary_response.headers["feedback-handoff-record-count"] == "1"
    assert summary_response.headers["feedback-handoff-outcome"] == "applied"
    assert timeline_response.headers["feedback-loop-stage"] == "handoff_applied"
    assert timeline_response.headers["feedback-timeline-entry-count"] == "10"
    assert timeline_response.headers["feedback-timeline-latest-stage"] == "handoff_applied"
    assert command_center_response.headers["feedback-loop-stage"] == "handoff_applied"
    assert command_center_response.headers["feedback-command-count"] == "4"
    assert command_center_response.headers["feedback-primary-command-id"] == (
        "record_next_performance_event"
    )
    assert payload["event_id"] == event.event_id
    assert payload["current_stage"] == "handoff_applied"
    assert payload["review_count"] == 2
    assert payload["lineage_count"] == 2
    assert payload["dry_run_count"] == 1
    assert payload["handoff_record_count"] == 1
    assert payload["latest_handoff_record_id"] == handoff_response.json()[
        "handoff_record_id"
    ]
    assert payload["latest_handoff_outcome"] == "applied"
    assert payload["reviews"]["count"] == 2
    assert payload["lineages"]["count"] == 2
    assert payload["dry_runs"]["items"][0]["dry_run_id"] == dry_run_response.json()[
        "dry_run_id"
    ]
    assert payload["handoff_records"]["items"][0]["handoff_record_id"] == (
        handoff_response.json()["handoff_record_id"]
    )
    assert payload["execution_ready_review_ids"] == [revision_review.review_id]
    assert timeline_payload["event_id"] == event.event_id
    assert timeline_payload["current_stage"] == "handoff_applied"
    assert timeline_payload["entry_count"] == 10
    assert timeline_payload["latest_entry_stage"] == "handoff_applied"
    assert [entry["stage"] for entry in timeline_payload["entries"]] == [
        "performance_event_analyzed",
        "feedback_action_plan_created",
        "optimization_draft_created",
        "revision_requested",
        "revision_draft_created",
        "revision_review_approved",
        "execution_plan_ready",
        "execution_dry_run_passed",
        "handoff_ready",
        "handoff_applied",
    ]
    assert timeline_payload["entries"][-1]["actor_id"] == "operator_003"
    assert command_center_payload["event_id"] == event.event_id
    assert command_center_payload["current_stage"] == "handoff_applied"
    assert command_center_payload["primary_command_id"] == (
        "record_next_performance_event"
    )
    assert command_center_payload["primary_command"]["api_path"] == (
        "/campaign-events/performance"
    )
    assert command_center_payload["command_count"] == 4
    assert any(
        command["command_id"] == "inspect_feedback_outcome_report"
        for command in command_center_payload["commands"]
    )
    assert command_center_payload["loop_summary"]["current_stage"] == "handoff_applied"
    assert command_center_payload["timeline"]["latest_entry_stage"] == "handoff_applied"
    assert event_store.requested_event_ids == [
        event.event_id,
        event.event_id,
        event.event_id,
    ]


def test_get_campaign_feedback_outcome_report_api_compares_followup_snapshot() -> None:
    baseline_event = api_module.CampaignPerformanceEventRequest.model_validate(
        _event_payload_with_strategy_context()
    )
    followup_payload = _event_payload_with_strategy_context()
    followup_payload["event_id"] = "evt_perf_followup_001"
    followup_payload["occurred_at"] = "2026-05-13T12:00:00Z"
    followup_payload["metrics"] = {
        "impressions": 12000,
        "clicks": 720,
        "spend": "900.00",
        "conversions": 90,
    }
    followup_event = api_module.CampaignPerformanceEventRequest.model_validate(
        followup_payload
    )
    baseline_detail = _event_detail(baseline_event)
    followup_detail = _event_detail(followup_event)
    event_store = CapturingPerformanceEventStore(
        details=[followup_detail, baseline_detail]
    )
    api_app.dependency_overrides[get_runtime_settings] = lambda: Settings(
        performance_event_persistence_backend="postgres"
    )
    api_app.dependency_overrides[get_runtime_performance_event_store] = (
        lambda: event_store
    )
    try:
        response = TestClient(api_app).get(
            f"/campaign-events/performance/{baseline_event.event_id}/feedback-outcome-report",
            params={"limit": "10"},
            headers={"X-Tenant-ID": "tenant_api"},
        )
    finally:
        api_app.dependency_overrides.clear()

    payload = response.json()
    assert response.status_code == 200
    assert response.headers["feedback-outcome-status"] == "improved"
    assert response.headers["feedback-followup-event-id"] == "evt_perf_followup_001"
    assert payload["outcome_status"] == "improved"
    assert payload["baseline_event_id"] == baseline_event.event_id
    assert payload["followup_event_id"] == "evt_perf_followup_001"
    assert payload["comparison_event_count"] == 1
    delta_by_name = {delta["metric_name"]: delta for delta in payload["metric_deltas"]}
    assert delta_by_name["cpa"]["delta_direction"] == "improved"
    assert delta_by_name["conversions"]["delta_direction"] == "improved"
    assert delta_by_name["spend"]["delta_direction"] == "informational"
    assert event_store.list_requests == [
        (
            baseline_event.advertiser_id,
            baseline_event.run_id,
            baseline_event.campaign_id,
            baseline_event.draft_id,
            PerformanceEventType.PERFORMANCE_SNAPSHOT,
            10,
        )
    ]


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


def test_get_feedback_optimization_revision_draft_api_returns_revised_draft() -> None:
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
            notes="Reduce the budget shift and explain the creative tradeoff.",
            selected_change_ids=[optimization_draft.changes[0].change_id],
        ),
        review_id="feedback_review_revision_api_001",
    )
    review_store = CapturingFeedbackOptimizationReviewStore(reviews=[review])
    api_app.dependency_overrides[get_runtime_settings] = lambda: Settings(
        feedback_review_persistence_backend="postgres"
    )
    api_app.dependency_overrides[get_runtime_feedback_review_store] = lambda: review_store
    try:
        response = TestClient(api_app).get(
            f"/feedback-optimization-reviews/{review.review_id}/revision-draft",
            headers={"X-Tenant-ID": "tenant_api"},
        )
    finally:
        api_app.dependency_overrides.clear()

    payload = response.json()
    assert response.status_code == 200
    assert response.headers["feedback-review-id"] == review.review_id
    assert response.headers["feedback-revision-draft-id"].startswith(
        "feedback_revision_draft_"
    )
    assert payload["source_review_id"] == review.review_id
    assert payload["status"] == "draft"
    assert payload["reviewer_notes"] == review.notes
    assert payload["changes"][0]["params"]["revision_source_review_id"] == review.review_id
    assert "Reduce the budget shift" in payload["changes"][0]["description"]


def test_get_feedback_optimization_revision_draft_api_rejects_approved_review() -> None:
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
        review_id="feedback_review_revision_api_blocked",
    )
    review_store = CapturingFeedbackOptimizationReviewStore(reviews=[review])
    api_app.dependency_overrides[get_runtime_settings] = lambda: Settings(
        feedback_review_persistence_backend="postgres"
    )
    api_app.dependency_overrides[get_runtime_feedback_review_store] = lambda: review_store
    try:
        response = TestClient(api_app).get(
            f"/feedback-optimization-reviews/{review.review_id}/revision-draft"
        )
    finally:
        api_app.dependency_overrides.clear()

    assert response.status_code == 409
    assert response.json()["detail"]["error_code"] == "FEEDBACK_REVISION_DRAFT_NOT_REQUESTED"


def test_submit_feedback_optimization_revision_review_api_allows_execution_plan() -> None:
    event = api_module.CampaignPerformanceEventRequest.model_validate(
        _event_payload_with_strategy_context()
    )
    detail = _event_detail(event)
    optimization_draft = build_campaign_feedback_optimization_draft(detail)
    source_review = build_campaign_feedback_optimization_review(
        optimization_draft,
        CampaignFeedbackOptimizationReviewRequest(
            decision=FeedbackOptimizationReviewDecision.NEEDS_REVISION,
            reviewer_id="operator_001",
            notes="Please reduce the budget movement before approval.",
            selected_change_ids=[optimization_draft.changes[0].change_id],
        ),
        review_id="feedback_review_revision_api_submit_001",
    )
    review_store = CapturingFeedbackOptimizationReviewStore(reviews=[source_review])
    api_app.dependency_overrides[get_runtime_settings] = lambda: Settings(
        feedback_review_persistence_backend="postgres"
    )
    api_app.dependency_overrides[get_runtime_feedback_review_store] = lambda: review_store
    try:
        client = TestClient(api_app)
        response = client.post(
            f"/feedback-optimization-reviews/{source_review.review_id}/revision-draft/reviews",
            json={
                "decision": "approved",
                "reviewer_id": "operator_002",
                "notes": "Approved the revised draft.",
            },
            headers={"X-Tenant-ID": "tenant_api"},
        )
        payload = response.json()
        plan_response = client.get(
            f"/feedback-optimization-reviews/{payload['review_id']}/execution-plan",
            headers={"X-Tenant-ID": "tenant_api"},
        )
    finally:
        api_app.dependency_overrides.clear()

    assert response.status_code == 201
    assert response.headers["source-feedback-review-id"] == source_review.review_id
    assert response.headers["feedback-revision-draft-id"].startswith(
        "feedback_revision_draft_"
    )
    assert payload["decision"] == "approved"
    assert payload["optimization_draft_id"].startswith("feedback_revision_draft_")
    assert payload["optimization_draft"]["changes"][0]["params"][
        "revision_source_review_id"
    ] == source_review.review_id
    assert review_store.recorded_draft_ids == [payload["optimization_draft_id"]]
    assert plan_response.status_code == 200
    assert plan_response.json()["review_id"] == payload["review_id"]
    assert plan_response.json()["optimization_draft_id"] == payload["optimization_draft_id"]


def test_get_feedback_optimization_review_lineage_api_returns_revision_chain() -> None:
    event = api_module.CampaignPerformanceEventRequest.model_validate(
        _event_payload_with_strategy_context()
    )
    detail = _event_detail(event)
    optimization_draft = build_campaign_feedback_optimization_draft(detail)
    source_review = build_campaign_feedback_optimization_review(
        optimization_draft,
        CampaignFeedbackOptimizationReviewRequest(
            decision=FeedbackOptimizationReviewDecision.NEEDS_REVISION,
            reviewer_id="operator_001",
            notes="Please reduce the budget movement before approval.",
            selected_change_ids=[optimization_draft.changes[0].change_id],
        ),
        review_id="feedback_review_lineage_api_source",
    )
    revision_draft = build_campaign_feedback_revision_reviewable_draft(source_review)
    revision_review = build_campaign_feedback_optimization_review(
        revision_draft,
        CampaignFeedbackOptimizationReviewRequest(
            decision=FeedbackOptimizationReviewDecision.APPROVED,
            reviewer_id="operator_002",
            selected_change_ids=[revision_draft.changes[0].change_id],
        ),
        review_id="feedback_review_lineage_api_revision",
    )
    review_store = CapturingFeedbackOptimizationReviewStore(
        reviews=[source_review, revision_review]
    )
    execution_store = CapturingFeedbackExecutionDryRunStore()
    api_app.dependency_overrides[get_runtime_settings] = lambda: Settings(
        feedback_review_persistence_backend="postgres",
        feedback_execution_persistence_backend="postgres",
    )
    api_app.dependency_overrides[get_runtime_feedback_review_store] = lambda: review_store
    api_app.dependency_overrides[get_runtime_feedback_execution_store] = (
        lambda: execution_store
    )
    try:
        client = TestClient(api_app)
        dry_run_response = client.post(
            f"/feedback-optimization-reviews/{revision_review.review_id}/execution-plan/dry-run",
            headers={"X-Tenant-ID": "tenant_api"},
        )
        source_response = client.get(
            f"/feedback-optimization-reviews/{source_review.review_id}/lineage",
            headers={"X-Tenant-ID": "tenant_api"},
        )
        revision_response = client.get(
            f"/feedback-optimization-reviews/{revision_review.review_id}/lineage",
            headers={"X-Tenant-ID": "tenant_api"},
        )
    finally:
        api_app.dependency_overrides.clear()

    source_payload = source_response.json()
    revision_payload = revision_response.json()
    dry_run_payload = dry_run_response.json()
    assert dry_run_response.status_code == 200
    assert source_response.status_code == 200
    assert source_response.headers["feedback-lineage-stage"] == "revision_requested"
    assert source_payload["source_review_id"] == source_review.review_id
    assert source_payload["revision_draft"]["revision_draft_id"] == (
        revision_draft.optimization_draft_id
    )
    assert source_payload["revision_reviews"][0]["review_id"] == revision_review.review_id
    assert source_payload["execution_ready_review_ids"] == [revision_review.review_id]
    assert source_payload["execution_summaries"][0]["review_id"] == revision_review.review_id
    assert source_payload["execution_summaries"][0]["dry_run_count"] == 1
    assert source_payload["execution_summaries"][0]["latest_dry_run_status"] == "passed"
    assert source_payload["execution_summaries"][0]["dry_runs"][0]["dry_run_id"] == (
        dry_run_payload["dry_run_id"]
    )
    assert revision_response.status_code == 200
    assert revision_payload["lineage_stage"] == "revision_review"
    assert revision_payload["source_review_id"] == source_review.review_id
    assert revision_payload["target_review"]["review_id"] == revision_review.review_id
    assert revision_payload["execution_summaries"][0]["dry_runs"][0]["dry_run_id"] == (
        dry_run_payload["dry_run_id"]
    )


def test_list_feedback_optimization_review_lineages_api_filters_revision_chain() -> None:
    event = api_module.CampaignPerformanceEventRequest.model_validate(
        _event_payload_with_strategy_context()
    )
    detail = _event_detail(event)
    optimization_draft = build_campaign_feedback_optimization_draft(detail)
    source_review = build_campaign_feedback_optimization_review(
        optimization_draft,
        CampaignFeedbackOptimizationReviewRequest(
            decision=FeedbackOptimizationReviewDecision.NEEDS_REVISION,
            reviewer_id="operator_001",
            notes="Please reduce the budget movement before approval.",
            selected_change_ids=[optimization_draft.changes[0].change_id],
        ),
        review_id="feedback_review_lineage_list_api_source",
    )
    revision_draft = build_campaign_feedback_revision_reviewable_draft(source_review)
    revision_review = build_campaign_feedback_optimization_review(
        revision_draft,
        CampaignFeedbackOptimizationReviewRequest(
            decision=FeedbackOptimizationReviewDecision.APPROVED,
            reviewer_id="operator_002",
            selected_change_ids=[revision_draft.changes[0].change_id],
        ),
        review_id="feedback_review_lineage_list_api_revision",
    )
    review_store = CapturingFeedbackOptimizationReviewStore(
        reviews=[source_review, revision_review]
    )
    execution_store = CapturingFeedbackExecutionDryRunStore()
    api_app.dependency_overrides[get_runtime_settings] = lambda: Settings(
        feedback_review_persistence_backend="postgres",
        feedback_execution_persistence_backend="postgres",
    )
    api_app.dependency_overrides[get_runtime_feedback_review_store] = lambda: review_store
    api_app.dependency_overrides[get_runtime_feedback_execution_store] = (
        lambda: execution_store
    )
    try:
        client = TestClient(api_app)
        dry_run_response = client.post(
            f"/feedback-optimization-reviews/{revision_review.review_id}/execution-plan/dry-run",
            headers={"X-Tenant-ID": "tenant_api"},
        )
        list_response = client.get(
            "/feedback-optimization-review-lineages",
            params={
                "event_id": event.event_id,
                "decision": "approved",
                "lineage_stage": "revision_review",
                "limit": "10",
            },
            headers={"X-Tenant-ID": "tenant_api"},
        )
    finally:
        api_app.dependency_overrides.clear()

    dry_run_payload = dry_run_response.json()
    payload = list_response.json()
    assert dry_run_response.status_code == 200
    assert list_response.status_code == 200
    assert list_response.headers["feedback-lineage-count"] == "1"
    assert payload["count"] == 1
    assert payload["limit"] == 10
    assert payload["event_id"] == event.event_id
    assert payload["decision"] == "approved"
    assert payload["lineage_stage"] == "revision_review"
    lineage = payload["items"][0]
    assert lineage["requested_review_id"] == revision_review.review_id
    assert lineage["source_review_id"] == source_review.review_id
    assert lineage["target_review"]["review_id"] == revision_review.review_id
    assert lineage["execution_summaries"][0]["dry_runs"][0]["dry_run_id"] == (
        dry_run_payload["dry_run_id"]
    )
    assert review_store.list_requests[0] == (
        event.event_id,
        None,
        None,
        FeedbackOptimizationReviewDecision.APPROVED,
        100,
    )


def test_submit_feedback_optimization_revision_review_api_rejects_non_revision_source() -> None:
    event = api_module.CampaignPerformanceEventRequest.model_validate(
        _event_payload_with_strategy_context()
    )
    detail = _event_detail(event)
    optimization_draft = build_campaign_feedback_optimization_draft(detail)
    source_review = build_campaign_feedback_optimization_review(
        optimization_draft,
        CampaignFeedbackOptimizationReviewRequest(
            decision=FeedbackOptimizationReviewDecision.APPROVED,
            reviewer_id="operator_001",
            selected_change_ids=[optimization_draft.changes[0].change_id],
        ),
        review_id="feedback_review_revision_api_submit_blocked",
    )
    review_store = CapturingFeedbackOptimizationReviewStore(reviews=[source_review])
    api_app.dependency_overrides[get_runtime_settings] = lambda: Settings(
        feedback_review_persistence_backend="postgres"
    )
    api_app.dependency_overrides[get_runtime_feedback_review_store] = lambda: review_store
    try:
        response = TestClient(api_app).post(
            f"/feedback-optimization-reviews/{source_review.review_id}/revision-draft/reviews",
            json={"decision": "approved", "reviewer_id": "operator_002"},
        )
    finally:
        api_app.dependency_overrides.clear()

    assert response.status_code == 409
    assert response.json()["detail"]["error_code"] == "FEEDBACK_REVISION_DRAFT_NOT_REQUESTED"


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


def test_get_feedback_handoff_package_api_returns_ready_package() -> None:
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
        review_id="feedback_review_handoff_api_001",
    )
    review_store = CapturingFeedbackOptimizationReviewStore(reviews=[review])
    execution_store = CapturingFeedbackExecutionDryRunStore()
    api_app.dependency_overrides[get_runtime_settings] = lambda: Settings(
        feedback_review_persistence_backend="postgres",
        feedback_execution_persistence_backend="postgres",
    )
    api_app.dependency_overrides[get_runtime_feedback_review_store] = lambda: review_store
    api_app.dependency_overrides[get_runtime_feedback_execution_store] = (
        lambda: execution_store
    )
    try:
        client = TestClient(api_app)
        dry_run_response = client.post(
            f"/feedback-optimization-reviews/{review.review_id}/execution-plan/dry-run",
            headers={"X-Tenant-ID": "tenant_api"},
        )
        package_response = client.get(
            f"/feedback-optimization-reviews/{review.review_id}/handoff-package",
            headers={"X-Tenant-ID": "tenant_api"},
        )
    finally:
        api_app.dependency_overrides.clear()

    dry_run_payload = dry_run_response.json()
    payload = package_response.json()
    assert dry_run_response.status_code == 200
    assert package_response.status_code == 200
    assert package_response.headers["feedback-handoff-status"] == (
        "ready_for_manual_handoff"
    )
    assert package_response.headers["feedback-review-id"] == review.review_id
    assert payload["status"] == "ready_for_manual_handoff"
    assert payload["review_id"] == review.review_id
    assert payload["latest_dry_run_id"] == dry_run_payload["dry_run_id"]
    assert payload["validated_step_count"] == 1
    assert payload["blocked_step_count"] == 0
    assert payload["manual_steps"][0]["dry_run_status"] == "validated"
    assert payload["latest_dry_run"]["dry_run_id"] == dry_run_payload["dry_run_id"]


def test_feedback_handoff_record_api_records_and_reads_operator_outcome() -> None:
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
        review_id="feedback_review_handoff_record_api_001",
    )
    execution_plan = api_module.build_feedback_execution_plan(review)
    completed_step_id = execution_plan.steps[0].step_id
    review_store = CapturingFeedbackOptimizationReviewStore(reviews=[review])
    execution_store = CapturingFeedbackExecutionDryRunStore()
    handoff_store = CapturingFeedbackHandoffRecordStore()
    memory_store = CapturingAdvertiserMemoryStore(
        AdvertiserMemoryWriteResult(
            persisted=True,
            status="recorded",
            source_id="memory:handoff:test:v1",
            memory_type="historical_performance",
        )
    )
    api_app.dependency_overrides[get_runtime_settings] = lambda: Settings(
        feedback_review_persistence_backend="postgres",
        feedback_execution_persistence_backend="postgres",
        advertiser_memory_persistence_backend="postgres",
    )
    api_app.dependency_overrides[get_runtime_feedback_review_store] = lambda: review_store
    api_app.dependency_overrides[get_runtime_feedback_execution_store] = (
        lambda: execution_store
    )
    api_app.dependency_overrides[get_runtime_feedback_handoff_store] = (
        lambda: handoff_store
    )
    api_app.dependency_overrides[get_runtime_advertiser_memory_store] = (
        lambda: memory_store
    )
    try:
        client = TestClient(api_app)
        dry_run_response = client.post(
            f"/feedback-optimization-reviews/{review.review_id}/execution-plan/dry-run",
            headers={"X-Tenant-ID": "tenant_api"},
        )
        submit_response = client.post(
            f"/feedback-optimization-reviews/{review.review_id}/handoff-records",
            json={
                "outcome": "applied",
                "operator_id": "operator_002",
                "notes": "Applied manually in the ads console.",
                "completed_step_ids": [completed_step_id],
            },
            headers={"X-Tenant-ID": "tenant_api"},
        )
        record_id = submit_response.json()["handoff_record_id"]
        get_response = client.get(
            f"/feedback-handoff-records/{record_id}",
            headers={"X-Tenant-ID": "tenant_api"},
        )
        list_response = client.get(
            f"/feedback-handoff-records?review_id={review.review_id}&outcome=applied",
            headers={"X-Tenant-ID": "tenant_api"},
        )
    finally:
        api_app.dependency_overrides.clear()

    payload = submit_response.json()
    assert dry_run_response.status_code == 200
    assert submit_response.status_code == 200
    assert submit_response.headers["feedback-handoff-record-id"] == record_id
    assert submit_response.headers["feedback-handoff-outcome"] == "applied"
    assert submit_response.headers["advertiser-memory-status"] == "recorded"
    assert submit_response.headers["advertiser-memory-source-id"] == "memory:handoff:test:v1"
    assert payload["review_id"] == review.review_id
    assert payload["latest_dry_run_id"] == dry_run_response.json()["dry_run_id"]
    assert payload["outcome"] == "applied"
    assert payload["requires_follow_up"] is False
    assert payload["completed_step_ids"] == [completed_step_id]
    assert get_response.status_code == 200
    assert get_response.json()["handoff_record_id"] == record_id
    assert list_response.status_code == 200
    assert list_response.json()["count"] == 1
    assert list_response.json()["items"][0]["handoff_record_id"] == record_id
    assert handoff_store.recorded_requests[0].operator_id == "operator_002"
    assert memory_store.handoff_records == [record_id]


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
    assert response.headers["feedback-dry-run-status"] == "not_recorded"
    assert payload["status"] == "passed"
    assert payload["validated_step_count"] == 1
    assert payload["blocked_step_count"] == 0
    assert payload["step_results"][0]["tool_result"]["success"] is True
    assert payload["step_results"][0]["tool_result"]["payload"]["mutation_performed"] is False


def test_dry_run_feedback_execution_plan_api_records_validation_when_enabled() -> None:
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
        review_id="feedback_review_dry_run_persisted_api_001",
    )
    review_store = CapturingFeedbackOptimizationReviewStore(reviews=[review])
    execution_store = CapturingFeedbackExecutionDryRunStore()
    api_app.dependency_overrides[get_runtime_settings] = lambda: Settings(
        feedback_review_persistence_backend="postgres",
        feedback_execution_persistence_backend="postgres",
    )
    api_app.dependency_overrides[get_runtime_feedback_review_store] = lambda: review_store
    api_app.dependency_overrides[get_runtime_feedback_execution_store] = (
        lambda: execution_store
    )
    try:
        response = TestClient(api_app).post(
            f"/feedback-optimization-reviews/{review.review_id}/execution-plan/dry-run",
            headers={"X-Tenant-ID": "tenant_api"},
        )
        dry_run_id = response.json()["dry_run_id"]
        detail_response = TestClient(api_app).get(
            f"/feedback-execution-dry-runs/{dry_run_id}",
            headers={"X-Tenant-ID": "tenant_api"},
        )
        list_response = TestClient(api_app).get(
            "/feedback-execution-dry-runs",
            params={"review_id": review.review_id, "status": "passed", "limit": "10"},
            headers={"X-Tenant-ID": "tenant_api"},
        )
    finally:
        api_app.dependency_overrides.clear()

    assert response.status_code == 200
    assert response.headers["feedback-dry-run-status"] == "recorded"
    assert execution_store.recorded_execution_plan_ids == [
        response.json()["execution_plan_id"]
    ]
    assert detail_response.status_code == 200
    assert detail_response.json()["dry_run_id"] == dry_run_id
    assert detail_response.headers["feedback-review-id"] == review.review_id
    assert list_response.status_code == 200
    assert list_response.json()["count"] == 1
    assert list_response.json()["items"][0]["dry_run_id"] == dry_run_id
    assert execution_store.list_requests == [
        (review.review_id, None, None, None, "passed", 10)
    ]


def test_get_feedback_execution_dry_run_api_requires_persistence() -> None:
    api_app.dependency_overrides[get_runtime_settings] = lambda: Settings(
        feedback_execution_persistence_backend="none"
    )
    try:
        response = TestClient(api_app).get("/feedback-execution-dry-runs/dry_run_missing")
    finally:
        api_app.dependency_overrides.clear()

    assert response.status_code == 503
    assert response.json()["detail"]["error_code"] == "FEEDBACK_EXECUTION_PERSISTENCE_DISABLED"


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


def test_get_feedback_loop_summary_cli_returns_operator_status(monkeypatch) -> None:
    event = api_module.CampaignPerformanceEventRequest.model_validate(
        _event_payload_with_strategy_context()
    )
    detail = _event_detail(event)
    optimization_draft = build_campaign_feedback_optimization_draft(detail)
    source_review = build_campaign_feedback_optimization_review(
        optimization_draft,
        CampaignFeedbackOptimizationReviewRequest(
            decision=FeedbackOptimizationReviewDecision.NEEDS_REVISION,
            reviewer_id="operator_001",
            selected_change_ids=[optimization_draft.changes[0].change_id],
        ),
        review_id="feedback_review_summary_cli_source",
    )
    revision_draft = build_campaign_feedback_revision_reviewable_draft(source_review)
    revision_review = build_campaign_feedback_optimization_review(
        revision_draft,
        CampaignFeedbackOptimizationReviewRequest(
            decision=FeedbackOptimizationReviewDecision.APPROVED,
            reviewer_id="operator_002",
            selected_change_ids=[revision_draft.changes[0].change_id],
        ),
        review_id="feedback_review_summary_cli_revision",
    )
    execution_plan = api_module.build_feedback_execution_plan(revision_review)
    dry_run = api_module.dry_run_feedback_execution_plan(execution_plan)
    event_store = CapturingPerformanceEventStore(details=[detail])
    review_store = CapturingFeedbackOptimizationReviewStore(
        reviews=[source_review, revision_review]
    )
    execution_store = CapturingFeedbackExecutionDryRunStore(dry_runs=[dry_run])
    handoff_store = CapturingFeedbackHandoffRecordStore()
    handoff_package = api_module.build_feedback_handoff_package(
        revision_review,
        execution_store,
    )
    handoff_record = handoff_store.record_handoff(
        handoff_package,
        CampaignFeedbackHandoffRecordRequest(
            outcome=FeedbackHandoffOutcome.APPLIED,
            operator_id="operator_cli_summary",
            completed_step_ids=[step.step_id for step in handoff_package.manual_steps],
        ),
    )

    monkeypatch.setattr(
        "ads_growth_agent.cli.get_settings",
        lambda: Settings(
            performance_event_persistence_backend="postgres",
            feedback_review_persistence_backend="postgres",
            feedback_execution_persistence_backend="postgres",
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
    monkeypatch.setattr(
        "ads_growth_agent.cli.build_configured_feedback_execution_store",
        lambda settings: execution_store,
    )
    monkeypatch.setattr(
        "ads_growth_agent.cli.build_configured_feedback_handoff_store",
        lambda settings: handoff_store,
    )

    result = CliRunner().invoke(
        cli_app,
        ["get-feedback-loop-summary", event.event_id, "--limit", "10"],
    )
    timeline_result = CliRunner().invoke(
        cli_app,
        ["get-feedback-loop-timeline", event.event_id, "--limit", "20"],
    )
    command_center_result = CliRunner().invoke(
        cli_app,
        ["get-feedback-loop-command-center", event.event_id, "--limit", "20"],
    )

    assert result.exit_code == 0
    assert timeline_result.exit_code == 0
    assert command_center_result.exit_code == 0
    payload = json.loads(result.stdout)
    timeline_payload = json.loads(timeline_result.stdout)
    command_center_payload = json.loads(command_center_result.stdout)
    assert payload["event_id"] == event.event_id
    assert payload["current_stage"] == "handoff_applied"
    assert payload["review_count"] == 2
    assert payload["lineage_count"] == 2
    assert payload["dry_run_count"] == 1
    assert payload["handoff_record_count"] == 1
    assert payload["latest_handoff_record_id"] == handoff_record.handoff_record_id
    assert payload["latest_handoff_outcome"] == "applied"
    assert payload["execution_ready_review_ids"] == [revision_review.review_id]
    assert payload["dry_runs"]["items"][0]["dry_run_id"] == dry_run.dry_run_id
    assert payload["handoff_records"]["items"][0]["handoff_record_id"] == (
        handoff_record.handoff_record_id
    )
    assert timeline_payload["event_id"] == event.event_id
    assert timeline_payload["current_stage"] == "handoff_applied"
    assert timeline_payload["entry_count"] == 10
    assert timeline_payload["latest_entry_stage"] == "handoff_applied"
    assert [entry["stage"] for entry in timeline_payload["entries"]] == [
        "performance_event_analyzed",
        "feedback_action_plan_created",
        "optimization_draft_created",
        "revision_requested",
        "revision_draft_created",
        "revision_review_approved",
        "execution_plan_ready",
        "execution_dry_run_passed",
        "handoff_ready",
        "handoff_applied",
    ]
    assert timeline_payload["entries"][-1]["actor_id"] == "operator_cli_summary"
    assert command_center_payload["event_id"] == event.event_id
    assert command_center_payload["current_stage"] == "handoff_applied"
    assert command_center_payload["primary_command_id"] == (
        "record_next_performance_event"
    )
    assert command_center_payload["primary_command"]["api_path"] == (
        "/campaign-events/performance"
    )
    assert command_center_payload["command_count"] == 4
    assert any(
        command["command_id"] == "inspect_feedback_outcome_report"
        for command in command_center_payload["commands"]
    )
    assert command_center_payload["loop_summary"]["current_stage"] == "handoff_applied"
    assert command_center_payload["timeline"]["latest_entry_stage"] == "handoff_applied"


def test_get_feedback_outcome_report_cli_compares_followup_snapshot(monkeypatch) -> None:
    baseline_event = api_module.CampaignPerformanceEventRequest.model_validate(
        _event_payload_with_strategy_context()
    )
    followup_payload = _event_payload_with_strategy_context()
    followup_payload["event_id"] = "evt_perf_followup_cli_001"
    followup_payload["occurred_at"] = "2026-05-13T12:00:00Z"
    followup_payload["metrics"] = {
        "impressions": 12000,
        "clicks": 720,
        "spend": "900.00",
        "conversions": 90,
    }
    followup_event = api_module.CampaignPerformanceEventRequest.model_validate(
        followup_payload
    )
    baseline_detail = _event_detail(baseline_event)
    followup_detail = _event_detail(followup_event)
    event_store = CapturingPerformanceEventStore(
        details=[followup_detail, baseline_detail]
    )

    monkeypatch.setattr(
        "ads_growth_agent.cli.get_settings",
        lambda: Settings(tenant_id="tenant_cli"),
    )
    monkeypatch.setattr(
        "ads_growth_agent.cli.build_configured_performance_event_store",
        lambda settings: event_store,
    )

    result = CliRunner().invoke(
        cli_app,
        ["get-feedback-outcome-report", baseline_event.event_id, "--limit", "10"],
    )

    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["outcome_status"] == "improved"
    assert payload["followup_event_id"] == "evt_perf_followup_cli_001"


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


def test_get_feedback_optimization_revision_draft_cli_returns_revised_draft(
    monkeypatch,
) -> None:
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
            notes="Make the budget change more conservative.",
            selected_change_ids=[optimization_draft.changes[0].change_id],
        ),
        review_id="feedback_review_revision_cli_001",
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
        ["get-feedback-optimization-revision-draft", review.review_id],
    )

    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["source_review_id"] == review.review_id
    assert payload["status"] == "draft"
    assert payload["changes"][0]["params"]["revision_source_review_id"] == review.review_id
    assert "Make the budget change" in payload["changes"][0]["description"]


def test_get_feedback_optimization_revision_draft_cli_rejects_approved_review(
    monkeypatch,
) -> None:
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
        review_id="feedback_review_revision_cli_blocked",
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
        ["get-feedback-optimization-revision-draft", review.review_id],
    )

    assert result.exit_code == 1
    assert "must request revision" in result.stderr


def test_submit_feedback_optimization_revision_review_cli_returns_review(
    monkeypatch,
) -> None:
    event = api_module.CampaignPerformanceEventRequest.model_validate(
        _event_payload_with_strategy_context()
    )
    detail = _event_detail(event)
    optimization_draft = build_campaign_feedback_optimization_draft(detail)
    source_review = build_campaign_feedback_optimization_review(
        optimization_draft,
        CampaignFeedbackOptimizationReviewRequest(
            decision=FeedbackOptimizationReviewDecision.NEEDS_REVISION,
            reviewer_id="operator_001",
            notes="Make the budget change more conservative.",
            selected_change_ids=[optimization_draft.changes[0].change_id],
        ),
        review_id="feedback_review_revision_cli_submit_001",
    )
    review_store = CapturingFeedbackOptimizationReviewStore(reviews=[source_review])

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
        [
            "submit-feedback-optimization-revision-review",
            source_review.review_id,
            "--decision",
            "approved",
            "--reviewer-id",
            "operator_002",
            "--notes",
            "Approved revised change.",
        ],
    )

    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["decision"] == "approved"
    assert payload["optimization_draft_id"].startswith("feedback_revision_draft_")
    assert payload["optimization_draft"]["changes"][0]["params"][
        "revision_source_review_id"
    ] == source_review.review_id
    assert review_store.recorded_draft_ids == [payload["optimization_draft_id"]]


def test_get_feedback_optimization_review_lineage_cli_returns_revision_chain(
    monkeypatch,
) -> None:
    event = api_module.CampaignPerformanceEventRequest.model_validate(
        _event_payload_with_strategy_context()
    )
    detail = _event_detail(event)
    optimization_draft = build_campaign_feedback_optimization_draft(detail)
    source_review = build_campaign_feedback_optimization_review(
        optimization_draft,
        CampaignFeedbackOptimizationReviewRequest(
            decision=FeedbackOptimizationReviewDecision.NEEDS_REVISION,
            reviewer_id="operator_001",
            selected_change_ids=[optimization_draft.changes[0].change_id],
        ),
        review_id="feedback_review_lineage_cli_source",
    )
    revision_draft = build_campaign_feedback_revision_reviewable_draft(source_review)
    revision_review = build_campaign_feedback_optimization_review(
        revision_draft,
        CampaignFeedbackOptimizationReviewRequest(
            decision=FeedbackOptimizationReviewDecision.APPROVED,
            reviewer_id="operator_002",
            selected_change_ids=[revision_draft.changes[0].change_id],
        ),
        review_id="feedback_review_lineage_cli_revision",
    )
    review_store = CapturingFeedbackOptimizationReviewStore(
        reviews=[source_review, revision_review]
    )

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
        ["get-feedback-optimization-review-lineage", revision_review.review_id],
    )

    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["requested_review_id"] == revision_review.review_id
    assert payload["lineage_stage"] == "revision_review"
    assert payload["source_review_id"] == source_review.review_id
    assert payload["revision_reviews"][0]["review_id"] == revision_review.review_id
    assert payload["execution_ready_review_ids"] == [revision_review.review_id]
    assert payload["execution_summaries"][0]["review_id"] == revision_review.review_id
    assert payload["execution_summaries"][0]["dry_run_count"] == 0


def test_list_feedback_optimization_review_lineages_cli_filters_revision_chain(
    monkeypatch,
) -> None:
    event = api_module.CampaignPerformanceEventRequest.model_validate(
        _event_payload_with_strategy_context()
    )
    detail = _event_detail(event)
    optimization_draft = build_campaign_feedback_optimization_draft(detail)
    source_review = build_campaign_feedback_optimization_review(
        optimization_draft,
        CampaignFeedbackOptimizationReviewRequest(
            decision=FeedbackOptimizationReviewDecision.NEEDS_REVISION,
            reviewer_id="operator_001",
            selected_change_ids=[optimization_draft.changes[0].change_id],
        ),
        review_id="feedback_review_lineage_list_cli_source",
    )
    revision_draft = build_campaign_feedback_revision_reviewable_draft(source_review)
    revision_review = build_campaign_feedback_optimization_review(
        revision_draft,
        CampaignFeedbackOptimizationReviewRequest(
            decision=FeedbackOptimizationReviewDecision.APPROVED,
            reviewer_id="operator_002",
            selected_change_ids=[revision_draft.changes[0].change_id],
        ),
        review_id="feedback_review_lineage_list_cli_revision",
    )
    review_store = CapturingFeedbackOptimizationReviewStore(
        reviews=[source_review, revision_review]
    )
    execution_store = CapturingFeedbackExecutionDryRunStore()

    monkeypatch.setattr(
        "ads_growth_agent.cli.get_settings",
        lambda: Settings(feedback_review_persistence_backend="postgres"),
    )
    monkeypatch.setattr(
        "ads_growth_agent.cli.build_configured_feedback_review_store",
        lambda settings: review_store,
    )
    monkeypatch.setattr(
        "ads_growth_agent.cli.build_configured_feedback_execution_store",
        lambda settings: execution_store,
    )

    result = CliRunner().invoke(
        cli_app,
        [
            "list-feedback-optimization-review-lineages",
            "--event-id",
            event.event_id,
            "--decision",
            "approved",
            "--lineage-stage",
            "revision_review",
            "--limit",
            "10",
        ],
    )

    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["count"] == 1
    assert payload["event_id"] == event.event_id
    assert payload["decision"] == "approved"
    assert payload["lineage_stage"] == "revision_review"
    assert payload["items"][0]["requested_review_id"] == revision_review.review_id
    assert payload["items"][0]["source_review_id"] == source_review.review_id
    assert payload["items"][0]["execution_summaries"][0]["dry_run_count"] == 0


def test_submit_feedback_optimization_revision_review_cli_rejects_approved_source(
    monkeypatch,
) -> None:
    event = api_module.CampaignPerformanceEventRequest.model_validate(
        _event_payload_with_strategy_context()
    )
    detail = _event_detail(event)
    optimization_draft = build_campaign_feedback_optimization_draft(detail)
    source_review = build_campaign_feedback_optimization_review(
        optimization_draft,
        CampaignFeedbackOptimizationReviewRequest(
            decision=FeedbackOptimizationReviewDecision.APPROVED,
            reviewer_id="operator_001",
            selected_change_ids=[optimization_draft.changes[0].change_id],
        ),
        review_id="feedback_review_revision_cli_submit_blocked",
    )
    review_store = CapturingFeedbackOptimizationReviewStore(reviews=[source_review])

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
        [
            "submit-feedback-optimization-revision-review",
            source_review.review_id,
            "--decision",
            "approved",
            "--reviewer-id",
            "operator_002",
        ],
    )

    assert result.exit_code == 1
    assert "must request revision" in result.stderr


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


def test_get_feedback_handoff_package_cli_returns_ready_package(monkeypatch) -> None:
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
        review_id="feedback_review_handoff_cli_001",
    )
    execution_plan = api_module.build_feedback_execution_plan(review)
    dry_run = api_module.dry_run_feedback_execution_plan(execution_plan)
    review_store = CapturingFeedbackOptimizationReviewStore(reviews=[review])
    execution_store = CapturingFeedbackExecutionDryRunStore(dry_runs=[dry_run])

    monkeypatch.setattr(
        "ads_growth_agent.cli.get_settings",
        lambda: Settings(
            tenant_id="tenant_cli",
            feedback_review_persistence_backend="postgres",
            feedback_execution_persistence_backend="postgres",
        ),
    )
    monkeypatch.setattr(
        "ads_growth_agent.cli.build_configured_feedback_review_store",
        lambda settings: review_store,
    )
    monkeypatch.setattr(
        "ads_growth_agent.cli.build_configured_feedback_execution_store",
        lambda settings: execution_store,
    )

    result = CliRunner().invoke(
        cli_app,
        ["get-feedback-handoff-package", review.review_id],
    )

    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["status"] == "ready_for_manual_handoff"
    assert payload["review_id"] == review.review_id
    assert payload["latest_dry_run_id"] == dry_run.dry_run_id
    assert payload["manual_steps"][0]["dry_run_status"] == "validated"
    assert payload["operator_checklist"][-1].endswith("manual campaign-platform handoff.")


def test_feedback_handoff_record_cli_records_and_reads_operator_outcome(monkeypatch) -> None:
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
        review_id="feedback_review_handoff_record_cli_001",
    )
    execution_plan = api_module.build_feedback_execution_plan(review)
    dry_run = api_module.dry_run_feedback_execution_plan(execution_plan)
    completed_step_id = execution_plan.steps[0].step_id
    review_store = CapturingFeedbackOptimizationReviewStore(reviews=[review])
    execution_store = CapturingFeedbackExecutionDryRunStore(dry_runs=[dry_run])
    handoff_store = CapturingFeedbackHandoffRecordStore()
    memory_store = CapturingAdvertiserMemoryStore(
        AdvertiserMemoryWriteResult(
            persisted=True,
            status="recorded",
            source_id="memory:handoff:cli-test:v1",
            memory_type="historical_performance",
        )
    )

    monkeypatch.setattr(
        "ads_growth_agent.cli.get_settings",
        lambda: Settings(
            tenant_id="tenant_cli",
            feedback_review_persistence_backend="postgres",
            feedback_execution_persistence_backend="postgres",
            advertiser_memory_persistence_backend="postgres",
        ),
    )
    monkeypatch.setattr(
        "ads_growth_agent.cli.build_configured_feedback_review_store",
        lambda settings: review_store,
    )
    monkeypatch.setattr(
        "ads_growth_agent.cli.build_configured_feedback_execution_store",
        lambda settings: execution_store,
    )
    monkeypatch.setattr(
        "ads_growth_agent.cli.build_configured_feedback_handoff_store",
        lambda settings: handoff_store,
    )
    monkeypatch.setattr(
        "ads_growth_agent.cli.build_configured_advertiser_memory_store",
        lambda settings: memory_store,
    )

    submit_result = CliRunner().invoke(
        cli_app,
        [
            "submit-feedback-handoff-record",
            review.review_id,
            "--outcome",
            "applied",
            "--operator-id",
            "operator_cli",
            "--notes",
            "Applied manually from CLI workflow.",
            "--completed-step-id",
            completed_step_id,
        ],
    )
    record_id = json.loads(submit_result.stdout)["handoff_record_id"]
    get_result = CliRunner().invoke(
        cli_app,
        ["get-feedback-handoff-record", record_id],
    )
    list_result = CliRunner().invoke(
        cli_app,
        [
            "list-feedback-handoff-records",
            "--review-id",
            review.review_id,
            "--outcome",
            "applied",
        ],
    )

    assert submit_result.exit_code == 0
    submit_payload = json.loads(submit_result.stdout)
    assert submit_payload["outcome"] == "applied"
    assert submit_payload["completed_step_ids"] == [completed_step_id]
    assert submit_payload["requires_follow_up"] is False
    assert get_result.exit_code == 0
    assert json.loads(get_result.stdout)["handoff_record_id"] == record_id
    assert list_result.exit_code == 0
    assert json.loads(list_result.stdout)["count"] == 1
    assert handoff_store.recorded_requests[0].operator_id == "operator_cli"
    assert memory_store.handoff_records == [record_id]


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


def test_feedback_execution_dry_run_cli_records_and_reads_persisted_result(
    monkeypatch,
) -> None:
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
        review_id="feedback_review_dry_run_persisted_cli_001",
    )
    review_store = CapturingFeedbackOptimizationReviewStore(reviews=[review])
    execution_store = CapturingFeedbackExecutionDryRunStore()

    monkeypatch.setattr(
        "ads_growth_agent.cli.get_settings",
        lambda: Settings(
            feedback_review_persistence_backend="postgres",
            feedback_execution_persistence_backend="postgres",
        ),
    )
    monkeypatch.setattr(
        "ads_growth_agent.cli.build_configured_feedback_review_store",
        lambda settings: review_store,
    )
    monkeypatch.setattr(
        "ads_growth_agent.cli.build_configured_feedback_execution_store",
        lambda settings: execution_store,
    )

    dry_run_result = CliRunner().invoke(
        cli_app,
        ["dry-run-feedback-execution-plan", review.review_id],
    )
    dry_run_id = json.loads(dry_run_result.stdout)["dry_run_id"]
    get_result = CliRunner().invoke(
        cli_app,
        ["get-feedback-execution-dry-run", dry_run_id],
    )
    list_result = CliRunner().invoke(
        cli_app,
        [
            "list-feedback-execution-dry-runs",
            "--review-id",
            review.review_id,
            "--status",
            "passed",
            "--limit",
            "10",
        ],
    )

    assert dry_run_result.exit_code == 0
    assert execution_store.recorded_execution_plan_ids == [
        json.loads(dry_run_result.stdout)["execution_plan_id"]
    ]
    assert get_result.exit_code == 0
    assert json.loads(get_result.stdout)["dry_run_id"] == dry_run_id
    assert list_result.exit_code == 0
    list_payload = json.loads(list_result.stdout)
    assert list_payload["count"] == 1
    assert list_payload["items"][0]["dry_run_id"] == dry_run_id


def test_get_feedback_execution_dry_run_cli_requires_persistence(monkeypatch) -> None:
    monkeypatch.setattr(
        "ads_growth_agent.cli.get_settings",
        lambda: Settings(feedback_execution_persistence_backend="none"),
    )

    result = CliRunner().invoke(
        cli_app,
        ["get-feedback-execution-dry-run", "feedback_dry_run_missing"],
    )

    assert result.exit_code == 2
    assert "Feedback execution dry-run persistence is disabled." in result.stderr


def test_list_feedback_execution_dry_runs_cli_rejects_invalid_status(monkeypatch) -> None:
    monkeypatch.setattr(
        "ads_growth_agent.cli.get_settings",
        lambda: Settings(feedback_execution_persistence_backend="postgres"),
    )

    result = CliRunner().invoke(
        cli_app,
        ["list-feedback-execution-dry-runs", "--status", "unknown"],
    )

    assert result.exit_code == 2
    assert "Invalid feedback execution dry-run status" in result.stderr


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


def test_list_feedback_optimization_review_lineages_cli_rejects_invalid_stage(
    monkeypatch,
) -> None:
    monkeypatch.setattr(
        "ads_growth_agent.cli.get_settings",
        lambda: Settings(feedback_review_persistence_backend="postgres"),
    )

    result = CliRunner().invoke(
        cli_app,
        ["list-feedback-optimization-review-lineages", "--lineage-stage", "unknown"],
    )

    assert result.exit_code == 2
    assert "Invalid feedback review lineage stage" in result.stderr


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


class CapturingFeedbackExecutionDryRunStore:
    def __init__(
        self,
        dry_runs: list[CampaignFeedbackExecutionDryRunResponse] | None = None,
    ) -> None:
        self.dry_runs = dry_runs or []
        self.recorded_execution_plan_ids: list[str] = []
        self.requested_dry_run_ids: list[str] = []
        self.list_requests: list[FeedbackExecutionDryRunListRequest] = []

    def record_dry_run(
        self,
        execution_plan,
        dry_run: CampaignFeedbackExecutionDryRunResponse,
    ) -> CampaignFeedbackExecutionDryRunResponse:
        self.recorded_execution_plan_ids.append(execution_plan.execution_plan_id)
        self.dry_runs = [
            existing
            for existing in self.dry_runs
            if existing.dry_run_id != dry_run.dry_run_id
        ]
        self.dry_runs.append(dry_run)
        return dry_run

    def get_dry_run(self, dry_run_id: str) -> CampaignFeedbackExecutionDryRunResponse | None:
        self.requested_dry_run_ids.append(dry_run_id)
        for dry_run in self.dry_runs:
            if dry_run.dry_run_id == dry_run_id:
                return dry_run
        return None

    def list_dry_runs(
        self,
        *,
        review_id: str | None = None,
        execution_plan_id: str | None = None,
        event_id: str | None = None,
        advertiser_id: str | None = None,
        status: str | None = None,
        limit: int = 50,
    ) -> CampaignFeedbackExecutionDryRunListResponse:
        self.list_requests.append(
            (review_id, execution_plan_id, event_id, advertiser_id, status, limit)
        )
        items = [
            dry_run
            for dry_run in self.dry_runs
            if (review_id is None or dry_run.review_id == review_id)
            and (execution_plan_id is None or dry_run.execution_plan_id == execution_plan_id)
            and (event_id is None or dry_run.event_id == event_id)
            and (advertiser_id is None or dry_run.advertiser_id == advertiser_id)
            and (status is None or dry_run.status == status)
        ][:limit]
        return CampaignFeedbackExecutionDryRunListResponse(
            items=items,
            count=len(items),
            limit=limit,
            review_id=review_id,
            execution_plan_id=execution_plan_id,
            event_id=event_id,
            advertiser_id=advertiser_id,
            status=status,
        )


class CapturingFeedbackHandoffRecordStore:
    def __init__(
        self,
        records: list[CampaignFeedbackHandoffRecordResponse] | None = None,
    ) -> None:
        self.records = records or []
        self.recorded_package_ids: list[str] = []
        self.recorded_requests: list[CampaignFeedbackHandoffRecordRequest] = []
        self.requested_record_ids: list[str] = []
        self.list_requests: list[FeedbackHandoffRecordListRequest] = []

    def record_handoff(
        self,
        handoff_package,
        request: CampaignFeedbackHandoffRecordRequest,
    ) -> CampaignFeedbackHandoffRecordResponse:
        self.recorded_package_ids.append(handoff_package.handoff_package_id)
        self.recorded_requests.append(request)
        record = build_feedback_handoff_record(handoff_package, request)
        self.records.append(record)
        return record

    def get_handoff_record(
        self,
        handoff_record_id: str,
    ) -> CampaignFeedbackHandoffRecordResponse | None:
        self.requested_record_ids.append(handoff_record_id)
        for record in self.records:
            if record.handoff_record_id == handoff_record_id:
                return record
        return None

    def list_handoff_records(
        self,
        *,
        review_id: str | None = None,
        handoff_package_id: str | None = None,
        event_id: str | None = None,
        advertiser_id: str | None = None,
        outcome: FeedbackHandoffOutcome | None = None,
        limit: int = 50,
    ) -> CampaignFeedbackHandoffRecordListResponse:
        self.list_requests.append(
            (review_id, handoff_package_id, event_id, advertiser_id, outcome, limit)
        )
        items = [
            record
            for record in self.records
            if (review_id is None or record.review_id == review_id)
            and (handoff_package_id is None or record.handoff_package_id == handoff_package_id)
            and (event_id is None or record.event_id == event_id)
            and (advertiser_id is None or record.advertiser_id == advertiser_id)
            and (outcome is None or record.outcome == outcome)
        ][:limit]
        return CampaignFeedbackHandoffRecordListResponse(
            items=items,
            count=len(items),
            limit=limit,
            review_id=review_id,
            handoff_package_id=handoff_package_id,
            event_id=event_id,
            advertiser_id=advertiser_id,
            outcome=outcome,
        )


class CapturingAdvertiserMemoryStore:
    def __init__(self, result: AdvertiserMemoryWriteResult) -> None:
        self.result = result
        self.records: list[tuple[str, str]] = []
        self.handoff_records: list[str] = []

    def record_feedback_memory(self, event, analysis) -> AdvertiserMemoryWriteResult:
        self.records.append((event.event_id, analysis.feedback_id))
        return self.result

    def record_handoff_memory(self, record) -> AdvertiserMemoryWriteResult:
        self.handoff_records.append(record.handoff_record_id)
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
