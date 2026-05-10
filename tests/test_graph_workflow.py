from decimal import Decimal

import pytest
from pydantic import BaseModel

from ads_growth_agent.contracts import AdvertiserBrief, AgentRole, BudgetPlan, ToolIntent
from ads_growth_agent.graph import StrategyGenerationError, run_growth_strategy_graph
from ads_growth_agent.tools import (
    ToolDefinition,
    ToolExecutionContext,
    ToolExecutionError,
    ToolRegistry,
    build_default_tool_registry,
)


def test_langgraph_workflow_runs_expected_node_path() -> None:
    response = run_growth_strategy_graph(_brief())

    assert response.node_path == ["planner", "tool_executor", "critic", "finalizer"]
    assert len(response.tool_results) == 5
    assert [result.tool_name for result in response.tool_results] == [
        "recommend_audience",
        "generate_creative_brief",
        "optimize_budget",
        "estimate_performance",
        "create_campaign_draft",
    ]
    assert response.strategy.critique.passed is True


def test_langgraph_workflow_preserves_budget_validation() -> None:
    response = run_growth_strategy_graph(_brief())

    budget_plan = BudgetPlan.model_validate(response.strategy.budget_plan)
    assert budget_plan.allocated_budget <= Decimal("2000.00")
    assert response.strategy.success_metrics


def test_langgraph_workflow_raises_structured_tool_error() -> None:
    registry = ToolRegistry()
    default_registry = build_default_tool_registry()

    registry.register(default_registry._tools["recommend_audience"])
    registry.register(default_registry._tools["generate_creative_brief"])
    registry.register(
        ToolDefinition(
            name="optimize_budget",
            input_model=FailingBudgetInput,
            output_model=FailingBudgetOutput,
            handler=failing_budget_tool,
            owner_role=AgentRole.BUDGET_OPTIMIZER,
        )
    )

    with pytest.raises(StrategyGenerationError) as exc_info:
        run_growth_strategy_graph(_brief(), registry)

    assert exc_info.value.tool_result.success is False
    assert exc_info.value.tool_result.error is not None
    assert exc_info.value.tool_result.error.code == "BUDGET_SERVICE_DOWN"


def _brief() -> AdvertiserBrief:
    return AdvertiserBrief(
        advertiser_id="adv_fitness_001",
        product_name="FitTrack Pro",
        product_category="fitness app",
        objective="registrations",
        budget=Decimal("2000.00"),
        currency="USD",
        duration_days=14,
        target_market="United States",
        primary_kpi="trial registrations",
        target_cpa=Decimal("20.00"),
    )


class FailingBudgetInput(BaseModel):
    pass


class FailingBudgetOutput(BaseModel):
    ok: bool


def failing_budget_tool(_: BaseModel) -> FailingBudgetOutput:
    raise ToolExecutionError("BUDGET_SERVICE_DOWN", "Budget mock failed", retryable=True)


def test_tool_registry_context_contract_still_importable() -> None:
    context = ToolExecutionContext(advertiser_id="adv", run_id="run")
    intent = ToolIntent(
        intent_id="intent",
        tool_name="recommend_audience",
        requested_by=AgentRole.PLANNER,
    )

    assert context.advertiser_id == "adv"
    assert intent.tool_name == "recommend_audience"
