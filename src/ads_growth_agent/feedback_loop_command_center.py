from typing import Any

from ads_growth_agent.contracts import (
    CampaignFeedbackExecutionDryRunResponse,
    CampaignFeedbackLoopCommandCenterResponse,
    CampaignFeedbackLoopSummaryResponse,
    CampaignFeedbackOutcomeReportResponse,
    CampaignPerformanceEventDetailResponse,
    FeedbackLoopCurrentStage,
    FeedbackLoopOperatorCommand,
)
from ads_growth_agent.feedback_loop_summary import (
    FeedbackExecutionLineageStore,
    FeedbackHandoffSummaryStore,
    FeedbackReviewLineageStore,
    build_campaign_feedback_loop_summary,
)
from ads_growth_agent.feedback_loop_timeline import build_campaign_feedback_loop_timeline
from ads_growth_agent.feedback_outcome_report import (
    FeedbackOutcomePerformanceEventStore,
    build_campaign_feedback_outcome_report,
)


def build_campaign_feedback_loop_command_center(
    event: CampaignPerformanceEventDetailResponse,
    review_store: FeedbackReviewLineageStore | None = None,
    execution_store: FeedbackExecutionLineageStore | None = None,
    handoff_store: FeedbackHandoffSummaryStore | None = None,
    *,
    review_persistence_enabled: bool = False,
    execution_persistence_enabled: bool = False,
    handoff_persistence_enabled: bool = False,
    outcome_event_store: FeedbackOutcomePerformanceEventStore | None = None,
    limit: int = 50,
) -> CampaignFeedbackLoopCommandCenterResponse:
    """Compose stage-aware operator commands for one persisted feedback event."""

    effective_limit = min(max(limit, 1), 100)
    loop_summary = build_campaign_feedback_loop_summary(
        event,
        review_store,
        execution_store,
        handoff_store,
        review_persistence_enabled=review_persistence_enabled,
        execution_persistence_enabled=execution_persistence_enabled,
        handoff_persistence_enabled=handoff_persistence_enabled,
        limit=effective_limit,
    )
    timeline = build_campaign_feedback_loop_timeline(
        event,
        review_store,
        execution_store,
        handoff_store,
        review_persistence_enabled=review_persistence_enabled,
        execution_persistence_enabled=execution_persistence_enabled,
        handoff_persistence_enabled=handoff_persistence_enabled,
        limit=effective_limit,
    )
    outcome_report = (
        build_campaign_feedback_outcome_report(
            event,
            outcome_event_store,
            limit=effective_limit,
        )
        if outcome_event_store is not None
        else None
    )
    current_stage = _command_center_stage(loop_summary, outcome_report)
    commands = _commands_for_stage(
        loop_summary,
        current_stage=current_stage,
        outcome_report=outcome_report,
        limit=effective_limit,
    )
    commands = _dedupe_commands(
        [*commands, *_inspection_commands(loop_summary, limit=effective_limit)]
    )
    primary_command = _primary_command(commands)

    return CampaignFeedbackLoopCommandCenterResponse(
        event_id=event.event_id,
        advertiser_id=event.advertiser_id,
        run_id=event.run_id,
        campaign_id=event.campaign_id,
        draft_id=event.draft_id,
        current_stage=current_stage,
        primary_command_id=primary_command.command_id if primary_command else None,
        primary_command=primary_command,
        outcome_status=outcome_report.outcome_status if outcome_report else None,
        outcome_report=outcome_report,
        command_count=len(commands),
        commands=commands,
        loop_summary=loop_summary,
        timeline=timeline,
        summary=(
            f"Feedback command center for event {event.event_id}: "
            f"stage={current_stage}, commands={len(commands)}."
        ),
        guardrails=[
            "Command center entries are operator affordances, not autonomous execution.",
            "v0.1 remains draft-only and requires human approval for campaign changes.",
        ],
    )


def _commands_for_stage(
    summary: CampaignFeedbackLoopSummaryResponse,
    *,
    current_stage: FeedbackLoopCurrentStage,
    outcome_report: CampaignFeedbackOutcomeReportResponse | None,
    limit: int,
) -> list[FeedbackLoopOperatorCommand]:
    match current_stage:
        case (
            "outcome_improved"
            | "outcome_regressed"
            | "outcome_mixed"
            | "outcome_insufficient_data"
        ):
            return _outcome_report_commands(summary, outcome_report, limit=limit)
        case "review_pending":
            return _review_pending_commands(summary)
        case "revision_requested":
            return _revision_requested_commands(summary)
        case "execution_ready" | "event_analyzed":
            return _execution_ready_commands(summary)
        case "dry_run_passed":
            return _dry_run_passed_commands(summary)
        case "dry_run_failed":
            return _dry_run_failed_commands(summary)
        case "handoff_applied":
            return _handoff_applied_commands(summary)
        case "handoff_blocked":
            return _handoff_blocked_commands(summary)
        case "handoff_skipped":
            return _handoff_skipped_commands(summary)
        case "rejected":
            return _rejected_commands(summary)
    return _inspection_commands(summary, limit=limit)


def _review_pending_commands(
    summary: CampaignFeedbackLoopSummaryResponse,
) -> list[FeedbackLoopOperatorCommand]:
    return [
        _command(
            command_id="inspect_optimization_draft",
            action_type="inspect_optimization_draft",
            priority=1,
            label="Inspect optimization draft",
            description="Review the draft-only optimization proposal before recording a decision.",
            api_method="GET",
            api_path=f"/campaign-events/performance/{summary.event_id}/optimization-draft",
            cli_command=[
                "ads-growth-agent",
                "get-feedback-optimization-draft",
                summary.event_id,
            ],
            resource_ids=_resource_ids(summary),
        ),
        _command(
            command_id="review_optimization_draft",
            action_type="review_optimization_draft",
            priority=2,
            enabled=summary.review_persistence_enabled,
            disabled_reason=_disabled_unless(
                summary.review_persistence_enabled,
                "Enable FEEDBACK_REVIEW_PERSISTENCE_BACKEND=postgres to record review decisions.",
            ),
            label="Record review decision",
            description="Approve, reject, or request revision for the draft-only proposal.",
            api_method="POST",
            api_path=(
                f"/campaign-events/performance/{summary.event_id}"
                "/optimization-draft/reviews"
            ),
            cli_command=[
                "ads-growth-agent",
                "submit-feedback-optimization-review",
                summary.event_id,
                "--decision",
                "approved",
                "--reviewer-id",
                "<operator_id>",
            ],
            body_template={
                "decision": "approved",
                "reviewer_id": "operator_001",
                "notes": "Approve safe draft changes.",
                "selected_change_ids": [],
            },
            resource_ids=_resource_ids(summary),
            requires_persistence=["feedback_review"],
            guardrails=["Review decisions are persisted audit records."],
        ),
    ]


def _revision_requested_commands(
    summary: CampaignFeedbackLoopSummaryResponse,
) -> list[FeedbackLoopOperatorCommand]:
    review_id = summary.latest_review_id
    return [
        _command(
            command_id="generate_revision_draft",
            action_type="generate_revision_draft",
            priority=1,
            enabled=review_id is not None,
            disabled_reason=_disabled_unless(review_id is not None, "No review ID is available."),
            label="Generate revision draft",
            description="Create a draft-only revision proposal from reviewer notes.",
            api_method="GET",
            api_path=(
                f"/feedback-optimization-reviews/{review_id or '<review_id>'}"
                "/revision-draft"
            ),
            cli_command=[
                "ads-growth-agent",
                "get-feedback-optimization-revision-draft",
                review_id or "<review_id>",
            ],
            resource_ids=_resource_ids(summary, review_id=review_id),
        ),
        _command(
            command_id="submit_revision_review",
            action_type="submit_revision_review",
            priority=2,
            enabled=review_id is not None and summary.review_persistence_enabled,
            disabled_reason=_disabled_unless(
                review_id is not None and summary.review_persistence_enabled,
                "A source review and feedback review persistence are required.",
            ),
            label="Review revision draft",
            description="Approve or reject the revised draft before execution planning.",
            api_method="POST",
            api_path=(
                f"/feedback-optimization-reviews/{review_id or '<review_id>'}"
                "/revision-draft/reviews"
            ),
            cli_command=[
                "ads-growth-agent",
                "submit-feedback-optimization-revision-review",
                review_id or "<review_id>",
                "--decision",
                "approved",
                "--reviewer-id",
                "<operator_id>",
            ],
            body_template={
                "decision": "approved",
                "reviewer_id": "operator_001",
                "notes": "Approve revised safe draft change.",
                "selected_change_ids": [],
            },
            resource_ids=_resource_ids(summary, review_id=review_id),
            requires_persistence=["feedback_review"],
        ),
    ]


def _execution_ready_commands(
    summary: CampaignFeedbackLoopSummaryResponse,
) -> list[FeedbackLoopOperatorCommand]:
    review_id = _execution_review_id(summary)
    return [
        _command(
            command_id="inspect_execution_plan",
            action_type="inspect_execution_plan",
            priority=1,
            enabled=review_id is not None,
            disabled_reason=_disabled_unless(
                review_id is not None,
                "No approved review is available.",
            ),
            label="Inspect dry-run execution plan",
            description="Review the ordered draft-only tool intents before validation.",
            api_method="GET",
            api_path=(
                f"/feedback-optimization-reviews/{review_id or '<review_id>'}"
                "/execution-plan"
            ),
            cli_command=[
                "ads-growth-agent",
                "get-feedback-execution-plan",
                review_id or "<review_id>",
            ],
            resource_ids=_resource_ids(summary, review_id=review_id),
        ),
        _command(
            command_id="run_execution_dry_run",
            action_type="run_execution_dry_run",
            priority=2,
            enabled=review_id is not None and summary.execution_persistence_enabled,
            disabled_reason=_disabled_unless(
                review_id is not None and summary.execution_persistence_enabled,
                "Enable FEEDBACK_EXECUTION_PERSISTENCE_BACKEND=postgres to persist "
                "dry-run validation.",
            ),
            label="Run dry-run validation",
            description="Validate draft-only tool intents without live campaign mutation.",
            api_method="POST",
            api_path=(
                f"/feedback-optimization-reviews/{review_id or '<review_id>'}"
                "/execution-plan/dry-run"
            ),
            cli_command=[
                "ads-growth-agent",
                "dry-run-feedback-execution-plan",
                review_id or "<review_id>",
            ],
            resource_ids=_resource_ids(summary, review_id=review_id),
            requires_persistence=["feedback_execution"],
            guardrails=["Dry-run validation must not mutate live campaign state."],
        ),
    ]


def _dry_run_passed_commands(
    summary: CampaignFeedbackLoopSummaryResponse,
) -> list[FeedbackLoopOperatorCommand]:
    review_id = _latest_dry_run(summary).review_id if _latest_dry_run(summary) else None
    return [
        _command(
            command_id="get_handoff_package",
            action_type="get_handoff_package",
            priority=1,
            enabled=review_id is not None,
            disabled_reason=_disabled_unless(
                review_id is not None,
                "No passed dry-run review is available.",
            ),
            label="Get manual handoff package",
            description=(
                "Prepare the approved dry-run-validated changes for manual "
                "platform handoff."
            ),
            api_method="GET",
            api_path=(
                f"/feedback-optimization-reviews/{review_id or '<review_id>'}"
                "/handoff-package"
            ),
            cli_command=[
                "ads-growth-agent",
                "get-feedback-handoff-package",
                review_id or "<review_id>",
            ],
            resource_ids=_resource_ids(summary, review_id=review_id),
        ),
        _command(
            command_id="submit_handoff_record",
            action_type="submit_handoff_record",
            priority=2,
            enabled=review_id is not None and summary.handoff_persistence_enabled,
            disabled_reason=_disabled_unless(
                review_id is not None and summary.handoff_persistence_enabled,
                "Enable feedback execution persistence to record handoff outcomes.",
            ),
            label="Record handoff outcome",
            description="Audit whether the manual handoff was applied, blocked, or skipped.",
            api_method="POST",
            api_path=(
                f"/feedback-optimization-reviews/{review_id or '<review_id>'}"
                "/handoff-records"
            ),
            cli_command=[
                "ads-growth-agent",
                "submit-feedback-handoff-record",
                review_id or "<review_id>",
                "--outcome",
                "applied",
                "--operator-id",
                "<operator_id>",
                "--completed-step-id",
                "<step_id>",
            ],
            body_template={
                "outcome": "applied",
                "operator_id": "operator_001",
                "completed_step_ids": ["<manual_step_id>"],
                "blocked_step_ids": [],
                "notes": None,
            },
            resource_ids=_resource_ids(summary, review_id=review_id),
            requires_persistence=["feedback_handoff"],
        ),
    ]


def _dry_run_failed_commands(
    summary: CampaignFeedbackLoopSummaryResponse,
) -> list[FeedbackLoopOperatorCommand]:
    dry_run = _latest_dry_run(summary)
    dry_run_id = dry_run.dry_run_id if dry_run else None
    review_id = dry_run.review_id if dry_run else _execution_review_id(summary)
    return [
        _command(
            command_id="inspect_failed_dry_run",
            action_type="inspect_failed_dry_run",
            priority=1,
            enabled=dry_run_id is not None,
            disabled_reason=_disabled_unless(
                dry_run_id is not None,
                "No failed dry-run record is available.",
            ),
            label="Inspect failed dry-run",
            description="Review blocked validation steps before creating a revised proposal.",
            api_method="GET",
            api_path=f"/feedback-execution-dry-runs/{dry_run_id or '<dry_run_id>'}",
            cli_command=[
                "ads-growth-agent",
                "get-feedback-execution-dry-run",
                dry_run_id or "<dry_run_id>",
            ],
            resource_ids=_resource_ids(summary, review_id=review_id, dry_run_id=dry_run_id),
        ),
    ]


def _handoff_applied_commands(
    summary: CampaignFeedbackLoopSummaryResponse,
) -> list[FeedbackLoopOperatorCommand]:
    return [
        _next_performance_event_command(
            summary,
            priority=1,
            description=(
                "Monitor manually applied changes by ingesting the next "
                "performance snapshot."
            ),
        )
    ]


def _outcome_report_commands(
    summary: CampaignFeedbackLoopSummaryResponse,
    outcome_report: CampaignFeedbackOutcomeReportResponse | None,
    *,
    limit: int,
) -> list[FeedbackLoopOperatorCommand]:
    commands = [
        _inspect_outcome_report_command(summary, priority=1, limit=limit),
    ]
    if outcome_report is None:
        return commands
    if outcome_report.outcome_status == "improved":
        return [
            *commands,
            _next_performance_event_command(
                summary,
                priority=2,
                description=(
                    "Continue monitoring improved post-handoff results with the "
                    "next performance snapshot."
                ),
            ),
        ]
    if outcome_report.outcome_status in {"regressed", "mixed"}:
        return [
            *commands,
            _followup_action_plan_command(summary, outcome_report, priority=2),
        ]
    return [
        *commands,
        _next_performance_event_command(
            summary,
            priority=2,
            description="Collect another snapshot before deciding whether to revise the plan.",
        ),
    ]


def _handoff_blocked_commands(
    summary: CampaignFeedbackLoopSummaryResponse,
) -> list[FeedbackLoopOperatorCommand]:
    return [
        _inspect_handoff_record_command(summary, priority=1),
        _command(
            command_id="inspect_optimization_draft",
            action_type="inspect_optimization_draft",
            priority=2,
            label="Revisit optimization draft",
            description="Use the existing draft context before creating a revised proposal.",
            api_method="GET",
            api_path=f"/campaign-events/performance/{summary.event_id}/optimization-draft",
            cli_command=[
                "ads-growth-agent",
                "get-feedback-optimization-draft",
                summary.event_id,
            ],
            resource_ids=_resource_ids(summary),
        ),
    ]


def _handoff_skipped_commands(
    summary: CampaignFeedbackLoopSummaryResponse,
) -> list[FeedbackLoopOperatorCommand]:
    return [
        _inspect_handoff_record_command(summary, priority=1),
        _next_performance_event_command(
            summary,
            priority=2,
            description="Keep monitoring after a skipped handoff and ingest the next snapshot.",
        ),
    ]


def _rejected_commands(
    summary: CampaignFeedbackLoopSummaryResponse,
) -> list[FeedbackLoopOperatorCommand]:
    return [
        _command(
            command_id="inspect_optimization_draft",
            action_type="inspect_optimization_draft",
            priority=1,
            label="Inspect rejected optimization draft",
            description=(
                "Review the rejected proposal and ingest new context before "
                "proposing again."
            ),
            api_method="GET",
            api_path=f"/campaign-events/performance/{summary.event_id}/optimization-draft",
            cli_command=[
                "ads-growth-agent",
                "get-feedback-optimization-draft",
                summary.event_id,
            ],
            resource_ids=_resource_ids(summary),
        ),
        _next_performance_event_command(
            summary,
            priority=2,
            description="Capture updated performance context before the next proposal.",
        ),
    ]


def _inspection_commands(
    summary: CampaignFeedbackLoopSummaryResponse,
    *,
    limit: int,
) -> list[FeedbackLoopOperatorCommand]:
    return [
        _command(
            command_id="inspect_feedback_loop_summary",
            action_type="inspect_feedback_loop_summary",
            priority=90,
            label="Inspect loop summary",
            description="Read current stage, counts, and next operator actions.",
            api_method="GET",
            api_path=f"/campaign-events/performance/{summary.event_id}/feedback-loop-summary",
            cli_command=[
                "ads-growth-agent",
                "get-feedback-loop-summary",
                summary.event_id,
                "--limit",
                str(limit),
            ],
            resource_ids=_resource_ids(summary),
        ),
        _command(
            command_id="inspect_feedback_loop_timeline",
            action_type="inspect_feedback_loop_timeline",
            priority=91,
            label="Inspect loop timeline",
            description="Read ordered audit milestones for the feedback loop.",
            api_method="GET",
            api_path=f"/campaign-events/performance/{summary.event_id}/feedback-loop-timeline",
            cli_command=[
                "ads-growth-agent",
                "get-feedback-loop-timeline",
                summary.event_id,
                "--limit",
                str(limit),
            ],
            resource_ids=_resource_ids(summary),
        ),
        _inspect_outcome_report_command(
            summary,
            priority=92,
            limit=limit,
        ),
    ]


def _next_performance_event_command(
    summary: CampaignFeedbackLoopSummaryResponse,
    *,
    priority: int,
    description: str,
) -> FeedbackLoopOperatorCommand:
    return _command(
        command_id="record_next_performance_event",
        action_type="record_next_performance_event",
        priority=priority,
        label="Ingest next performance event",
        description=description,
        api_method="POST",
        api_path="/campaign-events/performance",
        cli_command=[
            "ads-growth-agent",
            "analyze-performance",
            "examples/performance_event_underperforming.json",
        ],
        body_template={
            "event_id": f"{summary.event_id}_followup",
            "advertiser_id": summary.advertiser_id,
            "run_id": summary.run_id,
            "campaign_id": summary.campaign_id,
            "draft_id": summary.draft_id,
            "objective": summary.event.objective.value,
            "event_type": "performance_snapshot",
            "occurred_at": "<next_snapshot_timestamp>",
            "metrics": {
                "impressions": 0,
                "clicks": 0,
                "spend": "0.00",
                "conversions": 0,
            },
        },
        resource_ids=_resource_ids(summary),
        requires_persistence=["performance_event"],
    )


def _inspect_handoff_record_command(
    summary: CampaignFeedbackLoopSummaryResponse,
    *,
    priority: int,
) -> FeedbackLoopOperatorCommand:
    handoff_record_id = summary.latest_handoff_record_id
    return _command(
        command_id="inspect_handoff_record",
        action_type="inspect_handoff_record",
        priority=priority,
        enabled=handoff_record_id is not None,
        disabled_reason=_disabled_unless(
            handoff_record_id is not None,
            "No handoff outcome record is available.",
        ),
        label="Inspect handoff outcome",
        description="Review operator notes and completed or blocked handoff steps.",
        api_method="GET",
        api_path=(
            f"/feedback-handoff-records/"
            f"{handoff_record_id or '<handoff_record_id>'}"
        ),
        cli_command=[
            "ads-growth-agent",
            "get-feedback-handoff-record",
            handoff_record_id or "<handoff_record_id>",
        ],
        resource_ids=_resource_ids(summary, handoff_record_id=handoff_record_id),
    )


def _inspect_outcome_report_command(
    summary: CampaignFeedbackLoopSummaryResponse,
    *,
    priority: int,
    limit: int,
) -> FeedbackLoopOperatorCommand:
    return _command(
        command_id="inspect_feedback_outcome_report",
        action_type="inspect_feedback_outcome_report",
        priority=priority,
        label="Inspect outcome report",
        description=(
            "Compare this feedback event against the next persisted "
            "performance snapshot."
        ),
        api_method="GET",
        api_path=f"/campaign-events/performance/{summary.event_id}/feedback-outcome-report",
        cli_command=[
            "ads-growth-agent",
            "get-feedback-outcome-report",
            summary.event_id,
            "--limit",
            str(limit),
        ],
        resource_ids=_resource_ids(summary),
    )


def _followup_action_plan_command(
    summary: CampaignFeedbackLoopSummaryResponse,
    outcome_report: CampaignFeedbackOutcomeReportResponse,
    *,
    priority: int,
) -> FeedbackLoopOperatorCommand:
    followup_event_id = outcome_report.followup_event_id
    return _command(
        command_id="inspect_followup_action_plan",
        action_type="inspect_followup_action_plan",
        priority=priority,
        enabled=followup_event_id is not None,
        disabled_reason=_disabled_unless(
            followup_event_id is not None,
            "No follow-up performance event is available.",
        ),
        label="Inspect follow-up action plan",
        description=(
            "Review draft-only recommendations generated from the follow-up "
            "snapshot before opening another optimization loop."
        ),
        api_method="GET",
        api_path=(
            f"/campaign-events/performance/{followup_event_id or '<event_id>'}"
            "/action-plan"
        ),
        cli_command=[
            "ads-growth-agent",
            "get-feedback-action-plan",
            followup_event_id or "<event_id>",
        ],
        resource_ids=_resource_ids(summary, followup_event_id=followup_event_id),
        requires_persistence=["performance_event"],
    )


def _command_center_stage(
    summary: CampaignFeedbackLoopSummaryResponse,
    outcome_report: CampaignFeedbackOutcomeReportResponse | None,
) -> FeedbackLoopCurrentStage:
    if outcome_report is None or summary.current_stage != "handoff_applied":
        return summary.current_stage
    match outcome_report.outcome_status:
        case "improved":
            return "outcome_improved"
        case "regressed":
            return "outcome_regressed"
        case "mixed":
            return "outcome_mixed"
        case "insufficient_data":
            return "outcome_insufficient_data"
        case "no_followup_event":
            return summary.current_stage
    return summary.current_stage


def _dedupe_commands(
    commands: list[FeedbackLoopOperatorCommand],
) -> list[FeedbackLoopOperatorCommand]:
    deduped: list[FeedbackLoopOperatorCommand] = []
    seen: set[str] = set()
    for command in commands:
        if command.command_id in seen:
            continue
        seen.add(command.command_id)
        deduped.append(command)
    return deduped


def _command(
    *,
    command_id: str,
    action_type,
    priority: int,
    label: str,
    description: str,
    api_method: str,
    api_path: str,
    cli_command: list[str],
    enabled: bool = True,
    disabled_reason: str | None = None,
    body_template: dict[str, Any] | None = None,
    resource_ids: dict[str, str] | None = None,
    requires_persistence: list[str] | None = None,
    guardrails: list[str] | None = None,
) -> FeedbackLoopOperatorCommand:
    return FeedbackLoopOperatorCommand(
        command_id=command_id,
        action_type=action_type,
        priority=priority,
        enabled=enabled,
        disabled_reason=disabled_reason,
        label=label,
        description=description,
        api_method=api_method,
        api_path=api_path,
        cli_command=cli_command,
        body_template=body_template or {},
        resource_ids=resource_ids or {},
        requires_persistence=requires_persistence or [],
        guardrails=guardrails or [],
    )


def _primary_command(
    commands: list[FeedbackLoopOperatorCommand],
) -> FeedbackLoopOperatorCommand | None:
    if not commands:
        return None
    enabled_commands = [command for command in commands if command.enabled]
    candidates = enabled_commands or commands
    return sorted(candidates, key=lambda command: command.priority)[0]


def _execution_review_id(summary: CampaignFeedbackLoopSummaryResponse) -> str | None:
    dry_run = _latest_dry_run(summary)
    if dry_run is not None:
        return dry_run.review_id
    if summary.execution_ready_review_ids:
        return summary.execution_ready_review_ids[-1]
    if summary.approved_review_ids:
        return summary.approved_review_ids[-1]
    return summary.latest_review_id


def _latest_dry_run(
    summary: CampaignFeedbackLoopSummaryResponse,
) -> CampaignFeedbackExecutionDryRunResponse | None:
    if not summary.dry_runs.items:
        return None
    return sorted(summary.dry_runs.items, key=lambda dry_run: dry_run.created_at)[-1]


def _resource_ids(
    summary: CampaignFeedbackLoopSummaryResponse,
    **overrides: str | None,
) -> dict[str, str]:
    values = {
        "event_id": summary.event_id,
        "advertiser_id": summary.advertiser_id,
        "feedback_id": summary.event.analysis.feedback_id,
        "run_id": summary.run_id,
        "campaign_id": summary.campaign_id,
        "draft_id": summary.draft_id,
        "latest_review_id": summary.latest_review_id,
        "latest_dry_run_id": summary.latest_dry_run_id,
        "latest_handoff_record_id": summary.latest_handoff_record_id,
        **overrides,
    }
    return {key: value for key, value in values.items() if value}


def _disabled_unless(condition: bool, reason: str) -> str | None:
    return None if condition else reason
