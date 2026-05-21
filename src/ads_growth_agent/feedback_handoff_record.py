from datetime import UTC, datetime
from uuid import uuid4

from ads_growth_agent.contracts import (
    CampaignFeedbackHandoffPackageResponse,
    CampaignFeedbackHandoffRecordRequest,
    CampaignFeedbackHandoffRecordResponse,
    FeedbackHandoffOutcome,
)


class FeedbackHandoffRecordNotReadyError(ValueError):
    def __init__(self, handoff_package_id: str, package_status: str) -> None:
        super().__init__(
            "applied handoff records require a ready manual handoff package: "
            f"handoff_package_id={handoff_package_id} status={package_status}"
        )
        self.handoff_package_id = handoff_package_id
        self.package_status = package_status


class FeedbackHandoffRecordStepMismatchError(ValueError):
    def __init__(self, message: str) -> None:
        super().__init__(message)


def build_feedback_handoff_record(
    handoff_package: CampaignFeedbackHandoffPackageResponse,
    request: CampaignFeedbackHandoffRecordRequest,
    *,
    handoff_record_id: str | None = None,
) -> CampaignFeedbackHandoffRecordResponse:
    """Build an append-only operator acknowledgement for a handoff package."""

    _validate_handoff_record_request(handoff_package, request)
    return CampaignFeedbackHandoffRecordResponse(
        handoff_record_id=handoff_record_id or f"feedback_handoff_record_{uuid4().hex[:16]}",
        handoff_package_id=handoff_package.handoff_package_id,
        review_id=handoff_package.review_id,
        execution_plan_id=handoff_package.execution_plan_id,
        latest_dry_run_id=handoff_package.latest_dry_run_id,
        optimization_draft_id=handoff_package.optimization_draft_id,
        event_id=handoff_package.event_id,
        feedback_id=handoff_package.feedback_id,
        advertiser_id=handoff_package.advertiser_id,
        run_id=handoff_package.run_id,
        campaign_id=handoff_package.campaign_id,
        base_draft_id=handoff_package.base_draft_id,
        strategy_id=handoff_package.strategy_id,
        package_status=handoff_package.status,
        outcome=request.outcome,
        operator_id=request.operator_id,
        notes=request.notes,
        completed_step_ids=request.completed_step_ids,
        blocked_step_ids=request.blocked_step_ids,
        requires_follow_up=request.outcome != FeedbackHandoffOutcome.APPLIED,
        handoff_package=handoff_package,
        summary=_summary_text(handoff_package, request),
        guardrails=[
            (
                "This acknowledgement records operator outcome only; it does not "
                "execute live campaign changes."
            ),
            "Applied outcomes require a passed dry-run handoff package.",
            "Blocked or skipped outcomes should include enough notes for follow-up.",
        ],
        created_at=datetime.now(UTC),
    )


def _validate_handoff_record_request(
    handoff_package: CampaignFeedbackHandoffPackageResponse,
    request: CampaignFeedbackHandoffRecordRequest,
) -> None:
    known_step_ids = {step.step_id for step in handoff_package.manual_steps}
    completed_ids = set(request.completed_step_ids)
    blocked_ids = set(request.blocked_step_ids)
    unknown_completed = sorted(completed_ids - known_step_ids)
    unknown_blocked = sorted(blocked_ids - known_step_ids)
    if unknown_completed:
        raise FeedbackHandoffRecordStepMismatchError(
            f"completed_step_ids include unknown handoff steps: {unknown_completed}"
        )
    if unknown_blocked:
        raise FeedbackHandoffRecordStepMismatchError(
            f"blocked_step_ids include unknown handoff steps: {unknown_blocked}"
        )
    if request.outcome == FeedbackHandoffOutcome.APPLIED:
        if handoff_package.status != "ready_for_manual_handoff":
            raise FeedbackHandoffRecordNotReadyError(
                handoff_package.handoff_package_id,
                handoff_package.status,
            )
        missing_completed = sorted(known_step_ids - completed_ids)
        if missing_completed:
            raise FeedbackHandoffRecordStepMismatchError(
                "applied handoff records must complete every manual step: "
                f"{missing_completed}"
            )


def _summary_text(
    handoff_package: CampaignFeedbackHandoffPackageResponse,
    request: CampaignFeedbackHandoffRecordRequest,
) -> str:
    return (
        f"Manual handoff {request.outcome.value} for package "
        f"{handoff_package.handoff_package_id}: completed="
        f"{len(request.completed_step_ids)}, blocked={len(request.blocked_step_ids)}."
    )
