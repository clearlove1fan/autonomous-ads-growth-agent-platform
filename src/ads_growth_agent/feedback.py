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
    StrategyRuleMatch,
)

CENTS = Decimal("0.01")
LOW_CTR_THRESHOLD = Decimal("0.0100")
HIGH_CPA_MULTIPLIER = Decimal("1.25")


def analyze_campaign_performance_event(
    event: CampaignPerformanceEventRequest,
) -> CampaignFeedbackAnalysis:
    summary = _metrics_summary(event)
    health_status = _health_status(event, summary)
    matched_rules = _matched_strategy_rules(event, health_status)
    recommendations = _attach_strategy_context(
        _recommendations(event, summary, health_status),
        event,
        matched_rules,
    )
    feedback_id = _feedback_id(event)
    return CampaignFeedbackAnalysis(
        feedback_id=feedback_id,
        event_id=event.event_id,
        advertiser_id=event.advertiser_id,
        run_id=event.run_id,
        strategy_id=(
            event.strategy_context.strategy_id if event.strategy_context is not None else None
        ),
        draft_id=event.draft_id
        or (event.strategy_context.draft_id if event.strategy_context is not None else None),
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
        matched_strategy_rules=matched_rules,
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
        "target_cpa": str(_effective_target_cpa(event))
        if _effective_target_cpa(event) is not None
        else None,
        "forecasted_cpa": _forecasted_cpa(event),
        "forecasted_conversions": _forecasted_conversions(event),
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

    target_cpa = _effective_target_cpa(event)
    if target_cpa is not None and summary["cpa"] is not None:
        cpa = Decimal(str(summary["cpa"]))
        if cpa > (target_cpa * HIGH_CPA_MULTIPLIER):
            return FeedbackHealthStatus.UNDERPERFORMING

    return FeedbackHealthStatus.ON_TRACK


def _effective_target_cpa(event: CampaignPerformanceEventRequest) -> Decimal | None:
    if event.target_cpa is not None:
        return event.target_cpa
    if event.strategy_context is None:
        return None
    if event.strategy_context.target_cpa is not None:
        return event.strategy_context.target_cpa
    if event.strategy_context.performance_forecast is not None:
        return event.strategy_context.performance_forecast.estimated_cpa
    return None


def _forecasted_cpa(event: CampaignPerformanceEventRequest) -> str | None:
    if event.strategy_context is None or event.strategy_context.performance_forecast is None:
        return None
    return str(event.strategy_context.performance_forecast.estimated_cpa)


def _forecasted_conversions(event: CampaignPerformanceEventRequest) -> int | None:
    if event.strategy_context is None or event.strategy_context.performance_forecast is None:
        return None
    return event.strategy_context.performance_forecast.estimated_conversions


def _matched_strategy_rules(
    event: CampaignPerformanceEventRequest,
    health_status: FeedbackHealthStatus,
) -> list[StrategyRuleMatch]:
    if event.strategy_context is None:
        return []

    trigger_candidates = _trigger_candidates_for_status(health_status)
    matches: list[StrategyRuleMatch] = []
    for rule in event.strategy_context.optimization_rules:
        normalized_trigger = rule.trigger_metric.lower()
        if not _trigger_matches(normalized_trigger, trigger_candidates):
            continue
        matches.append(
            StrategyRuleMatch(
                rule_id=rule.rule_id,
                trigger_metric=rule.trigger_metric,
                recommended_action=rule.recommended_action,
                owner_role=rule.owner_role,
                priority=rule.priority,
                match_reason=(
                    f"Matched {health_status.value} feedback to strategy trigger "
                    f"{rule.trigger_metric}."
                ),
            )
        )
    return sorted(matches, key=lambda match: match.priority)


def _trigger_candidates_for_status(health_status: FeedbackHealthStatus) -> set[str]:
    match health_status:
        case FeedbackHealthStatus.UNDERPERFORMING:
            return {"cost_per_result", "cpa", "target_cpa", "budget_pacing"}
        case FeedbackHealthStatus.CREATIVE_FATIGUE:
            return {"creative_cell_conversions", "creative", "ctr"}
        case FeedbackHealthStatus.NEEDS_ATTENTION:
            return {"tracking", "primary_conversion", "conversion", "cost_per_result"}
        case FeedbackHealthStatus.INSUFFICIENT_DATA:
            return {"budget_pacing", "delivery", "impressions"}
        case FeedbackHealthStatus.ON_TRACK:
            return set()


def _trigger_matches(trigger: str, candidates: set[str]) -> bool:
    return any(candidate in trigger or trigger in candidate for candidate in candidates)


def _attach_strategy_context(
    recommendations: list[FeedbackRecommendation],
    event: CampaignPerformanceEventRequest,
    matched_rules: list[StrategyRuleMatch],
) -> list[FeedbackRecommendation]:
    if event.strategy_context is None:
        return recommendations

    matched_rule_ids = [match.rule_id for match in matched_rules]
    updated: list[FeedbackRecommendation] = []
    for recommendation in recommendations:
        params = dict(recommendation.params)
        params["strategy_id"] = event.strategy_context.strategy_id
        if event.draft_id or event.strategy_context.draft_id:
            params["draft_id"] = event.draft_id or event.strategy_context.draft_id
        if matched_rule_ids:
            params["matched_strategy_rule_ids"] = matched_rule_ids
        updated.append(recommendation.model_copy(update={"params": params}))
    return updated


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
