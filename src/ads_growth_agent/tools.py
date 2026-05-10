from collections.abc import Callable
from dataclasses import dataclass
from decimal import Decimal
from time import perf_counter
from typing import Any, Literal

from pydantic import BaseModel, Field, ValidationError

from ads_growth_agent.contracts import (
    AgentRole,
    BudgetAllocation,
    BudgetPlan,
    CampaignObjective,
    ToolError,
    ToolIntent,
    ToolResult,
)

CENTS = Decimal("0.01")


class ToolExecutionError(Exception):
    def __init__(self, code: str, message: str, retryable: bool = False) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.retryable = retryable


class ToolExecutionContext(BaseModel):
    advertiser_id: str = Field(min_length=1)
    run_id: str = Field(min_length=1)
    allowed_tools: set[str] | None = None


class AudienceRecommendationInput(BaseModel):
    advertiser_id: str = Field(min_length=1)
    product_category: str = Field(min_length=1)
    objective: CampaignObjective
    target_market: str = Field(min_length=1)
    known_audiences: list[str] = Field(default_factory=list)


class AudienceRecommendationOutput(BaseModel):
    segments: list[str] = Field(min_length=1)
    exclusions: list[str] = Field(default_factory=list)
    rationale: str = Field(min_length=1)
    source_id: str = Field(min_length=1)


class CreativeBriefInput(BaseModel):
    product_name: str = Field(min_length=1)
    product_category: str = Field(min_length=1)
    objective: CampaignObjective
    brand_voice: str | None = None
    constraints: list[str] = Field(default_factory=list)


class CreativeBriefOutput(BaseModel):
    creative_angles: list[str] = Field(min_length=1)
    messaging_constraints: list[str] = Field(default_factory=list)
    call_to_action: str = Field(min_length=1)
    source_id: str = Field(min_length=1)


class BudgetOptimizationInput(BaseModel):
    advertiser_id: str = Field(min_length=1)
    objective: CampaignObjective
    total_budget: Decimal = Field(gt=0, decimal_places=2)
    currency: str = Field(default="USD", min_length=3, max_length=3)
    duration_days: int = Field(ge=1, le=365)


class BudgetOptimizationOutput(BaseModel):
    budget_plan: BudgetPlan
    daily_budget: Decimal = Field(gt=0, decimal_places=2)
    bidding_strategy: str = Field(min_length=1)
    source_id: str = Field(min_length=1)


class PerformanceEstimateInput(BaseModel):
    product_category: str = Field(min_length=1)
    objective: CampaignObjective
    budget_plan: BudgetPlan
    target_cpa: Decimal | None = Field(default=None, gt=0, decimal_places=2)


class PerformanceEstimateOutput(BaseModel):
    estimated_conversions: int = Field(ge=0)
    estimated_cpa: Decimal = Field(gt=0, decimal_places=2)
    confidence_level: Literal["low", "medium", "high"]
    assumptions: list[str] = Field(min_length=1)
    source_id: str = Field(min_length=1)


class CampaignDraftInput(BaseModel):
    advertiser_id: str = Field(min_length=1)
    product_name: str = Field(min_length=1)
    objective: CampaignObjective
    budget_plan: BudgetPlan
    duration_days: int = Field(ge=1, le=365)
    audience_segments: list[str] = Field(min_length=1)
    creative_angles: list[str] = Field(min_length=1)


class CampaignDraftOutput(BaseModel):
    draft_id: str = Field(min_length=1)
    status: Literal["draft"]
    campaign_name: str = Field(min_length=1)
    objective: CampaignObjective
    total_budget: Decimal = Field(gt=0, decimal_places=2)
    daily_budget: Decimal = Field(gt=0, decimal_places=2)
    audience_segments: list[str] = Field(min_length=1)
    creative_angles: list[str] = Field(min_length=1)
    safety_note: str = Field(min_length=1)
    source_id: str = Field(min_length=1)


ToolHandler = Callable[[BaseModel], BaseModel | dict[str, Any]]


@dataclass(frozen=True)
class ToolDefinition:
    name: str
    input_model: type[BaseModel]
    output_model: type[BaseModel]
    handler: ToolHandler
    owner_role: AgentRole


class ToolRegistry:
    def __init__(self) -> None:
        self._tools: dict[str, ToolDefinition] = {}

    def register(self, definition: ToolDefinition) -> None:
        if definition.name in self._tools:
            raise ValueError(f"tool already registered: {definition.name}")
        self._tools[definition.name] = definition

    def execute(
        self,
        intent: ToolIntent,
        context: ToolExecutionContext,
    ) -> ToolResult:
        started_at = perf_counter()
        definition = self._tools.get(intent.tool_name)
        if definition is None:
            return self._error_result(
                intent.tool_name,
                started_at,
                "UNKNOWN_TOOL",
                f"Unknown tool: {intent.tool_name}",
                retryable=False,
            )

        if context.allowed_tools is not None and intent.tool_name not in context.allowed_tools:
            return self._error_result(
                intent.tool_name,
                started_at,
                "PERMISSION_DENIED",
                f"Tool is not allowed for this execution context: {intent.tool_name}",
                retryable=False,
            )

        try:
            tool_input = definition.input_model.model_validate(intent.params)
        except ValidationError as exc:
            return self._error_result(
                intent.tool_name,
                started_at,
                "VALIDATION_ERROR",
                exc.errors(include_url=False)[0]["msg"],
                retryable=False,
            )

        try:
            raw_output = definition.handler(tool_input)
            output = definition.output_model.model_validate(raw_output)
        except ToolExecutionError as exc:
            return self._error_result(
                intent.tool_name,
                started_at,
                exc.code,
                exc.message,
                retryable=exc.retryable,
            )
        except Exception as exc:  # pragma: no cover - defensive guard for external tools later.
            return self._error_result(
                intent.tool_name,
                started_at,
                "TOOL_FAILURE",
                str(exc),
                retryable=True,
            )

        return ToolResult(
            tool_name=intent.tool_name,
            success=True,
            payload=output.model_dump(mode="json"),
            latency_ms=self._elapsed_ms(started_at),
            source_metadata={
                "owner_role": definition.owner_role.value,
                "run_id": context.run_id,
                "advertiser_id": context.advertiser_id,
            },
        )

    @staticmethod
    def _elapsed_ms(started_at: float) -> int:
        return max(0, int((perf_counter() - started_at) * 1000))

    def _error_result(
        self,
        tool_name: str,
        started_at: float,
        code: str,
        message: str,
        retryable: bool,
    ) -> ToolResult:
        return ToolResult(
            tool_name=tool_name,
            success=False,
            payload={},
            error=ToolError(code=code, message=message, retryable=retryable),
            latency_ms=self._elapsed_ms(started_at),
        )


def build_default_tool_registry() -> ToolRegistry:
    registry = ToolRegistry()
    registry.register(
        ToolDefinition(
            name="recommend_audience",
            input_model=AudienceRecommendationInput,
            output_model=AudienceRecommendationOutput,
            handler=recommend_audience,
            owner_role=AgentRole.AUDIENCE_STRATEGIST,
        )
    )
    registry.register(
        ToolDefinition(
            name="generate_creative_brief",
            input_model=CreativeBriefInput,
            output_model=CreativeBriefOutput,
            handler=generate_creative_brief,
            owner_role=AgentRole.CREATIVE_STRATEGIST,
        )
    )
    registry.register(
        ToolDefinition(
            name="optimize_budget",
            input_model=BudgetOptimizationInput,
            output_model=BudgetOptimizationOutput,
            handler=optimize_budget,
            owner_role=AgentRole.BUDGET_OPTIMIZER,
        )
    )
    registry.register(
        ToolDefinition(
            name="estimate_performance",
            input_model=PerformanceEstimateInput,
            output_model=PerformanceEstimateOutput,
            handler=estimate_performance,
            owner_role=AgentRole.PERFORMANCE_ANALYST,
        )
    )
    registry.register(
        ToolDefinition(
            name="create_campaign_draft",
            input_model=CampaignDraftInput,
            output_model=CampaignDraftOutput,
            handler=create_campaign_draft,
            owner_role=AgentRole.PLANNER,
        )
    )
    return registry


def recommend_audience(input_model: BaseModel) -> AudienceRecommendationOutput:
    params = AudienceRecommendationInput.model_validate(input_model)
    category = params.product_category.lower()
    objective_label = params.objective.value.replace("_", " ")
    segments = [
        f"{params.target_market} users showing recent {category} interest",
        f"Lookalikes of high-intent {objective_label} converters",
        "Retargeting pool for landing-page visitors and engaged video viewers",
    ]
    segments.extend(params.known_audiences[:2])
    return AudienceRecommendationOutput(
        segments=segments,
        exclusions=["Existing converters from the last 30 days"],
        rationale="Prioritizes intent-rich prospecting while preserving a retargeting lane.",
        source_id="mock_tool:recommend_audience:v1",
    )


def generate_creative_brief(input_model: BaseModel) -> CreativeBriefOutput:
    params = CreativeBriefInput.model_validate(input_model)
    return CreativeBriefOutput(
        creative_angles=[
            f"Show the first useful moment inside {params.product_name}",
            f"Translate {params.product_category} benefits into a before-and-after story",
            "Use social proof and product walkthrough clips for conversion intent",
        ],
        messaging_constraints=params.constraints,
        call_to_action=f"Start with {params.product_name}",
        source_id="mock_tool:generate_creative_brief:v1",
    )


def optimize_budget(input_model: BaseModel) -> BudgetOptimizationOutput:
    params = BudgetOptimizationInput.model_validate(input_model)
    prospecting = (params.total_budget * Decimal("0.70")).quantize(CENTS)
    retargeting = (params.total_budget * Decimal("0.20")).quantize(CENTS)
    creative_tests = (params.total_budget - prospecting - retargeting).quantize(CENTS)
    budget_plan = BudgetPlan(
        total_budget=params.total_budget,
        currency=params.currency,
        allocations=[
            BudgetAllocation(
                channel="prospecting",
                amount=prospecting,
                rationale="Largest share goes to new qualified users for growth.",
            ),
            BudgetAllocation(
                channel="retargeting",
                amount=retargeting,
                rationale="Reserve budget for users who already engaged with ads or landing pages.",
            ),
            BudgetAllocation(
                channel="creative_tests",
                amount=creative_tests,
                rationale="Keep a controlled lane for creative learning before scaling winners.",
            ),
        ],
    )
    return BudgetOptimizationOutput(
        budget_plan=budget_plan,
        daily_budget=(params.total_budget / Decimal(params.duration_days)).quantize(CENTS),
        bidding_strategy=(
            "Start with lowest-cost bidding, then move winners to cost-cap once CPA stabilizes."
        ),
        source_id="mock_tool:optimize_budget:v1",
    )


def estimate_performance(input_model: BaseModel) -> PerformanceEstimateOutput:
    params = PerformanceEstimateInput.model_validate(input_model)
    cpa = params.target_cpa or _default_cpa(params.objective)
    conversions = int(params.budget_plan.allocated_budget / cpa)
    return PerformanceEstimateOutput(
        estimated_conversions=conversions,
        estimated_cpa=cpa.quantize(CENTS),
        confidence_level="medium",
        assumptions=[
            "Estimate uses deterministic v0.1 mock benchmarks, not live platform delivery data.",
            (
                "Budget allocation is fully draft-mode and totals "
                f"{params.budget_plan.allocated_budget}."
            ),
        ],
        source_id="mock_tool:estimate_performance:v1",
    )


def create_campaign_draft(input_model: BaseModel) -> CampaignDraftOutput:
    params = CampaignDraftInput.model_validate(input_model)
    slug = _slug(params.product_name)
    return CampaignDraftOutput(
        draft_id=f"draft_{params.advertiser_id}_{slug}_{params.objective.value}",
        status="draft",
        campaign_name=(
            f"{params.product_name} {params.objective.value.replace('_', ' ').title()} Growth"
        ),
        objective=params.objective,
        total_budget=params.budget_plan.total_budget,
        daily_budget=(
            params.budget_plan.total_budget / Decimal(params.duration_days)
        ).quantize(CENTS),
        audience_segments=params.audience_segments,
        creative_angles=params.creative_angles,
        safety_note="Draft only. No live campaign launch or spend mutation is performed.",
        source_id="mock_tool:create_campaign_draft:v1",
    )


def _default_cpa(objective: CampaignObjective) -> Decimal:
    match objective:
        case CampaignObjective.APP_INSTALLS:
            return Decimal("6.00")
        case CampaignObjective.REGISTRATIONS | CampaignObjective.LEADS:
            return Decimal("18.00")
        case CampaignObjective.PURCHASES:
            return Decimal("35.00")
        case CampaignObjective.TRAFFIC:
            return Decimal("2.50")
        case CampaignObjective.AWARENESS:
            return Decimal("1.25")


def _slug(value: str) -> str:
    chars = [character.lower() if character.isalnum() else "_" for character in value]
    return "_".join("".join(chars).split("_"))[:64]
