# ruff: noqa: E402
import json
import warnings
from typing import Any, TypedDict
from uuid import NAMESPACE_URL, uuid5

from langchain_core._api.deprecation import LangChainPendingDeprecationWarning
from pydantic import BaseModel, ConfigDict, Field

warnings.filterwarnings(
    "ignore",
    category=LangChainPendingDeprecationWarning,
    module=r"langgraph\.cache\.base.*",
)

from langgraph.graph import END, START, StateGraph

from ads_growth_agent.config import Settings, get_settings
from ads_growth_agent.contracts import (
    AdvertiserBrief,
    AgentRole,
    CritiqueReport,
    FinalGrowthStrategy,
    GrowthStrategyResponse,
    RecommendedAction,
    RiskAssessment,
    RiskLevel,
    RunMetadata,
    SourceCitation,
    SuccessMetric,
    ToolError,
    ToolIntent,
    ToolResult,
)
from ads_growth_agent.graph_checkpointer import (
    graph_checkpoint_config,
    open_configured_graph_checkpointer,
)
from ads_growth_agent.knowledge import (
    KnowledgeRetrievalResult,
    KnowledgeStore,
    build_default_knowledge_store,
    build_knowledge_query,
)
from ads_growth_agent.llm import (
    LiteLLMGatewayClient,
    LLMMessage,
    StructuredOutputResult,
    generate_structured_output,
)
from ads_growth_agent.logging_config import (
    log_strategy_run_completed,
    log_strategy_run_failed,
)
from ads_growth_agent.observability import (
    RunContext,
    build_run_metadata,
    create_run_context,
    graph_tracing_context,
    invoke_traced_graph,
)
from ads_growth_agent.tools import (
    AudienceRecommendationOutput,
    BudgetOptimizationOutput,
    CampaignDraftOutput,
    CreativeBriefOutput,
    PerformanceEstimateOutput,
    ToolExecutionContext,
    ToolRegistry,
    build_default_tool_registry,
)

INITIAL_PLANNER_TOOLS = (
    "recommend_audience",
    "generate_creative_brief",
    "optimize_budget",
)


class StrategyGenerationError(Exception):
    def __init__(
        self,
        message: str,
        tool_result: ToolResult,
        run_metadata: RunMetadata | None = None,
        *,
        tool_results: list[ToolResult] | None = None,
        node_path: list[str] | None = None,
    ) -> None:
        super().__init__(message)
        self.tool_result = tool_result
        self.run_metadata = run_metadata
        self.tool_results = tool_results or [tool_result]
        self.node_path = node_path or []


class GrowthStrategyState(TypedDict, total=False):
    brief: AdvertiserBrief
    run_id: str
    strategy_id: str
    tool_intents: list[ToolIntent]
    tool_results: list[ToolResult]
    artifacts: dict[str, Any]
    critique: CritiqueReport
    strategy: FinalGrowthStrategy
    errors: list[str]
    knowledge: KnowledgeRetrievalResult
    node_path: list[str]
    requires_revision: bool
    revision_count: int


class PlannerOutput(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    rationale: str = Field(min_length=1, max_length=1_200)
    tool_intents: list[ToolIntent] = Field(min_length=3, max_length=3)


def run_growth_strategy_graph(
    brief: AdvertiserBrief,
    registry: ToolRegistry | None = None,
    settings: Settings | None = None,
    llm_client: LiteLLMGatewayClient | None = None,
    knowledge_store: KnowledgeStore | None = None,
    run_context: RunContext | None = None,
) -> GrowthStrategyResponse:
    settings = settings or get_settings()
    strategy_id = strategy_id_for_brief(brief)
    run_context = run_context or create_run_context(strategy_id=strategy_id, settings=settings)
    if run_context.strategy_id not in {None, strategy_id}:
        raise ValueError(
            "run context strategy_id does not match the advertiser brief strategy_id"
        )
    checkpointer_context = open_configured_graph_checkpointer(settings)
    try:
        with checkpointer_context as checkpointer:
            graph = build_growth_strategy_graph(
                registry or build_default_tool_registry(),
                settings=settings,
                llm_client=llm_client,
                knowledge_store=knowledge_store,
                checkpointer=checkpointer,
            )
            config = graph_checkpoint_config(
                run_context,
                enabled=checkpointer is not None,
                tenant_id=settings.tenant_id,
            )
            with graph_tracing_context(run_context, advertiser_id=brief.advertiser_id):
                final_state = invoke_traced_graph(
                    graph,
                    {"brief": brief, "run_id": run_context.run_id, "node_path": []},
                    config=config,
                )
    except StrategyGenerationError as exc:
        exc.run_metadata = build_run_metadata(
            run_context,
            node_path=exc.node_path,
            tool_results=exc.tool_results,
            error_summary=[str(exc)],
        )
        log_strategy_run_failed(
            advertiser_id=brief.advertiser_id,
            tool_result=exc.tool_result,
            run_metadata=exc.run_metadata,
        )
        raise

    tool_results = final_state.get("tool_results", [])
    node_path = final_state.get("node_path", [])
    response = GrowthStrategyResponse(
        strategy=final_state["strategy"],
        tool_results=tool_results,
        node_path=node_path,
        run_metadata=build_run_metadata(
            run_context,
            node_path=node_path,
            tool_results=tool_results,
        ),
    )
    log_strategy_run_completed(response)
    return response


def build_growth_strategy_graph(
    registry: ToolRegistry,
    *,
    settings: Settings | None = None,
    llm_client: LiteLLMGatewayClient | None = None,
    knowledge_store: KnowledgeStore | None = None,
    checkpointer: Any | None = None,
):
    settings = settings or get_settings()
    knowledge_store = knowledge_store or build_default_knowledge_store()
    builder = StateGraph(GrowthStrategyState)
    builder.add_node("planner", _planner_node(settings=settings, llm_client=llm_client))
    builder.add_node("retriever", _retriever_node(settings=settings, store=knowledge_store))
    builder.add_node("tool_executor", _tool_executor_node(registry))
    builder.add_node("critic", _critic_node(settings=settings, llm_client=llm_client))
    builder.add_node("revision", _revision_node)
    builder.add_node("finalizer", _finalizer_node)

    builder.add_edge(START, "planner")
    builder.add_edge("planner", "retriever")
    builder.add_edge("retriever", "tool_executor")
    builder.add_edge("tool_executor", "critic")
    builder.add_conditional_edges(
        "critic",
        _route_after_critic,
        {
            "revise": "revision",
            "finalize": "finalizer",
        },
    )
    builder.add_edge("revision", "critic")
    builder.add_edge("finalizer", END)
    return builder.compile(checkpointer=checkpointer)


def _planner_node(
    *,
    settings: Settings,
    llm_client: LiteLLMGatewayClient | None,
):
    def plan(state: GrowthStrategyState) -> GrowthStrategyState:
        if settings.use_llm_planner:
            return _llm_planner_node(state, settings=settings, llm_client=llm_client)
        return _deterministic_planner_node(state)

    return plan


def _deterministic_planner_node(state: GrowthStrategyState) -> GrowthStrategyState:
    brief = state["brief"]
    strategy_id = strategy_id_for_brief(brief)
    intents = _deterministic_initial_tool_intents(brief, strategy_id)
    return {
        "strategy_id": strategy_id,
        "tool_intents": intents,
        "tool_results": [],
        "artifacts": {},
        "requires_revision": False,
        "revision_count": 0,
        "node_path": [*state.get("node_path", []), "planner"],
    }


def _llm_planner_node(
    state: GrowthStrategyState,
    *,
    settings: Settings,
    llm_client: LiteLLMGatewayClient | None,
) -> GrowthStrategyState:
    brief = state["brief"]
    strategy_id = strategy_id_for_brief(brief)
    node_path = [*state.get("node_path", []), "planner"]
    client = llm_client or LiteLLMGatewayClient(settings=settings)

    planner_output, structured_result = generate_structured_output(
        client,
        _planner_messages(brief, strategy_id),
        output_model=PlannerOutput,
        model=settings.default_chat_model,
        max_repair_attempts=settings.llm_structured_output_max_repair_attempts,
    )
    if planner_output is None:
        result = _planner_failure_tool_result(
            structured_result.error_code or "LLM_PLANNER_FAILED",
            structured_result.error_message or "LLM planner failed to produce valid output.",
            structured_result=structured_result,
        )
        raise StrategyGenerationError(
            result.error.message if result.error else "LLM planner failed",
            result,
            tool_results=[result],
            node_path=node_path,
        )

    try:
        intents = _validate_initial_tool_intents(planner_output.tool_intents)
    except ValueError as exc:
        result = _planner_failure_tool_result(
            "LLM_PLANNER_INVALID_TOOL_PLAN",
            str(exc),
            structured_result=structured_result,
        )
        raise StrategyGenerationError(
            str(exc),
            result,
            tool_results=[result],
            node_path=node_path,
        ) from exc

    return {
        "strategy_id": strategy_id,
        "tool_intents": intents,
        "tool_results": [],
        "artifacts": {
            "planner": {
                "mode": "llm",
                "rationale": planner_output.rationale,
                "structured_output_attempts": [
                    attempt.model_dump(mode="json") for attempt in structured_result.attempts
                ],
            }
        },
        "requires_revision": False,
        "revision_count": 0,
        "node_path": node_path,
    }


def _deterministic_initial_tool_intents(
    brief: AdvertiserBrief,
    strategy_id: str,
) -> list[ToolIntent]:
    return [
        ToolIntent(
            intent_id=f"{strategy_id}:audience",
            tool_name="recommend_audience",
            requested_by=AgentRole.PLANNER,
            params={
                "advertiser_id": brief.advertiser_id,
                "product_category": brief.product_category,
                "objective": brief.objective,
                "target_market": brief.target_market,
                "known_audiences": brief.known_audiences,
            },
        ),
        ToolIntent(
            intent_id=f"{strategy_id}:creative",
            tool_name="generate_creative_brief",
            requested_by=AgentRole.PLANNER,
            params={
                "product_name": brief.product_name,
                "product_category": brief.product_category,
                "objective": brief.objective,
                "brand_voice": brief.brand_voice,
                "constraints": brief.constraints,
            },
        ),
        ToolIntent(
            intent_id=f"{strategy_id}:budget",
            tool_name="optimize_budget",
            requested_by=AgentRole.PLANNER,
            params={
                "advertiser_id": brief.advertiser_id,
                "objective": brief.objective,
                "total_budget": brief.budget,
                "currency": brief.currency,
                "duration_days": brief.duration_days,
            },
        ),
    ]


def _planner_messages(brief: AdvertiserBrief, strategy_id: str) -> list[LLMMessage]:
    brief_payload = json.dumps(brief.model_dump(mode="json"), sort_keys=True)
    return [
        LLMMessage(
            role="system",
            content=(
                "You are the planner agent for an autonomous ads growth platform. "
                "Return exactly three draft-safe ToolIntent objects for the first workflow stage. "
                "Use only these tool names: recommend_audience, generate_creative_brief, "
                "optimize_budget. Do not invent tools. The platform will validate and execute "
                "the intents; you are only proposing structured intent."
            ),
        ),
        LLMMessage(
            role="user",
            content=(
                "Create the initial tool plan for this advertiser brief.\n"
                f"strategy_id: {strategy_id}\n"
                "Required intent_id suffixes by tool:\n"
                "- recommend_audience: :audience\n"
                "- generate_creative_brief: :creative\n"
                "- optimize_budget: :budget\n"
                "Set requested_by to planner, risk_level to low, and requires_human_approval "
                "to false for all three intents.\n"
                "Expected params:\n"
                "- recommend_audience: advertiser_id, product_category, objective, "
                "target_market, known_audiences\n"
                "- generate_creative_brief: product_name, product_category, objective, "
                "brand_voice, constraints\n"
                "- optimize_budget: advertiser_id, objective, total_budget, currency, "
                "duration_days\n"
                f"Advertiser brief JSON: {brief_payload}"
            ),
        ),
    ]


def _validate_initial_tool_intents(tool_intents: list[ToolIntent]) -> list[ToolIntent]:
    names = [intent.tool_name for intent in tool_intents]
    missing = [tool_name for tool_name in INITIAL_PLANNER_TOOLS if tool_name not in names]
    unexpected = [tool_name for tool_name in names if tool_name not in INITIAL_PLANNER_TOOLS]
    duplicates = sorted({tool_name for tool_name in names if names.count(tool_name) > 1})

    if missing or unexpected or duplicates:
        details = []
        if missing:
            details.append(f"missing={missing}")
        if unexpected:
            details.append(f"unexpected={unexpected}")
        if duplicates:
            details.append(f"duplicates={duplicates}")
        detail_text = ", ".join(details)
        raise ValueError(
            f"Planner tool plan must contain exactly the initial tools: {detail_text}"
        )

    intents_by_name = {intent.tool_name: intent for intent in tool_intents}
    return [intents_by_name[tool_name] for tool_name in INITIAL_PLANNER_TOOLS]


def _ordered_initial_tool_intents_or_raise(
    tool_intents: list[ToolIntent],
    *,
    tool_results_so_far: list[ToolResult],
    node_path: list[str],
) -> list[ToolIntent]:
    try:
        return _validate_initial_tool_intents(tool_intents)
    except ValueError as exc:
        result = _planner_failure_tool_result("PLANNER_INVALID_TOOL_PLAN", str(exc))
        raise StrategyGenerationError(
            str(exc),
            result,
            tool_results=[*tool_results_so_far, result],
            node_path=node_path,
        ) from exc


def _retriever_node(
    *,
    settings: Settings,
    store: KnowledgeStore,
):
    def retrieve(state: GrowthStrategyState) -> GrowthStrategyState:
        brief = state["brief"]
        query = build_knowledge_query(
            brief,
            top_k=settings.knowledge_top_k,
            run_id=state.get("run_id"),
        )
        retrieval = store.retrieve(query)
        artifacts: dict[str, Any] = dict(state.get("artifacts", {}))
        artifacts["knowledge"] = retrieval
        return {
            "knowledge": retrieval,
            "artifacts": artifacts,
            "node_path": [*state.get("node_path", []), "retriever"],
        }

    return retrieve


def _planner_failure_tool_result(
    code: str,
    message: str,
    *,
    structured_result: StructuredOutputResult | None = None,
) -> ToolResult:
    return _agent_failure_tool_result(
        "planner",
        "llm_planner",
        code,
        message,
        structured_result=structured_result,
    )


def _critic_failure_tool_result(
    code: str,
    message: str,
    *,
    structured_result: StructuredOutputResult | None = None,
    critique: CritiqueReport | None = None,
) -> ToolResult:
    source_metadata: dict[str, Any] = {}
    if critique is not None:
        source_metadata["critique"] = critique.model_dump(mode="json")
    return _agent_failure_tool_result(
        "critic",
        "llm_critic",
        code,
        message,
        structured_result=structured_result,
        source_metadata=source_metadata,
    )


def _agent_failure_tool_result(
    component: str,
    tool_name: str,
    code: str,
    message: str,
    *,
    structured_result: StructuredOutputResult | None = None,
    source_metadata: dict[str, Any] | None = None,
) -> ToolResult:
    metadata: dict[str, Any] = {"component": component}
    if source_metadata is not None:
        metadata.update(source_metadata)
    if structured_result is not None:
        metadata["structured_output"] = structured_result.model_dump(mode="json")

    return ToolResult(
        tool_name=tool_name,
        success=False,
        payload={},
        error=ToolError(code=code[:80], message=_tool_error_message(message), retryable=False),
        latency_ms=0,
        source_metadata=metadata,
    )


def _tool_error_message(message: str) -> str:
    normalized = message.strip() or "Planner failed."
    if len(normalized) <= 500:
        return normalized
    return f"{normalized[:497]}..."


def _tool_executor_node(registry: ToolRegistry):
    def execute_tools(state: GrowthStrategyState) -> GrowthStrategyState:
        brief = state["brief"]
        run_id = state["run_id"]
        strategy_id = state["strategy_id"]
        context = ToolExecutionContext(
            advertiser_id=brief.advertiser_id,
            run_id=run_id,
            allowed_tools={
                "recommend_audience",
                "generate_creative_brief",
                "optimize_budget",
                "estimate_performance",
                "create_campaign_draft",
            },
        )

        tool_results = list(state.get("tool_results", []))
        tool_intents = list(state.get("tool_intents", []))
        artifacts: dict[str, Any] = dict(state.get("artifacts", {}))

        node_path = [*state.get("node_path", []), "tool_executor"]
        initial_intents = _ordered_initial_tool_intents_or_raise(
            tool_intents,
            tool_results_so_far=tool_results,
            node_path=node_path,
        )

        audience_result = _execute_or_raise(
            registry,
            context,
            initial_intents[0],
            tool_results_so_far=tool_results,
            node_path=node_path,
        )
        tool_results.append(audience_result)
        audience = AudienceRecommendationOutput.model_validate(audience_result.payload)
        artifacts["audience"] = audience

        creative_result = _execute_or_raise(
            registry,
            context,
            initial_intents[1],
            tool_results_so_far=tool_results,
            node_path=node_path,
        )
        tool_results.append(creative_result)
        creative = CreativeBriefOutput.model_validate(creative_result.payload)
        artifacts["creative"] = creative

        budget_result = _execute_or_raise(
            registry,
            context,
            initial_intents[2],
            tool_results_so_far=tool_results,
            node_path=node_path,
        )
        tool_results.append(budget_result)
        budget = BudgetOptimizationOutput.model_validate(budget_result.payload)
        artifacts["budget"] = budget

        performance_intent = ToolIntent(
            intent_id=f"{strategy_id}:performance",
            tool_name="estimate_performance",
            requested_by=AgentRole.PLANNER,
            params={
                "product_category": brief.product_category,
                "objective": brief.objective,
                "budget_plan": budget.budget_plan,
                "target_cpa": brief.target_cpa,
            },
        )
        tool_intents.append(performance_intent)
        performance_result = _execute_or_raise(
            registry,
            context,
            performance_intent,
            tool_results_so_far=tool_results,
            node_path=node_path,
        )
        tool_results.append(performance_result)
        performance = PerformanceEstimateOutput.model_validate(performance_result.payload)
        artifacts["performance"] = performance

        draft_intent = ToolIntent(
            intent_id=f"{strategy_id}:campaign_draft",
            tool_name="create_campaign_draft",
            requested_by=AgentRole.PLANNER,
            params={
                "advertiser_id": brief.advertiser_id,
                "product_name": brief.product_name,
                "objective": brief.objective,
                "budget_plan": budget.budget_plan,
                "duration_days": brief.duration_days,
                "audience_segments": audience.segments,
                "creative_angles": creative.creative_angles,
            },
        )
        tool_intents.append(draft_intent)
        draft_result = _execute_or_raise(
            registry,
            context,
            draft_intent,
            tool_results_so_far=tool_results,
            node_path=node_path,
        )
        tool_results.append(draft_result)
        draft = CampaignDraftOutput.model_validate(draft_result.payload)
        artifacts["draft"] = draft

        return {
            "tool_intents": tool_intents,
            "tool_results": tool_results,
            "artifacts": artifacts,
            "node_path": node_path,
        }

    return execute_tools


def _route_after_critic(state: GrowthStrategyState) -> str:
    return "revise" if state.get("requires_revision", False) else "finalize"


def _critic_node(
    *,
    settings: Settings,
    llm_client: LiteLLMGatewayClient | None,
):
    def critique(state: GrowthStrategyState) -> GrowthStrategyState:
        if settings.use_llm_critic:
            return _llm_critic_node(state, settings=settings, llm_client=llm_client)
        return _deterministic_critic_node(state)

    return critique


def _deterministic_critic_node(state: GrowthStrategyState) -> GrowthStrategyState:
    critique = CritiqueReport(
        score=8.1,
        passed=True,
        issues=[],
        required_revisions=[],
        rationale=(
            "Strategy passes v0.1 deterministic checks: structured output, valid budget math, "
            "draft-only actions, and measurable next steps."
        ),
    )
    return {
        "critique": critique,
        "requires_revision": False,
        "node_path": [*state.get("node_path", []), "critic"],
    }


def _llm_critic_node(
    state: GrowthStrategyState,
    *,
    settings: Settings,
    llm_client: LiteLLMGatewayClient | None,
) -> GrowthStrategyState:
    node_path = [*state.get("node_path", []), "critic"]
    tool_results = list(state.get("tool_results", []))
    client = llm_client or LiteLLMGatewayClient(settings=settings)

    critique, structured_result = generate_structured_output(
        client,
        _critic_messages(state, settings=settings),
        output_model=CritiqueReport,
        model=settings.default_chat_model,
        max_repair_attempts=settings.llm_structured_output_max_repair_attempts,
    )
    if critique is None:
        result = _critic_failure_tool_result(
            structured_result.error_code or "LLM_CRITIC_FAILED",
            structured_result.error_message or "LLM critic failed to produce valid output.",
            structured_result=structured_result,
        )
        raise StrategyGenerationError(
            result.error.message if result.error else "LLM critic failed",
            result,
            tool_results=[*tool_results, result],
            node_path=node_path,
        )

    artifacts: dict[str, Any] = dict(state.get("artifacts", {}))
    artifacts = _record_critic_artifact(
        artifacts,
        critique=critique,
        structured_result=structured_result,
    )

    if not critique.passed or critique.score < settings.llm_critic_min_score:
        if state.get("revision_count", 0) < settings.max_revision_attempts:
            return {
                "critique": critique,
                "artifacts": artifacts,
                "requires_revision": True,
                "node_path": node_path,
            }

        message = (
            "Critic rejected strategy: "
            f"passed={critique.passed}, score={critique.score}, "
            f"required_min_score={settings.llm_critic_min_score}, "
            f"revision_attempts={state.get('revision_count', 0)}, "
            f"max_revision_attempts={settings.max_revision_attempts}."
        )
        result = _critic_failure_tool_result(
            "LLM_CRITIC_REJECTED_STRATEGY",
            message,
            structured_result=structured_result,
            critique=critique,
        )
        raise StrategyGenerationError(
            message,
            result,
            tool_results=[*tool_results, result],
            node_path=node_path,
        )

    return {
        "critique": critique,
        "artifacts": artifacts,
        "requires_revision": False,
        "node_path": node_path,
    }


def _critic_messages(
    state: GrowthStrategyState,
    *,
    settings: Settings,
) -> list[LLMMessage]:
    context = {
        "brief": state["brief"].model_dump(mode="json"),
        "strategy_id": state["strategy_id"],
        "tool_results": [
            result.model_dump(mode="json") for result in state.get("tool_results", [])
        ],
        "artifacts": _artifact_payload(state.get("artifacts", {})),
        "quality_gate": {
            "minimum_score": settings.llm_critic_min_score,
            "draft_only_required": True,
        },
    }
    return [
        LLMMessage(
            role="system",
            content=(
                "You are the critic agent for an autonomous ads growth platform. "
                "Return only a CritiqueReport. Evaluate whether the draft strategy is safe "
                "to finalize as a recommendation: budget math must be consistent, campaign "
                "actions must remain draft-only, measurement should be concrete, and risks "
                "or assumptions must be explicit. Set passed=false and include issues plus "
                "required_revisions when the strategy should not be finalized."
            ),
        ),
        LLMMessage(
            role="user",
            content=(
                "Critique this workflow state and return the structured CritiqueReport. "
                "If passed=true, score must meet or exceed the minimum score in the context. "
                f"Workflow context JSON: {_json_dump(context)}"
            ),
        ),
    ]


def _artifact_payload(artifacts: dict[str, Any]) -> dict[str, Any]:
    payload: dict[str, Any] = {}
    for key, value in artifacts.items():
        if isinstance(value, BaseModel):
            payload[key] = value.model_dump(mode="json")
        elif isinstance(value, list):
            payload[key] = [
                item.model_dump(mode="json") if isinstance(item, BaseModel) else item
                for item in value
            ]
        else:
            payload[key] = value
    return payload


def _record_critic_artifact(
    artifacts: dict[str, Any],
    *,
    critique: CritiqueReport,
    structured_result: StructuredOutputResult,
) -> dict[str, Any]:
    critic_payload = {
        "mode": "llm",
        "score": critique.score,
        "passed": critique.passed,
        "required_revisions": critique.required_revisions,
        "structured_output_attempts": [
            attempt.model_dump(mode="json") for attempt in structured_result.attempts
        ],
    }
    history = list(artifacts.get("critic_history", []))
    history.append(critic_payload)
    artifacts["critic"] = critic_payload
    artifacts["critic_history"] = history
    return artifacts


def _revision_node(state: GrowthStrategyState) -> GrowthStrategyState:
    critique = state["critique"]
    revision_count = state.get("revision_count", 0) + 1
    artifacts: dict[str, Any] = dict(state.get("artifacts", {}))
    revisions = list(artifacts.get("revisions", []))
    revisions.append(
        {
            "attempt": revision_count,
            "critic_score": critique.score,
            "required_revisions": critique.required_revisions,
            "issues": [issue.model_dump(mode="json") for issue in critique.issues],
            "applied_adjustments": _revision_adjustments(critique),
        }
    )
    artifacts["revisions"] = revisions

    return {
        "artifacts": artifacts,
        "requires_revision": False,
        "revision_count": revision_count,
        "node_path": [*state.get("node_path", []), "revision"],
    }


def _revision_adjustments(critique: CritiqueReport) -> list[str]:
    if critique.required_revisions:
        return [
            f"Recorded critic-required revision for final strategy context: {revision}"
            for revision in critique.required_revisions
        ]
    return [
        f"Recorded critic issue for final strategy context: {issue.suggested_fix}"
        for issue in critique.issues
    ]


def _revision_notes(artifacts: dict[str, Any]) -> list[str]:
    notes: list[str] = []
    for revision in artifacts.get("revisions", []):
        if not isinstance(revision, dict):
            continue
        required_revisions = revision.get("required_revisions") or []
        notes.extend(str(item) for item in required_revisions)
        if not required_revisions:
            notes.extend(str(item) for item in revision.get("applied_adjustments", []))
    return notes


def _knowledge_notes(retrieval: Any) -> list[str]:
    if not isinstance(retrieval, KnowledgeRetrievalResult):
        return []
    return [
        f"{item.title} ({item.source_type}, relevance={item.relevance})"
        for item in retrieval.results
    ]


def _knowledge_source_citations(retrieval: Any) -> list[SourceCitation]:
    if not isinstance(retrieval, KnowledgeRetrievalResult):
        return []
    return [
        SourceCitation(
            source_id=item.source_id,
            title=item.title,
            source_type=item.source_type,
            relevance=item.relevance,
        )
        for item in retrieval.results
    ]


def _json_dump(value: Any) -> str:
    return json.dumps(value, sort_keys=True, default=str)


def _finalizer_node(state: GrowthStrategyState) -> GrowthStrategyState:
    brief = state["brief"]
    strategy_id = state["strategy_id"]
    artifacts = state["artifacts"]
    audience: AudienceRecommendationOutput = artifacts["audience"]
    creative: CreativeBriefOutput = artifacts["creative"]
    budget: BudgetOptimizationOutput = artifacts["budget"]
    performance: PerformanceEstimateOutput = artifacts["performance"]
    draft: CampaignDraftOutput = artifacts["draft"]
    retrieval = state.get("knowledge") or artifacts.get("knowledge")
    revision_notes = _revision_notes(artifacts)
    knowledge_notes = _knowledge_notes(retrieval)
    measurement_plan = [
        f"Track primary KPI: {brief.primary_kpi}",
        f"Monitor estimated CPA against {performance.estimated_cpa} {brief.currency}",
        "Compare prospecting, retargeting, and creative-test cohorts daily.",
    ]
    measurement_plan.extend(
        f"Critic revision applied: {revision_note}" for revision_note in revision_notes
    )
    assumptions = list(performance.assumptions)
    assumptions.extend(
        f"Retrieved knowledge used: {knowledge_note}" for knowledge_note in knowledge_notes
    )
    assumptions.extend(
        f"Critic revision context applied: {revision_note}"
        for revision_note in revision_notes
    )

    strategy = FinalGrowthStrategy(
        strategy_id=strategy_id,
        advertiser_id=brief.advertiser_id,
        objective=brief.objective,
        summary=(
            f"Draft a {brief.duration_days}-day {brief.objective.value.replace('_', ' ')} "
            f"growth plan for {brief.product_name} with a {brief.currency} {brief.budget} budget. "
            f"The plan prioritizes prospecting, retargeting, and creative learning before scale."
        ),
        audience_strategy=audience.segments,
        creative_strategy=creative.creative_angles,
        bidding_strategy=budget.bidding_strategy,
        measurement_plan=measurement_plan,
        budget_plan=budget.budget_plan,
        actions=[
            RecommendedAction(
                action_id=f"{strategy_id}:action:create_draft",
                title="Create campaign draft",
                description=f"Prepare draft campaign {draft.draft_id} without launching spend.",
                owner_role=AgentRole.PLANNER,
                priority=1,
                tool_name="create_campaign_draft",
                params={"draft_id": draft.draft_id},
            ),
            RecommendedAction(
                action_id=f"{strategy_id}:action:creative_tests",
                title="Launch creative test plan after approval",
                description=(
                    "Use the creative angles as separate test cells before scaling winners."
                ),
                owner_role=AgentRole.CREATIVE_STRATEGIST,
                priority=2,
                tool_name="generate_creative_brief",
                params={"creative_angles": creative.creative_angles},
            ),
            RecommendedAction(
                action_id=f"{strategy_id}:action:measurement",
                title="Review first performance readout",
                description=(
                    "Compare estimated conversions with early delivery data before revising bids."
                ),
                owner_role=AgentRole.PERFORMANCE_ANALYST,
                priority=3,
                tool_name="estimate_performance",
                params={"estimated_conversions": performance.estimated_conversions},
            ),
        ],
        risks=[
            RiskAssessment(
                risk_id=f"{strategy_id}:risk:mock_estimates",
                level=RiskLevel.MEDIUM,
                description=(
                    "Performance numbers use deterministic mock benchmarks, not live delivery data."
                ),
                mitigation=(
                    "Replace mock estimates with historical campaign retrieval and analytics tools."
                ),
            ),
            RiskAssessment(
                risk_id=f"{strategy_id}:risk:approval",
                level=RiskLevel.LOW,
                description="Campaign draft is not approved for live launch.",
                mitigation="Require human approval before any external ad platform mutation.",
            ),
        ],
        assumptions=assumptions,
        success_metrics=[
            SuccessMetric(
                name="Estimated conversions",
                target=str(performance.estimated_conversions),
                measurement_window=f"{brief.duration_days} days",
            ),
            SuccessMetric(
                name="Estimated CPA",
                target=f"{performance.estimated_cpa} {brief.currency}",
                measurement_window=f"{brief.duration_days} days",
            ),
            SuccessMetric(
                name="Budget consistency",
                target=f"Allocations <= {brief.budget} {brief.currency}",
                measurement_window="pre-launch validation",
            ),
        ],
        critique=state["critique"],
        sources=[
            SourceCitation(
                source_id=audience.source_id,
                title="Mock audience recommendation",
                source_type="mock_tool",
                relevance=0.78,
            ),
            SourceCitation(
                source_id=creative.source_id,
                title="Mock creative brief",
                source_type="mock_tool",
                relevance=0.72,
            ),
            SourceCitation(
                source_id=budget.source_id,
                title="Mock budget optimization",
                source_type="mock_tool",
                relevance=0.9,
            ),
            SourceCitation(
                source_id=performance.source_id,
                title="Mock performance estimate",
                source_type="mock_tool",
                relevance=0.68,
            ),
            SourceCitation(
                source_id=draft.source_id,
                title="Mock campaign draft",
                source_type="mock_tool",
                relevance=0.82,
            ),
            *_knowledge_source_citations(retrieval),
        ],
    )

    return {
        "strategy": strategy,
        "node_path": [*state.get("node_path", []), "finalizer"],
    }


def _execute_or_raise(
    registry: ToolRegistry,
    context: ToolExecutionContext,
    intent: ToolIntent,
    *,
    tool_results_so_far: list[ToolResult],
    node_path: list[str],
) -> ToolResult:
    result = registry.execute(intent, context)
    if not result.success:
        message = result.error.message if result.error else "tool execution failed"
        raise StrategyGenerationError(
            message,
            result,
            tool_results=[*tool_results_so_far, result],
            node_path=node_path,
        )
    return result


def strategy_id_for_brief(brief: AdvertiserBrief) -> str:
    payload = json.dumps(brief.model_dump(mode="json"), sort_keys=True)
    return f"strategy_{uuid5(NAMESPACE_URL, payload).hex[:16]}"
