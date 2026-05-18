import json
from collections.abc import Callable, Sequence
from decimal import Decimal
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from ads_growth_agent.contracts import AdvertiserBrief, GrowthStrategyResponse
from ads_growth_agent.logging_config import log_evaluation_suite_completed
from ads_growth_agent.strategy import generate_mock_growth_strategy

REQUIRED_NODE_PATH = ["planner", "retriever", "tool_executor", "critic", "finalizer"]
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
    expected_revision_count: int = Field(default=0, ge=0)
    min_critic_score: float = Field(default=7.0, ge=0, le=10)
    max_budget: Decimal | None = Field(default=None, gt=0, decimal_places=2)
    require_draft_only: bool = True
    require_feedback_context: bool = True
    min_retrieved_source_count: int = Field(default=1, ge=0)
    min_retrieval_relevance: float = Field(default=0.3, ge=0, le=1)
    required_retrieved_source_ids: list[str] = Field(default_factory=list)
    required_retrieved_source_types: list[str] = Field(default_factory=list)


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
        evaluate_planner_orchestration,
        evaluate_budget_consistency,
        evaluate_tool_use_correctness,
        evaluate_strategy_completeness,
        evaluate_retrieval_grounding,
        evaluate_critic_quality_gate,
        evaluate_revision_behavior,
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


def evaluate_planner_orchestration(
    case: EvalCase,
    response: GrowthStrategyResponse,
) -> EvaluationScore:
    tool_names = [result.tool_name for result in response.tool_results]
    required_tools = case.expectations.required_tools
    checks = {
        "starts_with_planner": bool(response.node_path)
        and response.node_path[0] == "planner",
        "retriever_before_tool_executor": _appears_before(
            response.node_path,
            "retriever",
            "tool_executor",
        ),
        "tool_executor_before_critic": _appears_before(
            response.node_path,
            "tool_executor",
            "critic",
        ),
        "critic_before_finalizer": _appears_before(
            response.node_path,
            "critic",
            "finalizer",
        ),
        "required_tools_in_order": _contains_ordered_subsequence(tool_names, required_tools),
        "no_unexpected_initial_tools": set(tool_names).issuperset(required_tools),
    }
    passed = all(checks.values())
    return EvaluationScore(
        name="planner_orchestration",
        passed=passed,
        score=sum(1 for value in checks.values() if value) / len(checks),
        message="Planner orchestration follows the expected Phase 1 graph and tool plan."
        if passed
        else "Planner orchestration deviates from the expected node or tool order.",
        details={
            "node_path": response.node_path,
            "tool_names": tool_names,
            "required_tools": required_tools,
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
        "has_campaign_objective": strategy.campaign_objective.objective
        == case.brief.objective,
        "has_summary": bool(strategy.summary),
        "has_audience_strategy": bool(strategy.audience_strategy),
        "has_audience_segments": bool(strategy.audience_segments),
        "has_creative_strategy": bool(strategy.creative_strategy),
        "has_creative_tests": bool(strategy.creative_tests),
        "has_measurement_plan": bool(strategy.measurement_plan),
        "has_measurement_events": bool(strategy.measurement_events),
        "has_optimization_rules": bool(strategy.optimization_rules),
        "has_feedback_context": (not case.expectations.require_feedback_context)
        or strategy.feedback_context.strategy_id == strategy.strategy_id,
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


def evaluate_critic_quality_gate(
    case: EvalCase,
    response: GrowthStrategyResponse,
) -> EvaluationScore:
    critique = response.strategy.critique
    checks = {
        "critic_node_present": "critic" in response.node_path,
        "critic_precedes_finalizer": _appears_before(
            response.node_path,
            "critic",
            "finalizer",
        ),
        "critique_passed": critique.passed,
        "critic_score_meets_threshold": critique.score
        >= case.expectations.min_critic_score,
        "critique_has_rationale": bool(critique.rationale),
        "passing_critique_has_no_required_revisions": (
            not critique.passed or not critique.required_revisions
        ),
    }
    passed = all(checks.values())
    return EvaluationScore(
        name="critic_quality_gate",
        passed=passed,
        score=sum(1 for value in checks.values() if value) / len(checks),
        message="Critic quality gate passed before finalization."
        if passed
        else "Critic quality gate is missing, below threshold, or inconsistent.",
        details={
            "score": critique.score,
            "passed": critique.passed,
            "required_revisions": critique.required_revisions,
            "node_path": response.node_path,
            "checks": checks,
        },
    )


def evaluate_revision_behavior(
    case: EvalCase,
    response: GrowthStrategyResponse,
) -> EvaluationScore:
    revision_count = response.node_path.count("revision")
    has_revision_notes = any(
        "Critic revision" in item
        for item in [*response.strategy.measurement_plan, *response.strategy.assumptions]
    )
    checks = {
        "revision_count_matches_expectation": revision_count
        == case.expectations.expected_revision_count,
        "revision_routes_back_to_critic": revision_count == 0
        or _appears_before(response.node_path, "revision", "critic", start_after_first=True),
        "revision_context_recorded_when_used": revision_count == 0 or has_revision_notes,
        "finalizer_runs_after_revision_flow": response.node_path[-1:] == ["finalizer"],
    }
    passed = all(checks.values())
    return EvaluationScore(
        name="revision_behavior",
        passed=passed,
        score=sum(1 for value in checks.values() if value) / len(checks),
        message="Revision behavior matches the expected bounded critique loop."
        if passed
        else "Revision behavior does not match the expected bounded critique loop.",
        details={
            "expected_revision_count": case.expectations.expected_revision_count,
            "actual_revision_count": revision_count,
            "node_path": response.node_path,
            "checks": checks,
        },
    )


def evaluate_retrieval_grounding(
    case: EvalCase,
    response: GrowthStrategyResponse,
) -> EvaluationScore:
    retrieved_sources = [
        source
        for source in response.strategy.sources
        if source.source_type in {"rag_document", "historical_case", "advertiser_memory"}
    ]
    relevant_sources = [
        source
        for source in retrieved_sources
        if source.relevance >= case.expectations.min_retrieval_relevance
    ]
    relevant_source_ids = {source.source_id for source in relevant_sources}
    relevant_source_types = {source.source_type for source in relevant_sources}
    duplicate_source_ids = sorted(
        source_id
        for source_id in relevant_source_ids
        if [source.source_id for source in relevant_sources].count(source_id) > 1
    )
    missing_source_ids = sorted(
        set(case.expectations.required_retrieved_source_ids) - relevant_source_ids
    )
    missing_source_types = sorted(
        set(case.expectations.required_retrieved_source_types) - relevant_source_types
    )
    source_types = sorted({source.source_type for source in retrieved_sources})
    checks = {
        "has_min_retrieved_sources": len(relevant_sources)
        >= case.expectations.min_retrieved_source_count,
        "required_source_ids_present": not missing_source_ids,
        "required_source_types_present": not missing_source_types,
        "no_duplicate_source_ids": not duplicate_source_ids,
    }
    passed = all(checks.values())
    score = sum(1 for value in checks.values() if value) / len(checks)
    return EvaluationScore(
        name="retrieval_grounding",
        passed=passed,
        score=score,
        message="Strategy cites expected, relevant retrieved sources."
        if passed
        else "Strategy is missing expected retrieved sources or cites weak grounding.",
        details={
            "retrieved_source_count": len(retrieved_sources),
            "relevant_retrieved_source_count": len(relevant_sources),
            "min_retrieved_source_count": case.expectations.min_retrieved_source_count,
            "min_retrieval_relevance": case.expectations.min_retrieval_relevance,
            "retrieved_source_types": source_types,
            "relevant_source_types": sorted(relevant_source_types),
            "source_ids": [source.source_id for source in retrieved_sources],
            "relevant_source_ids": sorted(relevant_source_ids),
            "missing_source_ids": missing_source_ids,
            "missing_source_types": missing_source_types,
            "duplicate_source_ids": duplicate_source_ids,
            "checks": checks,
        },
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
        "execution_id_matches_run_id": metadata.execution_id == metadata.run_id,
        "strategy_id_matches_response": metadata.strategy_id == response.strategy.strategy_id,
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
            "execution_id": metadata.execution_id,
            "strategy_id": metadata.strategy_id,
            "trace_id": metadata.trace_id,
            "node_path": metadata.node_path,
            "checks": checks,
        },
    )


def _appears_before(
    items: Sequence[str],
    first: str,
    second: str,
    *,
    start_after_first: bool = False,
) -> bool:
    try:
        first_index = items.index(first)
        if start_after_first:
            second_index = items.index(second, first_index + 1)
        else:
            second_index = items.index(second)
    except ValueError:
        return False
    return first_index < second_index


def _contains_ordered_subsequence(items: Sequence[str], required: Sequence[str]) -> bool:
    cursor = 0
    for item in items:
        if cursor < len(required) and item == required[cursor]:
            cursor += 1
    return cursor == len(required)
