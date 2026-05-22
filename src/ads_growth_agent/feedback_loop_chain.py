from ads_growth_agent.contracts import (
    CampaignFeedbackLoopChainResponse,
    CampaignFeedbackLoopSummaryResponse,
    CampaignFeedbackOutcomeReportResponse,
    CampaignPerformanceEventDetailResponse,
    FeedbackLoopChainRecommendedFocus,
)
from ads_growth_agent.feedback_loop_summary import (
    FeedbackExecutionLineageStore,
    FeedbackHandoffSummaryStore,
    FeedbackReviewLineageStore,
    build_campaign_feedback_loop_summary,
)
from ads_growth_agent.feedback_outcome_report import (
    FeedbackOutcomePerformanceEventStore,
    build_campaign_feedback_outcome_report,
)


def build_campaign_feedback_loop_chain(
    event: CampaignPerformanceEventDetailResponse,
    event_store: FeedbackOutcomePerformanceEventStore,
    review_store: FeedbackReviewLineageStore | None = None,
    execution_store: FeedbackExecutionLineageStore | None = None,
    handoff_store: FeedbackHandoffSummaryStore | None = None,
    *,
    review_persistence_enabled: bool = False,
    execution_persistence_enabled: bool = False,
    handoff_persistence_enabled: bool = False,
    limit: int = 50,
) -> CampaignFeedbackLoopChainResponse:
    """Compose a baseline -> outcome -> follow-up loop status projection."""

    effective_limit = min(max(limit, 1), 100)
    baseline_summary = build_campaign_feedback_loop_summary(
        event,
        review_store,
        execution_store,
        handoff_store,
        review_persistence_enabled=review_persistence_enabled,
        execution_persistence_enabled=execution_persistence_enabled,
        handoff_persistence_enabled=handoff_persistence_enabled,
        limit=effective_limit,
    )
    outcome_report = build_campaign_feedback_outcome_report(
        event,
        event_store,
        limit=effective_limit,
    )
    followup_summary = _followup_loop_summary(
        outcome_report,
        review_store,
        execution_store,
        handoff_store,
        review_persistence_enabled=review_persistence_enabled,
        execution_persistence_enabled=execution_persistence_enabled,
        handoff_persistence_enabled=handoff_persistence_enabled,
        limit=effective_limit,
    )
    recommended_focus = _recommended_focus(outcome_report, followup_summary)

    return CampaignFeedbackLoopChainResponse(
        event_id=event.event_id,
        advertiser_id=event.advertiser_id,
        run_id=event.run_id,
        campaign_id=event.campaign_id,
        draft_id=event.draft_id,
        baseline_current_stage=baseline_summary.current_stage,
        outcome_status=outcome_report.outcome_status,
        followup_event_id=outcome_report.followup_event_id,
        followup_current_stage=(
            followup_summary.current_stage if followup_summary else None
        ),
        recommended_focus=recommended_focus,
        baseline_summary=baseline_summary,
        outcome_report=outcome_report,
        followup_summary=followup_summary,
        summary=_summary_text(
            event.event_id,
            outcome_report,
            followup_summary,
            recommended_focus,
        ),
        guardrails=[
            "Feedback loop chain is a read-only operator projection.",
            "Follow-up optimization still requires draft review and dry-run validation.",
        ],
    )


def _followup_loop_summary(
    outcome_report: CampaignFeedbackOutcomeReportResponse,
    review_store: FeedbackReviewLineageStore | None,
    execution_store: FeedbackExecutionLineageStore | None,
    handoff_store: FeedbackHandoffSummaryStore | None,
    *,
    review_persistence_enabled: bool,
    execution_persistence_enabled: bool,
    handoff_persistence_enabled: bool,
    limit: int,
) -> CampaignFeedbackLoopSummaryResponse | None:
    if outcome_report.followup_event is None:
        return None
    return build_campaign_feedback_loop_summary(
        outcome_report.followup_event,
        review_store,
        execution_store,
        handoff_store,
        review_persistence_enabled=review_persistence_enabled,
        execution_persistence_enabled=execution_persistence_enabled,
        handoff_persistence_enabled=handoff_persistence_enabled,
        limit=limit,
    )


def _recommended_focus(
    outcome_report: CampaignFeedbackOutcomeReportResponse,
    followup_summary: CampaignFeedbackLoopSummaryResponse | None,
) -> FeedbackLoopChainRecommendedFocus:
    if outcome_report.outcome_status in {"no_followup_event", "insufficient_data"}:
        return "record_followup_snapshot"
    if outcome_report.outcome_status == "improved":
        return "monitor_followup_outcome"
    if followup_summary is None:
        return "review_followup_optimization_draft"
    match followup_summary.current_stage:
        case "review_pending" | "event_analyzed" | "rejected":
            return "review_followup_optimization_draft"
        case "revision_requested":
            return "generate_followup_revision"
        case "execution_ready":
            return "run_followup_execution_dry_run"
        case "dry_run_failed":
            return "inspect_followup_dry_run"
        case "dry_run_passed":
            return "prepare_followup_handoff"
        case "handoff_applied" | "handoff_blocked" | "handoff_skipped":
            return "monitor_followup_handoff"
    return "review_followup_optimization_draft"


def _summary_text(
    event_id: str,
    outcome_report: CampaignFeedbackOutcomeReportResponse,
    followup_summary: CampaignFeedbackLoopSummaryResponse | None,
    recommended_focus: FeedbackLoopChainRecommendedFocus,
) -> str:
    if followup_summary is None:
        return (
            f"Feedback loop chain for event {event_id}: "
            f"outcome={outcome_report.outcome_status}, "
            f"focus={recommended_focus}."
        )
    return (
        f"Feedback loop chain for event {event_id}: "
        f"outcome={outcome_report.outcome_status}, "
        f"followup_stage={followup_summary.current_stage}, "
        f"focus={recommended_focus}."
    )
