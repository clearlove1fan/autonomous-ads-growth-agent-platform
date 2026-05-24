from datetime import UTC, datetime

from ads_growth_agent.config import Settings
from ads_growth_agent.contracts import (
    AgentRunDetailResponse,
    CampaignPerformanceEventDetailResponse,
    FeedbackHealthStatus,
    OpsFeedbackAttentionSummary,
    OpsOutboxEventSummary,
    OpsRunSummary,
    OpsStrategyJobSummary,
    OpsSummaryResponse,
    OutboxEventStatus,
    StrategyJobDetailResponse,
    StrategyJobStatus,
)
from ads_growth_agent.feedback_loop_command_center import (
    build_campaign_feedback_loop_command_center,
)
from ads_growth_agent.persistence.outbox_store import OutboxEventRecord, OutboxStore
from ads_growth_agent.persistence.performance_event_store import CampaignPerformanceEventStore
from ads_growth_agent.persistence.run_read_store import AgentRunReadStore
from ads_growth_agent.persistence.strategy_job_store import StrategyJobStore


def build_ops_summary(
    *,
    settings: Settings,
    run_store: AgentRunReadStore,
    strategy_job_store: StrategyJobStore,
    outbox_store: OutboxStore,
    performance_event_store: CampaignPerformanceEventStore,
    review_store,
    feedback_execution_store,
    handoff_store,
    limit: int = 20,
) -> OpsSummaryResponse:
    failed_runs = [
        _run_summary(run)
        for run in run_store.list_runs(status="failed", limit=limit)
    ]
    failed_jobs = [
        _strategy_job_summary(job)
        for job in strategy_job_store.list_jobs(
            status=StrategyJobStatus.FAILED,
            limit=limit,
        )
    ]
    failed_outbox_events = [
        _outbox_event_summary(event)
        for event in outbox_store.list_events(
            status=OutboxEventStatus.FAILED.value,
            limit=limit,
        )
    ]
    feedback_attention = _feedback_attention_summaries(
        settings=settings,
        performance_event_store=performance_event_store,
        review_store=review_store,
        feedback_execution_store=feedback_execution_store,
        handoff_store=handoff_store,
        limit=limit,
    )
    return OpsSummaryResponse(
        tenant_id=settings.tenant_id,
        generated_at=datetime.now(UTC),
        limit=limit,
        failed_run_count=len(failed_runs),
        failed_strategy_job_count=len(failed_jobs),
        failed_outbox_event_count=len(failed_outbox_events),
        feedback_attention_count=len(feedback_attention),
        failed_runs=failed_runs,
        failed_strategy_jobs=failed_jobs,
        failed_outbox_events=failed_outbox_events,
        feedback_needing_attention=feedback_attention,
        backends={
            "run_persistence": settings.run_persistence_backend,
            "strategy_job": settings.strategy_job_backend,
            "performance_event": settings.performance_event_persistence_backend,
            "feedback_review": settings.feedback_review_persistence_backend,
            "feedback_execution": settings.feedback_execution_persistence_backend,
            "feedback_handoff": settings.feedback_execution_persistence_backend,
            "outbox": settings.outbox_backend,
        },
        guardrails=[
            "Ops summary is a local Phase 2 diagnostic read model, not an SLO dashboard.",
            "Counts are bounded by the request limit and are not full historical totals.",
            "v0.1 remains draft-only and does not execute live ad platform mutations.",
        ],
    )


def _run_summary(run: AgentRunDetailResponse) -> OpsRunSummary:
    return OpsRunSummary(
        run_id=run.run_id,
        execution_id=run.execution_id,
        strategy_id=run.strategy_id,
        advertiser_id=run.advertiser_id,
        objective=run.objective,
        status=run.status,
        trace_id=run.trace_id,
        error_summary=run.error_summary,
        created_at=run.created_at,
        completed_at=run.completed_at,
    )


def _strategy_job_summary(job: StrategyJobDetailResponse) -> OpsStrategyJobSummary:
    error = job.error or {}
    return OpsStrategyJobSummary(
        job_id=job.job_id,
        status=job.status,
        strategy_id=job.strategy_id,
        advertiser_id=job.advertiser_id,
        objective=job.objective,
        run_id=job.run_id,
        trace_id=job.trace_id,
        attempt_count=job.attempt_count,
        max_attempts=job.max_attempts,
        next_attempt_at=job.next_attempt_at,
        locked_by=job.locked_by,
        locked_until=job.locked_until,
        error_code=_string_or_none(error.get("error_code")),
        error_message=_string_or_none(error.get("message") or error.get("detail")),
        created_at=job.created_at,
        updated_at=job.updated_at,
        completed_at=job.completed_at,
    )


def _outbox_event_summary(event: OutboxEventRecord) -> OpsOutboxEventSummary:
    error = event.error_json or {}
    return OpsOutboxEventSummary(
        outbox_event_id=event.outbox_event_id,
        event_type=event.event_type,
        aggregate_type=event.aggregate_type,
        aggregate_id=event.aggregate_id,
        status=event.status,
        attempt_count=event.attempt_count,
        max_attempts=event.max_attempts,
        next_attempt_at=event.next_attempt_at,
        locked_by=event.locked_by,
        locked_until=event.locked_until,
        error_type=_string_or_none(error.get("type") or error.get("error_type")),
        error_message=_string_or_none(error.get("message") or error.get("detail")),
        created_at=event.created_at,
        updated_at=event.updated_at,
        completed_at=event.completed_at,
    )


def _feedback_attention_summaries(
    *,
    settings: Settings,
    performance_event_store: CampaignPerformanceEventStore,
    review_store,
    feedback_execution_store,
    handoff_store,
    limit: int,
) -> list[OpsFeedbackAttentionSummary]:
    events = performance_event_store.list_events(limit=limit)
    attention_events = [
        event
        for event in events
        if event.analysis.health_status != FeedbackHealthStatus.ON_TRACK
    ][:limit]
    return [
        _feedback_attention_summary(
            event,
            settings=settings,
            performance_event_store=performance_event_store,
            review_store=review_store,
            feedback_execution_store=feedback_execution_store,
            handoff_store=handoff_store,
            limit=limit,
        )
        for event in attention_events
    ]


def _feedback_attention_summary(
    event: CampaignPerformanceEventDetailResponse,
    *,
    settings: Settings,
    performance_event_store: CampaignPerformanceEventStore,
    review_store,
    feedback_execution_store,
    handoff_store,
    limit: int,
) -> OpsFeedbackAttentionSummary:
    command_center = build_campaign_feedback_loop_command_center(
        event,
        review_store,
        feedback_execution_store,
        handoff_store,
        review_persistence_enabled=settings.feedback_review_persistence_backend != "none",
        execution_persistence_enabled=(
            settings.feedback_execution_persistence_backend != "none"
        ),
        handoff_persistence_enabled=(
            settings.feedback_execution_persistence_backend != "none"
        ),
        outcome_event_store=performance_event_store,
        limit=limit,
    )
    primary_command = command_center.primary_command
    return OpsFeedbackAttentionSummary(
        event_id=event.event_id,
        feedback_id=event.analysis.feedback_id,
        advertiser_id=event.advertiser_id,
        run_id=event.run_id,
        campaign_id=event.campaign_id,
        draft_id=event.draft_id,
        health_status=event.analysis.health_status,
        current_stage=command_center.current_stage,
        primary_command_id=command_center.primary_command_id,
        primary_command_label=primary_command.label if primary_command else None,
        primary_command_api_method=(
            primary_command.api_method if primary_command else None
        ),
        primary_command_api_path=primary_command.api_path if primary_command else None,
        primary_command_cli=primary_command.cli_command if primary_command else [],
        occurred_at=event.occurred_at,
        created_at=event.created_at,
    )


def _string_or_none(value: object) -> str | None:
    if value is None:
        return None
    text = str(value)
    return text if text else None
