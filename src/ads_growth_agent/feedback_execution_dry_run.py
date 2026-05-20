from datetime import UTC, datetime
from uuid import NAMESPACE_URL, uuid5

from ads_growth_agent.contracts import (
    CampaignFeedbackExecutionDryRunResponse,
    CampaignFeedbackExecutionPlanResponse,
    FeedbackExecutionDryRunStepResult,
    FeedbackExecutionPlanStep,
    ToolError,
    ToolResult,
)
from ads_growth_agent.tools import (
    ToolExecutionContext,
    ToolRegistry,
    build_default_tool_registry,
)

DRY_RUN_EXECUTION_TOOLS = {
    "draft_budget_reallocation",
    "draft_creative_refresh",
    "draft_audience_refinement",
    "draft_measurement_followup",
}


def dry_run_feedback_execution_plan(
    execution_plan: CampaignFeedbackExecutionPlanResponse,
    *,
    registry: ToolRegistry | None = None,
) -> CampaignFeedbackExecutionDryRunResponse:
    """Validate a feedback execution plan through draft-only tool handlers."""

    tool_registry = registry or build_default_tool_registry()
    step_results = [
        _dry_run_step(execution_plan, step, registry=tool_registry)
        for step in execution_plan.steps
    ]
    blocked_count = sum(1 for result in step_results if result.status == "blocked")
    validated_count = len(step_results) - blocked_count
    return CampaignFeedbackExecutionDryRunResponse(
        dry_run_id=_dry_run_id(execution_plan.execution_plan_id),
        execution_plan_id=execution_plan.execution_plan_id,
        review_id=execution_plan.review_id,
        advertiser_id=execution_plan.advertiser_id,
        event_id=execution_plan.event_id,
        status="failed" if blocked_count else "passed",
        step_results=step_results,
        validated_step_count=validated_count,
        blocked_step_count=blocked_count,
        guardrails=[
            "Dry-run validation does not mutate live campaign state.",
            "Only draft feedback execution tools are allowed in this validation context.",
            "Live execution must use a separate permissioned executor and approval gate.",
        ],
        created_at=datetime.now(UTC),
    )


def _dry_run_step(
    execution_plan: CampaignFeedbackExecutionPlanResponse,
    step: FeedbackExecutionPlanStep,
    *,
    registry: ToolRegistry,
) -> FeedbackExecutionDryRunStepResult:
    precheck_safety_checks = [
        "execution_mode_is_dry_run",
        "tool_is_draft_only",
        "tool_params_request_dry_run",
        "execution_plan_identity_matches_tool_params",
    ]
    precheck_error = _precheck_step(execution_plan, step)
    if precheck_error is not None:
        return FeedbackExecutionDryRunStepResult(
            step_id=step.step_id,
            change_id=step.change_id,
            sequence=step.sequence,
            tool_name=step.tool_intent.tool_name,
            status="blocked",
            safety_checks=[
                *precheck_safety_checks,
                "tool_registry_validation_skipped_after_precheck_failure",
            ],
            tool_result=precheck_error,
        )

    tool_result = registry.execute(
        step.tool_intent,
        ToolExecutionContext(
            advertiser_id=execution_plan.advertiser_id,
            run_id=execution_plan.run_id or execution_plan.execution_plan_id,
            allowed_tools=DRY_RUN_EXECUTION_TOOLS,
        ),
    )
    return FeedbackExecutionDryRunStepResult(
        step_id=step.step_id,
        change_id=step.change_id,
        sequence=step.sequence,
        tool_name=step.tool_intent.tool_name,
        status="validated" if tool_result.success else "blocked",
        safety_checks=[*precheck_safety_checks, "tool_registry_validation_ran"],
        tool_result=tool_result,
    )


def _precheck_step(
    execution_plan: CampaignFeedbackExecutionPlanResponse,
    step: FeedbackExecutionPlanStep,
) -> ToolResult | None:
    if step.execution_mode != "dry_run":
        return _blocked_tool_result(
            step.tool_intent.tool_name,
            code="EXECUTION_MODE_NOT_DRY_RUN",
            message="feedback execution plans can only be dry-run validated",
        )
    if step.tool_intent.tool_name not in DRY_RUN_EXECUTION_TOOLS:
        return _blocked_tool_result(
            step.tool_intent.tool_name,
            code="NON_DRAFT_TOOL_BLOCKED",
            message=f"tool is not allowed for feedback dry-run: {step.tool_intent.tool_name}",
        )
    if step.tool_intent.params.get("dry_run") is not True:
        return _blocked_tool_result(
            step.tool_intent.tool_name,
            code="DRY_RUN_FLAG_MISSING",
            message="tool intent params must include dry_run=true",
        )
    identity_mismatch = _tool_identity_mismatch(execution_plan, step)
    if identity_mismatch is not None:
        return _blocked_tool_result(
            step.tool_intent.tool_name,
            code="EXECUTION_CONTEXT_MISMATCH",
            message=identity_mismatch,
        )
    return None


def _tool_identity_mismatch(
    execution_plan: CampaignFeedbackExecutionPlanResponse,
    step: FeedbackExecutionPlanStep,
) -> str | None:
    expected_values = {
        "advertiser_id": execution_plan.advertiser_id,
        "event_id": execution_plan.event_id,
        "feedback_id": execution_plan.feedback_id,
        "approval_reference_id": execution_plan.review_id,
        "optimization_draft_id": execution_plan.optimization_draft_id,
        "change_id": step.change_id,
    }
    for key, expected_value in expected_values.items():
        if step.tool_intent.params.get(key) != expected_value:
            return f"tool intent {key} does not match execution plan"
    return None


def _blocked_tool_result(tool_name: str, *, code: str, message: str) -> ToolResult:
    return ToolResult(
        tool_name=tool_name,
        success=False,
        payload={},
        error=ToolError(code=code, message=message, retryable=False),
        latency_ms=0,
        source_metadata={"execution_mode": "dry_run"},
    )


def _dry_run_id(execution_plan_id: str) -> str:
    return f"feedback_dry_run_{uuid5(NAMESPACE_URL, execution_plan_id).hex[:16]}"
