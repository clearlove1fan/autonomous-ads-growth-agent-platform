import json
from collections.abc import Callable, Sequence
from decimal import Decimal
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from ads_growth_agent.contracts import AdvertiserBrief, GrowthStrategyResponse
from ads_growth_agent.logging_config import log_evaluation_suite_completed
from ads_growth_agent.strategy import generate_mock_growth_strategy

REQUIRED_NODE_PATH = ["planner", "tool_executor", "critic", "finalizer"]
REQUIRED_TOOLS = [
    "recommend_audience",
    "generate_creative_brief",
    "optimize_budget",
    "estimate_performance",
    "create_campaign_draft",
]
DRAFT_SAFE_TOOLS = {
    "recommend_audience",
    "generate_creative_brief",
    "optimize_budget",
    "estimate_performance",
    "create_campaign_draft",
}


class EvalExpectations(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    required_tools: list[str] = Field(default_factory=lambda: list(REQUIRED_TOOLS))
    required_node_path: list[str] = Field(default_factory=lambda: list(REQUIRED_NODE_PATH))
    max_budget: Decimal | None = Field(default=None, gt=0, decimal_places=2)
    require_draft_only: bool = True


class EvalCase(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    case_id: str = Field(min_length=1, max_length=128)
    description: str = Field(min_length=1, max_length=500)
    brief: AdvertiserBrief
    expectations: EvalExpectations = Field(default_factory=EvalExpectations)


class EvaluationScore(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    name: str = Field(min_length=1, max_length=120)
    passed: bool
    score: float = Field(ge=0, le=1)
    message: str = Field(min_length=1, max_length=500)
    details: dict[str, Any] = Field(default_factory=dict)


class EvaluationReport(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    case_id: str = Field(min_length=1, max_length=128)
    passed: bool
    overall_score: float = Field(ge=0, le=1)
    strategy_id: str = Field(min_length=1, max_length=128)
    trace_id: str = Field(min_length=1, max_length=128)
    scores: list[EvaluationScore] = Field(min_length=1)


class EvaluationSuiteReport(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    suite_id: str = Field(min_length=1, max_length=128)
    total_cases: int = Field(ge=0)
    passed_cases: int = Field(ge=0)
    failed_cases: int = Field(ge=0)
    pass_rate: float = Field(ge=0, le=1)
    reports: list[EvaluationReport] = Field(default_factory=list)


Evaluator = Callable[[EvalCase, GrowthStrategyResponse], EvaluationScore]


def load_eval_cases(path: Path) -> list[EvalCase]:
    payload = json.loads(path.read_text())
    raw_cases = payload["cases"] if isinstance(payload, dict) and "cases" in payload else payload
    return [EvalCase.model_validate(raw_case) for raw_case in raw_cases]


def run_local_eval_suite(
    cases: Sequence[EvalCase],
    *,
    suite_id: str = "local_eval_v0_1",
) -> EvaluationSuiteReport:
    reports = [
        evaluate_growth_strategy(case, generate_mock_growth_strategy(case.brief))
        for case in cases
    ]
    passed_cases = sum(1 for report in reports if report.passed)
    total_cases = len(reports)
    report = EvaluationSuiteReport(
        suite_id=suite_id,
        total_cases=total_cases,
        passed_cases=passed_cases,
        failed_cases=total_cases - passed_cases,
        pass_rate=passed_cases / total_cases if total_cases else 0,
        reports=reports,
    )
    log_evaluation_suite_completed(report)
    return report


def evaluate_growth_strategy(
    case: EvalCase,
    response: GrowthStrategyResponse,
    evaluators: Sequence[Evaluator] | None = None,
) -> EvaluationReport:
    evaluators = evaluators or [
        evaluate_budget_consistency,
        evaluate_tool_use_correctness,
        evaluate_strategy_completeness,
        evaluate_draft_only_safety,
        evaluate_observability_metadata,
    ]
    scores = [evaluator(case, response) for evaluator in evaluators]
    passed = all(score.passed for score in scores)
    overall_score = sum(score.score for score in scores) / len(scores)
    return EvaluationReport(
        case_id=case.case_id,
        passed=passed,
        overall_score=round(overall_score, 4),
        strategy_id=response.strategy.strategy_id,
        trace_id=response.run_metadata.trace_id,
        scores=scores,
    )


def evaluate_budget_consistency(
    case: EvalCase,
    response: GrowthStrategyResponse,
) -> EvaluationScore:
    budget_plan = response.strategy.budget_plan
    max_budget = case.expectations.max_budget or case.brief.budget
    allocated = budget_plan.allocated_budget
    checks = {
        "allocation_within_budget": allocated <= max_budget,
        "strategy_total_within_budget": budget_plan.total_budget <= max_budget,
        "currency_matches_brief": budget_plan.currency == case.brief.currency,
    }
    passed = all(checks.values())
    return EvaluationScore(
        name="budget_consistency",
        passed=passed,
        score=1.0 if passed else 0.0,
        message="Budget allocations are internally consistent."
        if passed
        else "Budget plan exceeds the allowed advertiser budget or currency contract.",
        details={
            "allocated_budget": str(allocated),
            "strategy_total_budget": str(budget_plan.total_budget),
            "max_budget": str(max_budget),
            "currency": budget_plan.currency,
            "checks": checks,
        },
    )


def evaluate_tool_use_correctness(
    case: EvalCase,
    response: GrowthStrategyResponse,
) -> EvaluationScore:
    tool_names = [result.tool_name for result in response.tool_results]
    successful_tools = {result.tool_name for result in response.tool_results if result.success}
    required_tools = set(case.expectations.required_tools)
    missing_tools = sorted(required_tools - successful_tools)
    failed_tools = sorted(
        result.tool_name for result in response.tool_results if not result.success
    )
    metadata_count_matches = response.run_metadata.tool_count == len(response.tool_results)
    passed = not missing_tools and not failed_tools and metadata_count_matches
    return EvaluationScore(
        name="tool_use_correctness",
        passed=passed,
        score=1.0 if passed else 0.0,
        message="Required tools were called successfully."
        if passed
        else "Tool execution was incomplete, failed, or inconsistent with metadata.",
        details={
            "tool_names": tool_names,
            "missing_tools": missing_tools,
            "failed_tools": failed_tools,
            "metadata_tool_count": response.run_metadata.tool_count,
        },
    )


def evaluate_strategy_completeness(
    case: EvalCase,
    response: GrowthStrategyResponse,
) -> EvaluationScore:
    strategy = response.strategy
    checks = {
        "objective_matches": strategy.objective == case.brief.objective,
        "has_summary": bool(strategy.summary),
        "has_audience_strategy": bool(strategy.audience_strategy),
        "has_creative_strategy": bool(strategy.creative_strategy),
        "has_measurement_plan": bool(strategy.measurement_plan),
        "has_actions": bool(strategy.actions),
        "has_success_metrics": bool(strategy.success_metrics),
        "has_sources": bool(strategy.sources),
        "critique_passed": strategy.critique.passed,
    }
    passed = all(checks.values())
    return EvaluationScore(
        name="strategy_completeness",
        passed=passed,
        score=sum(1 for value in checks.values() if value) / len(checks),
        message="Strategy contains the required structured sections."
        if passed
        else "Strategy is missing one or more required sections.",
        details={"checks": checks},
    )


def evaluate_draft_only_safety(
    case: EvalCase,
    response: GrowthStrategyResponse,
) -> EvaluationScore:
    if not case.expectations.require_draft_only:
        return EvaluationScore(
            name="draft_only_safety",
            passed=True,
            score=1.0,
            message="Draft-only safety check was not required for this case.",
        )

    action_tools = {action.tool_name for action in response.strategy.actions if action.tool_name}
    draft_payloads = [
        result.payload
        for result in response.tool_results
        if result.tool_name == "create_campaign_draft" and result.success
    ]
    draft_statuses = [payload.get("status") for payload in draft_payloads]
    safety_notes = [payload.get("safety_note", "") for payload in draft_payloads]
    checks = {
        "actions_use_draft_safe_tools": action_tools.issubset(DRAFT_SAFE_TOOLS),
        "campaign_draft_created": bool(draft_payloads),
        "campaign_status_is_draft": bool(draft_statuses)
        and all(status == "draft" for status in draft_statuses),
        "safety_note_blocks_live_mutation": bool(safety_notes)
        and all("No live" in note and "spend mutation" in note for note in safety_notes),
    }
    passed = all(checks.values())
    return EvaluationScore(
        name="draft_only_safety",
        passed=passed,
        score=sum(1 for value in checks.values() if value) / len(checks),
        message="Strategy remains draft-only and avoids live spend mutation."
        if passed
        else "Strategy may imply live launch or unsafe spend mutation.",
        details={
            "action_tools": sorted(action_tools),
            "draft_statuses": draft_statuses,
            "checks": checks,
        },
    )


def evaluate_observability_metadata(
    case: EvalCase,
    response: GrowthStrategyResponse,
) -> EvaluationScore:
    metadata = response.run_metadata
    checks = {
        "run_id_matches_strategy_id": metadata.run_id == response.strategy.strategy_id,
        "trace_id_present": metadata.trace_id.startswith("trace_"),
        "node_path_matches_response": metadata.node_path == response.node_path,
        "node_path_matches_expected": metadata.node_path == case.expectations.required_node_path,
        "failed_tool_count_matches": metadata.failed_tool_count
        == sum(1 for result in response.tool_results if not result.success),
    }
    passed = all(checks.values())
    return EvaluationScore(
        name="observability_metadata",
        passed=passed,
        score=sum(1 for value in checks.values() if value) / len(checks),
        message="Run metadata is complete and internally consistent."
        if passed
        else "Run metadata is missing or inconsistent.",
        details={
            "run_id": metadata.run_id,
            "trace_id": metadata.trace_id,
            "node_path": metadata.node_path,
            "checks": checks,
        },
    )
