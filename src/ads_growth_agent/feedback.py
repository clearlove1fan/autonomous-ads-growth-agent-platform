from datetime import UTC, datetime
from decimal import Decimal
from uuid import NAMESPACE_URL, uuid4, uuid5

from ads_growth_agent.contracts import (
    AgentRole,
    CampaignFeedbackActionPlanResponse,
    CampaignFeedbackAnalysis,
    CampaignFeedbackOptimizationDraftResponse,
    CampaignFeedbackOptimizationReviewRequest,
    CampaignFeedbackOptimizationReviewResponse,
    CampaignFeedbackOptimizationRevisionDraftResponse,
    CampaignPerformanceEventDetailResponse,
    CampaignPerformanceEventRequest,
    FeedbackActionPlanStep,
    FeedbackActionType,
    FeedbackHealthStatus,
    FeedbackOptimizationDraftChange,
    FeedbackOptimizationReviewDecision,
    FeedbackRecommendation,
    RiskLevel,
    StrategyRuleMatch,
)

CENTS = Decimal("0.01")
LOW_CTR_THRESHOLD = Decimal("0.0100")
HIGH_CPA_MULTIPLIER = Decimal("1.25")


class FeedbackRevisionDraftNotRequestedError(Exception):
    def __init__(
        self,
        review_id: str,
        decision: FeedbackOptimizationReviewDecision,
    ) -> None:
        super().__init__(
            "Feedback optimization review must request revision before building a revision "
            f"draft: review_id={review_id} decision={decision.value}"
        )
        self.review_id = review_id
        self.decision = decision


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


def build_campaign_feedback_action_plan(
    event: CampaignPerformanceEventDetailResponse,
) -> CampaignFeedbackActionPlanResponse:
    """Build a ranked, draft-only action plan from persisted feedback analysis."""

    analysis = event.analysis
    steps = [
        _action_plan_step(event, analysis, recommendation)
        for recommendation in sorted(
            analysis.recommendations,
            key=lambda item: (item.priority, item.action_type.value, item.recommendation_id),
        )
    ]
    return CampaignFeedbackActionPlanResponse(
        event_id=event.event_id,
        feedback_id=analysis.feedback_id,
        advertiser_id=event.advertiser_id,
        run_id=event.run_id,
        campaign_id=event.campaign_id,
        draft_id=analysis.draft_id or event.draft_id,
        strategy_id=analysis.strategy_id,
        health_status=analysis.health_status,
        summary=_action_plan_summary(analysis),
        steps=steps,
        guardrails=[
            *analysis.guardrails,
            "Action plan steps are recommendations only and do not execute live changes.",
        ],
        created_at=analysis.created_at,
    )


def build_campaign_feedback_optimization_draft(
    event: CampaignPerformanceEventDetailResponse,
) -> CampaignFeedbackOptimizationDraftResponse:
    """Build a concrete draft-only optimization proposal from feedback."""

    action_plan = build_campaign_feedback_action_plan(event)
    changes = [_optimization_change(step) for step in action_plan.steps]
    return CampaignFeedbackOptimizationDraftResponse(
        optimization_draft_id=_optimization_draft_id(event.event_id),
        event_id=event.event_id,
        feedback_id=action_plan.feedback_id,
        advertiser_id=event.advertiser_id,
        run_id=event.run_id,
        campaign_id=event.campaign_id,
        base_draft_id=action_plan.draft_id,
        strategy_id=action_plan.strategy_id,
        status="draft",
        health_status=action_plan.health_status,
        summary=_optimization_draft_summary(action_plan, changes),
        changes=changes,
        requires_human_approval=any(change.requires_human_approval for change in changes),
        guardrails=[
            *action_plan.guardrails,
            "Optimization draft is review-only and does not mutate live campaign state.",
        ],
        created_at=action_plan.created_at,
    )


def build_campaign_feedback_optimization_review(
    optimization_draft: CampaignFeedbackOptimizationDraftResponse,
    request: CampaignFeedbackOptimizationReviewRequest,
    *,
    review_id: str | None = None,
    created_at: datetime | None = None,
) -> CampaignFeedbackOptimizationReviewResponse:
    """Build an auditable human review decision for an optimization draft."""

    selected_change_ids = _selected_optimization_change_ids(
        optimization_draft,
        requested_change_ids=request.selected_change_ids,
    )
    return CampaignFeedbackOptimizationReviewResponse(
        review_id=review_id or f"feedback_review_{uuid4().hex[:16]}",
        optimization_draft_id=optimization_draft.optimization_draft_id,
        event_id=optimization_draft.event_id,
        feedback_id=optimization_draft.feedback_id,
        advertiser_id=optimization_draft.advertiser_id,
        run_id=optimization_draft.run_id,
        campaign_id=optimization_draft.campaign_id,
        base_draft_id=optimization_draft.base_draft_id,
        strategy_id=optimization_draft.strategy_id,
        decision=request.decision,
        reviewer_id=request.reviewer_id,
        notes=request.notes,
        selected_change_ids=selected_change_ids,
        optimization_draft=optimization_draft,
        created_at=created_at or datetime.now(UTC),
    )


def build_campaign_feedback_optimization_revision_draft(
    review: CampaignFeedbackOptimizationReviewResponse,
) -> CampaignFeedbackOptimizationRevisionDraftResponse:
    """Build a revised optimization draft from a needs-revision review."""

    if review.decision != FeedbackOptimizationReviewDecision.NEEDS_REVISION:
        raise FeedbackRevisionDraftNotRequestedError(review.review_id, review.decision)

    selected_change_ids = set(review.selected_change_ids)
    revised_changes = [
        _revision_change(review, change)
        for change in review.optimization_draft.changes
        if change.change_id in selected_change_ids
    ]
    if not revised_changes:
        raise ValueError("needs-revision review does not include any selected changes")

    return CampaignFeedbackOptimizationRevisionDraftResponse(
        revision_draft_id=_revision_draft_id(review.review_id),
        source_review_id=review.review_id,
        original_optimization_draft_id=review.optimization_draft_id,
        event_id=review.event_id,
        feedback_id=review.feedback_id,
        advertiser_id=review.advertiser_id,
        run_id=review.run_id,
        campaign_id=review.campaign_id,
        base_draft_id=review.base_draft_id,
        strategy_id=review.strategy_id,
        status="draft",
        reviewer_id=review.reviewer_id,
        reviewer_notes=review.notes,
        summary=_revision_draft_summary(review, revised_changes),
        changes=revised_changes,
        requires_human_approval=True,
        guardrails=[
            *review.optimization_draft.guardrails,
            "Revision draft is review-only and must be approved before execution planning.",
            "Revision draft generation does not mutate live campaign state.",
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


def _action_plan_step(
    event: CampaignPerformanceEventDetailResponse,
    analysis: CampaignFeedbackAnalysis,
    recommendation: FeedbackRecommendation,
) -> FeedbackActionPlanStep:
    owner_role = _owner_role_for_action(recommendation.action_type)
    matched_rule_ids = _matched_rule_ids(recommendation)
    matched_action = _matched_rule_action(
        analysis,
        matched_rule_ids=matched_rule_ids,
        owner_role=owner_role,
    )
    params = {
        **recommendation.params,
        "event_id": event.event_id,
        "feedback_id": analysis.feedback_id,
        "health_status": analysis.health_status.value,
    }
    if event.campaign_id is not None:
        params["campaign_id"] = event.campaign_id
    if analysis.draft_id or event.draft_id:
        params["draft_id"] = analysis.draft_id or event.draft_id

    requires_approval = recommendation.requires_human_approval
    return FeedbackActionPlanStep(
        step_id=recommendation.recommendation_id,
        action_type=recommendation.action_type,
        title=recommendation.title,
        rationale=recommendation.rationale,
        recommended_action=matched_action or recommendation.title,
        priority=recommendation.priority,
        owner_role=owner_role,
        risk_level=recommendation.risk_level,
        requires_human_approval=requires_approval,
        status="draft_recommendation" if requires_approval else "monitor_only",
        tool_name=_tool_name_for_action(recommendation.action_type),
        params=params,
        matched_strategy_rule_ids=matched_rule_ids,
    )


def _optimization_change(step: FeedbackActionPlanStep) -> FeedbackOptimizationDraftChange:
    change_type = _change_type_for_action(step.action_type)
    params = {
        **step.params,
        "source_action_type": step.action_type.value,
        "source_step_status": step.status,
    }
    params.update(_draft_params_for_action(step))
    return FeedbackOptimizationDraftChange(
        change_id=f"{step.step_id}:optimization_draft",
        source_step_id=step.step_id,
        change_type=change_type,
        title=_draft_title_for_change(step, change_type=change_type),
        description=_draft_description_for_change(step, change_type=change_type),
        owner_role=step.owner_role,
        risk_level=step.risk_level,
        status="draft_change" if step.requires_human_approval else "monitor_only",
        requires_human_approval=step.requires_human_approval,
        params=params,
    )


def _selected_optimization_change_ids(
    optimization_draft: CampaignFeedbackOptimizationDraftResponse,
    *,
    requested_change_ids: list[str],
) -> list[str]:
    available_change_ids = {change.change_id for change in optimization_draft.changes}
    if not requested_change_ids:
        return [change.change_id for change in optimization_draft.changes]

    unknown_change_ids = sorted(set(requested_change_ids) - available_change_ids)
    if unknown_change_ids:
        unknown = ", ".join(unknown_change_ids)
        raise ValueError(f"selected_change_ids include unknown change IDs: {unknown}")

    return requested_change_ids


def _revision_change(
    review: CampaignFeedbackOptimizationReviewResponse,
    change: FeedbackOptimizationDraftChange,
) -> FeedbackOptimizationDraftChange:
    revision_note = review.notes or "Reviewer requested revision before approval."
    return FeedbackOptimizationDraftChange(
        change_id=_revision_change_id(review.review_id, change.change_id),
        source_step_id=change.source_step_id,
        change_type=change.change_type,
        title=_bounded_text(f"Revised {change.title}", max_length=160),
        description=_bounded_text(
            f"{change.description} Reviewer revision request: {revision_note}",
            max_length=1_000,
        ),
        owner_role=change.owner_role,
        risk_level=change.risk_level,
        status="draft_change",
        requires_human_approval=True,
        params={
            **change.params,
            "revision_source_review_id": review.review_id,
            "original_change_id": change.change_id,
            "reviewer_id": review.reviewer_id,
            "reviewer_notes": review.notes,
            "revision_status": "needs_review",
        },
    )


def _revision_draft_summary(
    review: CampaignFeedbackOptimizationReviewResponse,
    changes: list[FeedbackOptimizationDraftChange],
) -> str:
    note = f" Reviewer notes: {review.notes}" if review.notes else ""
    return _bounded_text(
        (
            f"Revision draft for review {review.review_id}. Prepared {len(changes)} "
            f"revised draft change(s) for another approval pass.{note}"
        ),
        max_length=1_000,
    )


def _bounded_text(value: str, *, max_length: int) -> str:
    if len(value) <= max_length:
        return value
    return value[: max_length - 3].rstrip() + "..."


def _change_type_for_action(action_type: FeedbackActionType):
    match action_type:
        case FeedbackActionType.ADJUST_BUDGET:
            return "budget"
        case FeedbackActionType.REFRESH_CREATIVE:
            return "creative"
        case FeedbackActionType.NARROW_AUDIENCE:
            return "audience"
        case FeedbackActionType.INSPECT_TRACKING | FeedbackActionType.CONTINUE_MONITORING:
            return "measurement"


def _draft_title_for_change(
    step: FeedbackActionPlanStep,
    *,
    change_type: str,
) -> str:
    match change_type:
        case "budget":
            return "Draft budget reallocation"
        case "creative":
            return "Draft creative refresh"
        case "audience":
            return "Draft audience refinement"
        case "measurement":
            return "Draft measurement follow-up"
        case _:
            return step.title


def _draft_description_for_change(
    step: FeedbackActionPlanStep,
    *,
    change_type: str,
) -> str:
    base = step.recommended_action
    match change_type:
        case "budget":
            return (
                f"{base} Keep the change in draft mode until a reviewer approves budget "
                "movement."
            )
        case "creative":
            return (
                f"{base} Prepare new creative angles for review before replacing active ads."
            )
        case "audience":
            return (
                f"{base} Prepare narrowed targeting guidance without changing live audiences."
            )
        case "measurement":
            return f"{base} Treat this as an investigation or monitoring task."
        case _:
            return base


def _draft_params_for_action(step: FeedbackActionPlanStep) -> dict[str, object]:
    match step.action_type:
        case FeedbackActionType.ADJUST_BUDGET:
            return {
                "budget_guardrail": "Do not increase total budget without human approval.",
                "recommended_budget_shift": (
                    "Reduce broad exploration and protect retargeting or proven segments."
                ),
            }
        case FeedbackActionType.REFRESH_CREATIVE:
            return {
                "creative_refresh_focus": [
                    "conversion proof",
                    "first useful product moment",
                    "clearer value proposition",
                ],
            }
        case FeedbackActionType.NARROW_AUDIENCE:
            return {
                "audience_refinement": (
                    "Prioritize high-intent and previously engaged segments before scaling."
                ),
            }
        case FeedbackActionType.INSPECT_TRACKING:
            return {
                "measurement_checklist": [
                    "conversion event fires",
                    "landing-page handoff works",
                    "attribution window matches campaign objective",
                ],
            }
        case FeedbackActionType.CONTINUE_MONITORING:
            return {
                "monitor_until": (
                    "Collect enough impressions, clicks, or spend before changing drafts."
                ),
            }


def _matched_rule_ids(recommendation: FeedbackRecommendation) -> list[str]:
    raw_rule_ids = recommendation.params.get("matched_strategy_rule_ids", [])
    if not isinstance(raw_rule_ids, list):
        return []
    return [rule_id for rule_id in raw_rule_ids if isinstance(rule_id, str)]


def _matched_rule_action(
    analysis: CampaignFeedbackAnalysis,
    *,
    matched_rule_ids: list[str],
    owner_role: AgentRole,
) -> str | None:
    for match in analysis.matched_strategy_rules:
        if match.rule_id in matched_rule_ids and match.owner_role == owner_role:
            return match.recommended_action
    return None


def _owner_role_for_action(action_type: FeedbackActionType) -> AgentRole:
    match action_type:
        case FeedbackActionType.ADJUST_BUDGET:
            return AgentRole.BUDGET_OPTIMIZER
        case FeedbackActionType.REFRESH_CREATIVE:
            return AgentRole.CREATIVE_STRATEGIST
        case FeedbackActionType.NARROW_AUDIENCE:
            return AgentRole.AUDIENCE_STRATEGIST
        case FeedbackActionType.INSPECT_TRACKING | FeedbackActionType.CONTINUE_MONITORING:
            return AgentRole.PERFORMANCE_ANALYST


def _tool_name_for_action(action_type: FeedbackActionType) -> str | None:
    match action_type:
        case FeedbackActionType.ADJUST_BUDGET:
            return "optimize_budget"
        case FeedbackActionType.REFRESH_CREATIVE:
            return "generate_creative_brief"
        case FeedbackActionType.NARROW_AUDIENCE:
            return "recommend_audience"
        case FeedbackActionType.INSPECT_TRACKING | FeedbackActionType.CONTINUE_MONITORING:
            return None


def _action_plan_summary(analysis: CampaignFeedbackAnalysis) -> str:
    summary = analysis.metrics_summary
    cpa = summary.get("cpa")
    target_cpa = summary.get("target_cpa")
    metric_note = ""
    if cpa is not None and target_cpa is not None:
        metric_note = f" Observed CPA is {cpa} against target CPA {target_cpa}."
    elif cpa is not None:
        metric_note = f" Observed CPA is {cpa}."
    return (
        f"Feedback is {analysis.health_status.value} for event {analysis.event_id}."
        f"{metric_note} Generated {len(analysis.recommendations)} draft-only next step(s)."
    )


def _optimization_draft_summary(
    action_plan: CampaignFeedbackActionPlanResponse,
    changes: list[FeedbackOptimizationDraftChange],
) -> str:
    change_types = ", ".join(sorted({change.change_type for change in changes}))
    return (
        f"Draft optimization proposal for {action_plan.health_status.value} feedback on "
        f"event {action_plan.event_id}. Includes {len(changes)} reviewable change(s)"
        f" across {change_types}."
    )


def _optimization_draft_id(event_id: str) -> str:
    return f"optimization_draft_{uuid5(NAMESPACE_URL, event_id).hex[:16]}"


def _revision_draft_id(review_id: str) -> str:
    return f"feedback_revision_draft_{uuid5(NAMESPACE_URL, review_id).hex[:16]}"


def _revision_change_id(review_id: str, change_id: str) -> str:
    source = f"{review_id}:{change_id}"
    return f"feedback_revision_change_{uuid5(NAMESPACE_URL, source).hex[:16]}"


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
