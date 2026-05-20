from typing import Protocol

from ads_growth_agent.contracts import (
    CampaignFeedbackExecutionDryRunListResponse,
    CampaignFeedbackOptimizationReviewLineageListResponse,
    CampaignFeedbackOptimizationReviewLineageResponse,
    CampaignFeedbackOptimizationReviewListResponse,
    CampaignFeedbackOptimizationReviewResponse,
    FeedbackOptimizationReviewDecision,
    FeedbackReviewLineageDryRunSummary,
    FeedbackReviewLineageExecutionSummary,
)
from ads_growth_agent.feedback import build_campaign_feedback_optimization_revision_draft
from ads_growth_agent.feedback_execution_plan import build_feedback_execution_plan


class FeedbackReviewLineageStore(Protocol):
    def get_review(
        self,
        review_id: str,
    ) -> CampaignFeedbackOptimizationReviewResponse | None:
        """Return one persisted optimization review for the configured tenant."""

    def list_reviews(
        self,
        *,
        event_id: str | None = None,
        advertiser_id: str | None = None,
        optimization_draft_id: str | None = None,
        decision: FeedbackOptimizationReviewDecision | None = None,
        limit: int = 50,
    ) -> CampaignFeedbackOptimizationReviewListResponse:
        """Return recent optimization reviews for the configured tenant."""


class FeedbackExecutionLineageStore(Protocol):
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
        """Return recent persisted dry-run validation results for the configured tenant."""


def build_feedback_optimization_review_lineage(
    target_review: CampaignFeedbackOptimizationReviewResponse,
    store: FeedbackReviewLineageStore,
    execution_store: FeedbackExecutionLineageStore | None = None,
) -> CampaignFeedbackOptimizationReviewLineageResponse:
    """Build an audit lineage around a feedback optimization review."""

    revision_source_review_id = _revision_source_review_id(target_review)
    source_review = target_review
    if revision_source_review_id is not None:
        source_review = store.get_review(revision_source_review_id)
        if source_review is None:
            raise ValueError(
                "revision source review was not found for the effective tenant: "
                f"review_id={revision_source_review_id}"
            )

    revision_draft = None
    revision_reviews: list[CampaignFeedbackOptimizationReviewResponse] = []
    if source_review.decision == FeedbackOptimizationReviewDecision.NEEDS_REVISION:
        revision_draft = build_campaign_feedback_optimization_revision_draft(source_review)
        revision_reviews = store.list_reviews(
            optimization_draft_id=revision_draft.revision_draft_id,
            limit=100,
        ).items

    reviewed_records = [source_review, *revision_reviews]
    approved_reviews = [
        review
        for review in reviewed_records
        if review.decision == FeedbackOptimizationReviewDecision.APPROVED
    ]
    approved_review_ids = [review.review_id for review in approved_reviews]
    execution_summaries = [
        summary
        for review in approved_reviews
        if (summary := _execution_summary(review, execution_store)) is not None
    ]
    execution_ready_review_ids = [summary.review_id for summary in execution_summaries]

    return CampaignFeedbackOptimizationReviewLineageResponse(
        requested_review_id=target_review.review_id,
        lineage_stage=_lineage_stage(target_review, revision_source_review_id),
        source_review_id=source_review.review_id,
        target_review=target_review,
        source_review=source_review,
        revision_draft=revision_draft,
        revision_reviews=revision_reviews,
        approved_review_ids=approved_review_ids,
        execution_ready_review_ids=execution_ready_review_ids,
        execution_summaries=execution_summaries,
        summary=_lineage_summary(
            source_review=source_review,
            target_review=target_review,
            revision_review_count=len(revision_reviews),
            execution_ready_count=len(execution_ready_review_ids),
            dry_run_count=sum(summary.dry_run_count for summary in execution_summaries),
        ),
        guardrails=[
            "Lineage is derived from persisted review snapshots for audit and debugging.",
            (
                "Execution readiness means an approved review can generate a dry-run "
                "plan; it is not live execution."
            ),
        ],
    )


def list_feedback_optimization_review_lineages(
    store: FeedbackReviewLineageStore,
    execution_store: FeedbackExecutionLineageStore | None = None,
    *,
    event_id: str | None = None,
    advertiser_id: str | None = None,
    optimization_draft_id: str | None = None,
    decision: FeedbackOptimizationReviewDecision | None = None,
    lineage_stage: str | None = None,
    limit: int = 50,
) -> CampaignFeedbackOptimizationReviewLineageListResponse:
    """Return derived lineage records for a filtered review set."""

    review_list = store.list_reviews(
        event_id=event_id,
        advertiser_id=advertiser_id,
        optimization_draft_id=optimization_draft_id,
        decision=decision,
        limit=100,
    )
    lineages: list[CampaignFeedbackOptimizationReviewLineageResponse] = []
    for review in review_list.items:
        lineage = build_feedback_optimization_review_lineage(
            review,
            store,
            execution_store,
        )
        if lineage_stage is not None and lineage.lineage_stage != lineage_stage:
            continue
        lineages.append(lineage)
        if len(lineages) >= limit:
            break

    return CampaignFeedbackOptimizationReviewLineageListResponse(
        items=lineages,
        count=len(lineages),
        limit=limit,
        event_id=event_id,
        advertiser_id=advertiser_id,
        optimization_draft_id=optimization_draft_id,
        decision=decision,
        lineage_stage=lineage_stage,
    )


def _execution_summary(
    review: CampaignFeedbackOptimizationReviewResponse,
    execution_store: FeedbackExecutionLineageStore | None,
) -> FeedbackReviewLineageExecutionSummary | None:
    try:
        execution_plan = build_feedback_execution_plan(review)
    except ValueError:
        return None

    dry_runs: list[FeedbackReviewLineageDryRunSummary] = []
    if execution_store is not None:
        dry_runs = [
            FeedbackReviewLineageDryRunSummary(
                dry_run_id=dry_run.dry_run_id,
                execution_plan_id=dry_run.execution_plan_id,
                review_id=dry_run.review_id,
                status=dry_run.status,
                validated_step_count=dry_run.validated_step_count,
                blocked_step_count=dry_run.blocked_step_count,
                created_at=dry_run.created_at,
            )
            for dry_run in execution_store.list_dry_runs(
                execution_plan_id=execution_plan.execution_plan_id,
                limit=20,
            ).items
        ]

    latest_dry_run_status = dry_runs[0].status if dry_runs else None
    return FeedbackReviewLineageExecutionSummary(
        review_id=review.review_id,
        execution_plan_id=execution_plan.execution_plan_id,
        step_count=len(execution_plan.steps),
        dry_run_count=len(dry_runs),
        latest_dry_run_status=latest_dry_run_status,
        dry_runs=dry_runs,
    )


def _revision_source_review_id(
    review: CampaignFeedbackOptimizationReviewResponse,
) -> str | None:
    for change in review.optimization_draft.changes:
        value = change.params.get("revision_source_review_id")
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None


def _lineage_stage(
    review: CampaignFeedbackOptimizationReviewResponse,
    revision_source_review_id: str | None,
):
    if revision_source_review_id is not None:
        return "revision_review"
    if review.decision == FeedbackOptimizationReviewDecision.NEEDS_REVISION:
        return "revision_requested"
    if review.decision == FeedbackOptimizationReviewDecision.APPROVED:
        return "approved"
    return "rejected"


def _lineage_summary(
    *,
    source_review: CampaignFeedbackOptimizationReviewResponse,
    target_review: CampaignFeedbackOptimizationReviewResponse,
    revision_review_count: int,
    execution_ready_count: int,
    dry_run_count: int,
) -> str:
    return (
        f"Lineage for review {target_review.review_id}. Source review "
        f"{source_review.review_id} has decision {source_review.decision.value}, "
        f"{revision_review_count} revision review(s), and {execution_ready_count} "
        f"execution-ready approved review(s), with {dry_run_count} persisted "
        "dry-run validation record(s)."
    )
