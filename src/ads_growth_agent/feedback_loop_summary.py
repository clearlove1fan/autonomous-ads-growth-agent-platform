from typing import Protocol

from ads_growth_agent.contracts import (
    CampaignFeedbackExecutionDryRunListResponse,
    CampaignFeedbackHandoffRecordListResponse,
    CampaignFeedbackLoopSummaryResponse,
    CampaignFeedbackOptimizationReviewLineageListResponse,
    CampaignFeedbackOptimizationReviewListResponse,
    CampaignPerformanceEventDetailResponse,
    FeedbackHandoffOutcome,
    FeedbackOptimizationReviewDecision,
)
from ads_growth_agent.feedback import (
    build_campaign_feedback_action_plan,
    build_campaign_feedback_optimization_draft,
)
from ads_growth_agent.feedback_lineage import (
    FeedbackExecutionLineageStore,
    FeedbackReviewLineageStore,
    list_feedback_optimization_review_lineages,
)


class FeedbackHandoffSummaryStore(Protocol):
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
        """Return recent handoff acknowledgement records for summary projection."""


def build_campaign_feedback_loop_summary(
    event: CampaignPerformanceEventDetailResponse,
    review_store: FeedbackReviewLineageStore | None = None,
    execution_store: FeedbackExecutionLineageStore | None = None,
    handoff_store: FeedbackHandoffSummaryStore | None = None,
    *,
    review_persistence_enabled: bool = False,
    execution_persistence_enabled: bool = False,
    handoff_persistence_enabled: bool = False,
    limit: int = 50,
) -> CampaignFeedbackLoopSummaryResponse:
    """Compose an operator-facing summary for one persisted feedback event."""

    action_plan = build_campaign_feedback_action_plan(event)
    optimization_draft = build_campaign_feedback_optimization_draft(event)
    reviews = _list_reviews(event, review_store, limit=limit)
    dry_runs = _list_dry_runs(event, execution_store, limit=limit)
    handoff_records = _list_handoff_records(event, handoff_store, limit=limit)
    lineages = list_feedback_optimization_review_lineages(
        review_store,
        execution_store,
        event_id=event.event_id,
        limit=limit,
    ) if review_store is not None else _empty_lineage_list(event, limit=limit)

    review_items = sorted(reviews.items, key=lambda review: review.created_at, reverse=True)
    dry_run_items = sorted(
        dry_runs.items,
        key=lambda dry_run: dry_run.created_at,
        reverse=True,
    )
    latest_review = review_items[0] if review_items else None
    latest_dry_run = dry_run_items[0] if dry_run_items else None
    handoff_record_items = sorted(
        handoff_records.items,
        key=lambda record: record.created_at,
        reverse=True,
    )
    latest_handoff_record = handoff_record_items[0] if handoff_record_items else None
    approved_review_ids = _unique(
        review.review_id
        for review in reviews.items
        if review.decision == FeedbackOptimizationReviewDecision.APPROVED
    )
    execution_ready_review_ids = _unique(
        review_id
        for lineage in lineages.items
        for review_id in lineage.execution_ready_review_ids
    )
    current_stage = _current_stage(
        latest_review_decision=latest_review.decision if latest_review else None,
        execution_ready_review_ids=execution_ready_review_ids,
        latest_dry_run_status=latest_dry_run.status if latest_dry_run else None,
        latest_handoff_outcome=(
            latest_handoff_record.outcome if latest_handoff_record else None
        ),
    )

    return CampaignFeedbackLoopSummaryResponse(
        event_id=event.event_id,
        advertiser_id=event.advertiser_id,
        run_id=event.run_id,
        campaign_id=event.campaign_id,
        draft_id=event.draft_id,
        current_stage=current_stage,
        review_persistence_enabled=review_persistence_enabled,
        execution_persistence_enabled=execution_persistence_enabled,
        handoff_persistence_enabled=handoff_persistence_enabled,
        review_count=reviews.count,
        lineage_count=lineages.count,
        dry_run_count=dry_runs.count,
        handoff_record_count=handoff_records.count,
        latest_review_id=latest_review.review_id if latest_review else None,
        latest_review_decision=latest_review.decision if latest_review else None,
        latest_dry_run_id=latest_dry_run.dry_run_id if latest_dry_run else None,
        latest_dry_run_status=latest_dry_run.status if latest_dry_run else None,
        latest_handoff_record_id=(
            latest_handoff_record.handoff_record_id if latest_handoff_record else None
        ),
        latest_handoff_outcome=(
            latest_handoff_record.outcome if latest_handoff_record else None
        ),
        approved_review_ids=approved_review_ids,
        execution_ready_review_ids=execution_ready_review_ids,
        next_operator_actions=_next_operator_actions(
            current_stage,
            review_persistence_enabled=review_persistence_enabled,
            execution_persistence_enabled=execution_persistence_enabled,
        ),
        summary=_summary_text(
            event_id=event.event_id,
            current_stage=current_stage,
            review_count=reviews.count,
            lineage_count=lineages.count,
            dry_run_count=dry_runs.count,
            handoff_record_count=handoff_records.count,
        ),
        event=event,
        action_plan=action_plan,
        optimization_draft=optimization_draft,
        reviews=reviews,
        lineages=lineages,
        dry_runs=dry_runs,
        handoff_records=handoff_records,
        guardrails=[
            "Feedback loop summary is a read-only operator projection.",
            "v0.1 remains draft-only and does not execute live campaign changes.",
        ],
    )


def _list_handoff_records(
    event: CampaignPerformanceEventDetailResponse,
    handoff_store: FeedbackHandoffSummaryStore | None,
    *,
    limit: int,
) -> CampaignFeedbackHandoffRecordListResponse:
    if handoff_store is None:
        return CampaignFeedbackHandoffRecordListResponse(
            items=[],
            count=0,
            limit=limit,
            event_id=event.event_id,
            advertiser_id=event.advertiser_id,
        )
    return handoff_store.list_handoff_records(
        event_id=event.event_id,
        advertiser_id=event.advertiser_id,
        limit=limit,
    )


def _list_reviews(
    event: CampaignPerformanceEventDetailResponse,
    review_store: FeedbackReviewLineageStore | None,
    *,
    limit: int,
) -> CampaignFeedbackOptimizationReviewListResponse:
    if review_store is None:
        return CampaignFeedbackOptimizationReviewListResponse(
            items=[],
            count=0,
            limit=limit,
            event_id=event.event_id,
            advertiser_id=event.advertiser_id,
        )
    return review_store.list_reviews(
        event_id=event.event_id,
        advertiser_id=event.advertiser_id,
        limit=limit,
    )


def _list_dry_runs(
    event: CampaignPerformanceEventDetailResponse,
    execution_store: FeedbackExecutionLineageStore | None,
    *,
    limit: int,
) -> CampaignFeedbackExecutionDryRunListResponse:
    if execution_store is None:
        return CampaignFeedbackExecutionDryRunListResponse(
            items=[],
            count=0,
            limit=limit,
            event_id=event.event_id,
            advertiser_id=event.advertiser_id,
        )
    return execution_store.list_dry_runs(
        event_id=event.event_id,
        advertiser_id=event.advertiser_id,
        limit=limit,
    )


def _empty_lineage_list(
    event: CampaignPerformanceEventDetailResponse,
    *,
    limit: int,
) -> CampaignFeedbackOptimizationReviewLineageListResponse:
    return CampaignFeedbackOptimizationReviewLineageListResponse(
        items=[],
        count=0,
        limit=limit,
        event_id=event.event_id,
        advertiser_id=event.advertiser_id,
    )


def _current_stage(
    *,
    latest_review_decision: FeedbackOptimizationReviewDecision | None,
    execution_ready_review_ids: list[str],
    latest_dry_run_status: str | None,
    latest_handoff_outcome: FeedbackHandoffOutcome | None,
):
    if latest_handoff_outcome == FeedbackHandoffOutcome.APPLIED:
        return "handoff_applied"
    if latest_handoff_outcome == FeedbackHandoffOutcome.BLOCKED:
        return "handoff_blocked"
    if latest_handoff_outcome == FeedbackHandoffOutcome.SKIPPED:
        return "handoff_skipped"
    if latest_dry_run_status == "passed":
        return "dry_run_passed"
    if latest_dry_run_status == "failed":
        return "dry_run_failed"
    if execution_ready_review_ids:
        return "execution_ready"
    if latest_review_decision == FeedbackOptimizationReviewDecision.NEEDS_REVISION:
        return "revision_requested"
    if latest_review_decision == FeedbackOptimizationReviewDecision.REJECTED:
        return "rejected"
    if latest_review_decision is None:
        return "review_pending"
    return "event_analyzed"


def _next_operator_actions(
    current_stage: str,
    *,
    review_persistence_enabled: bool,
    execution_persistence_enabled: bool,
) -> list[str]:
    if not review_persistence_enabled:
        return ["Enable feedback review persistence to record approval decisions."]
    if current_stage == "review_pending":
        return ["Review the optimization draft and approve, reject, or request revision."]
    if current_stage == "revision_requested":
        return ["Generate a revision draft and submit a second-pass review."]
    if current_stage == "rejected":
        return ["Create a new optimization proposal from updated performance context."]
    if current_stage == "execution_ready":
        if not execution_persistence_enabled:
            return ["Run dry-run execution validation before any manual handoff."]
        return ["Run and persist dry-run execution validation for the approved review."]
    if current_stage == "dry_run_failed":
        return ["Inspect blocked dry-run steps and revise the approved draft before handoff."]
    if current_stage == "dry_run_passed":
        return ["Use the validated draft package for manual campaign-platform handoff."]
    if current_stage == "handoff_applied":
        return ["Monitor the manually applied changes and ingest the next performance event."]
    if current_stage == "handoff_blocked":
        return ["Review blocked handoff notes and create a revised optimization proposal."]
    if current_stage == "handoff_skipped":
        return ["Confirm the skip reason and continue monitoring campaign performance."]
    return ["Inspect feedback recommendations and decide the next review action."]


def _summary_text(
    *,
    event_id: str,
    current_stage: str,
    review_count: int,
    lineage_count: int,
    dry_run_count: int,
    handoff_record_count: int,
) -> str:
    return (
        f"Feedback loop summary for event {event_id}: stage={current_stage}, "
        f"reviews={review_count}, lineages={lineage_count}, dry_runs={dry_run_count}, "
        f"handoffs={handoff_record_count}."
    )


def _unique(values) -> list[str]:
    seen: set[str] = set()
    ordered: list[str] = []
    for value in values:
        if value in seen:
            continue
        seen.add(value)
        ordered.append(value)
    return ordered
