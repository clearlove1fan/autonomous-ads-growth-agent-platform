import json
from collections.abc import Callable
from decimal import Decimal

import httpx
import pytest
from pydantic import BaseModel

from ads_growth_agent.config import Settings
from ads_growth_agent.contracts import AdvertiserBrief, AgentRole, BudgetPlan, ToolIntent
from ads_growth_agent.graph import StrategyGenerationError, run_growth_strategy_graph
from ads_growth_agent.llm import LiteLLMGatewayClient
from ads_growth_agent.observability import _trace_outputs
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
    assert response.run_metadata.run_id == response.strategy.strategy_id
    assert response.run_metadata.trace_id.startswith("trace_")
    assert response.run_metadata.tracing_enabled is False
    assert response.run_metadata.node_path == response.node_path
    assert response.run_metadata.tool_count == 5
    assert response.run_metadata.failed_tool_count == 0
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
    assert exc_info.value.run_metadata is not None
    assert exc_info.value.run_metadata.node_path == ["planner", "tool_executor"]
    assert exc_info.value.run_metadata.tool_count == 3
    assert exc_info.value.run_metadata.failed_tool_count == 1
    assert [summary.tool_name for summary in exc_info.value.run_metadata.tool_summaries] == [
        "recommend_audience",
        "generate_creative_brief",
        "optimize_budget",
    ]
    assert exc_info.value.run_metadata.tool_summaries[-1].error_code == "BUDGET_SERVICE_DOWN"
    assert exc_info.value.run_metadata.error_summary == ["Budget mock failed"]


def test_langgraph_default_planner_does_not_call_llm_client() -> None:
    settings = _settings(use_llm_planner=False)

    def handler(_: httpx.Request) -> httpx.Response:
        raise AssertionError("LLM client should not be called when USE_LLM_PLANNER=false")

    response = run_growth_strategy_graph(
        _brief(),
        settings=settings,
        llm_client=_llm_client(settings, handler),
    )

    assert response.node_path == ["planner", "tool_executor", "critic", "finalizer"]
    assert response.run_metadata.failed_tool_count == 0


def test_langgraph_llm_planner_runs_valid_structured_tool_plan() -> None:
    settings = _settings(use_llm_planner=True)
    requests: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(json.loads(request.content))
        return httpx.Response(
            200,
            json=_completion(_planner_payload(_brief())),
        )

    response = run_growth_strategy_graph(
        _brief(),
        settings=settings,
        llm_client=_llm_client(settings, handler),
    )

    assert len(requests) == 1
    assert requests[0]["model"] == "test-model"
    assert requests[0]["response_format"]["type"] == "json_schema"
    assert response.node_path == ["planner", "tool_executor", "critic", "finalizer"]
    assert [result.tool_name for result in response.tool_results] == [
        "recommend_audience",
        "generate_creative_brief",
        "optimize_budget",
        "estimate_performance",
        "create_campaign_draft",
    ]
    assert response.run_metadata.tool_count == 5
    assert response.run_metadata.failed_tool_count == 0


def test_langgraph_llm_planner_rejects_invalid_tool_plan_before_tools_execute() -> None:
    settings = _settings(use_llm_planner=True)
    payload = _planner_payload(_brief())
    payload["tool_intents"][2]["tool_name"] = "launch_campaign"

    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=_completion(payload))

    with pytest.raises(StrategyGenerationError) as exc_info:
        run_growth_strategy_graph(
            _brief(),
            settings=settings,
            llm_client=_llm_client(settings, handler),
        )

    assert exc_info.value.tool_result.tool_name == "llm_planner"
    assert exc_info.value.tool_result.error is not None
    assert exc_info.value.tool_result.error.code == "LLM_PLANNER_INVALID_TOOL_PLAN"
    assert exc_info.value.run_metadata is not None
    assert exc_info.value.run_metadata.node_path == ["planner"]
    assert exc_info.value.run_metadata.tool_count == 1
    assert exc_info.value.run_metadata.failed_tool_count == 1


def test_langgraph_llm_planner_gateway_failure_is_safe_failure() -> None:
    settings = _settings(use_llm_planner=True)

    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(500, text="gateway unavailable")

    with pytest.raises(StrategyGenerationError) as exc_info:
        run_growth_strategy_graph(
            _brief(),
            settings=settings,
            llm_client=_llm_client(settings, handler),
        )

    assert exc_info.value.tool_result.tool_name == "llm_planner"
    assert exc_info.value.tool_result.error is not None
    assert exc_info.value.tool_result.error.code == "MODEL_GATEWAY_HTTP_ERROR"
    assert exc_info.value.run_metadata is not None
    assert exc_info.value.run_metadata.node_path == ["planner"]
    assert exc_info.value.run_metadata.failed_tool_count == 1


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


def _settings(*, use_llm_planner: bool) -> Settings:
    return Settings(
        litellm_base_url="http://llm.local",
        litellm_api_key="test-key",
        default_chat_model="test-model",
        use_llm_planner=use_llm_planner,
        llm_structured_output_max_repair_attempts=0,
        langsmith_tracing=False,
    )


def _llm_client(
    settings: Settings,
    handler: Callable[[httpx.Request], httpx.Response],
) -> LiteLLMGatewayClient:
    return LiteLLMGatewayClient(
        settings=settings,
        http_client=httpx.Client(transport=httpx.MockTransport(handler)),
    )


def _completion(content: dict) -> dict:
    return {
        "model": "test-model",
        "choices": [
            {
                "message": {"content": json.dumps(content)},
                "finish_reason": "stop",
            }
        ],
    }


def _planner_payload(brief: AdvertiserBrief) -> dict:
    return {
        "rationale": "Plan audience, creative, and budget first before dependent analysis.",
        "tool_intents": [
            {
                "intent_id": "llm:audience",
                "tool_name": "recommend_audience",
                "requested_by": "planner",
                "risk_level": "low",
                "requires_human_approval": False,
                "params": {
                    "advertiser_id": brief.advertiser_id,
                    "product_category": brief.product_category,
                    "objective": brief.objective.value,
                    "target_market": brief.target_market,
                    "known_audiences": brief.known_audiences,
                },
            },
            {
                "intent_id": "llm:creative",
                "tool_name": "generate_creative_brief",
                "requested_by": "planner",
                "risk_level": "low",
                "requires_human_approval": False,
                "params": {
                    "product_name": brief.product_name,
                    "product_category": brief.product_category,
                    "objective": brief.objective.value,
                    "brand_voice": brief.brand_voice,
                    "constraints": brief.constraints,
                },
            },
            {
                "intent_id": "llm:budget",
                "tool_name": "optimize_budget",
                "requested_by": "planner",
                "risk_level": "low",
                "requires_human_approval": False,
                "params": {
                    "advertiser_id": brief.advertiser_id,
                    "objective": brief.objective.value,
                    "total_budget": str(brief.budget),
                    "currency": brief.currency,
                    "duration_days": brief.duration_days,
                },
            },
        ],
    }


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


def test_trace_outputs_tolerates_missing_outputs() -> None:
    assert _trace_outputs(None) == {
        "node_path": [],
        "tool_count": 0,
        "failed_tool_count": 0,
    }
