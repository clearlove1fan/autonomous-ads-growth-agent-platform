import json
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient
from typer.testing import CliRunner

from ads_growth_agent.api import app as api_app
from ads_growth_agent.api import get_runtime_settings
from ads_growth_agent.cli import app as cli_app
from ads_growth_agent.config import Settings
from ads_growth_agent.contracts import GrowthStrategyResponse
from ads_growth_agent.strategy_job_store_factory import clear_memory_strategy_job_store

pytestmark = pytest.mark.e2e


def test_direct_api_growth_strategy_product_smoke() -> None:
    api_app.dependency_overrides[get_runtime_settings] = _deterministic_settings
    try:
        response = TestClient(api_app).post(
            "/growth-strategies",
            json={"brief": _brief_payload()},
            headers={"X-Tenant-ID": "tenant_smoke"},
        )
    finally:
        api_app.dependency_overrides.clear()

    assert response.status_code == 200
    assert response.headers["x-tenant-id"] == "tenant_smoke"
    _assert_strategy_smoke(GrowthStrategyResponse.model_validate(response.json()))


def test_text_intake_api_growth_strategy_product_smoke() -> None:
    api_app.dependency_overrides[get_runtime_settings] = _deterministic_settings
    try:
        response = TestClient(api_app).post(
            "/growth-strategies/from-text",
            json={
                "text": (
                    "I want to use a $2000 budget to promote a fitness app in the "
                    "United States and increase trial registrations over 14 days."
                ),
                "advertiser_id": "adv_fitness_001",
            },
            headers={"X-Tenant-ID": "tenant_smoke"},
        )
    finally:
        api_app.dependency_overrides.clear()

    payload = response.json()
    assert response.status_code == 200
    assert response.headers["x-tenant-id"] == "tenant_smoke"
    assert payload["intake"]["mode"] == "heuristic"
    _assert_strategy_smoke(
        GrowthStrategyResponse.model_validate(payload["growth_strategy"])
    )


def test_async_strategy_job_product_smoke() -> None:
    clear_memory_strategy_job_store()
    api_app.dependency_overrides[get_runtime_settings] = _deterministic_settings
    try:
        client = TestClient(api_app)
        accepted = client.post(
            "/growth-strategies/jobs",
            json={"brief": _brief_payload()},
            headers={"X-Tenant-ID": "tenant_smoke"},
        )
        detail = client.get(
            accepted.json()["polling_url"],
            headers={"X-Tenant-ID": "tenant_smoke"},
        )
    finally:
        api_app.dependency_overrides.clear()
        clear_memory_strategy_job_store()

    accepted_payload = accepted.json()
    detail_payload = detail.json()
    assert accepted.status_code == 202
    assert accepted.headers["location"] == accepted_payload["polling_url"]
    assert detail.status_code == 200
    assert detail_payload["status"] == "completed"
    assert detail_payload["run_id"] == accepted_payload["run_id"]
    assert detail_payload["trace_id"] == accepted_payload["trace_id"]
    assert detail_payload["error"] is None
    assert detail_payload["result"] is not None
    _assert_strategy_smoke(GrowthStrategyResponse.model_validate(detail_payload["result"]))


def test_text_strategy_to_feedback_product_smoke() -> None:
    api_app.dependency_overrides[get_runtime_settings] = _deterministic_settings
    try:
        client = TestClient(api_app)
        strategy_response = client.post(
            "/growth-strategies/from-text",
            json={
                "text": (
                    "I want to use a $2000 budget to promote a fitness app in the "
                    "United States and increase trial registrations over 14 days."
                ),
                "advertiser_id": "adv_fitness_001",
            },
            headers={"X-Tenant-ID": "tenant_smoke"},
        )
        strategy_payload = strategy_response.json()["growth_strategy"]
        strategy = strategy_payload["strategy"]
        feedback_response = client.post(
            "/campaign-events/performance",
            json={
                "event_id": "evt_strategy_to_feedback_smoke",
                "advertiser_id": "adv_fitness_001",
                "run_id": strategy_payload["run_metadata"]["run_id"],
                "draft_id": strategy["campaign_draft"]["draft_id"],
                "objective": strategy["objective"],
                "event_type": "performance_snapshot",
                "occurred_at": "2026-05-12T12:00:00Z",
                "metrics": {
                    "impressions": 10_000,
                    "clicks": 500,
                    "spend": "1000.00",
                    "conversions": 20,
                },
                "strategy_context": strategy["feedback_context"],
            },
            headers={"X-Tenant-ID": "tenant_smoke"},
        )
    finally:
        api_app.dependency_overrides.clear()

    feedback_payload = feedback_response.json()
    assert strategy_response.status_code == 200
    assert feedback_response.status_code == 200
    assert feedback_response.headers["x-tenant-id"] == "tenant_smoke"
    assert feedback_payload["analysis"]["health_status"] == "underperforming"
    assert feedback_payload["analysis"]["strategy_id"] == strategy["strategy_id"]
    assert feedback_payload["analysis"]["draft_id"] == strategy["campaign_draft"]["draft_id"]
    assert feedback_payload["analysis"]["matched_strategy_rules"][0]["rule_id"] == (
        f"{strategy['strategy_id']}:rule:cpa_guardrail"
    )
    assert feedback_payload["analysis"]["recommendations"][0]["params"]["strategy_id"] == (
        strategy["strategy_id"]
    )


def test_cli_plan_product_smoke() -> None:
    result = CliRunner().invoke(cli_app, ["plan", "examples/fitness_app_brief.json"])

    assert result.exit_code == 0, result.output
    _assert_strategy_smoke(GrowthStrategyResponse.model_validate(json.loads(result.stdout)))


def _assert_strategy_smoke(response: GrowthStrategyResponse) -> None:
    strategy = response.strategy
    expected_path = ["planner", "retriever", "tool_executor", "critic", "finalizer"]
    expected_tools = {
        "recommend_audience",
        "generate_creative_brief",
        "optimize_budget",
        "estimate_performance",
        "create_campaign_draft",
    }
    source_types = {source.source_type for source in strategy.sources}

    assert response.node_path == expected_path
    assert response.run_metadata.node_path == expected_path
    assert response.run_metadata.tool_count == 5
    assert response.run_metadata.failed_tool_count == 0
    assert response.run_metadata.run_id.startswith("run_")
    assert response.run_metadata.execution_id == response.run_metadata.run_id
    assert response.run_metadata.trace_id.startswith("trace_")
    assert response.run_metadata.tracing_enabled is False
    assert {result.tool_name for result in response.tool_results} == expected_tools
    assert all(result.success for result in response.tool_results)
    assert strategy.advertiser_id == "adv_fitness_001"
    assert strategy.campaign_objective.product_name
    assert strategy.campaign_objective.product_category == "fitness app"
    assert strategy.campaign_objective.primary_kpi
    assert strategy.budget_plan.total_budget == Decimal("2000.00")
    assert strategy.budget_plan.allocated_budget <= strategy.budget_plan.total_budget
    assert strategy.campaign_draft.status == "draft"
    assert strategy.performance_forecast.estimated_conversions > 0
    assert strategy.measurement_events
    assert strategy.optimization_rules
    assert strategy.feedback_context.strategy_id == strategy.strategy_id
    assert strategy.feedback_context.draft_id == strategy.campaign_draft.draft_id
    assert any(rule.trigger_metric == "cost_per_result" for rule in strategy.optimization_rules)
    assert strategy.critique.passed is True
    assert strategy.actions
    assert {"advertiser_memory", "rag_document"}.issubset(source_types)


def _deterministic_settings() -> Settings:
    return Settings(
        tenant_id="default",
        knowledge_store_backend="memory",
        run_persistence_backend="none",
        campaign_draft_persistence_backend="none",
        performance_event_persistence_backend="none",
        idempotency_backend="none",
        strategy_job_backend="memory",
        graph_checkpointer_backend="none",
        use_llm_planner=False,
        use_llm_critic=False,
        langsmith_tracing=False,
    )


def _brief_payload() -> dict[str, object]:
    return {
        "advertiser_id": "adv_fitness_001",
        "product_name": "FitTrack Pro",
        "product_category": "fitness app",
        "objective": "registrations",
        "budget": "2000.00",
        "currency": "USD",
        "duration_days": 14,
        "target_market": "United States",
        "primary_kpi": "trial registrations",
        "target_cpa": "20.00",
        "landing_page_url": "https://example.com/fittrack",
        "brand_voice": "motivational and practical",
        "constraints": [
            "Avoid unrealistic body transformation claims",
            "Do not imply medical outcomes",
        ],
        "known_audiences": [
            "Home workout beginners",
            "Wearable fitness tracker users",
        ],
        "historical_context": (
            "Previous organic posts performed best when showing short workout streaks "
            "and beginner-friendly progress tracking."
        ),
    }
