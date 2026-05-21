from dataclasses import dataclass
from typing import Any

from ads_growth_agent.contracts import (
    CampaignFeedbackExecutionDryRunResponse,
    CampaignFeedbackHandoffPackageResponse,
    CampaignFeedbackHandoffRecordResponse,
    CampaignFeedbackLoopTimelineResponse,
    CampaignFeedbackOptimizationReviewResponse,
    CampaignPerformanceEventDetailResponse,
    FeedbackHandoffOutcome,
    FeedbackLoopTimelineEntry,
    FeedbackLoopTimelineStage,
    FeedbackOptimizationReviewDecision,
)
from ads_growth_agent.feedback import (
    FeedbackRevisionDraftNotRequestedError,
    build_campaign_feedback_optimization_revision_draft,
)
from ads_growth_agent.feedback_execution_plan import (
    FeedbackExecutionPlanNotApprovedError,
    build_feedback_execution_plan,
)
from ads_growth_agent.feedback_handoff_package import build_feedback_handoff_package
from ads_growth_agent.feedback_loop_summary import (
    FeedbackExecutionLineageStore,
    FeedbackHandoffSummaryStore,
    FeedbackReviewLineageStore,
    build_campaign_feedback_loop_summary,
)


@dataclass(frozen=True)
class _PendingTimelineEntry:
    sort_group: int
    sort_index: int
    entry: FeedbackLoopTimelineEntry


def build_campaign_feedback_loop_timeline(
    event: CampaignPerformanceEventDetailResponse,
    review_store: FeedbackReviewLineageStore | None = None,
    execution_store: FeedbackExecutionLineageStore | None = None,
    handoff_store: FeedbackHandoffSummaryStore | None = None,
    *,
    review_persistence_enabled: bool = False,
    execution_persistence_enabled: bool = False,
    handoff_persistence_enabled: bool = False,
    limit: int = 50,
) -> CampaignFeedbackLoopTimelineResponse:
    """Compose an operator audit timeline for one persisted feedback event."""

    effective_limit = min(max(limit, 1), 100)
    summary = build_campaign_feedback_loop_summary(
        event,
        review_store,
        execution_store,
        handoff_store,
        review_persistence_enabled=review_persistence_enabled,
        execution_persistence_enabled=execution_persistence_enabled,
        handoff_persistence_enabled=handoff_persistence_enabled,
        limit=effective_limit,
    )

    pending: list[_PendingTimelineEntry] = []
    _add_entry(
        pending,
        sort_group=0,
        sort_index=0,
        entry_id=f"timeline:{event.event_id}:performance-event",
        occurred_at=event.created_at,
        stage="performance_event_analyzed",
        resource_type="performance_event",
        resource_id=event.event_id,
        status=event.status,
        title="Performance event analyzed",
        summary=(
            f"Analyzed {event.event_type.value} with "
            f"health={event.analysis.health_status.value} and "
            f"{len(event.analysis.recommendations)} recommendation(s)."
        ),
        related_ids=_related_ids(event_id=event.event_id, feedback_id=event.analysis.feedback_id),
        metadata={
            "event_type": event.event_type.value,
            "health_status": event.analysis.health_status.value,
        },
    )
    _add_entry(
        pending,
        sort_group=1,
        sort_index=0,
        entry_id=f"timeline:{event.event_id}:action-plan",
        occurred_at=event.created_at,
        stage="feedback_action_plan_created",
        resource_type="feedback_action_plan",
        resource_id=summary.action_plan.feedback_id,
        status=summary.action_plan.health_status.value,
        title="Feedback action plan created",
        summary=summary.action_plan.summary,
        related_ids=_related_ids(
            event_id=event.event_id,
            feedback_id=summary.action_plan.feedback_id,
            draft_id=summary.action_plan.draft_id,
            strategy_id=summary.action_plan.strategy_id,
        ),
        metadata={
            "step_count": len(summary.action_plan.steps),
            "derived_created_at": summary.action_plan.created_at.isoformat(),
        },
    )
    _add_entry(
        pending,
        sort_group=2,
        sort_index=0,
        entry_id=f"timeline:{event.event_id}:optimization-draft",
        occurred_at=event.created_at,
        stage="optimization_draft_created",
        resource_type="optimization_draft",
        resource_id=summary.optimization_draft.optimization_draft_id,
        status=summary.optimization_draft.status,
        title="Optimization draft created",
        summary=summary.optimization_draft.summary,
        related_ids=_related_ids(
            event_id=event.event_id,
            feedback_id=summary.optimization_draft.feedback_id,
            optimization_draft_id=summary.optimization_draft.optimization_draft_id,
            draft_id=summary.optimization_draft.base_draft_id,
            strategy_id=summary.optimization_draft.strategy_id,
        ),
        metadata={
            "change_count": len(summary.optimization_draft.changes),
            "requires_human_approval": summary.optimization_draft.requires_human_approval,
            "derived_created_at": summary.optimization_draft.created_at.isoformat(),
        },
    )

    review_items = sorted(summary.reviews.items, key=lambda review: review.created_at)
    for review_index, review in enumerate(review_items):
        review_group = 100 + review_index * 10
        _add_review_entry(pending, review, sort_group=review_group)
        if review.decision == FeedbackOptimizationReviewDecision.NEEDS_REVISION:
            _add_revision_draft_entry(pending, review, sort_group=review_group + 1)
        if review.decision == FeedbackOptimizationReviewDecision.APPROVED:
            _add_execution_plan_entry(pending, review, sort_group=review_group + 2)
            _add_handoff_package_entry(
                pending,
                review,
                execution_store,
                sort_group=600 + review_index,
            )

    for dry_run_index, dry_run in enumerate(
        sorted(summary.dry_runs.items, key=lambda item: item.created_at)
    ):
        _add_dry_run_entry(
            pending,
            dry_run,
            sort_group=500,
            sort_index=dry_run_index,
        )

    for handoff_index, handoff_record in enumerate(
        sorted(summary.handoff_records.items, key=lambda item: item.created_at)
    ):
        _add_handoff_record_entry(
            pending,
            handoff_record,
            sort_group=700,
            sort_index=handoff_index,
        )

    ordered = sorted(
        pending,
        key=lambda item: (item.sort_group, item.entry.occurred_at, item.sort_index),
    )
    total_entry_count = len(ordered)
    returned = ordered[:effective_limit]
    entries = [
        item.entry.model_copy(update={"sequence": sequence})
        for sequence, item in enumerate(returned, start=1)
    ]
    latest = ordered[-1].entry if ordered else None

    return CampaignFeedbackLoopTimelineResponse(
        event_id=event.event_id,
        advertiser_id=event.advertiser_id,
        run_id=event.run_id,
        campaign_id=event.campaign_id,
        draft_id=event.draft_id,
        current_stage=summary.current_stage,
        latest_entry_id=latest.entry_id if latest else None,
        latest_entry_stage=latest.stage if latest else None,
        entry_count=len(entries),
        total_entry_count=total_entry_count,
        limit=effective_limit,
        truncated=total_entry_count > effective_limit,
        entries=entries,
        summary=(
            f"Feedback loop timeline for event {event.event_id}: "
            f"{len(entries)} of {total_entry_count} entry(s), "
            f"current_stage={summary.current_stage}."
        ),
        guardrails=[
            "Feedback loop timeline is a read-only operator audit projection.",
            "Derived entries do not execute or mutate live campaign changes.",
        ],
    )


def _add_review_entry(
    pending: list[_PendingTimelineEntry],
    review: CampaignFeedbackOptimizationReviewResponse,
    *,
    sort_group: int,
) -> None:
    stage = _review_stage(review)
    _add_entry(
        pending,
        sort_group=sort_group,
        sort_index=0,
        entry_id=f"timeline:{review.event_id}:review:{review.review_id}",
        occurred_at=review.created_at,
        stage=stage,
        resource_type="optimization_review",
        resource_id=review.review_id,
        status=review.decision.value,
        title=_stage_title(stage),
        summary=_review_summary(review),
        actor_id=review.reviewer_id,
        related_ids=_related_ids(
            event_id=review.event_id,
            feedback_id=review.feedback_id,
            review_id=review.review_id,
            optimization_draft_id=review.optimization_draft_id,
            draft_id=review.base_draft_id,
            strategy_id=review.strategy_id,
        ),
        metadata={
            "selected_change_count": len(review.selected_change_ids),
            "notes_present": review.notes is not None,
        },
    )


def _add_revision_draft_entry(
    pending: list[_PendingTimelineEntry],
    review: CampaignFeedbackOptimizationReviewResponse,
    *,
    sort_group: int,
) -> None:
    try:
        revision_draft = build_campaign_feedback_optimization_revision_draft(review)
    except (FeedbackRevisionDraftNotRequestedError, ValueError):
        return
    _add_entry(
        pending,
        sort_group=sort_group,
        sort_index=0,
        entry_id=f"timeline:{review.event_id}:revision-draft:{revision_draft.revision_draft_id}",
        occurred_at=review.created_at,
        stage="revision_draft_created",
        resource_type="revision_draft",
        resource_id=revision_draft.revision_draft_id,
        status=revision_draft.status,
        title="Revision draft created",
        summary=revision_draft.summary,
        actor_id=review.reviewer_id,
        related_ids=_related_ids(
            event_id=revision_draft.event_id,
            feedback_id=revision_draft.feedback_id,
            source_review_id=review.review_id,
            optimization_draft_id=revision_draft.original_optimization_draft_id,
            revision_draft_id=revision_draft.revision_draft_id,
            draft_id=revision_draft.base_draft_id,
            strategy_id=revision_draft.strategy_id,
        ),
        metadata={
            "change_count": len(revision_draft.changes),
            "derived_created_at": revision_draft.created_at.isoformat(),
        },
    )


def _add_execution_plan_entry(
    pending: list[_PendingTimelineEntry],
    review: CampaignFeedbackOptimizationReviewResponse,
    *,
    sort_group: int,
) -> None:
    try:
        execution_plan = build_feedback_execution_plan(review)
    except (FeedbackExecutionPlanNotApprovedError, ValueError):
        return
    _add_entry(
        pending,
        sort_group=sort_group,
        sort_index=0,
        entry_id=f"timeline:{review.event_id}:execution-plan:{execution_plan.execution_plan_id}",
        occurred_at=review.created_at,
        stage="execution_plan_ready",
        resource_type="execution_plan",
        resource_id=execution_plan.execution_plan_id,
        status=execution_plan.status,
        title="Dry-run execution plan ready",
        summary=execution_plan.summary,
        related_ids=_related_ids(
            event_id=execution_plan.event_id,
            feedback_id=execution_plan.feedback_id,
            review_id=execution_plan.review_id,
            execution_plan_id=execution_plan.execution_plan_id,
            optimization_draft_id=execution_plan.optimization_draft_id,
            draft_id=execution_plan.base_draft_id,
            strategy_id=execution_plan.strategy_id,
        ),
        metadata={
            "step_count": len(execution_plan.steps),
            "execution_mode": execution_plan.execution_mode,
            "derived_created_at": execution_plan.created_at.isoformat(),
        },
    )


def _add_dry_run_entry(
    pending: list[_PendingTimelineEntry],
    dry_run: CampaignFeedbackExecutionDryRunResponse,
    *,
    sort_group: int,
    sort_index: int,
) -> None:
    stage: FeedbackLoopTimelineStage = (
        "execution_dry_run_passed"
        if dry_run.status == "passed"
        else "execution_dry_run_failed"
    )
    _add_entry(
        pending,
        sort_group=sort_group,
        sort_index=sort_index,
        entry_id=f"timeline:{dry_run.event_id}:dry-run:{dry_run.dry_run_id}",
        occurred_at=dry_run.created_at,
        stage=stage,
        resource_type="execution_dry_run",
        resource_id=dry_run.dry_run_id,
        status=dry_run.status,
        title=_stage_title(stage),
        summary=(
            f"Dry-run validation {dry_run.status}: "
            f"{dry_run.validated_step_count} validated step(s), "
            f"{dry_run.blocked_step_count} blocked step(s)."
        ),
        related_ids=_related_ids(
            event_id=dry_run.event_id,
            review_id=dry_run.review_id,
            execution_plan_id=dry_run.execution_plan_id,
            dry_run_id=dry_run.dry_run_id,
        ),
        metadata={
            "validated_step_count": dry_run.validated_step_count,
            "blocked_step_count": dry_run.blocked_step_count,
        },
    )


def _add_handoff_package_entry(
    pending: list[_PendingTimelineEntry],
    review: CampaignFeedbackOptimizationReviewResponse,
    execution_store: FeedbackExecutionLineageStore | None,
    *,
    sort_group: int,
) -> None:
    try:
        package = build_feedback_handoff_package(review, execution_store)
    except (FeedbackExecutionPlanNotApprovedError, ValueError):
        return
    stage = _handoff_package_stage(package)
    _add_entry(
        pending,
        sort_group=sort_group,
        sort_index=0,
        entry_id=f"timeline:{review.event_id}:handoff-package:{package.handoff_package_id}",
        occurred_at=package.created_at,
        stage=stage,
        resource_type="handoff_package",
        resource_id=package.handoff_package_id,
        status=package.status,
        title=_stage_title(stage),
        summary=package.summary,
        related_ids=_related_ids(
            event_id=package.event_id,
            feedback_id=package.feedback_id,
            review_id=package.review_id,
            execution_plan_id=package.execution_plan_id,
            handoff_package_id=package.handoff_package_id,
            dry_run_id=package.latest_dry_run_id,
            optimization_draft_id=package.optimization_draft_id,
            draft_id=package.base_draft_id,
            strategy_id=package.strategy_id,
        ),
        metadata={
            "step_count": package.step_count,
            "validated_step_count": package.validated_step_count,
            "blocked_step_count": package.blocked_step_count,
        },
    )


def _add_handoff_record_entry(
    pending: list[_PendingTimelineEntry],
    handoff_record: CampaignFeedbackHandoffRecordResponse,
    *,
    sort_group: int,
    sort_index: int,
) -> None:
    stage = _handoff_record_stage(handoff_record.outcome)
    _add_entry(
        pending,
        sort_group=sort_group,
        sort_index=sort_index,
        entry_id=(
            f"timeline:{handoff_record.event_id}:handoff-record:"
            f"{handoff_record.handoff_record_id}"
        ),
        occurred_at=handoff_record.created_at,
        stage=stage,
        resource_type="handoff_record",
        resource_id=handoff_record.handoff_record_id,
        status=handoff_record.outcome.value,
        title=_stage_title(stage),
        summary=handoff_record.summary,
        actor_id=handoff_record.operator_id,
        related_ids=_related_ids(
            event_id=handoff_record.event_id,
            feedback_id=handoff_record.feedback_id,
            review_id=handoff_record.review_id,
            execution_plan_id=handoff_record.execution_plan_id,
            handoff_package_id=handoff_record.handoff_package_id,
            handoff_record_id=handoff_record.handoff_record_id,
            dry_run_id=handoff_record.latest_dry_run_id,
            optimization_draft_id=handoff_record.optimization_draft_id,
            draft_id=handoff_record.base_draft_id,
            strategy_id=handoff_record.strategy_id,
        ),
        metadata={
            "completed_step_count": len(handoff_record.completed_step_ids),
            "blocked_step_count": len(handoff_record.blocked_step_ids),
            "requires_follow_up": handoff_record.requires_follow_up,
        },
    )


def _add_entry(
    pending: list[_PendingTimelineEntry],
    *,
    sort_group: int,
    sort_index: int,
    entry_id: str,
    occurred_at,
    stage: FeedbackLoopTimelineStage,
    resource_type,
    resource_id: str,
    status: str,
    title: str,
    summary: str,
    actor_id: str | None = None,
    related_ids: dict[str, str] | None = None,
    metadata: dict[str, Any] | None = None,
) -> None:
    pending.append(
        _PendingTimelineEntry(
            sort_group=sort_group,
            sort_index=sort_index,
            entry=FeedbackLoopTimelineEntry(
                sequence=1,
                entry_id=entry_id,
                occurred_at=occurred_at,
                stage=stage,
                resource_type=resource_type,
                resource_id=resource_id,
                status=status,
                title=title,
                summary=summary,
                actor_id=actor_id,
                related_ids=related_ids or {},
                metadata=metadata or {},
            ),
        )
    )


def _review_stage(
    review: CampaignFeedbackOptimizationReviewResponse,
) -> FeedbackLoopTimelineStage:
    if review.decision == FeedbackOptimizationReviewDecision.NEEDS_REVISION:
        return "revision_requested"
    if _is_revision_review(review):
        if review.decision == FeedbackOptimizationReviewDecision.APPROVED:
            return "revision_review_approved"
        return "revision_review_rejected"
    if review.decision == FeedbackOptimizationReviewDecision.APPROVED:
        return "optimization_review_approved"
    return "optimization_review_rejected"


def _handoff_package_stage(
    package: CampaignFeedbackHandoffPackageResponse,
) -> FeedbackLoopTimelineStage:
    if package.status == "ready_for_manual_handoff":
        return "handoff_ready"
    if package.status == "validation_failed":
        return "handoff_validation_failed"
    return "handoff_validation_missing"


def _handoff_record_stage(outcome: FeedbackHandoffOutcome) -> FeedbackLoopTimelineStage:
    if outcome == FeedbackHandoffOutcome.APPLIED:
        return "handoff_applied"
    if outcome == FeedbackHandoffOutcome.BLOCKED:
        return "handoff_blocked"
    return "handoff_skipped"


def _stage_title(stage: FeedbackLoopTimelineStage) -> str:
    return {
        "optimization_review_approved": "Optimization review approved",
        "optimization_review_rejected": "Optimization review rejected",
        "revision_requested": "Revision requested",
        "revision_review_approved": "Revision review approved",
        "revision_review_rejected": "Revision review rejected",
        "execution_dry_run_passed": "Dry-run validation passed",
        "execution_dry_run_failed": "Dry-run validation failed",
        "handoff_ready": "Manual handoff package ready",
        "handoff_validation_missing": "Manual handoff validation missing",
        "handoff_validation_failed": "Manual handoff validation failed",
        "handoff_applied": "Manual handoff applied",
        "handoff_blocked": "Manual handoff blocked",
        "handoff_skipped": "Manual handoff skipped",
    }[stage]


def _review_summary(review: CampaignFeedbackOptimizationReviewResponse) -> str:
    selected_count = len(review.selected_change_ids)
    suffix = f" Selected {selected_count} change(s)."
    if review.notes:
        suffix += " Reviewer notes were recorded."
    return (
        f"Review decision={review.decision.value} for draft "
        f"{review.optimization_draft_id}.{suffix}"
    )


def _is_revision_review(review: CampaignFeedbackOptimizationReviewResponse) -> bool:
    return review.optimization_draft_id.startswith("feedback_revision_draft_")


def _related_ids(**values: str | None) -> dict[str, str]:
    return {key: value for key, value in values.items() if value}
