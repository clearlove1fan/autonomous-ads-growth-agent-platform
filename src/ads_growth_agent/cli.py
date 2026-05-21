import json
from pathlib import Path
from typing import cast

import sqlalchemy as sa
import typer
from pydantic import ValidationError
from sqlalchemy.engine import make_url

from ads_growth_agent import __version__
from ads_growth_agent.advertiser_memory_store_factory import (
    build_configured_advertiser_memory_store,
)
from ads_growth_agent.brief_intake import parse_advertiser_brief
from ads_growth_agent.campaign_draft_store_factory import build_configured_campaign_draft_store
from ads_growth_agent.config import get_settings
from ads_growth_agent.contracts import (
    AdvertiserBrief,
    AdvertiserBriefIntakeRequest,
    AdvertiserMemoryListResponse,
    AdvertiserMemoryType,
    CampaignDraftListResponse,
    CampaignFeedbackExecutionDryRunListResponse,
    CampaignFeedbackHandoffPackageResponse,
    CampaignFeedbackHandoffRecordListResponse,
    CampaignFeedbackHandoffRecordRequest,
    CampaignFeedbackLoopCommandCenterResponse,
    CampaignFeedbackLoopSummaryResponse,
    CampaignFeedbackLoopTimelineResponse,
    CampaignFeedbackOptimizationReviewLineageListResponse,
    CampaignFeedbackOptimizationReviewListResponse,
    CampaignFeedbackOptimizationReviewRequest,
    CampaignFeedbackOutcomeReportResponse,
    CampaignPerformanceEventListResponse,
    CampaignPerformanceEventRequest,
    FeedbackHandoffOutcome,
    FeedbackOptimizationReviewDecision,
    GrowthStrategyRequest,
    PerformanceEventType,
    StrategyJobFromTextResponse,
    StrategyJobListResponse,
    StrategyJobStatus,
)
from ads_growth_agent.evaluation import load_eval_cases, run_local_eval_suite
from ads_growth_agent.feedback import (
    FeedbackRevisionDraftNotRequestedError,
    analyze_campaign_performance_event,
    build_campaign_feedback_action_plan,
    build_campaign_feedback_optimization_draft,
    build_campaign_feedback_optimization_revision_draft,
    build_campaign_feedback_revision_reviewable_draft,
)
from ads_growth_agent.feedback_execution_dry_run import dry_run_feedback_execution_plan
from ads_growth_agent.feedback_execution_plan import (
    FeedbackExecutionPlanNotApprovedError,
    build_feedback_execution_plan,
)
from ads_growth_agent.feedback_execution_store_factory import (
    build_configured_feedback_execution_store,
)
from ads_growth_agent.feedback_handoff_package import build_feedback_handoff_package
from ads_growth_agent.feedback_handoff_record import (
    FeedbackHandoffRecordNotReadyError,
    FeedbackHandoffRecordStepMismatchError,
)
from ads_growth_agent.feedback_handoff_store_factory import (
    build_configured_feedback_handoff_store,
)
from ads_growth_agent.feedback_lineage import (
    build_feedback_optimization_review_lineage,
)
from ads_growth_agent.feedback_lineage import (
    list_feedback_optimization_review_lineages as build_feedback_optimization_review_lineage_list,
)
from ads_growth_agent.feedback_loop_command_center import (
    build_campaign_feedback_loop_command_center,
)
from ads_growth_agent.feedback_loop_summary import build_campaign_feedback_loop_summary
from ads_growth_agent.feedback_loop_timeline import build_campaign_feedback_loop_timeline
from ads_growth_agent.feedback_outcome_report import build_campaign_feedback_outcome_report
from ads_growth_agent.feedback_review_store_factory import build_configured_feedback_review_store
from ads_growth_agent.handoff_memory import schedule_or_record_handoff_memory
from ads_growth_agent.logging_config import configure_logging
from ads_growth_agent.outbox import process_configured_outbox
from ads_growth_agent.outbox_store_factory import build_configured_outbox_store
from ads_growth_agent.performance_event_store_factory import (
    build_configured_performance_event_store,
)
from ads_growth_agent.persistence.advertiser_memory_store import AdvertiserMemoryConflictError
from ads_growth_agent.persistence.feedback_execution_store import FeedbackExecutionDryRunStatus
from ads_growth_agent.persistence.knowledge_seed import seed_default_knowledge
from ads_growth_agent.persistence.outbox_store import OutboxConflictError
from ads_growth_agent.strategy import StrategyGenerationError, generate_growth_strategy
from ads_growth_agent.strategy_job_store_factory import build_configured_strategy_job_store
from ads_growth_agent.strategy_job_submission import enqueue_strategy_job
from ads_growth_agent.strategy_job_worker import process_configured_strategy_jobs

app = typer.Typer(
    help="Autonomous Ads Growth Agent Platform CLI.",
    no_args_is_help=True,
)
BRIEF_FILE_ARGUMENT = typer.Argument(
    ...,
    exists=True,
    dir_okay=False,
    readable=True,
    help="Path to an advertiser brief JSON file.",
)
EVAL_FILE_ARGUMENT = typer.Argument(
    ...,
    exists=True,
    dir_okay=False,
    readable=True,
    help="Path to a local evaluation cases JSON file.",
)
STRATEGY_JOB_STATUS_OPTION = typer.Option(None, "--status")
STRATEGY_JOB_ADVERTISER_ID_OPTION = typer.Option(None, "--advertiser-id")
STRATEGY_JOB_RUN_ID_OPTION = typer.Option(None, "--run-id")
STRATEGY_JOB_LIST_LIMIT_OPTION = typer.Option(50, "--limit", min=1, max=100)
STRATEGY_JOB_ID_ARGUMENT = typer.Argument(..., help="Strategy job ID.")
STRATEGY_JOB_REQUESTED_BY_OPTION = typer.Option(
    "cli",
    "--requested-by",
    help="Operator or automation identifier recorded in retry metadata.",
)
STRATEGY_JOB_CANCEL_REASON_OPTION = typer.Option(
    None,
    "--reason",
    help="Human-readable cancellation reason.",
)
CAMPAIGN_DRAFT_ID_ARGUMENT = typer.Argument(..., help="Campaign draft ID.")
CAMPAIGN_DRAFT_ADVERTISER_ID_OPTION = typer.Option(None, "--advertiser-id")
CAMPAIGN_DRAFT_LIST_LIMIT_OPTION = typer.Option(50, "--limit", min=1, max=100)
ADVERTISER_MEMORY_ADVERTISER_ID_ARGUMENT = typer.Argument(..., help="Advertiser ID.")
ADVERTISER_MEMORY_SOURCE_ID_ARGUMENT = typer.Argument(..., help="Advertiser memory source ID.")
ADVERTISER_MEMORY_TYPE_OPTION = typer.Option(None, "--memory-type")
ADVERTISER_MEMORY_LIST_LIMIT_OPTION = typer.Option(50, "--limit", min=1, max=100)
ALLOWED_ADVERTISER_MEMORY_TYPES = {
    "profile",
    "constraint",
    "preference",
    "historical_performance",
}
BRIEF_TEXT_ARGUMENT = typer.Argument(
    ...,
    help="Plain-language advertiser goal or campaign brief.",
)
BRIEF_TEXT_ADVERTISER_ID_OPTION = typer.Option(None, "--advertiser-id")
BRIEF_TEXT_TARGET_MARKET_OPTION = typer.Option("United States", "--target-market")
BRIEF_TEXT_CURRENCY_OPTION = typer.Option("USD", "--currency")
BRIEF_TEXT_DURATION_OPTION = typer.Option(14, "--duration-days", min=1, max=365)
DEMO_TEXT_OPTION = typer.Option(
    (
        "I want to use a $2000 budget to promote a fitness app in the "
        "United States and increase trial registrations over 14 days."
    ),
    "--text",
    help="Plain-language advertiser goal for the deterministic Phase 1 demo.",
)
DEMO_ADVERTISER_ID_OPTION = typer.Option("adv_fitness_001", "--advertiser-id")
PERFORMANCE_EVENT_FILE_ARGUMENT = typer.Argument(
    ...,
    exists=True,
    dir_okay=False,
    readable=True,
    help="Path to a campaign performance event JSON file.",
)
PERFORMANCE_EVENT_ID_ARGUMENT = typer.Argument(..., help="Campaign performance event ID.")
PERFORMANCE_EVENT_ADVERTISER_ID_OPTION = typer.Option(None, "--advertiser-id")
PERFORMANCE_EVENT_RUN_ID_OPTION = typer.Option(None, "--run-id")
PERFORMANCE_EVENT_CAMPAIGN_ID_OPTION = typer.Option(None, "--campaign-id")
PERFORMANCE_EVENT_DRAFT_ID_OPTION = typer.Option(None, "--draft-id")
PERFORMANCE_EVENT_TYPE_OPTION = typer.Option(None, "--event-type")
PERFORMANCE_EVENT_LIST_LIMIT_OPTION = typer.Option(50, "--limit", min=1, max=100)
FEEDBACK_REVIEW_ID_ARGUMENT = typer.Argument(..., help="Feedback optimization review ID.")
FEEDBACK_REVIEW_DECISION_OPTION = typer.Option(..., "--decision")
FEEDBACK_REVIEW_DECISION_FILTER_OPTION = typer.Option(None, "--decision")
FEEDBACK_REVIEW_REVIEWER_ID_OPTION = typer.Option(..., "--reviewer-id")
FEEDBACK_REVIEW_NOTES_OPTION = typer.Option(None, "--notes")
FEEDBACK_REVIEW_SELECTED_CHANGE_ID_OPTION = typer.Option(None, "--selected-change-id")
FEEDBACK_REVIEW_EVENT_ID_OPTION = typer.Option(None, "--event-id")
FEEDBACK_REVIEW_OPTIMIZATION_DRAFT_ID_OPTION = typer.Option(None, "--optimization-draft-id")
FEEDBACK_REVIEW_LINEAGE_STAGE_OPTION = typer.Option(None, "--lineage-stage")
FEEDBACK_REVIEW_LIST_LIMIT_OPTION = typer.Option(50, "--limit", min=1, max=100)
FEEDBACK_EXECUTION_DRY_RUN_ID_ARGUMENT = typer.Argument(
    ...,
    help="Feedback execution dry-run ID.",
)
FEEDBACK_EXECUTION_REVIEW_ID_OPTION = typer.Option(None, "--review-id")
FEEDBACK_EXECUTION_PLAN_ID_OPTION = typer.Option(None, "--execution-plan-id")
FEEDBACK_EXECUTION_DRY_RUN_STATUS_OPTION = typer.Option(None, "--status")
FEEDBACK_EXECUTION_DRY_RUN_LIST_LIMIT_OPTION = typer.Option(50, "--limit", min=1, max=100)
FEEDBACK_HANDOFF_RECORD_ID_ARGUMENT = typer.Argument(..., help="Feedback handoff record ID.")
FEEDBACK_HANDOFF_PACKAGE_ID_OPTION = typer.Option(None, "--handoff-package-id")
FEEDBACK_HANDOFF_OUTCOME_OPTION = typer.Option(..., "--outcome")
FEEDBACK_HANDOFF_OUTCOME_FILTER_OPTION = typer.Option(None, "--outcome")
FEEDBACK_HANDOFF_OPERATOR_ID_OPTION = typer.Option(..., "--operator-id")
FEEDBACK_HANDOFF_NOTES_OPTION = typer.Option(None, "--notes")
FEEDBACK_HANDOFF_COMPLETED_STEP_ID_OPTION = typer.Option(None, "--completed-step-id")
FEEDBACK_HANDOFF_BLOCKED_STEP_ID_OPTION = typer.Option(None, "--blocked-step-id")
FEEDBACK_HANDOFF_LIST_LIMIT_OPTION = typer.Option(50, "--limit", min=1, max=100)
ALLOWED_PERFORMANCE_EVENT_TYPES = {
    "performance_snapshot",
    "budget_pacing",
    "creative_fatigue",
    "conversion_drop",
}
ALLOWED_FEEDBACK_REVIEW_DECISIONS = {
    "approved",
    "rejected",
    "needs_revision",
}
ALLOWED_FEEDBACK_EXECUTION_DRY_RUN_STATUSES = {
    "passed",
    "failed",
}
ALLOWED_FEEDBACK_REVIEW_LINEAGE_STAGES = {
    "approved",
    "rejected",
    "revision_requested",
    "revision_review",
}
ALLOWED_FEEDBACK_HANDOFF_OUTCOMES = {
    "applied",
    "blocked",
    "skipped",
}


@app.command()
def health() -> None:
    """Print local service health information."""
    settings = get_settings()
    typer.echo(
        {
            "status": "ok",
            "service": "ads-growth-agent",
            "version": __version__,
            "environment": settings.ads_growth_env,
        }
    )


@app.command()
def plan(brief_file: Path = BRIEF_FILE_ARGUMENT) -> None:
    """Generate a deterministic draft growth strategy from an advertiser brief."""
    try:
        payload = json.loads(brief_file.read_text())
        request = _parse_strategy_request(payload)
        response = generate_growth_strategy(request.brief)
    except json.JSONDecodeError as exc:
        typer.echo(f"Invalid JSON: {exc}", err=True)
        raise typer.Exit(2) from exc
    except ValidationError as exc:
        typer.echo(_validation_errors_json(exc), err=True)
        raise typer.Exit(2) from exc
    except StrategyGenerationError as exc:
        typer.echo(f"Strategy generation failed: {exc}", err=True)
        raise typer.Exit(1) from exc

    typer.echo(response.model_dump_json(indent=2))


@app.command("parse-brief-text")
def parse_brief_text(
    text: str = BRIEF_TEXT_ARGUMENT,
    advertiser_id: str | None = BRIEF_TEXT_ADVERTISER_ID_OPTION,
    target_market: str = BRIEF_TEXT_TARGET_MARKET_OPTION,
    currency: str = BRIEF_TEXT_CURRENCY_OPTION,
    duration_days: int = BRIEF_TEXT_DURATION_OPTION,
) -> None:
    """Parse a plain-language advertiser request into a structured brief."""
    try:
        settings = get_settings()
        response = parse_advertiser_brief(
            AdvertiserBriefIntakeRequest(
                text=text,
                advertiser_id=advertiser_id,
                default_target_market=target_market,
                default_currency=currency,
                default_duration_days=duration_days,
            ),
            settings=settings,
        )
    except ValidationError as exc:
        typer.echo(_validation_errors_json(exc), err=True)
        raise typer.Exit(2) from exc

    typer.echo(response.model_dump_json(indent=2))


@app.command("plan-text")
def plan_text(
    text: str = BRIEF_TEXT_ARGUMENT,
    advertiser_id: str | None = BRIEF_TEXT_ADVERTISER_ID_OPTION,
    target_market: str = BRIEF_TEXT_TARGET_MARKET_OPTION,
    currency: str = BRIEF_TEXT_CURRENCY_OPTION,
    duration_days: int = BRIEF_TEXT_DURATION_OPTION,
) -> None:
    """Generate a growth strategy directly from a plain-language advertiser request."""
    try:
        settings = get_settings()
        intake = parse_advertiser_brief(
            AdvertiserBriefIntakeRequest(
                text=text,
                advertiser_id=advertiser_id,
                default_target_market=target_market,
                default_currency=currency,
                default_duration_days=duration_days,
            ),
            settings=settings,
        )
        response = generate_growth_strategy(intake.brief, settings=settings)
    except ValidationError as exc:
        typer.echo(_validation_errors_json(exc), err=True)
        raise typer.Exit(2) from exc
    except StrategyGenerationError as exc:
        typer.echo(f"Strategy generation failed: {exc}", err=True)
        raise typer.Exit(1) from exc

    typer.echo(
        json.dumps(
            {
                "intake": intake.model_dump(mode="json"),
                "growth_strategy": response.model_dump(mode="json"),
            },
            indent=2,
        )
    )


@app.command("analyze-performance")
def analyze_performance(
    event_file: Path = PERFORMANCE_EVENT_FILE_ARGUMENT,
) -> None:
    """Analyze a campaign performance event and return draft optimization guidance."""
    try:
        payload = json.loads(event_file.read_text())
        event = CampaignPerformanceEventRequest.model_validate(payload)
        analysis = analyze_campaign_performance_event(event)
    except json.JSONDecodeError as exc:
        typer.echo(f"Invalid JSON: {exc}", err=True)
        raise typer.Exit(2) from exc
    except ValidationError as exc:
        typer.echo(_validation_errors_json(exc), err=True)
        raise typer.Exit(2) from exc

    typer.echo(analysis.model_dump_json(indent=2))


@app.command("get-performance-event")
def get_performance_event(event_id: str = PERFORMANCE_EVENT_ID_ARGUMENT) -> None:
    """Fetch one persisted campaign performance event by ID."""
    settings = get_settings()
    store = build_configured_performance_event_store(settings)
    event = store.get_event(event_id)
    if event is None:
        typer.echo(f"Performance event not found: {event_id}", err=True)
        raise typer.Exit(1)
    typer.echo(event.model_dump_json(indent=2))


@app.command("get-feedback-action-plan")
def get_feedback_action_plan(event_id: str = PERFORMANCE_EVENT_ID_ARGUMENT) -> None:
    """Fetch draft-only next steps for one persisted campaign performance event."""
    settings = get_settings()
    store = build_configured_performance_event_store(settings)
    event = store.get_event(event_id)
    if event is None:
        typer.echo(f"Performance event not found: {event_id}", err=True)
        raise typer.Exit(1)
    action_plan = build_campaign_feedback_action_plan(event)
    typer.echo(action_plan.model_dump_json(indent=2))


@app.command("get-feedback-optimization-draft")
def get_feedback_optimization_draft(event_id: str = PERFORMANCE_EVENT_ID_ARGUMENT) -> None:
    """Fetch a draft-only optimization proposal for one persisted feedback event."""
    settings = get_settings()
    store = build_configured_performance_event_store(settings)
    event = store.get_event(event_id)
    if event is None:
        typer.echo(f"Performance event not found: {event_id}", err=True)
        raise typer.Exit(1)
    optimization_draft = build_campaign_feedback_optimization_draft(event)
    typer.echo(optimization_draft.model_dump_json(indent=2))


@app.command("get-feedback-loop-summary")
def get_feedback_loop_summary(
    event_id: str = PERFORMANCE_EVENT_ID_ARGUMENT,
    limit: int = FEEDBACK_REVIEW_LIST_LIMIT_OPTION,
) -> None:
    """Fetch a read-only operator summary for one persisted feedback event."""
    settings = get_settings()
    event_store = build_configured_performance_event_store(settings)
    event = event_store.get_event(event_id)
    if event is None:
        typer.echo(f"Performance event not found: {event_id}", err=True)
        raise typer.Exit(1)
    review_store = build_configured_feedback_review_store(settings)
    execution_store = build_configured_feedback_execution_store(settings)
    handoff_store = build_configured_feedback_handoff_store(settings)
    summary = build_campaign_feedback_loop_summary(
        event,
        review_store,
        execution_store,
        handoff_store,
        review_persistence_enabled=settings.feedback_review_persistence_backend != "none",
        execution_persistence_enabled=(
            settings.feedback_execution_persistence_backend != "none"
        ),
        handoff_persistence_enabled=(
            settings.feedback_execution_persistence_backend != "none"
        ),
        limit=limit,
    )
    response = CampaignFeedbackLoopSummaryResponse.model_validate(summary)
    typer.echo(response.model_dump_json(indent=2))


@app.command("get-feedback-loop-timeline")
def get_feedback_loop_timeline(
    event_id: str = PERFORMANCE_EVENT_ID_ARGUMENT,
    limit: int = FEEDBACK_REVIEW_LIST_LIMIT_OPTION,
) -> None:
    """Fetch an ordered operator timeline for one persisted feedback event."""
    settings = get_settings()
    event_store = build_configured_performance_event_store(settings)
    event = event_store.get_event(event_id)
    if event is None:
        typer.echo(f"Performance event not found: {event_id}", err=True)
        raise typer.Exit(1)
    review_store = build_configured_feedback_review_store(settings)
    execution_store = build_configured_feedback_execution_store(settings)
    handoff_store = build_configured_feedback_handoff_store(settings)
    timeline = build_campaign_feedback_loop_timeline(
        event,
        review_store,
        execution_store,
        handoff_store,
        review_persistence_enabled=settings.feedback_review_persistence_backend != "none",
        execution_persistence_enabled=(
            settings.feedback_execution_persistence_backend != "none"
        ),
        handoff_persistence_enabled=(
            settings.feedback_execution_persistence_backend != "none"
        ),
        limit=limit,
    )
    response = CampaignFeedbackLoopTimelineResponse.model_validate(timeline)
    typer.echo(response.model_dump_json(indent=2))


@app.command("get-feedback-loop-command-center")
def get_feedback_loop_command_center(
    event_id: str = PERFORMANCE_EVENT_ID_ARGUMENT,
    limit: int = FEEDBACK_REVIEW_LIST_LIMIT_OPTION,
) -> None:
    """Fetch stage-aware operator commands for one persisted feedback event."""
    settings = get_settings()
    event_store = build_configured_performance_event_store(settings)
    event = event_store.get_event(event_id)
    if event is None:
        typer.echo(f"Performance event not found: {event_id}", err=True)
        raise typer.Exit(1)
    review_store = build_configured_feedback_review_store(settings)
    execution_store = build_configured_feedback_execution_store(settings)
    handoff_store = build_configured_feedback_handoff_store(settings)
    command_center = build_campaign_feedback_loop_command_center(
        event,
        review_store,
        execution_store,
        handoff_store,
        review_persistence_enabled=settings.feedback_review_persistence_backend != "none",
        execution_persistence_enabled=(
            settings.feedback_execution_persistence_backend != "none"
        ),
        handoff_persistence_enabled=(
            settings.feedback_execution_persistence_backend != "none"
        ),
        outcome_event_store=event_store,
        limit=limit,
    )
    response = CampaignFeedbackLoopCommandCenterResponse.model_validate(command_center)
    typer.echo(response.model_dump_json(indent=2))


@app.command("get-feedback-outcome-report")
def get_feedback_outcome_report(
    event_id: str = PERFORMANCE_EVENT_ID_ARGUMENT,
    limit: int = FEEDBACK_REVIEW_LIST_LIMIT_OPTION,
) -> None:
    """Compare one persisted feedback event with the next performance snapshot."""
    settings = get_settings()
    event_store = build_configured_performance_event_store(settings)
    event = event_store.get_event(event_id)
    if event is None:
        typer.echo(f"Performance event not found: {event_id}", err=True)
        raise typer.Exit(1)
    report = build_campaign_feedback_outcome_report(event, event_store, limit=limit)
    response = CampaignFeedbackOutcomeReportResponse.model_validate(report)
    typer.echo(response.model_dump_json(indent=2))


@app.command("submit-feedback-optimization-review")
def submit_feedback_optimization_review(
    event_id: str = PERFORMANCE_EVENT_ID_ARGUMENT,
    decision: str = FEEDBACK_REVIEW_DECISION_OPTION,
    reviewer_id: str = FEEDBACK_REVIEW_REVIEWER_ID_OPTION,
    notes: str | None = FEEDBACK_REVIEW_NOTES_OPTION,
    selected_change_id: list[str] | None = FEEDBACK_REVIEW_SELECTED_CHANGE_ID_OPTION,
) -> None:
    """Record a human review decision for one feedback optimization draft."""
    try:
        settings = get_settings()
        _ensure_feedback_review_persistence_enabled(settings)
        event_store = build_configured_performance_event_store(settings)
        event = event_store.get_event(event_id)
        if event is None:
            typer.echo(f"Performance event not found: {event_id}", err=True)
            raise typer.Exit(1)
        request = CampaignFeedbackOptimizationReviewRequest(
            decision=_feedback_review_decision_or_exit(decision),
            reviewer_id=reviewer_id,
            notes=notes,
            selected_change_ids=selected_change_id or [],
        )
        review_store = build_configured_feedback_review_store(settings)
        optimization_draft = build_campaign_feedback_optimization_draft(event)
        review = review_store.record_review(optimization_draft, request)
    except ValidationError as exc:
        typer.echo(_validation_errors_json(exc), err=True)
        raise typer.Exit(2) from exc
    except ValueError as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(2) from exc
    except RuntimeError as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(2) from exc

    typer.echo(review.model_dump_json(indent=2))


@app.command("get-feedback-optimization-review")
def get_feedback_optimization_review(review_id: str = FEEDBACK_REVIEW_ID_ARGUMENT) -> None:
    """Fetch one persisted feedback optimization review by ID."""
    settings = get_settings()
    _ensure_feedback_review_persistence_enabled(settings)
    store = build_configured_feedback_review_store(settings)
    review = store.get_review(review_id)
    if review is None:
        typer.echo(f"Feedback optimization review not found: {review_id}", err=True)
        raise typer.Exit(1)
    typer.echo(review.model_dump_json(indent=2))


@app.command("get-feedback-optimization-review-lineage")
def get_feedback_optimization_review_lineage(
    review_id: str = FEEDBACK_REVIEW_ID_ARGUMENT,
) -> None:
    """Fetch audit lineage for one feedback optimization review."""
    try:
        settings = get_settings()
        _ensure_feedback_review_persistence_enabled(settings)
        store = build_configured_feedback_review_store(settings)
        review = store.get_review(review_id)
        if review is None:
            typer.echo(f"Feedback optimization review not found: {review_id}", err=True)
            raise typer.Exit(1)
        execution_store = build_configured_feedback_execution_store(settings)
        lineage = build_feedback_optimization_review_lineage(review, store, execution_store)
    except ValueError as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(2) from exc

    typer.echo(lineage.model_dump_json(indent=2))


@app.command("get-feedback-optimization-revision-draft")
def get_feedback_optimization_revision_draft(
    review_id: str = FEEDBACK_REVIEW_ID_ARGUMENT,
) -> None:
    """Fetch a revised optimization draft for one needs-revision review."""
    try:
        settings = get_settings()
        _ensure_feedback_review_persistence_enabled(settings)
        store = build_configured_feedback_review_store(settings)
        review = store.get_review(review_id)
        if review is None:
            typer.echo(f"Feedback optimization review not found: {review_id}", err=True)
            raise typer.Exit(1)
        revision_draft = build_campaign_feedback_optimization_revision_draft(review)
    except FeedbackRevisionDraftNotRequestedError as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(1) from exc
    except ValueError as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(2) from exc

    typer.echo(revision_draft.model_dump_json(indent=2))


@app.command("submit-feedback-optimization-revision-review")
def submit_feedback_optimization_revision_review(
    review_id: str = FEEDBACK_REVIEW_ID_ARGUMENT,
    decision: str = FEEDBACK_REVIEW_DECISION_OPTION,
    reviewer_id: str = FEEDBACK_REVIEW_REVIEWER_ID_OPTION,
    notes: str | None = FEEDBACK_REVIEW_NOTES_OPTION,
    selected_change_id: list[str] | None = FEEDBACK_REVIEW_SELECTED_CHANGE_ID_OPTION,
) -> None:
    """Record a human review decision for one revision draft."""
    try:
        settings = get_settings()
        _ensure_feedback_review_persistence_enabled(settings)
        store = build_configured_feedback_review_store(settings)
        source_review = store.get_review(review_id)
        if source_review is None:
            typer.echo(f"Feedback optimization review not found: {review_id}", err=True)
            raise typer.Exit(1)
        request = CampaignFeedbackOptimizationReviewRequest(
            decision=_feedback_review_decision_or_exit(decision),
            reviewer_id=reviewer_id,
            notes=notes,
            selected_change_ids=selected_change_id or [],
        )
        reviewable_draft = build_campaign_feedback_revision_reviewable_draft(source_review)
        review = store.record_review(reviewable_draft, request)
    except FeedbackRevisionDraftNotRequestedError as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(1) from exc
    except ValidationError as exc:
        typer.echo(_validation_errors_json(exc), err=True)
        raise typer.Exit(2) from exc
    except ValueError as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(2) from exc
    except RuntimeError as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(2) from exc

    typer.echo(review.model_dump_json(indent=2))


@app.command("get-feedback-execution-plan")
def get_feedback_execution_plan(review_id: str = FEEDBACK_REVIEW_ID_ARGUMENT) -> None:
    """Fetch a dry-run execution plan for one approved feedback optimization review."""
    try:
        settings = get_settings()
        _ensure_feedback_review_persistence_enabled(settings)
        store = build_configured_feedback_review_store(settings)
        review = store.get_review(review_id)
        if review is None:
            typer.echo(f"Feedback optimization review not found: {review_id}", err=True)
            raise typer.Exit(1)
        execution_plan = build_feedback_execution_plan(review)
    except FeedbackExecutionPlanNotApprovedError as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(1) from exc
    except ValueError as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(2) from exc

    typer.echo(execution_plan.model_dump_json(indent=2))


@app.command("get-feedback-handoff-package")
def get_feedback_handoff_package(review_id: str = FEEDBACK_REVIEW_ID_ARGUMENT) -> None:
    """Fetch a read-only manual handoff package for one approved feedback review."""
    try:
        settings = get_settings()
        _ensure_feedback_review_persistence_enabled(settings)
        store = build_configured_feedback_review_store(settings)
        review = store.get_review(review_id)
        if review is None:
            typer.echo(f"Feedback optimization review not found: {review_id}", err=True)
            raise typer.Exit(1)
        execution_store = build_configured_feedback_execution_store(settings)
        package = build_feedback_handoff_package(review, execution_store)
    except FeedbackExecutionPlanNotApprovedError as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(1) from exc
    except ValueError as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(2) from exc

    response = CampaignFeedbackHandoffPackageResponse.model_validate(package)
    typer.echo(response.model_dump_json(indent=2))


@app.command("submit-feedback-handoff-record")
def submit_feedback_handoff_record(
    review_id: str = FEEDBACK_REVIEW_ID_ARGUMENT,
    outcome: str = FEEDBACK_HANDOFF_OUTCOME_OPTION,
    operator_id: str = FEEDBACK_HANDOFF_OPERATOR_ID_OPTION,
    notes: str | None = FEEDBACK_HANDOFF_NOTES_OPTION,
    completed_step_ids: list[str] | None = FEEDBACK_HANDOFF_COMPLETED_STEP_ID_OPTION,
    blocked_step_ids: list[str] | None = FEEDBACK_HANDOFF_BLOCKED_STEP_ID_OPTION,
) -> None:
    """Record an operator outcome for one manual feedback handoff package."""
    try:
        settings = get_settings()
        _ensure_feedback_review_persistence_enabled(settings)
        _ensure_feedback_execution_persistence_enabled(settings)
        review_store = build_configured_feedback_review_store(settings)
        review = review_store.get_review(review_id)
        if review is None:
            typer.echo(f"Feedback optimization review not found: {review_id}", err=True)
            raise typer.Exit(1)
        execution_store = build_configured_feedback_execution_store(settings)
        handoff_package = build_feedback_handoff_package(review, execution_store)
        validated_outcome = _feedback_handoff_outcome_or_exit(outcome)
        if validated_outcome is None:
            raise ValueError("feedback handoff outcome is required")
        request = CampaignFeedbackHandoffRecordRequest(
            outcome=validated_outcome,
            operator_id=operator_id,
            notes=notes,
            completed_step_ids=completed_step_ids or [],
            blocked_step_ids=blocked_step_ids or [],
        )
        handoff_store = build_configured_feedback_handoff_store(settings)
        record = handoff_store.record_handoff(handoff_package, request)
        schedule_or_record_handoff_memory(
            settings,
            record,
            memory_store=build_configured_advertiser_memory_store(settings),
            outbox_store=build_configured_outbox_store(settings),
        )
    except FeedbackExecutionPlanNotApprovedError as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(1) from exc
    except FeedbackHandoffRecordNotReadyError as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(1) from exc
    except (AdvertiserMemoryConflictError, OutboxConflictError) as exc:
        typer.echo(f"Failed to record handoff memory: {exc}", err=True)
        raise typer.Exit(1) from exc
    except (FeedbackHandoffRecordStepMismatchError, ValueError) as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(2) from exc

    typer.echo(record.model_dump_json(indent=2))


@app.command("get-feedback-handoff-record")
def get_feedback_handoff_record(
    handoff_record_id: str = FEEDBACK_HANDOFF_RECORD_ID_ARGUMENT,
) -> None:
    """Fetch one persisted feedback handoff acknowledgement record."""
    settings = get_settings()
    _ensure_feedback_execution_persistence_enabled(settings)
    store = build_configured_feedback_handoff_store(settings)
    record = store.get_handoff_record(handoff_record_id)
    if record is None:
        typer.echo(f"Feedback handoff record not found: {handoff_record_id}", err=True)
        raise typer.Exit(1)
    typer.echo(record.model_dump_json(indent=2))


@app.command("list-feedback-handoff-records")
def list_feedback_handoff_records(
    review_id: str | None = FEEDBACK_EXECUTION_REVIEW_ID_OPTION,
    handoff_package_id: str | None = FEEDBACK_HANDOFF_PACKAGE_ID_OPTION,
    event_id: str | None = FEEDBACK_REVIEW_EVENT_ID_OPTION,
    advertiser_id: str | None = PERFORMANCE_EVENT_ADVERTISER_ID_OPTION,
    outcome: str | None = FEEDBACK_HANDOFF_OUTCOME_FILTER_OPTION,
    limit: int = FEEDBACK_HANDOFF_LIST_LIMIT_OPTION,
) -> None:
    """List recent persisted feedback handoff acknowledgement records."""
    settings = get_settings()
    _ensure_feedback_execution_persistence_enabled(settings)
    validated_outcome = _feedback_handoff_outcome_or_exit(outcome)
    store = build_configured_feedback_handoff_store(settings)
    records = store.list_handoff_records(
        review_id=review_id,
        handoff_package_id=handoff_package_id,
        event_id=event_id,
        advertiser_id=advertiser_id,
        outcome=validated_outcome,
        limit=limit,
    )
    response = CampaignFeedbackHandoffRecordListResponse(
        items=records.items,
        count=records.count,
        limit=limit,
        review_id=review_id,
        handoff_package_id=handoff_package_id,
        event_id=event_id,
        advertiser_id=advertiser_id,
        outcome=validated_outcome,
    )
    typer.echo(response.model_dump_json(indent=2))


@app.command("dry-run-feedback-execution-plan")
def dry_run_feedback_execution_plan_command(
    review_id: str = FEEDBACK_REVIEW_ID_ARGUMENT,
) -> None:
    """Validate an approved feedback execution plan through draft-only tools."""
    try:
        settings = get_settings()
        _ensure_feedback_review_persistence_enabled(settings)
        store = build_configured_feedback_review_store(settings)
        review = store.get_review(review_id)
        if review is None:
            typer.echo(f"Feedback optimization review not found: {review_id}", err=True)
            raise typer.Exit(1)
        execution_plan = build_feedback_execution_plan(review)
        dry_run = dry_run_feedback_execution_plan(execution_plan)
        feedback_execution_store = build_configured_feedback_execution_store(settings)
        dry_run = feedback_execution_store.record_dry_run(execution_plan, dry_run)
    except FeedbackExecutionPlanNotApprovedError as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(1) from exc
    except ValueError as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(2) from exc

    typer.echo(dry_run.model_dump_json(indent=2))


@app.command("get-feedback-execution-dry-run")
def get_feedback_execution_dry_run(
    dry_run_id: str = FEEDBACK_EXECUTION_DRY_RUN_ID_ARGUMENT,
) -> None:
    """Fetch one persisted feedback execution dry-run result by ID."""
    settings = get_settings()
    _ensure_feedback_execution_persistence_enabled(settings)
    store = build_configured_feedback_execution_store(settings)
    dry_run = store.get_dry_run(dry_run_id)
    if dry_run is None:
        typer.echo(f"Feedback execution dry run not found: {dry_run_id}", err=True)
        raise typer.Exit(1)
    typer.echo(dry_run.model_dump_json(indent=2))


@app.command("list-feedback-execution-dry-runs")
def list_feedback_execution_dry_runs(
    review_id: str | None = FEEDBACK_EXECUTION_REVIEW_ID_OPTION,
    execution_plan_id: str | None = FEEDBACK_EXECUTION_PLAN_ID_OPTION,
    event_id: str | None = FEEDBACK_REVIEW_EVENT_ID_OPTION,
    advertiser_id: str | None = PERFORMANCE_EVENT_ADVERTISER_ID_OPTION,
    status: str | None = FEEDBACK_EXECUTION_DRY_RUN_STATUS_OPTION,
    limit: int = FEEDBACK_EXECUTION_DRY_RUN_LIST_LIMIT_OPTION,
) -> None:
    """List recent persisted feedback execution dry-run results."""
    settings = get_settings()
    _ensure_feedback_execution_persistence_enabled(settings)
    validated_status = _feedback_execution_dry_run_status_or_exit(status)
    store = build_configured_feedback_execution_store(settings)
    dry_runs = store.list_dry_runs(
        review_id=review_id,
        execution_plan_id=execution_plan_id,
        event_id=event_id,
        advertiser_id=advertiser_id,
        status=validated_status,
        limit=limit,
    )
    response = CampaignFeedbackExecutionDryRunListResponse(
        items=dry_runs.items,
        count=dry_runs.count,
        limit=limit,
        review_id=review_id,
        execution_plan_id=execution_plan_id,
        event_id=event_id,
        advertiser_id=advertiser_id,
        status=validated_status,
    )
    typer.echo(response.model_dump_json(indent=2))


@app.command("list-feedback-optimization-reviews")
def list_feedback_optimization_reviews(
    event_id: str | None = FEEDBACK_REVIEW_EVENT_ID_OPTION,
    advertiser_id: str | None = PERFORMANCE_EVENT_ADVERTISER_ID_OPTION,
    optimization_draft_id: str | None = FEEDBACK_REVIEW_OPTIMIZATION_DRAFT_ID_OPTION,
    decision: str | None = FEEDBACK_REVIEW_DECISION_FILTER_OPTION,
    limit: int = FEEDBACK_REVIEW_LIST_LIMIT_OPTION,
) -> None:
    """List recent persisted feedback optimization reviews."""
    settings = get_settings()
    _ensure_feedback_review_persistence_enabled(settings)
    validated_decision = _feedback_review_decision_or_exit(decision)
    store = build_configured_feedback_review_store(settings)
    reviews = store.list_reviews(
        event_id=event_id,
        advertiser_id=advertiser_id,
        optimization_draft_id=optimization_draft_id,
        decision=validated_decision,
        limit=limit,
    )
    response = CampaignFeedbackOptimizationReviewListResponse(
        items=reviews.items,
        count=reviews.count,
        limit=limit,
        event_id=event_id,
        advertiser_id=advertiser_id,
        optimization_draft_id=optimization_draft_id,
        decision=validated_decision,
    )
    typer.echo(response.model_dump_json(indent=2))


@app.command("list-feedback-optimization-review-lineages")
def list_feedback_optimization_review_lineages_command(
    event_id: str | None = FEEDBACK_REVIEW_EVENT_ID_OPTION,
    advertiser_id: str | None = PERFORMANCE_EVENT_ADVERTISER_ID_OPTION,
    optimization_draft_id: str | None = FEEDBACK_REVIEW_OPTIMIZATION_DRAFT_ID_OPTION,
    decision: str | None = FEEDBACK_REVIEW_DECISION_FILTER_OPTION,
    lineage_stage: str | None = FEEDBACK_REVIEW_LINEAGE_STAGE_OPTION,
    limit: int = FEEDBACK_REVIEW_LIST_LIMIT_OPTION,
) -> None:
    """List derived audit lineage records for persisted feedback optimization reviews."""
    settings = get_settings()
    _ensure_feedback_review_persistence_enabled(settings)
    validated_decision = _feedback_review_decision_or_exit(decision)
    validated_lineage_stage = _feedback_review_lineage_stage_or_exit(lineage_stage)
    review_store = build_configured_feedback_review_store(settings)
    execution_store = build_configured_feedback_execution_store(settings)
    lineages = build_feedback_optimization_review_lineage_list(
        review_store,
        execution_store,
        event_id=event_id,
        advertiser_id=advertiser_id,
        optimization_draft_id=optimization_draft_id,
        decision=validated_decision,
        lineage_stage=validated_lineage_stage,
        limit=limit,
    )
    response = CampaignFeedbackOptimizationReviewLineageListResponse(
        items=lineages.items,
        count=lineages.count,
        limit=limit,
        event_id=event_id,
        advertiser_id=advertiser_id,
        optimization_draft_id=optimization_draft_id,
        decision=validated_decision,
        lineage_stage=validated_lineage_stage,
    )
    typer.echo(response.model_dump_json(indent=2))


@app.command("list-performance-events")
def list_performance_events(
    advertiser_id: str | None = PERFORMANCE_EVENT_ADVERTISER_ID_OPTION,
    run_id: str | None = PERFORMANCE_EVENT_RUN_ID_OPTION,
    campaign_id: str | None = PERFORMANCE_EVENT_CAMPAIGN_ID_OPTION,
    draft_id: str | None = PERFORMANCE_EVENT_DRAFT_ID_OPTION,
    event_type: str | None = PERFORMANCE_EVENT_TYPE_OPTION,
    limit: int = PERFORMANCE_EVENT_LIST_LIMIT_OPTION,
) -> None:
    """List recent persisted campaign performance events."""
    settings = get_settings()
    store = build_configured_performance_event_store(settings)
    validated_event_type = _performance_event_type_or_exit(event_type)
    events = store.list_events(
        advertiser_id=advertiser_id,
        run_id=run_id,
        campaign_id=campaign_id,
        draft_id=draft_id,
        event_type=validated_event_type,
        limit=limit,
    )
    response = CampaignPerformanceEventListResponse(
        items=events,
        count=len(events),
        limit=limit,
        advertiser_id=advertiser_id,
        run_id=run_id,
        campaign_id=campaign_id,
        draft_id=draft_id,
        event_type=validated_event_type,
    )
    typer.echo(response.model_dump_json(indent=2))


@app.command("demo")
def demo(
    text: str = DEMO_TEXT_OPTION,
    advertiser_id: str = DEMO_ADVERTISER_ID_OPTION,
    target_market: str = BRIEF_TEXT_TARGET_MARKET_OPTION,
    currency: str = BRIEF_TEXT_CURRENCY_OPTION,
    duration_days: int = BRIEF_TEXT_DURATION_OPTION,
) -> None:
    """Run the deterministic Phase 1 MVP flow end to end."""
    try:
        settings = get_settings()
        intake = parse_advertiser_brief(
            AdvertiserBriefIntakeRequest(
                text=text,
                advertiser_id=advertiser_id,
                default_target_market=target_market,
                default_currency=currency,
                default_duration_days=duration_days,
            ),
            settings=settings,
        )
        growth_strategy = generate_growth_strategy(intake.brief, settings=settings)
        event = _demo_performance_event(
            advertiser_id=intake.brief.advertiser_id,
            run_id=growth_strategy.run_metadata.run_id,
            objective=growth_strategy.strategy.objective,
            draft_id=growth_strategy.strategy.campaign_draft.draft_id,
            strategy_context=growth_strategy.strategy.feedback_context,
        )
        feedback_analysis = analyze_campaign_performance_event(event)
    except ValidationError as exc:
        typer.echo(_validation_errors_json(exc), err=True)
        raise typer.Exit(2) from exc
    except StrategyGenerationError as exc:
        typer.echo(f"Strategy generation failed: {exc}", err=True)
        raise typer.Exit(1) from exc

    typer.echo(
        json.dumps(
            {
                "demo_case": "phase1_fitness_app_underperforming_feedback",
                "input_text": text,
                "intake": intake.model_dump(mode="json"),
                "growth_strategy": growth_strategy.model_dump(mode="json"),
                "performance_event": event.model_dump(mode="json"),
                "feedback_analysis": feedback_analysis.model_dump(mode="json"),
            },
            indent=2,
        )
    )


@app.command("seed-knowledge")
def seed_knowledge() -> None:
    """Seed the default RAG and advertiser-memory corpus into PostgreSQL."""
    settings = get_settings()
    engine = sa.create_engine(settings.database_url, pool_pre_ping=True)
    try:
        seed_default_knowledge(engine, tenant_id=settings.tenant_id)
    finally:
        engine.dispose()

    typer.echo(
        json.dumps(
            {
                "status": "ok",
                "tenant_id": settings.tenant_id,
                "database_url": _safe_database_url(settings.database_url),
                "seeded_corpus": "default_knowledge_documents",
            },
            indent=2,
        )
    )


@app.command("process-outbox")
def process_outbox(
    limit: int = typer.Option(100, "--limit", min=1, max=1_000),
    worker_id: str | None = typer.Option(None, "--worker-id"),
) -> None:
    """Process a bounded batch of durable outbox events."""
    settings = get_settings()
    report = process_configured_outbox(settings, limit=limit, worker_id=worker_id)
    typer.echo(report.model_dump_json(indent=2))


@app.command("get-campaign-draft")
def get_campaign_draft(draft_id: str = CAMPAIGN_DRAFT_ID_ARGUMENT) -> None:
    """Fetch one persisted campaign draft by ID."""
    settings = get_settings()
    store = build_configured_campaign_draft_store(settings)
    draft = store.get_draft(draft_id)
    if draft is None:
        typer.echo(f"Campaign draft not found: {draft_id}", err=True)
        raise typer.Exit(1)
    typer.echo(draft.model_dump_json(indent=2))


@app.command("list-campaign-drafts")
def list_campaign_drafts(
    advertiser_id: str | None = CAMPAIGN_DRAFT_ADVERTISER_ID_OPTION,
    limit: int = CAMPAIGN_DRAFT_LIST_LIMIT_OPTION,
) -> None:
    """List recent persisted campaign drafts."""
    settings = get_settings()
    store = build_configured_campaign_draft_store(settings)
    drafts = store.list_drafts(advertiser_id=advertiser_id, limit=limit)
    response = CampaignDraftListResponse(
        items=drafts,
        count=len(drafts),
        limit=limit,
        advertiser_id=advertiser_id,
    )
    typer.echo(response.model_dump_json(indent=2))


@app.command("get-advertiser-memory")
def get_advertiser_memory(
    advertiser_id: str = ADVERTISER_MEMORY_ADVERTISER_ID_ARGUMENT,
    source_id: str = ADVERTISER_MEMORY_SOURCE_ID_ARGUMENT,
) -> None:
    """Fetch one persisted advertiser memory by source ID."""
    settings = get_settings()
    store = build_configured_advertiser_memory_store(settings)
    memory = store.get_memory(advertiser_id=advertiser_id, source_id=source_id)
    if memory is None:
        typer.echo(
            f"Advertiser memory not found: advertiser_id={advertiser_id} source_id={source_id}",
            err=True,
        )
        raise typer.Exit(1)
    typer.echo(memory.model_dump_json(indent=2))


@app.command("list-advertiser-memories")
def list_advertiser_memories(
    advertiser_id: str = ADVERTISER_MEMORY_ADVERTISER_ID_ARGUMENT,
    memory_type: str | None = ADVERTISER_MEMORY_TYPE_OPTION,
    limit: int = ADVERTISER_MEMORY_LIST_LIMIT_OPTION,
) -> None:
    """List recent persisted advertiser memories."""
    settings = get_settings()
    store = build_configured_advertiser_memory_store(settings)
    validated_memory_type = _advertiser_memory_type_or_exit(memory_type)
    memories = store.list_memories(
        advertiser_id=advertiser_id,
        memory_type=validated_memory_type,
        limit=limit,
    )
    response = AdvertiserMemoryListResponse(
        items=memories,
        count=len(memories),
        limit=limit,
        advertiser_id=advertiser_id,
        memory_type=validated_memory_type,
    )
    typer.echo(response.model_dump_json(indent=2))


@app.command("process-strategy-jobs")
def process_strategy_jobs(
    limit: int = typer.Option(10, "--limit", min=1, max=100),
    worker_id: str | None = typer.Option(None, "--worker-id"),
    lock_seconds: int = typer.Option(1_800, "--lock-seconds", min=30, max=86_400),
) -> None:
    """Process a bounded batch of queued strategy-generation jobs."""
    settings = get_settings()
    report = process_configured_strategy_jobs(
        settings,
        limit=limit,
        worker_id=worker_id,
        lock_seconds=lock_seconds,
    )
    typer.echo(report.model_dump_json(indent=2))


@app.command("submit-strategy-job")
def submit_strategy_job(brief_file: Path = BRIEF_FILE_ARGUMENT) -> None:
    """Queue a strategy-generation job from a structured advertiser brief."""
    try:
        payload = json.loads(brief_file.read_text())
        request = _parse_strategy_request(payload)
        settings = get_settings()
        store = build_configured_strategy_job_store(settings)
        job = enqueue_strategy_job(request, settings=settings, job_store=store)
    except json.JSONDecodeError as exc:
        typer.echo(f"Invalid JSON: {exc}", err=True)
        raise typer.Exit(2) from exc
    except ValidationError as exc:
        typer.echo(_validation_errors_json(exc), err=True)
        raise typer.Exit(2) from exc

    typer.echo(job.model_dump_json(indent=2))


@app.command("submit-strategy-job-text")
def submit_strategy_job_text(
    text: str = BRIEF_TEXT_ARGUMENT,
    advertiser_id: str | None = BRIEF_TEXT_ADVERTISER_ID_OPTION,
    target_market: str = BRIEF_TEXT_TARGET_MARKET_OPTION,
    currency: str = BRIEF_TEXT_CURRENCY_OPTION,
    duration_days: int = BRIEF_TEXT_DURATION_OPTION,
) -> None:
    """Queue a strategy-generation job from a plain-language advertiser request."""
    try:
        settings = get_settings()
        intake = parse_advertiser_brief(
            AdvertiserBriefIntakeRequest(
                text=text,
                advertiser_id=advertiser_id,
                default_target_market=target_market,
                default_currency=currency,
                default_duration_days=duration_days,
            ),
            settings=settings,
        )
        store = build_configured_strategy_job_store(settings)
        job = enqueue_strategy_job(
            GrowthStrategyRequest(brief=intake.brief),
            settings=settings,
            job_store=store,
        )
    except ValidationError as exc:
        typer.echo(_validation_errors_json(exc), err=True)
        raise typer.Exit(2) from exc

    typer.echo(StrategyJobFromTextResponse(intake=intake, job=job).model_dump_json(indent=2))


@app.command("get-strategy-job")
def get_strategy_job(job_id: str = STRATEGY_JOB_ID_ARGUMENT) -> None:
    """Fetch one strategy-generation job by ID."""
    settings = get_settings()
    store = build_configured_strategy_job_store(settings)
    job = store.get_job(job_id)
    if job is None:
        typer.echo(f"Strategy job not found: {job_id}", err=True)
        raise typer.Exit(1)
    typer.echo(job.model_dump_json(indent=2))


@app.command("list-strategy-jobs")
def list_strategy_jobs(
    status: StrategyJobStatus | None = STRATEGY_JOB_STATUS_OPTION,
    advertiser_id: str | None = STRATEGY_JOB_ADVERTISER_ID_OPTION,
    run_id: str | None = STRATEGY_JOB_RUN_ID_OPTION,
    limit: int = STRATEGY_JOB_LIST_LIMIT_OPTION,
) -> None:
    """List recent strategy-generation jobs for queue inspection."""
    settings = get_settings()
    store = build_configured_strategy_job_store(settings)
    jobs = store.list_jobs(
        status=status,
        advertiser_id=advertiser_id,
        run_id=run_id,
        limit=limit,
    )
    response = StrategyJobListResponse(
        items=jobs,
        count=len(jobs),
        limit=limit,
        status=status,
        advertiser_id=advertiser_id,
        run_id=run_id,
    )
    typer.echo(response.model_dump_json(indent=2))


@app.command("retry-strategy-job")
def retry_strategy_job(
    job_id: str = STRATEGY_JOB_ID_ARGUMENT,
    requested_by: str = STRATEGY_JOB_REQUESTED_BY_OPTION,
) -> None:
    """Manually requeue a failed strategy-generation job."""
    settings = get_settings()
    store = build_configured_strategy_job_store(settings)
    job = store.get_job(job_id)
    if job is None:
        typer.echo(f"Strategy job not found: {job_id}", err=True)
        raise typer.Exit(1)
    if job.status != StrategyJobStatus.FAILED:
        typer.echo(
            f"Strategy job is not retryable: {job_id} status={job.status.value}",
            err=True,
        )
        raise typer.Exit(1)
    retried = store.retry_failed(
        job_id,
        max_attempts=settings.strategy_job_max_attempts,
        requested_by=requested_by.strip() or "cli",
    )
    if retried is None:
        typer.echo(f"Strategy job could not be retried: {job_id}", err=True)
        raise typer.Exit(1)
    typer.echo(retried.model_dump_json(indent=2))


@app.command("cancel-strategy-job")
def cancel_strategy_job(
    job_id: str = STRATEGY_JOB_ID_ARGUMENT,
    requested_by: str = STRATEGY_JOB_REQUESTED_BY_OPTION,
    reason: str | None = STRATEGY_JOB_CANCEL_REASON_OPTION,
) -> None:
    """Manually cancel a queued or running strategy-generation job."""
    settings = get_settings()
    store = build_configured_strategy_job_store(settings)
    job = store.get_job(job_id)
    if job is None:
        typer.echo(f"Strategy job not found: {job_id}", err=True)
        raise typer.Exit(1)
    if job.status not in {StrategyJobStatus.QUEUED, StrategyJobStatus.RUNNING}:
        typer.echo(
            f"Strategy job is not cancellable: {job_id} status={job.status.value}",
            err=True,
        )
        raise typer.Exit(1)
    cancelled = store.cancel(
        job_id,
        requested_by=requested_by.strip() or "cli",
        reason=reason.strip() if reason else None,
    )
    if cancelled is None:
        typer.echo(f"Strategy job could not be cancelled: {job_id}", err=True)
        raise typer.Exit(1)
    typer.echo(cancelled.model_dump_json(indent=2))


@app.command("eval")
def run_eval(eval_file: Path = EVAL_FILE_ARGUMENT) -> None:
    """Run deterministic local evaluators against curated advertiser briefs."""
    try:
        cases = load_eval_cases(eval_file)
        report = run_local_eval_suite(cases)
    except json.JSONDecodeError as exc:
        typer.echo(f"Invalid JSON: {exc}", err=True)
        raise typer.Exit(2) from exc
    except ValidationError as exc:
        typer.echo(_validation_errors_json(exc), err=True)
        raise typer.Exit(2) from exc

    typer.echo(report.model_dump_json(indent=2))


def _parse_strategy_request(payload: object) -> GrowthStrategyRequest:
    if isinstance(payload, dict) and "brief" in payload:
        return GrowthStrategyRequest.model_validate(payload)
    return GrowthStrategyRequest(brief=AdvertiserBrief.model_validate(payload))


def _safe_database_url(database_url: str) -> str:
    return make_url(database_url).render_as_string(hide_password=True)


def _validation_errors_json(exc: ValidationError) -> str:
    return json.dumps(
        exc.errors(include_url=False, include_context=False),
        indent=2,
    )


def _advertiser_memory_type_or_exit(value: str | None) -> AdvertiserMemoryType | None:
    if value is None:
        return None
    normalized = value.strip()
    if normalized in ALLOWED_ADVERTISER_MEMORY_TYPES:
        return cast(AdvertiserMemoryType, normalized)

    allowed = ", ".join(sorted(ALLOWED_ADVERTISER_MEMORY_TYPES))
    typer.echo(f"Invalid advertiser memory type: {value}. Expected one of: {allowed}", err=True)
    raise typer.Exit(2)


def _performance_event_type_or_exit(value: str | None) -> PerformanceEventType | None:
    if value is None:
        return None
    normalized = value.strip()
    if normalized in ALLOWED_PERFORMANCE_EVENT_TYPES:
        return PerformanceEventType(normalized)

    allowed = ", ".join(sorted(ALLOWED_PERFORMANCE_EVENT_TYPES))
    typer.echo(f"Invalid performance event type: {value}. Expected one of: {allowed}", err=True)
    raise typer.Exit(2)


def _feedback_review_decision_or_exit(
    value: str | None,
) -> FeedbackOptimizationReviewDecision | None:
    if value is None:
        return None
    normalized = value.strip()
    if normalized in ALLOWED_FEEDBACK_REVIEW_DECISIONS:
        return FeedbackOptimizationReviewDecision(normalized)

    allowed = ", ".join(sorted(ALLOWED_FEEDBACK_REVIEW_DECISIONS))
    typer.echo(f"Invalid feedback review decision: {value}. Expected one of: {allowed}", err=True)
    raise typer.Exit(2)


def _feedback_execution_dry_run_status_or_exit(
    value: str | None,
) -> FeedbackExecutionDryRunStatus | None:
    if value is None:
        return None
    normalized = value.strip()
    if normalized in ALLOWED_FEEDBACK_EXECUTION_DRY_RUN_STATUSES:
        return cast(FeedbackExecutionDryRunStatus, normalized)

    allowed = ", ".join(sorted(ALLOWED_FEEDBACK_EXECUTION_DRY_RUN_STATUSES))
    typer.echo(
        f"Invalid feedback execution dry-run status: {value}. Expected one of: {allowed}",
        err=True,
    )
    raise typer.Exit(2)


def _feedback_handoff_outcome_or_exit(value: str | None) -> FeedbackHandoffOutcome | None:
    if value is None:
        return None
    normalized = value.strip().lower()
    try:
        return FeedbackHandoffOutcome(normalized)
    except ValueError as exc:
        allowed = ", ".join(sorted(ALLOWED_FEEDBACK_HANDOFF_OUTCOMES))
        typer.echo(
            f"Invalid feedback handoff outcome: {value}. Expected one of: {allowed}",
            err=True,
        )
        raise typer.Exit(2) from exc


def _feedback_review_lineage_stage_or_exit(value: str | None) -> str | None:
    if value is None:
        return None
    normalized = value.strip()
    if normalized in ALLOWED_FEEDBACK_REVIEW_LINEAGE_STAGES:
        return normalized

    allowed = ", ".join(sorted(ALLOWED_FEEDBACK_REVIEW_LINEAGE_STAGES))
    typer.echo(
        f"Invalid feedback review lineage stage: {value}. Expected one of: {allowed}",
        err=True,
    )
    raise typer.Exit(2)


def _ensure_feedback_review_persistence_enabled(settings) -> None:
    if settings.feedback_review_persistence_backend != "none":
        return
    typer.echo("Feedback optimization review persistence is disabled.", err=True)
    raise typer.Exit(2)


def _ensure_feedback_execution_persistence_enabled(settings) -> None:
    if settings.feedback_execution_persistence_backend != "none":
        return
    typer.echo("Feedback execution dry-run persistence is disabled.", err=True)
    raise typer.Exit(2)


def _demo_performance_event(
    *,
    advertiser_id: str,
    run_id: str,
    objective,
    draft_id: str,
    strategy_context,
) -> CampaignPerformanceEventRequest:
    return CampaignPerformanceEventRequest.model_validate(
        {
            "event_id": f"evt_demo_{run_id}",
            "advertiser_id": advertiser_id,
            "run_id": run_id,
            "draft_id": draft_id,
            "objective": objective,
            "event_type": "performance_snapshot",
            "occurred_at": "2026-05-12T12:00:00Z",
            "metrics": {
                "impressions": 10_000,
                "clicks": 500,
                "spend": "1000.00",
                "conversions": 20,
            },
            "strategy_context": strategy_context,
        }
    )


@app.callback(invoke_without_command=True)
def main(version: bool = typer.Option(False, "--version", help="Show version and exit.")) -> None:
    configure_logging()
    if version:
        typer.echo(__version__)
        raise typer.Exit()
