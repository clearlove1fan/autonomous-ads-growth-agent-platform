from uuid import NAMESPACE_URL, uuid5

from ads_growth_agent.contracts import (
    CampaignFeedbackExecutionDryRunResponse,
    CampaignFeedbackHandoffPackageResponse,
    CampaignFeedbackOptimizationReviewResponse,
    FeedbackManualHandoffStep,
)
from ads_growth_agent.feedback_execution_plan import build_feedback_execution_plan
from ads_growth_agent.feedback_lineage import FeedbackExecutionLineageStore


def build_feedback_handoff_package(
    review: CampaignFeedbackOptimizationReviewResponse,
    execution_store: FeedbackExecutionLineageStore | None = None,
) -> CampaignFeedbackHandoffPackageResponse:
    """Build a read-only package for manual campaign-platform handoff."""

    execution_plan = build_feedback_execution_plan(review)
    latest_dry_run = _latest_dry_run(
        execution_plan_id=execution_plan.execution_plan_id,
        execution_store=execution_store,
    )
    status = _package_status(latest_dry_run)
    step_status_by_id = _step_status_by_id(latest_dry_run)
    manual_steps = [
        FeedbackManualHandoffStep(
            step_id=step.step_id,
            change_id=step.change_id,
            sequence=step.sequence,
            title=step.title,
            change_type=step.change_type,
            owner_role=step.owner_role,
            risk_level=step.risk_level,
            tool_name=step.tool_intent.tool_name,
            dry_run_status=step_status_by_id.get(step.step_id, "not_validated"),
            manual_action=_manual_action(step.tool_intent.tool_name),
            source_params=step.tool_intent.params,
        )
        for step in execution_plan.steps
    ]

    validated_step_count = latest_dry_run.validated_step_count if latest_dry_run else 0
    blocked_step_count = latest_dry_run.blocked_step_count if latest_dry_run else 0
    return CampaignFeedbackHandoffPackageResponse(
        handoff_package_id=_handoff_package_id(execution_plan.execution_plan_id),
        status=status,
        review_id=review.review_id,
        execution_plan_id=execution_plan.execution_plan_id,
        optimization_draft_id=review.optimization_draft_id,
        event_id=review.event_id,
        feedback_id=review.feedback_id,
        advertiser_id=review.advertiser_id,
        run_id=review.run_id,
        campaign_id=review.campaign_id,
        base_draft_id=review.base_draft_id,
        strategy_id=review.strategy_id,
        latest_dry_run_id=latest_dry_run.dry_run_id if latest_dry_run else None,
        latest_dry_run_status=latest_dry_run.status if latest_dry_run else None,
        step_count=len(execution_plan.steps),
        validated_step_count=validated_step_count,
        blocked_step_count=blocked_step_count,
        review=review,
        execution_plan=execution_plan,
        latest_dry_run=latest_dry_run,
        manual_steps=manual_steps,
        operator_checklist=_operator_checklist(status),
        summary=_summary_text(
            review_id=review.review_id,
            status=status,
            step_count=len(execution_plan.steps),
            validated_step_count=validated_step_count,
            blocked_step_count=blocked_step_count,
        ),
        guardrails=[
            "This handoff package is read-only and does not execute live campaign changes.",
            "Operators must recheck platform permissions, policy, and budget before manual use.",
            "Only packages with passed dry-run validation are ready for manual handoff.",
        ],
        created_at=latest_dry_run.created_at if latest_dry_run else execution_plan.created_at,
    )


def _latest_dry_run(
    *,
    execution_plan_id: str,
    execution_store: FeedbackExecutionLineageStore | None,
) -> CampaignFeedbackExecutionDryRunResponse | None:
    if execution_store is None:
        return None
    dry_runs = execution_store.list_dry_runs(
        execution_plan_id=execution_plan_id,
        limit=1,
    ).items
    return dry_runs[0] if dry_runs else None


def _package_status(
    latest_dry_run: CampaignFeedbackExecutionDryRunResponse | None,
):
    if latest_dry_run is None:
        return "validation_missing"
    if latest_dry_run.status == "failed":
        return "validation_failed"
    return "ready_for_manual_handoff"


def _step_status_by_id(
    latest_dry_run: CampaignFeedbackExecutionDryRunResponse | None,
) -> dict[str, str]:
    if latest_dry_run is None:
        return {}
    return {
        step_result.step_id: step_result.status
        for step_result in latest_dry_run.step_results
    }


def _manual_action(tool_name: str) -> str:
    match tool_name:
        case "draft_budget_reallocation":
            return (
                "Open the draft budget recommendation and manually apply the "
                "approved allocation."
            )
        case "draft_creative_refresh":
            return "Create or update creative briefs from the approved draft recommendation."
        case "draft_audience_refinement":
            return "Review targeting changes and manually update the draft audience setup."
        case "draft_measurement_followup":
            return "Create a measurement investigation task before changing campaign setup."
    return "Review the draft action and apply it manually only after policy approval."


def _operator_checklist(status: str) -> list[str]:
    base = [
        "Confirm advertiser, campaign, and draft identifiers match the target workspace.",
        "Review selected change IDs against the approved optimization draft snapshot.",
        "Recheck policy, budget guardrails, and campaign objective before manual entry.",
    ]
    if status == "ready_for_manual_handoff":
        return [
            *base,
            "Confirm latest dry-run validation passed before manual campaign-platform handoff.",
        ]
    if status == "validation_failed":
        return [
            *base,
            "Resolve blocked dry-run validation steps before manual handoff.",
        ]
    return [
        *base,
        "Run dry-run execution validation before manual handoff.",
    ]


def _summary_text(
    *,
    review_id: str,
    status: str,
    step_count: int,
    validated_step_count: int,
    blocked_step_count: int,
) -> str:
    return (
        f"Manual handoff package for approved review {review_id}: "
        f"status={status}, steps={step_count}, validated={validated_step_count}, "
        f"blocked={blocked_step_count}."
    )


def _handoff_package_id(execution_plan_id: str) -> str:
    return f"feedback_handoff_{uuid5(NAMESPACE_URL, execution_plan_id).hex[:16]}"
