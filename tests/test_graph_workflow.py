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

    assert response.node_path == ["planner", "retriever", "tool_executor", "critic", "finalizer"]
    assert len(response.tool_results) == 5
    assert [result.tool_name for result in response.tool_results] == [
        "recommend_audience",
        "generate_creative_brief",
        "optimize_budget",
        "estimate_performance",
        "create_campaign_draft",
    ]
    assert response.run_metadata.run_id.startswith("run_")
    assert response.run_metadata.execution_id == response.run_metadata.run_id
    assert response.run_metadata.strategy_id == response.strategy.strategy_id
    assert response.run_metadata.trace_id.startswith("trace_")
    assert response.run_metadata.tracing_enabled is False
    assert response.run_metadata.node_path == response.node_path
    assert response.run_metadata.tool_count == 5
    assert response.run_metadata.failed_tool_count == 0
    assert response.strategy.critique.passed is True
    assert any(source.source_type == "advertiser_memory" for source in response.strategy.sources)
    assert any(source.source_type == "rag_document" for source in response.strategy.sources)


def test_langgraph_workflow_preserves_budget_validation() -> None:
    response = run_growth_strategy_graph(_brief())

    budget_plan = BudgetPlan.model_validate(response.strategy.budget_plan)
    assert budget_plan.allocated_budget <= Decimal("2000.00")
    assert response.strategy.success_metrics


def test_langgraph_workflow_separates_strategy_id_from_execution_run_id() -> None:
    first = run_growth_strategy_graph(_brief())
    second = run_growth_strategy_graph(_brief())

    assert first.strategy.strategy_id == second.strategy.strategy_id
    assert first.run_metadata.strategy_id == first.strategy.strategy_id
    assert second.run_metadata.strategy_id == second.strategy.strategy_id
    assert first.run_metadata.run_id != second.run_metadata.run_id
    assert first.run_metadata.execution_id == first.run_metadata.run_id
    assert second.run_metadata.execution_id == second.run_metadata.run_id


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
    assert exc_info.value.run_metadata.node_path == ["planner", "retriever", "tool_executor"]
    assert exc_info.value.run_metadata.tool_count == 3
    assert exc_info.value.run_metadata.failed_tool_count == 1
    assert [summary.tool_name for summary in exc_info.value.run_metadata.tool_summaries] == [
        "recommend_audience",
        "generate_creative_brief",
        "optimize_budget",
    ]
    assert exc_info.value.run_metadata.tool_summaries[-1].error_code == "BUDGET_SERVICE_DOWN"
    assert exc_info.value.run_metadata.error_summary == ["Budget mock failed"]


def test_langgraph_default_agent_nodes_do_not_call_llm_client() -> None:
    settings = _settings(use_llm_planner=False)

    def handler(_: httpx.Request) -> httpx.Response:
        raise AssertionError("LLM client should not be called when LLM agent flags are false")

    response = run_growth_strategy_graph(
        _brief(),
        settings=settings,
        llm_client=_llm_client(settings, handler),
    )

    assert response.node_path == ["planner", "retriever", "tool_executor", "critic", "finalizer"]
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
    assert response.node_path == ["planner", "retriever", "tool_executor", "critic", "finalizer"]
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


def test_langgraph_llm_critic_runs_valid_structured_critique() -> None:
    settings = _settings(use_llm_planner=False, use_llm_critic=True)
    requests: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(json.loads(request.content))
        return httpx.Response(200, json=_completion(_passing_critique_payload()))

    response = run_growth_strategy_graph(
        _brief(),
        settings=settings,
        llm_client=_llm_client(settings, handler),
    )

    assert len(requests) == 1
    assert requests[0]["model"] == "test-model"
    assert requests[0]["response_format"]["json_schema"]["name"] == "CritiqueReport"
    assert response.node_path == ["planner", "retriever", "tool_executor", "critic", "finalizer"]
    assert response.strategy.critique.score == 8.7
    assert response.strategy.critique.passed is True
    assert response.run_metadata.tool_count == 5
    assert response.run_metadata.failed_tool_count == 0


def test_langgraph_llm_critic_revision_loop_finalizes_after_second_pass() -> None:
    settings = _settings(use_llm_planner=False, use_llm_critic=True)
    payloads = [_failing_critique_payload(), _passing_critique_payload()]
    requests: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(json.loads(request.content))
        return httpx.Response(200, json=_completion(payloads.pop(0)))

    response = run_growth_strategy_graph(
        _brief(),
        settings=settings,
        llm_client=_llm_client(settings, handler),
    )

    assert len(requests) == 2
    assert "revisions" in requests[1]["messages"][-1]["content"]
    assert response.node_path == [
        "planner",
        "retriever",
        "tool_executor",
        "critic",
        "revision",
        "critic",
        "finalizer",
    ]
    assert response.strategy.critique.score == 8.7
    assert response.strategy.critique.passed is True
    assert any(
        "Add a concrete feedback threshold" in item
        for item in response.strategy.measurement_plan
    )
    assert response.run_metadata.tool_count == 5
    assert response.run_metadata.failed_tool_count == 0


def test_langgraph_llm_critic_rejection_after_revision_limit_is_safe_failure() -> None:
    settings = _settings(use_llm_planner=False, use_llm_critic=True)
    request_count = 0

    def handler(_: httpx.Request) -> httpx.Response:
        nonlocal request_count
        request_count += 1
        return httpx.Response(200, json=_completion(_failing_critique_payload()))

    with pytest.raises(StrategyGenerationError) as exc_info:
        run_growth_strategy_graph(
            _brief(),
            settings=settings,
            llm_client=_llm_client(settings, handler),
        )

    assert exc_info.value.tool_result.tool_name == "llm_critic"
    assert exc_info.value.tool_result.error is not None
    assert exc_info.value.tool_result.error.code == "LLM_CRITIC_REJECTED_STRATEGY"
    assert "revision_attempts=1" in exc_info.value.tool_result.error.message
    assert exc_info.value.run_metadata is not None
    assert exc_info.value.run_metadata.node_path == [
        "planner",
        "retriever",
        "tool_executor",
        "critic",
        "revision",
        "critic",
    ]
    assert exc_info.value.run_metadata.tool_count == 6
    assert exc_info.value.run_metadata.failed_tool_count == 1
    assert request_count == 2
    assert [summary.tool_name for summary in exc_info.value.run_metadata.tool_summaries][-1] == (
        "llm_critic"
    )


def test_langgraph_llm_critic_gateway_failure_is_safe_failure() -> None:
    settings = _settings(use_llm_planner=False, use_llm_critic=True)

    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(500, text="critic gateway unavailable")

    with pytest.raises(StrategyGenerationError) as exc_info:
        run_growth_strategy_graph(
            _brief(),
            settings=settings,
            llm_client=_llm_client(settings, handler),
        )

    assert exc_info.value.tool_result.tool_name == "llm_critic"
    assert exc_info.value.tool_result.error is not None
    assert exc_info.value.tool_result.error.code == "MODEL_GATEWAY_HTTP_ERROR"
    assert exc_info.value.run_metadata is not None
    assert exc_info.value.run_metadata.node_path == [
        "planner",
        "retriever",
        "tool_executor",
        "critic",
    ]
    assert exc_info.value.run_metadata.tool_count == 6
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


def _settings(
    *,
    use_llm_planner: bool,
    use_llm_critic: bool = False,
    max_revision_attempts: int = 1,
) -> Settings:
    return Settings(
        litellm_base_url="http://llm.local",
        litellm_api_key="test-key",
        default_chat_model="test-model",
        use_llm_planner=use_llm_planner,
        use_llm_critic=use_llm_critic,
        llm_structured_output_max_repair_attempts=0,
        llm_critic_min_score=7.0,
        max_revision_attempts=max_revision_attempts,
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


def _passing_critique_payload() -> dict:
    return {
        "score": 8.7,
        "passed": True,
        "issues": [],
        "required_revisions": [],
        "rationale": (
            "The plan is draft-only, has consistent budget allocation, measurable next "
            "steps, and explicit assumptions."
        ),
    }


def _failing_critique_payload() -> dict:
    return {
        "score": 5.8,
        "passed": False,
        "issues": [
            {
                "severity": "medium",
                "message": "Measurement plan does not define a clear feedback threshold.",
                "suggested_fix": "Add explicit CPA and conversion thresholds before finalization.",
            }
        ],
        "required_revisions": ["Add a concrete feedback threshold before approving the draft."],
        "rationale": "The strategy needs a sharper feedback loop before finalization.",
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
