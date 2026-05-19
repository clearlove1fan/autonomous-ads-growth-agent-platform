from uuid import NAMESPACE_URL, uuid5

from ads_growth_agent.contracts import (
    AgentRole,
    CampaignFeedbackExecutionPlanResponse,
    CampaignFeedbackOptimizationReviewResponse,
    FeedbackExecutionPlanStep,
    FeedbackOptimizationDraftChange,
    FeedbackOptimizationReviewDecision,
    RiskLevel,
    ToolIntent,
)


class FeedbackExecutionPlanNotApprovedError(Exception):
    def __init__(self, review_id: str, decision: FeedbackOptimizationReviewDecision) -> None:
        super().__init__(
            "Feedback optimization review must be approved before building an execution plan: "
            f"review_id={review_id} decision={decision.value}"
        )
        self.review_id = review_id
        self.decision = decision


def build_feedback_execution_plan(
    review: CampaignFeedbackOptimizationReviewResponse,
) -> CampaignFeedbackExecutionPlanResponse:
    """Build a dry-run action plan from an approved optimization review."""

    if review.decision != FeedbackOptimizationReviewDecision.APPROVED:
        raise FeedbackExecutionPlanNotApprovedError(review.review_id, review.decision)

    selected_change_ids = set(review.selected_change_ids)
    selected_changes = [
        change
        for change in review.optimization_draft.changes
        if change.change_id in selected_change_ids
    ]
    if not selected_changes:
        raise ValueError("approved review does not include any selected changes")

    steps = [
        _execution_step(review, change, sequence=index)
        for index, change in enumerate(selected_changes, start=1)
    ]
    return CampaignFeedbackExecutionPlanResponse(
        execution_plan_id=_execution_plan_id(review.review_id),
        review_id=review.review_id,
        optimization_draft_id=review.optimization_draft_id,
        event_id=review.event_id,
        feedback_id=review.feedback_id,
        advertiser_id=review.advertiser_id,
        run_id=review.run_id,
        campaign_id=review.campaign_id,
        base_draft_id=review.base_draft_id,
        strategy_id=review.strategy_id,
        review_decision=review.decision,
        status="ready",
        summary=(
            f"Dry-run execution plan for approved review {review.review_id}. "
            f"Includes {len(steps)} draft action intent(s) and performs no live mutation."
        ),
        steps=steps,
        guardrails=[
            (
                "Execution mode is dry_run; no live campaign, budget, audience, "
                "or creative mutation is performed."
            ),
            (
                "A separate execution service must revalidate permissions before "
                "any live platform action."
            ),
            (
                "Discarding the draft plan is the rollback path until live execution "
                "is explicitly enabled."
            ),
        ],
        created_at=review.created_at,
    )


def _execution_step(
    review: CampaignFeedbackOptimizationReviewResponse,
    change: FeedbackOptimizationDraftChange,
    *,
    sequence: int,
) -> FeedbackExecutionPlanStep:
    return FeedbackExecutionPlanStep(
        step_id=f"{review.review_id}:step:{sequence}",
        change_id=change.change_id,
        sequence=sequence,
        title=change.title,
        description=change.description,
        change_type=change.change_type,
        owner_role=change.owner_role,
        risk_level=change.risk_level,
        tool_intent=ToolIntent(
            intent_id=_tool_intent_id(review.review_id, change.change_id),
            tool_name=_tool_name_for_change(change),
            params=_tool_params_for_change(review, change),
            requested_by=change.owner_role,
            risk_level=change.risk_level,
            requires_human_approval=change.requires_human_approval,
        ),
        status="ready",
        preconditions=_preconditions_for_change(review, change),
        rollback_plan=_rollback_plan_for_change(change),
    )


def _tool_name_for_change(change: FeedbackOptimizationDraftChange) -> str:
    match change.change_type:
        case "budget":
            return "draft_budget_reallocation"
        case "creative":
            return "draft_creative_refresh"
        case "audience":
            return "draft_audience_refinement"
        case "measurement":
            return "draft_measurement_followup"


def _tool_params_for_change(
    review: CampaignFeedbackOptimizationReviewResponse,
    change: FeedbackOptimizationDraftChange,
) -> dict[str, object]:
    return {
        "dry_run": True,
        "approval_reference_id": review.review_id,
        "optimization_draft_id": review.optimization_draft_id,
        "event_id": review.event_id,
        "feedback_id": review.feedback_id,
        "advertiser_id": review.advertiser_id,
        "run_id": review.run_id,
        "campaign_id": review.campaign_id,
        "base_draft_id": review.base_draft_id,
        "strategy_id": review.strategy_id,
        "change_id": change.change_id,
        "change_type": change.change_type,
        "source_step_id": change.source_step_id,
        "change_title": change.title,
        "change_description": change.description,
        "change_params": change.params,
        "requested_by_role": change.owner_role.value,
        "risk_level": change.risk_level.value,
    }


def _preconditions_for_change(
    review: CampaignFeedbackOptimizationReviewResponse,
    change: FeedbackOptimizationDraftChange,
) -> list[str]:
    preconditions = [
        f"Review {review.review_id} is approved.",
        "Execution mode remains dry_run.",
        "The source optimization draft snapshot is available for audit.",
    ]
    if review.campaign_id is not None:
        preconditions.append(f"Campaign reference {review.campaign_id} must be resolvable.")
    if review.base_draft_id is not None:
        preconditions.append(f"Base draft {review.base_draft_id} must still be reviewable.")
    if change.risk_level in {RiskLevel.MEDIUM, RiskLevel.HIGH}:
        preconditions.append("Budget or targeting changes require a second live-execution gate.")
    if change.owner_role == AgentRole.PERFORMANCE_ANALYST:
        preconditions.append("Measurement follow-up should be handled as an investigation task.")
    return preconditions


def _rollback_plan_for_change(change: FeedbackOptimizationDraftChange) -> str:
    match change.change_type:
        case "budget":
            return "Discard the draft budget reallocation; no live budget was changed."
        case "creative":
            return "Discard the draft creative refresh; active creatives remain unchanged."
        case "audience":
            return "Discard the draft audience refinement; live targeting remains unchanged."
        case "measurement":
            return "Close the draft measurement task; no tracking configuration was changed."


def _execution_plan_id(review_id: str) -> str:
    return f"feedback_execution_plan_{uuid5(NAMESPACE_URL, review_id).hex[:16]}"


def _tool_intent_id(review_id: str, change_id: str) -> str:
    return f"feedback_exec_{uuid5(NAMESPACE_URL, f'{review_id}:{change_id}').hex[:16]}"
