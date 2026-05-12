from datetime import UTC, datetime
from decimal import Decimal
from uuid import NAMESPACE_URL, uuid5

from ads_growth_agent.contracts import (
    CampaignFeedbackAnalysis,
    CampaignPerformanceEventRequest,
    FeedbackActionType,
    FeedbackHealthStatus,
    FeedbackRecommendation,
    RiskLevel,
)

CENTS = Decimal("0.01")
LOW_CTR_THRESHOLD = Decimal("0.0100")
HIGH_CPA_MULTIPLIER = Decimal("1.25")


def analyze_campaign_performance_event(
    event: CampaignPerformanceEventRequest,
) -> CampaignFeedbackAnalysis:
    summary = _metrics_summary(event)
    health_status = _health_status(event, summary)
    recommendations = _recommendations(event, summary, health_status)
    feedback_id = _feedback_id(event)
    return CampaignFeedbackAnalysis(
        feedback_id=feedback_id,
        event_id=event.event_id,
        advertiser_id=event.advertiser_id,
        run_id=event.run_id,
        health_status=health_status,
        metrics_summary=summary,
        recommendations=[
            recommendation.model_copy(
                update={
                    "recommendation_id": f"{feedback_id}:{recommendation.recommendation_id}"
                }
            )
            for recommendation in recommendations
        ],
        guardrails=[
            "Recommendations are draft-only and do not mutate live campaign spend.",
            "Budget or targeting changes require human approval before execution.",
        ],
        created_at=datetime.now(UTC),
    )


def _metrics_summary(event: CampaignPerformanceEventRequest) -> dict[str, str | int | None]:
    metrics = event.metrics
    ctr = _ratio(metrics.clicks, metrics.impressions)
    cvr = _ratio(metrics.conversions, metrics.clicks)
    cpa = (
        (metrics.spend / Decimal(metrics.conversions)).quantize(CENTS)
        if metrics.conversions
        else None
    )
    roas = (
        (metrics.revenue / metrics.spend).quantize(Decimal("0.0001"))
        if metrics.revenue is not None and metrics.spend > 0
        else None
    )
    return {
        "impressions": metrics.impressions,
        "clicks": metrics.clicks,
        "conversions": metrics.conversions,
        "spend": str(metrics.spend),
        "ctr": str(ctr),
        "cvr": str(cvr),
        "cpa": str(cpa) if cpa is not None else None,
        "target_cpa": str(event.target_cpa) if event.target_cpa is not None else None,
        "roas": str(roas) if roas is not None else None,
        "attribution_window_days": event.attribution_window_days,
    }


def _health_status(
    event: CampaignPerformanceEventRequest,
    summary: dict[str, str | int | None],
) -> FeedbackHealthStatus:
    metrics = event.metrics
    if metrics.impressions < 100 or metrics.spend == 0:
        return FeedbackHealthStatus.INSUFFICIENT_DATA
    if metrics.conversions == 0:
        return FeedbackHealthStatus.NEEDS_ATTENTION

    ctr = Decimal(str(summary["ctr"]))
    if ctr < LOW_CTR_THRESHOLD:
        return FeedbackHealthStatus.CREATIVE_FATIGUE

    if event.target_cpa is not None and summary["cpa"] is not None:
        cpa = Decimal(str(summary["cpa"]))
        if cpa > (event.target_cpa * HIGH_CPA_MULTIPLIER):
            return FeedbackHealthStatus.UNDERPERFORMING

    return FeedbackHealthStatus.ON_TRACK


def _recommendations(
    event: CampaignPerformanceEventRequest,
    summary: dict[str, str | int | None],
    health_status: FeedbackHealthStatus,
) -> list[FeedbackRecommendation]:
    match health_status:
        case FeedbackHealthStatus.INSUFFICIENT_DATA:
            return [
                FeedbackRecommendation(
                    recommendation_id="continue_monitoring",
                    action_type=FeedbackActionType.CONTINUE_MONITORING,
                    title="Continue monitoring before changing the draft plan",
                    rationale=(
                        "The event does not contain enough delivery or spend to separate "
                        "signal from noise."
                    ),
                    priority=3,
                    risk_level=RiskLevel.LOW,
                    requires_human_approval=False,
                    params={"minimum_impressions": 100},
                )
            ]
        case FeedbackHealthStatus.NEEDS_ATTENTION:
            return [
                FeedbackRecommendation(
                    recommendation_id="inspect_tracking",
                    action_type=FeedbackActionType.INSPECT_TRACKING,
                    title="Inspect conversion tracking and landing-page handoff",
                    rationale=(
                        "The campaign has spend and clicks but no attributed conversions, "
                        "which can indicate tracking or funnel issues."
                    ),
                    priority=1,
                    risk_level=RiskLevel.MEDIUM,
                    params={
                        "spend": summary["spend"],
                        "clicks": summary["clicks"],
                    },
                ),
                FeedbackRecommendation(
                    recommendation_id="narrow_audience",
                    action_type=FeedbackActionType.NARROW_AUDIENCE,
                    title="Narrow audience toward higher-intent segments",
                    rationale=(
                        "Budget should stay in draft mode until the system can explain "
                        "why traffic is not converting."
                    ),
                    priority=2,
                    risk_level=RiskLevel.MEDIUM,
                    params={"objective": event.objective.value},
                ),
            ]
        case FeedbackHealthStatus.CREATIVE_FATIGUE:
            return [
                FeedbackRecommendation(
                    recommendation_id="refresh_creative",
                    action_type=FeedbackActionType.REFRESH_CREATIVE,
                    title="Refresh creative hooks before scaling spend",
                    rationale=(
                        "CTR is below the draft threshold, suggesting the current creative "
                        "angle is not earning enough attention."
                    ),
                    priority=1,
                    risk_level=RiskLevel.LOW,
                    params={"ctr": summary["ctr"], "threshold": str(LOW_CTR_THRESHOLD)},
                )
            ]
        case FeedbackHealthStatus.UNDERPERFORMING:
            return [
                FeedbackRecommendation(
                    recommendation_id="adjust_budget",
                    action_type=FeedbackActionType.ADJUST_BUDGET,
                    title="Shift budget toward the best converting lane",
                    rationale=(
                        "Observed CPA is materially above target, so the next draft should "
                        "reduce broad exploration and protect retargeting or proven segments."
                    ),
                    priority=1,
                    risk_level=RiskLevel.MEDIUM,
                    params={
                        "observed_cpa": summary["cpa"],
                        "target_cpa": summary["target_cpa"],
                    },
                ),
                FeedbackRecommendation(
                    recommendation_id="refresh_creative",
                    action_type=FeedbackActionType.REFRESH_CREATIVE,
                    title="Create a conversion-focused creative variant",
                    rationale=(
                        "A new creative brief can test clearer value proof before any live "
                        "budget increase is approved."
                    ),
                    priority=2,
                    risk_level=RiskLevel.LOW,
                    params={"objective": event.objective.value},
                ),
            ]
        case FeedbackHealthStatus.ON_TRACK:
            return [
                FeedbackRecommendation(
                    recommendation_id="continue_monitoring",
                    action_type=FeedbackActionType.CONTINUE_MONITORING,
                    title="Keep the current draft optimization path",
                    rationale=(
                        "Delivery, conversion rate, and CPA are within the configured "
                        "feedback thresholds."
                    ),
                    priority=4,
                    risk_level=RiskLevel.LOW,
                    requires_human_approval=False,
                    params={"cpa": summary["cpa"], "target_cpa": summary["target_cpa"]},
                )
            ]


def _ratio(numerator: int, denominator: int) -> Decimal:
    if denominator == 0:
        return Decimal("0.0000")
    return (Decimal(numerator) / Decimal(denominator)).quantize(Decimal("0.0001"))


def _feedback_id(event: CampaignPerformanceEventRequest) -> str:
    return f"feedback_{uuid5(NAMESPACE_URL, event.event_id).hex[:16]}"
