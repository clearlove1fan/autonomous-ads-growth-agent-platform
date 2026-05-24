import json
from datetime import UTC, datetime

from fastapi.testclient import TestClient
from typer.testing import CliRunner

from ads_growth_agent.api import (
    app as api_app,
)
from ads_growth_agent.api import (
    get_runtime_feedback_execution_store,
    get_runtime_feedback_handoff_store,
    get_runtime_feedback_review_store,
    get_runtime_outbox_store,
    get_runtime_performance_event_store,
    get_runtime_run_read_store,
    get_runtime_settings,
    get_runtime_strategy_job_store,
)
from ads_growth_agent.cli import app as cli_app
from ads_growth_agent.config import Settings
from ads_growth_agent.contracts import (
    AdvertiserBrief,
    AgentRunDetailResponse,
    CampaignPerformanceEventDetailResponse,
    CampaignPerformanceEventRequest,
    GrowthStrategyRequest,
    StrategyJobDetailResponse,
    StrategyJobStatus,
)
from ads_growth_agent.feedback import analyze_campaign_performance_event
from ads_growth_agent.ops_summary import build_ops_summary
from ads_growth_agent.persistence.feedback_execution_store import (
    NoopFeedbackExecutionDryRunStore,
)
from ads_growth_agent.persistence.feedback_handoff_store import (
    NoopFeedbackHandoffRecordStore,
)
from ads_growth_agent.persistence.feedback_review_store import (
    NoopFeedbackOptimizationReviewStore,
)
from ads_growth_agent.persistence.outbox_store import OutboxEventRecord


def test_ops_summary_builder_returns_compact_attention_view() -> None:
    settings = Settings(
        tenant_id="tenant_ops",
        run_persistence_backend="postgres",
        strategy_job_backend="memory",
        performance_event_persistence_backend="postgres",
        outbox_backend="postgres",
    )
    event = _performance_event_detail()
    summary = build_ops_summary(
        settings=settings,
        run_store=FakeRunReadStore([_failed_run()]),
        strategy_job_store=FakeStrategyJobStore([_failed_job()]),
        outbox_store=FakeOutboxStore([_failed_outbox_event()]),
        performance_event_store=FakePerformanceEventStore([event]),
        review_store=NoopFeedbackOptimizationReviewStore(),
        feedback_execution_store=NoopFeedbackExecutionDryRunStore(),
        handoff_store=NoopFeedbackHandoffRecordStore(),
        limit=10,
    )

    assert summary.tenant_id == "tenant_ops"
    assert summary.failed_run_count == 1
    assert summary.failed_runs[0].run_id == "run_failed_ops"
    assert summary.failed_strategy_job_count == 1
    assert summary.failed_strategy_jobs[0].error_code == "STRATEGY_JOB_EXECUTION_FAILED"
    assert summary.failed_outbox_event_count == 1
    assert summary.failed_outbox_events[0].error_message == "memory write failed"
    assert summary.feedback_attention_count == 1
    assert summary.feedback_needing_attention[0].event_id == event.event_id
    assert summary.feedback_needing_attention[0].health_status == "underperforming"
    assert summary.feedback_needing_attention[0].primary_command_id is not None
    assert summary.backends["outbox"] == "postgres"


def test_ops_summary_api_returns_headers_and_payload() -> None:
    event = _performance_event_detail()
    settings = Settings(
        tenant_id="tenant_ops_api",
        run_persistence_backend="postgres",
        strategy_job_backend="memory",
        performance_event_persistence_backend="postgres",
        outbox_backend="postgres",
    )
    api_app.dependency_overrides[get_runtime_settings] = lambda: settings
    api_app.dependency_overrides[get_runtime_run_read_store] = lambda: FakeRunReadStore(
        [_failed_run()]
    )
    api_app.dependency_overrides[get_runtime_strategy_job_store] = lambda: (
        FakeStrategyJobStore([_failed_job()])
    )
    api_app.dependency_overrides[get_runtime_outbox_store] = lambda: FakeOutboxStore(
        [_failed_outbox_event()]
    )
    api_app.dependency_overrides[get_runtime_performance_event_store] = lambda: (
        FakePerformanceEventStore([event])
    )
    api_app.dependency_overrides[get_runtime_feedback_review_store] = (
        lambda: NoopFeedbackOptimizationReviewStore()
    )
    api_app.dependency_overrides[get_runtime_feedback_execution_store] = (
        lambda: NoopFeedbackExecutionDryRunStore()
    )
    api_app.dependency_overrides[get_runtime_feedback_handoff_store] = (
        lambda: NoopFeedbackHandoffRecordStore()
    )
    try:
        response = TestClient(api_app).get(
            "/ops/summary",
            params={"limit": "5"},
            headers={"X-Tenant-ID": "tenant_ops_api"},
        )
    finally:
        api_app.dependency_overrides.clear()

    payload = response.json()
    assert response.status_code == 200
    assert response.headers["x-tenant-id"] == "tenant_ops_api"
    assert response.headers["ops-failed-run-count"] == "1"
    assert response.headers["ops-failed-strategy-job-count"] == "1"
    assert response.headers["ops-failed-outbox-event-count"] == "1"
    assert response.headers["ops-feedback-attention-count"] == "1"
    assert payload["limit"] == 5
    assert payload["failed_runs"][0]["run_id"] == "run_failed_ops"
    assert payload["feedback_needing_attention"][0]["primary_command_id"] is not None


def test_ops_summary_cli_outputs_same_contract(monkeypatch) -> None:
    event = _performance_event_detail()
    monkeypatch.setattr(
        "ads_growth_agent.cli.get_settings",
        lambda: Settings(
            tenant_id="tenant_ops_cli",
            run_persistence_backend="postgres",
            strategy_job_backend="memory",
            performance_event_persistence_backend="postgres",
            outbox_backend="postgres",
        ),
    )
    monkeypatch.setattr(
        "ads_growth_agent.cli.build_configured_run_read_store",
        lambda settings: FakeRunReadStore([_failed_run()]),
    )
    monkeypatch.setattr(
        "ads_growth_agent.cli.build_configured_strategy_job_store",
        lambda settings: FakeStrategyJobStore([_failed_job()]),
    )
    monkeypatch.setattr(
        "ads_growth_agent.cli.build_configured_outbox_store",
        lambda settings: FakeOutboxStore([_failed_outbox_event()]),
    )
    monkeypatch.setattr(
        "ads_growth_agent.cli.build_configured_performance_event_store",
        lambda settings: FakePerformanceEventStore([event]),
    )
    monkeypatch.setattr(
        "ads_growth_agent.cli.build_configured_feedback_review_store",
        lambda settings: NoopFeedbackOptimizationReviewStore(),
    )
    monkeypatch.setattr(
        "ads_growth_agent.cli.build_configured_feedback_execution_store",
        lambda settings: NoopFeedbackExecutionDryRunStore(),
    )
    monkeypatch.setattr(
        "ads_growth_agent.cli.build_configured_feedback_handoff_store",
        lambda settings: NoopFeedbackHandoffRecordStore(),
    )

    result = CliRunner().invoke(cli_app, ["ops-summary", "--limit", "5"])

    payload = json.loads(result.stdout)
    assert result.exit_code == 0
    assert payload["tenant_id"] == "tenant_ops_cli"
    assert payload["failed_run_count"] == 1
    assert payload["failed_strategy_job_count"] == 1
    assert payload["failed_outbox_event_count"] == 1
    assert payload["feedback_attention_count"] == 1


class FakeRunReadStore:
    def __init__(self, runs: list[AgentRunDetailResponse] | None = None) -> None:
        self.runs = runs or []

    def get_run(self, run_id: str):
        return next((run for run in self.runs if run.run_id == run_id), None)

    def list_runs(self, *, status=None, limit: int = 50):
        runs = self.runs
        if status is not None:
            runs = [run for run in runs if run.status == status]
        return runs[:limit]


class FakeStrategyJobStore:
    def __init__(self, jobs: list[StrategyJobDetailResponse] | None = None) -> None:
        self.jobs = jobs or []

    def list_jobs(
        self,
        *,
        status=None,
        advertiser_id=None,
        run_id=None,
        limit: int = 50,
    ):
        jobs = self.jobs
        if status is not None:
            jobs = [job for job in jobs if job.status == status]
        if advertiser_id is not None:
            jobs = [job for job in jobs if job.advertiser_id == advertiser_id]
        if run_id is not None:
            jobs = [job for job in jobs if job.run_id == run_id]
        return jobs[:limit]


class FakeOutboxStore:
    def __init__(self, events: list[OutboxEventRecord] | None = None) -> None:
        self.events = events or []

    def list_events(
        self,
        *,
        status=None,
        event_type=None,
        aggregate_type=None,
        aggregate_id=None,
        limit: int = 50,
    ):
        events = self.events
        if status is not None:
            events = [event for event in events if event.status == status]
        return events[:limit]


class FakePerformanceEventStore:
    def __init__(
        self,
        events: list[CampaignPerformanceEventDetailResponse] | None = None,
    ) -> None:
        self.events = events or []

    def get_event(self, event_id: str):
        return next((event for event in self.events if event.event_id == event_id), None)

    def list_events(
        self,
        *,
        advertiser_id=None,
        run_id=None,
        campaign_id=None,
        draft_id=None,
        event_type=None,
        limit: int = 50,
    ):
        events = self.events
        if advertiser_id is not None:
            events = [event for event in events if event.advertiser_id == advertiser_id]
        return events[:limit]


def _failed_run() -> AgentRunDetailResponse:
    now = datetime.now(UTC)
    return AgentRunDetailResponse(
        run_id="run_failed_ops",
        execution_id="run_failed_ops",
        strategy_id="strategy_ops",
        advertiser_id="adv_ops",
        objective="registrations",
        status="failed",
        trace_id="trace_ops",
        error_summary=["planner failed"],
        metadata={},
        steps=[],
        created_at=now,
        completed_at=now,
    )


def _failed_job() -> StrategyJobDetailResponse:
    now = datetime.now(UTC)
    request = GrowthStrategyRequest(brief=_brief())
    return StrategyJobDetailResponse(
        job_id="job_failed_ops",
        status=StrategyJobStatus.FAILED,
        strategy_id="strategy_ops",
        advertiser_id="adv_ops",
        objective="registrations",
        run_id="run_job_failed_ops",
        trace_id="trace_job_ops",
        request=request,
        error={
            "error_code": "STRATEGY_JOB_EXECUTION_FAILED",
            "message": "planner failed",
        },
        attempt_count=3,
        max_attempts=3,
        created_at=now,
        updated_at=now,
        completed_at=now,
    )


def _failed_outbox_event() -> OutboxEventRecord:
    now = datetime.now(UTC)
    return OutboxEventRecord(
        outbox_event_id="outbox_failed_ops",
        event_type="campaign_performance_analyzed",
        aggregate_type="campaign_performance_event",
        aggregate_id="evt_ops",
        idempotency_key="idem_ops",
        status="failed",
        payload={"event_id": "evt_ops"},
        error_json={"type": "RuntimeError", "message": "memory write failed"},
        attempt_count=3,
        max_attempts=3,
        metadata={},
        created_at=now,
        updated_at=now,
        completed_at=now,
    )


def _performance_event_detail() -> CampaignPerformanceEventDetailResponse:
    event = CampaignPerformanceEventRequest(
        event_id="evt_ops",
        advertiser_id="adv_ops",
        run_id="run_ops",
        campaign_id="cmp_ops",
        draft_id="draft_ops",
        objective="registrations",
        occurred_at=datetime.now(UTC),
        metrics={
            "impressions": 10_000,
            "clicks": 500,
            "spend": "1000.00",
            "conversions": 20,
        },
        target_cpa="20.00",
    )
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
        metadata={},
        analysis=analysis,
        created_at=analysis.created_at,
        updated_at=analysis.created_at,
    )


def _brief() -> AdvertiserBrief:
    return AdvertiserBrief(
        advertiser_id="adv_ops",
        product_name="FitTrack Pro",
        product_category="fitness app",
        objective="registrations",
        budget="2000.00",
        currency="USD",
        duration_days=14,
        target_market="United States",
        primary_kpi="trial registrations",
        target_cpa="20.00",
    )
