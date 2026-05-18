from ads_growth_agent.contracts import (
    AgentRole,
    CampaignObjective,
    CampaignPerformanceEventRequest,
    FeedbackActionType,
    FeedbackHealthStatus,
    FeedbackStrategyContext,
    OptimizationRule,
    PerformanceMetrics,
)
from ads_growth_agent.feedback import analyze_campaign_performance_event


def test_feedback_analysis_flags_underperforming_cpa() -> None:
    analysis = analyze_campaign_performance_event(
        CampaignPerformanceEventRequest(
            event_id="evt_underperforming_cpa",
            advertiser_id="adv_fitness_001",
            run_id="run_001",
            objective=CampaignObjective.REGISTRATIONS,
            occurred_at="2026-05-12T12:00:00Z",
            metrics=PerformanceMetrics(
                impressions=10_000,
                clicks=500,
                spend="1000.00",
                conversions=20,
            ),
            target_cpa="20.00",
        )
    )

    assert analysis.health_status == FeedbackHealthStatus.UNDERPERFORMING
    assert analysis.feedback_id.startswith("feedback_")
    assert analysis.metrics_summary["cpa"] == "50.00"
    assert [item.action_type for item in analysis.recommendations] == [
        FeedbackActionType.ADJUST_BUDGET,
        FeedbackActionType.REFRESH_CREATIVE,
    ]
    assert all(item.requires_human_approval for item in analysis.recommendations)
    assert "do not mutate live campaign spend" in analysis.guardrails[0]


def test_feedback_analysis_matches_strategy_optimization_rules() -> None:
    analysis = analyze_campaign_performance_event(
        CampaignPerformanceEventRequest(
            event_id="evt_strategy_context",
            advertiser_id="adv_fitness_001",
            run_id="run_001",
            draft_id="draft_fittrack",
            objective=CampaignObjective.REGISTRATIONS,
            occurred_at="2026-05-12T12:00:00Z",
            metrics=PerformanceMetrics(
                impressions=10_000,
                clicks=500,
                spend="1000.00",
                conversions=20,
            ),
            strategy_context=FeedbackStrategyContext(
                strategy_id="strategy_001",
                draft_id="draft_fittrack",
                target_cpa="20.00",
                optimization_rules=[
                    OptimizationRule(
                        rule_id="strategy_001:rule:cpa_guardrail",
                        trigger_metric="cost_per_result",
                        condition="Observed CPA exceeds target by more than 20%.",
                        recommended_action="Shift budget toward the best converting lane.",
                        owner_role=AgentRole.BUDGET_OPTIMIZER,
                        priority=1,
                        rationale="CPA is the primary efficiency guardrail.",
                    ),
                    OptimizationRule(
                        rule_id="strategy_001:rule:creative_learning",
                        trigger_metric="creative_cell_conversions",
                        condition="One creative angle wins.",
                        recommended_action="Generate close variants of the winning hook.",
                        owner_role=AgentRole.CREATIVE_STRATEGIST,
                        priority=2,
                        rationale="Creative learning should happen before broad scaling.",
                    ),
                ],
            ),
        )
    )

    assert analysis.health_status == FeedbackHealthStatus.UNDERPERFORMING
    assert analysis.strategy_id == "strategy_001"
    assert analysis.draft_id == "draft_fittrack"
    assert analysis.metrics_summary["target_cpa"] == "20.00"
    assert [match.rule_id for match in analysis.matched_strategy_rules] == [
        "strategy_001:rule:cpa_guardrail"
    ]
    assert analysis.recommendations[0].params["strategy_id"] == "strategy_001"
    assert analysis.recommendations[0].params["draft_id"] == "draft_fittrack"
    assert analysis.recommendations[0].params["matched_strategy_rule_ids"] == [
        "strategy_001:rule:cpa_guardrail"
    ]


def test_feedback_analysis_continues_monitoring_for_low_signal_event() -> None:
    analysis = analyze_campaign_performance_event(
        CampaignPerformanceEventRequest(
            event_id="evt_low_signal",
            advertiser_id="adv_fitness_001",
            draft_id="draft_001",
            objective=CampaignObjective.REGISTRATIONS,
            occurred_at="2026-05-12T12:00:00Z",
            metrics=PerformanceMetrics(
                impressions=25,
                clicks=2,
                spend="0.00",
                conversions=0,
            ),
            target_cpa="20.00",
        )
    )

    assert analysis.health_status == FeedbackHealthStatus.INSUFFICIENT_DATA
    assert analysis.recommendations[0].action_type == FeedbackActionType.CONTINUE_MONITORING
    assert analysis.recommendations[0].requires_human_approval is False


def test_feedback_analysis_flags_zero_conversion_attention() -> None:
    analysis = analyze_campaign_performance_event(
        CampaignPerformanceEventRequest(
            event_id="evt_zero_conversions",
            advertiser_id="adv_fitness_001",
            campaign_id="cmp_001",
            objective=CampaignObjective.REGISTRATIONS,
            occurred_at="2026-05-12T12:00:00Z",
            metrics=PerformanceMetrics(
                impressions=5_000,
                clicks=200,
                spend="350.00",
                conversions=0,
            ),
            target_cpa="20.00",
        )
    )

    assert analysis.health_status == FeedbackHealthStatus.NEEDS_ATTENTION
    assert [item.action_type for item in analysis.recommendations] == [
        FeedbackActionType.INSPECT_TRACKING,
        FeedbackActionType.NARROW_AUDIENCE,
    ]
