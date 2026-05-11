import json
from decimal import Decimal

from fastapi.testclient import TestClient
from typer.testing import CliRunner

from ads_growth_agent.api import app as api_app
from ads_growth_agent.cli import app as cli_app
from ads_growth_agent.contracts import AdvertiserBrief
from ads_growth_agent.strategy import generate_mock_growth_strategy


def test_generate_mock_growth_strategy_returns_valid_budget_plan() -> None:
    response = generate_mock_growth_strategy(AdvertiserBrief.model_validate(_brief_payload()))

    assert response.strategy.advertiser_id == "adv_fitness_001"
    assert response.strategy.budget_plan.allocated_budget <= Decimal("2000.00")
    assert response.strategy.actions
    assert len(response.tool_results) == 5
    assert response.node_path == ["planner", "retriever", "tool_executor", "critic", "finalizer"]
    assert response.run_metadata.tool_count == 5
    assert response.run_metadata.node_path == response.node_path
    assert all(result.success for result in response.tool_results)


def test_growth_strategy_api_returns_structured_strategy() -> None:
    client = TestClient(api_app)
    response = client.post("/growth-strategies", json={"brief": _brief_payload()})

    assert response.status_code == 200
    payload = response.json()
    strategy = payload["strategy"]
    allocations = strategy["budget_plan"]["allocations"]
    allocated_budget = sum(Decimal(str(item["amount"])) for item in allocations)

    assert strategy["advertiser_id"] == "adv_fitness_001"
    assert allocated_budget <= Decimal(str(strategy["budget_plan"]["total_budget"]))
    assert strategy["critique"]["passed"] is True
    assert payload["node_path"] == ["planner", "retriever", "tool_executor", "critic", "finalizer"]
    assert payload["run_metadata"]["tool_count"] == 5
    assert payload["run_metadata"]["node_path"] == payload["node_path"]
    assert payload["run_metadata"]["trace_id"].startswith("trace_")
    assert payload["tool_results"][0]["success"] is True


def test_plan_cli_accepts_brief_file(tmp_path) -> None:
    brief_file = tmp_path / "brief.json"
    brief_file.write_text(json.dumps(_brief_payload()))

    result = CliRunner().invoke(cli_app, ["plan", str(brief_file)])

    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["strategy"]["advertiser_id"] == "adv_fitness_001"
    assert payload["strategy"]["actions"]
    assert payload["run_metadata"]["tool_count"] == 5


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
        "brand_voice": "motivational and practical",
        "constraints": [
            "Avoid unrealistic body transformation claims",
            "Do not imply medical outcomes",
        ],
        "known_audiences": [
            "Home workout beginners",
            "Wearable fitness tracker users",
        ],
    }
