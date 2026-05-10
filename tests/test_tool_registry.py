from decimal import Decimal

from pydantic import BaseModel

from ads_growth_agent.contracts import AgentRole, BudgetPlan, ToolIntent
from ads_growth_agent.tools import (
    ToolDefinition,
    ToolExecutionContext,
    ToolExecutionError,
    ToolRegistry,
    build_default_tool_registry,
)


def test_tool_registry_executes_registered_tool() -> None:
    registry = build_default_tool_registry()
    result = registry.execute(
        ToolIntent(
            intent_id="intent_001",
            tool_name="recommend_audience",
            requested_by=AgentRole.PLANNER,
            params={
                "advertiser_id": "adv_001",
                "product_category": "fitness app",
                "objective": "registrations",
                "target_market": "United States",
            },
        ),
        _context(),
    )

    assert result.success is True
    assert result.payload["segments"]
    assert result.error is None


def test_tool_registry_returns_unknown_tool_error() -> None:
    registry = build_default_tool_registry()
    result = registry.execute(
        ToolIntent(
            intent_id="intent_001",
            tool_name="not_a_real_tool",
            requested_by=AgentRole.PLANNER,
            params={},
        ),
        _context(),
    )

    assert result.success is False
    assert result.error is not None
    assert result.error.code == "UNKNOWN_TOOL"


def test_tool_registry_returns_validation_error_for_invalid_params() -> None:
    registry = build_default_tool_registry()
    result = registry.execute(
        ToolIntent(
            intent_id="intent_001",
            tool_name="recommend_audience",
            requested_by=AgentRole.PLANNER,
            params={"advertiser_id": "adv_001"},
        ),
        _context(),
    )

    assert result.success is False
    assert result.error is not None
    assert result.error.code == "VALIDATION_ERROR"


def test_tool_registry_returns_permission_denied() -> None:
    registry = build_default_tool_registry()
    result = registry.execute(
        ToolIntent(
            intent_id="intent_001",
            tool_name="recommend_audience",
            requested_by=AgentRole.PLANNER,
            params={
                "advertiser_id": "adv_001",
                "product_category": "fitness app",
                "objective": "registrations",
                "target_market": "United States",
            },
        ),
        _context(allowed_tools={"optimize_budget"}),
    )

    assert result.success is False
    assert result.error is not None
    assert result.error.code == "PERMISSION_DENIED"


def test_tool_registry_returns_structured_tool_failure() -> None:
    class EmptyInput(BaseModel):
        pass

    class EmptyOutput(BaseModel):
        ok: bool

    def failing_tool(_: BaseModel) -> EmptyOutput:
        raise ToolExecutionError("UPSTREAM_TIMEOUT", "Mock upstream timed out", retryable=True)

    registry = ToolRegistry()
    registry.register(
        ToolDefinition(
            name="failing_tool",
            input_model=EmptyInput,
            output_model=EmptyOutput,
            handler=failing_tool,
            owner_role=AgentRole.PERFORMANCE_ANALYST,
        )
    )

    result = registry.execute(
        ToolIntent(
            intent_id="intent_001",
            tool_name="failing_tool",
            requested_by=AgentRole.PLANNER,
            params={},
        ),
        _context(allowed_tools={"failing_tool"}),
    )

    assert result.success is False
    assert result.error is not None
    assert result.error.code == "UPSTREAM_TIMEOUT"
    assert result.error.retryable is True


def test_optimize_budget_does_not_exceed_advertiser_budget() -> None:
    registry = build_default_tool_registry()
    result = registry.execute(
        ToolIntent(
            intent_id="intent_001",
            tool_name="optimize_budget",
            requested_by=AgentRole.PLANNER,
            params={
                "advertiser_id": "adv_001",
                "objective": "registrations",
                "total_budget": "2000.00",
                "currency": "USD",
                "duration_days": 14,
            },
        ),
        _context(),
    )

    budget_plan = BudgetPlan.model_validate(result.payload["budget_plan"])
    assert budget_plan.allocated_budget <= Decimal("2000.00")
    assert [allocation.channel for allocation in budget_plan.allocations] == [
        "prospecting",
        "retargeting",
        "creative_tests",
    ]
    assert [allocation.amount for allocation in budget_plan.allocations] == [
        Decimal("1400.00"),
        Decimal("400.00"),
        Decimal("200.00"),
    ]


def _context(allowed_tools: set[str] | None = None) -> ToolExecutionContext:
    return ToolExecutionContext(
        advertiser_id="adv_001",
        run_id="run_001",
        allowed_tools=allowed_tools
        or {
            "recommend_audience",
            "generate_creative_brief",
            "optimize_budget",
            "estimate_performance",
            "create_campaign_draft",
        },
    )
