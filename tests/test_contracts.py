from decimal import Decimal

import pytest
from pydantic import ValidationError

from ads_growth_agent.contracts import (
    AdvertiserBrief,
    AgentRole,
    AgentTask,
    AgentTaskType,
    BudgetAllocation,
    BudgetPlan,
    CampaignObjective,
    CampaignPerformanceEventRequest,
    CritiqueIssue,
    CritiqueReport,
    PerformanceMetrics,
    RiskLevel,
    RunMetadata,
    ToolIntent,
    ToolResult,
)


def test_advertiser_brief_normalizes_currency() -> None:
    brief = AdvertiserBrief(
        advertiser_id="adv_001",
        product_name="FitTrack Pro",
        product_category="fitness app",
        objective=CampaignObjective.REGISTRATIONS,
        budget=Decimal("2000.00"),
        currency="usd",
        duration_days=14,
        target_market="United States",
    )

    assert brief.currency == "USD"


def test_agent_task_contract_validates_expected_output() -> None:
    task = AgentTask(
        task_id="task_001",
        task_type=AgentTaskType.RECOMMEND_AUDIENCE,
        owner_role=AgentRole.AUDIENCE_STRATEGIST,
        input_payload={"objective": "registrations"},
        expected_output="Audience recommendation with rationale",
    )

    assert task.owner_role == AgentRole.AUDIENCE_STRATEGIST


def test_budget_plan_rejects_over_allocation() -> None:
    with pytest.raises(ValidationError, match="budget allocations cannot exceed total budget"):
        BudgetPlan(
            total_budget=Decimal("100.00"),
            currency="USD",
            allocations=[
                BudgetAllocation(channel="prospecting", amount=Decimal("80.00"), rationale="Scale"),
                BudgetAllocation(
                    channel="retargeting",
                    amount=Decimal("30.00"),
                    rationale="Convert",
                ),
            ],
        )


def test_high_risk_tool_intent_requires_human_approval() -> None:
    with pytest.raises(ValidationError, match="high-risk tool intents require human approval"):
        ToolIntent(
            intent_id="intent_001",
            tool_name="change_budget",
            requested_by=AgentRole.PLANNER,
            risk_level=RiskLevel.HIGH,
        )


def test_failed_tool_result_requires_error() -> None:
    with pytest.raises(ValidationError, match="failed tool results must include an error"):
        ToolResult(tool_name="recommend_audience", success=False, latency_ms=0)


def test_run_metadata_defaults_execution_id_to_run_id() -> None:
    metadata = RunMetadata(
        run_id="run_001",
        strategy_id="strategy_001",
        trace_id="trace_001",
        langsmith_project="test",
        tracing_enabled=False,
        tool_count=0,
        failed_tool_count=0,
    )

    assert metadata.execution_id == "run_001"
    assert metadata.strategy_id == "strategy_001"


def test_passing_critique_requires_minimum_score() -> None:
    with pytest.raises(ValidationError, match="passing critique reports require score >= 7"):
        CritiqueReport(score=6.9, passed=True, rationale="Almost good enough")


def test_failed_critique_requires_issues() -> None:
    report = CritiqueReport(
        score=5,
        passed=False,
        issues=[
            CritiqueIssue(
                severity=RiskLevel.MEDIUM,
                message="Budget is not tied to expected conversions.",
                suggested_fix="Add budget rationale and performance estimate.",
            )
        ],
        required_revisions=["Add performance grounding"],
        rationale="Needs revision before finalization.",
    )

    assert report.issues[0].severity == RiskLevel.MEDIUM


def test_performance_metrics_reject_impossible_click_count() -> None:
    with pytest.raises(ValidationError, match="clicks cannot exceed impressions"):
        PerformanceMetrics(
            impressions=10,
            clicks=11,
            spend=Decimal("20.00"),
            conversions=1,
        )


def test_performance_event_requires_campaign_or_run_reference() -> None:
    with pytest.raises(
        ValidationError,
        match="performance events require run_id, campaign_id, or draft_id",
    ):
        CampaignPerformanceEventRequest(
            event_id="evt_missing_reference",
            advertiser_id="adv_001",
            objective=CampaignObjective.REGISTRATIONS,
            occurred_at="2026-05-12T12:00:00Z",
            metrics=PerformanceMetrics(
                impressions=100,
                clicks=10,
                spend=Decimal("50.00"),
                conversions=1,
            ),
        )
