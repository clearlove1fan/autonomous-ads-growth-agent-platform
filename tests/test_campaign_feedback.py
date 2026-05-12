from ads_growth_agent.contracts import (
    CampaignObjective,
    CampaignPerformanceEventRequest,
    FeedbackActionType,
    FeedbackHealthStatus,
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
