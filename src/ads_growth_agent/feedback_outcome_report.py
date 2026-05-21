from decimal import Decimal, InvalidOperation
from typing import Protocol

from ads_growth_agent.contracts import (
    CampaignFeedbackOutcomeReportResponse,
    CampaignPerformanceEventDetailResponse,
    FeedbackOutcomeDeltaDirection,
    FeedbackOutcomeMetricDelta,
    FeedbackOutcomeMetricDirection,
    FeedbackOutcomeStatus,
    PerformanceEventType,
)

PERCENT_QUANT = Decimal("0.0001")


class FeedbackOutcomePerformanceEventStore(Protocol):
    def list_events(
        self,
        *,
        advertiser_id: str | None = None,
        run_id: str | None = None,
        campaign_id: str | None = None,
        draft_id: str | None = None,
        event_type: PerformanceEventType | None = None,
        limit: int = 50,
    ) -> list[CampaignPerformanceEventDetailResponse]:
        """Return persisted performance events for outcome comparison."""


def build_campaign_feedback_outcome_report(
    event: CampaignPerformanceEventDetailResponse,
    event_store: FeedbackOutcomePerformanceEventStore,
    *,
    limit: int = 50,
) -> CampaignFeedbackOutcomeReportResponse:
    """Compare one feedback event against the next later performance snapshot."""

    effective_limit = min(max(limit, 1), 100)
    followup_events = _followup_events(event, event_store, limit=effective_limit)
    followup_event = followup_events[0] if followup_events else None
    metric_deltas = _metric_deltas(event, followup_event) if followup_event else []
    improved_count = sum(1 for delta in metric_deltas if delta.delta_direction == "improved")
    regressed_count = sum(1 for delta in metric_deltas if delta.delta_direction == "regressed")
    outcome_status = _outcome_status(
        followup_event=followup_event,
        metric_deltas=metric_deltas,
        improved_count=improved_count,
        regressed_count=regressed_count,
    )

    return CampaignFeedbackOutcomeReportResponse(
        event_id=event.event_id,
        advertiser_id=event.advertiser_id,
        run_id=event.run_id,
        campaign_id=event.campaign_id,
        draft_id=event.draft_id,
        outcome_status=outcome_status,
        baseline_event_id=event.event_id,
        followup_event_id=followup_event.event_id if followup_event else None,
        comparison_event_count=len(followup_events),
        improved_metric_count=improved_count,
        regressed_metric_count=regressed_count,
        metric_deltas=metric_deltas,
        recommendation=_recommendation(outcome_status),
        summary=_summary_text(
            event=event,
            followup_event=followup_event,
            outcome_status=outcome_status,
            improved_count=improved_count,
            regressed_count=regressed_count,
        ),
        baseline_event=event,
        followup_event=followup_event,
        guardrails=[
            "Outcome report is read-only and does not execute campaign changes.",
            "Treat one follow-up snapshot as directional; confirm with additional data.",
            "v0.1 uses deterministic metric rules rather than causal lift modeling.",
        ],
    )


def _followup_events(
    event: CampaignPerformanceEventDetailResponse,
    event_store: FeedbackOutcomePerformanceEventStore,
    *,
    limit: int,
) -> list[CampaignPerformanceEventDetailResponse]:
    candidates = event_store.list_events(
        advertiser_id=event.advertiser_id,
        run_id=event.run_id,
        campaign_id=event.campaign_id,
        draft_id=event.draft_id,
        event_type=PerformanceEventType.PERFORMANCE_SNAPSHOT,
        limit=limit,
    )
    followups = [
        candidate
        for candidate in candidates
        if candidate.event_id != event.event_id and candidate.occurred_at > event.occurred_at
    ]
    return sorted(followups, key=lambda item: (item.occurred_at, item.event_id))


def _metric_deltas(
    baseline: CampaignPerformanceEventDetailResponse,
    followup: CampaignPerformanceEventDetailResponse,
) -> list[FeedbackOutcomeMetricDelta]:
    return [
        _metric_delta(
            baseline,
            followup,
            metric_name="conversions",
            display_name="Conversions",
            desired_direction="higher_is_better",
        ),
        _metric_delta(
            baseline,
            followup,
            metric_name="cpa",
            display_name="CPA",
            desired_direction="lower_is_better",
        ),
        _metric_delta(
            baseline,
            followup,
            metric_name="cvr",
            display_name="CVR",
            desired_direction="higher_is_better",
        ),
        _metric_delta(
            baseline,
            followup,
            metric_name="ctr",
            display_name="CTR",
            desired_direction="higher_is_better",
        ),
        _metric_delta(
            baseline,
            followup,
            metric_name="roas",
            display_name="ROAS",
            desired_direction="higher_is_better",
        ),
        _metric_delta(
            baseline,
            followup,
            metric_name="spend",
            display_name="Spend",
            desired_direction="neutral",
        ),
    ]


def _metric_delta(
    baseline: CampaignPerformanceEventDetailResponse,
    followup: CampaignPerformanceEventDetailResponse,
    *,
    metric_name: str,
    display_name: str,
    desired_direction: FeedbackOutcomeMetricDirection,
) -> FeedbackOutcomeMetricDelta:
    baseline_value = _metric_value(baseline, metric_name)
    followup_value = _metric_value(followup, metric_name)
    absolute_delta = (
        followup_value - baseline_value
        if baseline_value is not None and followup_value is not None
        else None
    )
    percent_change = _percent_change(baseline_value, absolute_delta)
    delta_direction = _delta_direction(
        baseline_value=baseline_value,
        followup_value=followup_value,
        desired_direction=desired_direction,
    )
    return FeedbackOutcomeMetricDelta(
        metric_name=metric_name,
        display_name=display_name,
        baseline_value=baseline_value,
        followup_value=followup_value,
        absolute_delta=absolute_delta,
        percent_change=percent_change,
        desired_direction=desired_direction,
        delta_direction=delta_direction,
        summary=_delta_summary(
            display_name=display_name,
            baseline_value=baseline_value,
            followup_value=followup_value,
            delta_direction=delta_direction,
        ),
    )


def _metric_value(
    event: CampaignPerformanceEventDetailResponse,
    metric_name: str,
) -> Decimal | None:
    raw_value = event.analysis.metrics_summary.get(metric_name)
    if raw_value is None:
        return None
    try:
        return Decimal(str(raw_value))
    except (InvalidOperation, ValueError):
        return None


def _percent_change(
    baseline_value: Decimal | None,
    absolute_delta: Decimal | None,
) -> Decimal | None:
    if baseline_value is None or absolute_delta is None or baseline_value == 0:
        return None
    return ((absolute_delta / baseline_value) * Decimal("100")).quantize(PERCENT_QUANT)


def _delta_direction(
    *,
    baseline_value: Decimal | None,
    followup_value: Decimal | None,
    desired_direction: FeedbackOutcomeMetricDirection,
) -> FeedbackOutcomeDeltaDirection:
    if baseline_value is None or followup_value is None:
        return "not_available"
    if desired_direction == "neutral":
        return "informational"
    if followup_value == baseline_value:
        return "unchanged"
    if desired_direction == "higher_is_better":
        return "improved" if followup_value > baseline_value else "regressed"
    return "improved" if followup_value < baseline_value else "regressed"


def _outcome_status(
    *,
    followup_event: CampaignPerformanceEventDetailResponse | None,
    metric_deltas: list[FeedbackOutcomeMetricDelta],
    improved_count: int,
    regressed_count: int,
) -> FeedbackOutcomeStatus:
    if followup_event is None:
        return "no_followup_event"
    comparable_count = sum(
        1
        for delta in metric_deltas
        if delta.delta_direction in {"improved", "regressed", "unchanged"}
    )
    if comparable_count == 0:
        return "insufficient_data"
    if improved_count > regressed_count:
        return "improved"
    if regressed_count > improved_count:
        return "regressed"
    if improved_count == 0 and regressed_count == 0:
        return "insufficient_data"
    return "mixed"


def _recommendation(outcome_status: FeedbackOutcomeStatus) -> str:
    match outcome_status:
        case "no_followup_event":
            return "Ingest the next performance snapshot after manual handoff."
        case "insufficient_data":
            return "Collect more conversion data before changing the optimization plan."
        case "improved":
            return (
                "Continue monitoring and consider a cautious budget increase if "
                "guardrails remain satisfied."
            )
        case "regressed":
            return "Open a new feedback loop and review budget, audience, and creative changes."
        case "mixed":
            return "Inspect metric-level tradeoffs before deciding whether to revise the plan."


def _summary_text(
    *,
    event: CampaignPerformanceEventDetailResponse,
    followup_event: CampaignPerformanceEventDetailResponse | None,
    outcome_status: FeedbackOutcomeStatus,
    improved_count: int,
    regressed_count: int,
) -> str:
    if followup_event is None:
        return (
            f"Feedback outcome report for event {event.event_id}: "
            "no later performance snapshot was found."
        )
    return (
        f"Feedback outcome report for event {event.event_id} compared with "
        f"{followup_event.event_id}: status={outcome_status}, "
        f"improved_metrics={improved_count}, regressed_metrics={regressed_count}."
    )


def _delta_summary(
    *,
    display_name: str,
    baseline_value: Decimal | None,
    followup_value: Decimal | None,
    delta_direction: FeedbackOutcomeDeltaDirection,
) -> str:
    if delta_direction == "not_available":
        return f"{display_name} could not be compared from the available snapshots."
    return (
        f"{display_name} moved from {baseline_value} to {followup_value}; "
        f"direction={delta_direction}."
    )
