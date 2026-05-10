import json
from uuid import NAMESPACE_URL, uuid5

from ads_growth_agent.contracts import (
    AdvertiserBrief,
    AgentRole,
    CritiqueReport,
    FinalGrowthStrategy,
    GrowthStrategyResponse,
    RecommendedAction,
    RiskAssessment,
    RiskLevel,
    SourceCitation,
    SuccessMetric,
    ToolIntent,
    ToolResult,
)
from ads_growth_agent.tools import (
    AudienceRecommendationOutput,
    BudgetOptimizationOutput,
    CampaignDraftOutput,
    CreativeBriefOutput,
    PerformanceEstimateOutput,
    ToolExecutionContext,
    ToolRegistry,
    build_default_tool_registry,
)


class StrategyGenerationError(Exception):
    def __init__(self, message: str, tool_result: ToolResult) -> None:
        super().__init__(message)
        self.tool_result = tool_result


def generate_mock_growth_strategy(
    brief: AdvertiserBrief,
    registry: ToolRegistry | None = None,
) -> GrowthStrategyResponse:
    registry = registry or build_default_tool_registry()
    strategy_id = _strategy_id(brief)
    context = ToolExecutionContext(
        advertiser_id=brief.advertiser_id,
        run_id=strategy_id,
        allowed_tools={
            "recommend_audience",
            "generate_creative_brief",
            "optimize_budget",
            "estimate_performance",
            "create_campaign_draft",
        },
    )

    tool_results: list[ToolResult] = []

    audience_result = _execute_or_raise(
        registry,
        context,
        ToolIntent(
            intent_id=f"{strategy_id}:audience",
            tool_name="recommend_audience",
            requested_by=AgentRole.PLANNER,
            params={
                "advertiser_id": brief.advertiser_id,
                "product_category": brief.product_category,
                "objective": brief.objective,
                "target_market": brief.target_market,
                "known_audiences": brief.known_audiences,
            },
        ),
    )
    tool_results.append(audience_result)
    audience = AudienceRecommendationOutput.model_validate(audience_result.payload)

    creative_result = _execute_or_raise(
        registry,
        context,
        ToolIntent(
            intent_id=f"{strategy_id}:creative",
            tool_name="generate_creative_brief",
            requested_by=AgentRole.PLANNER,
            params={
                "product_name": brief.product_name,
                "product_category": brief.product_category,
                "objective": brief.objective,
                "brand_voice": brief.brand_voice,
                "constraints": brief.constraints,
            },
        ),
    )
    tool_results.append(creative_result)
    creative = CreativeBriefOutput.model_validate(creative_result.payload)

    budget_result = _execute_or_raise(
        registry,
        context,
        ToolIntent(
            intent_id=f"{strategy_id}:budget",
            tool_name="optimize_budget",
            requested_by=AgentRole.PLANNER,
            params={
                "advertiser_id": brief.advertiser_id,
                "objective": brief.objective,
                "total_budget": brief.budget,
                "currency": brief.currency,
                "duration_days": brief.duration_days,
            },
        ),
    )
    tool_results.append(budget_result)
    budget = BudgetOptimizationOutput.model_validate(budget_result.payload)

    performance_result = _execute_or_raise(
        registry,
        context,
        ToolIntent(
            intent_id=f"{strategy_id}:performance",
            tool_name="estimate_performance",
            requested_by=AgentRole.PLANNER,
            params={
                "product_category": brief.product_category,
                "objective": brief.objective,
                "budget_plan": budget.budget_plan,
                "target_cpa": brief.target_cpa,
            },
        ),
    )
    tool_results.append(performance_result)
    performance = PerformanceEstimateOutput.model_validate(performance_result.payload)

    draft_result = _execute_or_raise(
        registry,
        context,
        ToolIntent(
            intent_id=f"{strategy_id}:campaign_draft",
            tool_name="create_campaign_draft",
            requested_by=AgentRole.PLANNER,
            params={
                "advertiser_id": brief.advertiser_id,
                "product_name": brief.product_name,
                "objective": brief.objective,
                "budget_plan": budget.budget_plan,
                "duration_days": brief.duration_days,
                "audience_segments": audience.segments,
                "creative_angles": creative.creative_angles,
            },
        ),
    )
    tool_results.append(draft_result)
    draft = CampaignDraftOutput.model_validate(draft_result.payload)

    strategy = FinalGrowthStrategy(
        strategy_id=strategy_id,
        advertiser_id=brief.advertiser_id,
        objective=brief.objective,
        summary=(
            f"Draft a {brief.duration_days}-day {brief.objective.value.replace('_', ' ')} "
            f"growth plan for {brief.product_name} with a {brief.currency} {brief.budget} budget. "
            f"The plan prioritizes prospecting, retargeting, and creative learning before scale."
        ),
        audience_strategy=audience.segments,
        creative_strategy=creative.creative_angles,
        bidding_strategy=budget.bidding_strategy,
        measurement_plan=[
            f"Track primary KPI: {brief.primary_kpi}",
            f"Monitor estimated CPA against {performance.estimated_cpa} {brief.currency}",
            "Compare prospecting, retargeting, and creative-test cohorts daily.",
        ],
        budget_plan=budget.budget_plan,
        actions=[
            RecommendedAction(
                action_id=f"{strategy_id}:action:create_draft",
                title="Create campaign draft",
                description=f"Prepare draft campaign {draft.draft_id} without launching spend.",
                owner_role=AgentRole.PLANNER,
                priority=1,
                tool_name="create_campaign_draft",
                params={"draft_id": draft.draft_id},
            ),
            RecommendedAction(
                action_id=f"{strategy_id}:action:creative_tests",
                title="Launch creative test plan after approval",
                description=(
                    "Use the creative angles as separate test cells before scaling winners."
                ),
                owner_role=AgentRole.CREATIVE_STRATEGIST,
                priority=2,
                tool_name="generate_creative_brief",
                params={"creative_angles": creative.creative_angles},
            ),
            RecommendedAction(
                action_id=f"{strategy_id}:action:measurement",
                title="Review first performance readout",
                description=(
                    "Compare estimated conversions with early delivery data before revising bids."
                ),
                owner_role=AgentRole.PERFORMANCE_ANALYST,
                priority=3,
                tool_name="estimate_performance",
                params={"estimated_conversions": performance.estimated_conversions},
            ),
        ],
        risks=[
            RiskAssessment(
                risk_id=f"{strategy_id}:risk:mock_estimates",
                level=RiskLevel.MEDIUM,
                description=(
                    "Performance numbers use deterministic mock benchmarks, not live delivery data."
                ),
                mitigation=(
                    "Replace mock estimates with historical campaign retrieval and analytics tools."
                ),
            ),
            RiskAssessment(
                risk_id=f"{strategy_id}:risk:approval",
                level=RiskLevel.LOW,
                description="Campaign draft is not approved for live launch.",
                mitigation="Require human approval before any external ad platform mutation.",
            ),
        ],
        assumptions=performance.assumptions,
        success_metrics=[
            SuccessMetric(
                name="Estimated conversions",
                target=str(performance.estimated_conversions),
                measurement_window=f"{brief.duration_days} days",
            ),
            SuccessMetric(
                name="Estimated CPA",
                target=f"{performance.estimated_cpa} {brief.currency}",
                measurement_window=f"{brief.duration_days} days",
            ),
            SuccessMetric(
                name="Budget consistency",
                target=f"Allocations <= {brief.budget} {brief.currency}",
                measurement_window="pre-launch validation",
            ),
        ],
        critique=CritiqueReport(
            score=8.1,
            passed=True,
            issues=[],
            required_revisions=[],
            rationale=(
                "Strategy passes v0.1 deterministic checks: structured output, valid budget math, "
                "draft-only actions, and measurable next steps."
            ),
        ),
        sources=[
            SourceCitation(
                source_id=audience.source_id,
                title="Mock audience recommendation",
                source_type="mock_tool",
                relevance=0.78,
            ),
            SourceCitation(
                source_id=creative.source_id,
                title="Mock creative brief",
                source_type="mock_tool",
                relevance=0.72,
            ),
            SourceCitation(
                source_id=budget.source_id,
                title="Mock budget optimization",
                source_type="mock_tool",
                relevance=0.9,
            ),
            SourceCitation(
                source_id=performance.source_id,
                title="Mock performance estimate",
                source_type="mock_tool",
                relevance=0.68,
            ),
            SourceCitation(
                source_id=draft.source_id,
                title="Mock campaign draft",
                source_type="mock_tool",
                relevance=0.82,
            ),
        ],
    )

    return GrowthStrategyResponse(strategy=strategy, tool_results=tool_results)


def _execute_or_raise(
    registry: ToolRegistry,
    context: ToolExecutionContext,
    intent: ToolIntent,
) -> ToolResult:
    result = registry.execute(intent, context)
    if not result.success:
        message = result.error.message if result.error else "tool execution failed"
        raise StrategyGenerationError(message, result)
    return result


def _strategy_id(brief: AdvertiserBrief) -> str:
    payload = json.dumps(brief.model_dump(mode="json"), sort_keys=True)
    return f"strategy_{uuid5(NAMESPACE_URL, payload).hex[:16]}"
