import json
from decimal import Decimal
from pathlib import Path

from typer.testing import CliRunner

from ads_growth_agent.cli import app as cli_app
from ads_growth_agent.contracts import AdvertiserBrief
from ads_growth_agent.evaluation import (
    EvalCase,
    evaluate_budget_consistency,
    evaluate_critic_quality_gate,
    evaluate_growth_strategy,
    evaluate_planner_orchestration,
    evaluate_retrieval_grounding,
    evaluate_revision_behavior,
    load_eval_cases,
    run_local_eval_suite,
)
from ads_growth_agent.strategy import generate_mock_growth_strategy


def test_load_eval_cases_from_examples() -> None:
    cases = load_eval_cases(Path("examples/eval_cases.json"))

    assert [case.case_id for case in cases] == [
        "fitness_app_registrations",
        "skincare_purchase_growth",
        "saas_lead_generation",
    ]
    assert cases[0].brief.objective == "registrations"


def test_local_eval_suite_passes_curated_cases() -> None:
    cases = load_eval_cases(Path("examples/eval_cases.json"))
    report = run_local_eval_suite(cases)

    assert report.total_cases == 3
    assert report.passed_cases == 3
    assert report.failed_cases == 0
    assert report.pass_rate == 1
    assert all(report.passed for report in report.reports)


def test_evaluation_report_contains_expected_score_names() -> None:
    case = load_eval_cases(Path("examples/eval_cases.json"))[0]
    response = generate_mock_growth_strategy(case.brief)

    report = evaluate_growth_strategy(case, response)

    assert report.passed is True
    assert [score.name for score in report.scores] == [
        "planner_orchestration",
        "budget_consistency",
        "tool_use_correctness",
        "strategy_completeness",
        "retrieval_grounding",
        "critic_quality_gate",
        "revision_behavior",
        "draft_only_safety",
        "observability_metadata",
    ]


def test_planner_orchestration_evaluator_passes_expected_phase1_flow() -> None:
    case = load_eval_cases(Path("examples/eval_cases.json"))[0]
    response = generate_mock_growth_strategy(case.brief)

    score = evaluate_planner_orchestration(case, response)

    assert score.passed is True
    assert score.details["checks"]["starts_with_planner"] is True
    assert score.details["checks"]["required_tools_in_order"] is True


def test_critic_quality_gate_evaluator_requires_passing_critique() -> None:
    case = load_eval_cases(Path("examples/eval_cases.json"))[0]
    response = generate_mock_growth_strategy(case.brief)

    score = evaluate_critic_quality_gate(case, response)

    assert score.passed is True
    assert score.details["passed"] is True
    assert score.details["score"] >= 7


def test_revision_behavior_evaluator_expects_no_revision_for_deterministic_cases() -> None:
    case = load_eval_cases(Path("examples/eval_cases.json"))[0]
    response = generate_mock_growth_strategy(case.brief)

    score = evaluate_revision_behavior(case, response)

    assert score.passed is True
    assert score.details["expected_revision_count"] == 0
    assert score.details["actual_revision_count"] == 0


def test_budget_evaluator_fails_when_case_budget_is_lower_than_strategy_budget() -> None:
    response = generate_mock_growth_strategy(_brief(budget=Decimal("2000.00")))
    under_budget_case = EvalCase(
        case_id="under_budget_case",
        description="Case budget is intentionally lower than generated strategy budget.",
        brief=_brief(budget=Decimal("100.00")),
    )

    score = evaluate_budget_consistency(under_budget_case, response)

    assert score.passed is False
    assert score.score == 0
    assert score.details["max_budget"] == "100.00"


def test_retrieval_evaluator_requires_expected_source_ids() -> None:
    case = EvalCase(
        case_id="missing_source_case",
        description="Require a source that the generated strategy will not cite.",
        brief=_brief(budget=Decimal("2000.00")),
        expectations={
            "required_retrieved_source_ids": ["rag:missing:source"],
            "required_retrieved_source_types": ["rag_document"],
        },
    )
    response = generate_mock_growth_strategy(case.brief)

    score = evaluate_retrieval_grounding(case, response)

    assert score.passed is False
    assert score.details["missing_source_ids"] == ["rag:missing:source"]
    assert score.details["checks"]["required_source_ids_present"] is False


def test_eval_cli_outputs_suite_report() -> None:
    result = CliRunner().invoke(cli_app, ["eval", "examples/eval_cases.json"])

    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["suite_id"] == "local_eval_v0_1"
    assert payload["total_cases"] == 3
    assert payload["passed_cases"] == 3


def _brief(*, budget: Decimal) -> AdvertiserBrief:
    return AdvertiserBrief(
        advertiser_id="adv_eval_budget",
        product_name="Budget Check App",
        product_category="fitness app",
        objective="registrations",
        budget=budget,
        currency="USD",
        duration_days=14,
        target_market="United States",
        primary_kpi="trial registrations",
        target_cpa=Decimal("20.00"),
    )
