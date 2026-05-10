from decimal import Decimal
from enum import StrEnum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class CampaignObjective(StrEnum):
    APP_INSTALLS = "app_installs"
    REGISTRATIONS = "registrations"
    PURCHASES = "purchases"
    LEADS = "leads"
    TRAFFIC = "traffic"
    AWARENESS = "awareness"


class AgentRole(StrEnum):
    INTAKE = "intake"
    PLANNER = "planner"
    SUPERVISOR = "supervisor"
    AUDIENCE_STRATEGIST = "audience_strategist"
    CREATIVE_STRATEGIST = "creative_strategist"
    BUDGET_OPTIMIZER = "budget_optimizer"
    PERFORMANCE_ANALYST = "performance_analyst"
    CRITIC = "critic"
    FINALIZER = "finalizer"


class AgentTaskType(StrEnum):
    EXTRACT_BRIEF = "extract_brief"
    PLAN_STRATEGY = "plan_strategy"
    RECOMMEND_AUDIENCE = "recommend_audience"
    GENERATE_CREATIVE_BRIEF = "generate_creative_brief"
    OPTIMIZE_BUDGET = "optimize_budget"
    ESTIMATE_PERFORMANCE = "estimate_performance"
    CREATE_CAMPAIGN_DRAFT = "create_campaign_draft"
    CRITIQUE = "critique"
    FINALIZE = "finalize"


class RiskLevel(StrEnum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


class AdvertiserBrief(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    advertiser_id: str = Field(min_length=1, max_length=128)
    product_name: str = Field(min_length=1, max_length=160)
    product_category: str = Field(min_length=1, max_length=120)
    objective: CampaignObjective
    budget: Decimal = Field(gt=0, decimal_places=2)
    currency: str = Field(default="USD", min_length=3, max_length=3)
    duration_days: int = Field(default=14, ge=1, le=365)
    target_market: str = Field(min_length=1, max_length=120)
    primary_kpi: str = Field(default="registrations", min_length=1, max_length=80)
    target_cpa: Decimal | None = Field(default=None, gt=0, decimal_places=2)
    landing_page_url: str | None = Field(default=None, max_length=512)
    brand_voice: str | None = Field(default=None, max_length=240)
    constraints: list[str] = Field(default_factory=list)
    known_audiences: list[str] = Field(default_factory=list)
    historical_context: str | None = Field(default=None, max_length=2_000)

    @field_validator("currency")
    @classmethod
    def normalize_currency(cls, value: str) -> str:
        return value.upper()


class AgentTask(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    task_id: str = Field(min_length=1, max_length=128)
    task_type: AgentTaskType
    owner_role: AgentRole
    input_payload: dict[str, Any] = Field(default_factory=dict)
    dependencies: list[str] = Field(default_factory=list)
    expected_output: str = Field(min_length=1, max_length=240)


class ToolIntent(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    intent_id: str = Field(min_length=1, max_length=128)
    tool_name: str = Field(min_length=1, max_length=120)
    params: dict[str, Any] = Field(default_factory=dict)
    requested_by: AgentRole
    risk_level: RiskLevel = RiskLevel.LOW
    requires_human_approval: bool = False

    @model_validator(mode="after")
    def validate_approval_for_high_risk(self) -> "ToolIntent":
        if self.risk_level == RiskLevel.HIGH and not self.requires_human_approval:
            raise ValueError("high-risk tool intents require human approval")
        return self


class ToolError(BaseModel):
    code: str = Field(min_length=1, max_length=80)
    message: str = Field(min_length=1, max_length=500)
    retryable: bool = False


class ToolResult(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    tool_name: str = Field(min_length=1, max_length=120)
    success: bool
    payload: dict[str, Any] = Field(default_factory=dict)
    error: ToolError | None = None
    latency_ms: int = Field(ge=0)
    source_metadata: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_success_error_shape(self) -> "ToolResult":
        if self.success and self.error is not None:
            raise ValueError("successful tool results must not include an error")
        if not self.success and self.error is None:
            raise ValueError("failed tool results must include an error")
        return self


class BudgetAllocation(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    channel: str = Field(min_length=1, max_length=120)
    amount: Decimal = Field(ge=0, decimal_places=2)
    rationale: str = Field(min_length=1, max_length=500)


class BudgetPlan(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    total_budget: Decimal = Field(gt=0, decimal_places=2)
    currency: str = Field(default="USD", min_length=3, max_length=3)
    allocations: list[BudgetAllocation] = Field(min_length=1)

    @field_validator("currency")
    @classmethod
    def normalize_currency(cls, value: str) -> str:
        return value.upper()

    @model_validator(mode="after")
    def validate_allocations_do_not_exceed_budget(self) -> "BudgetPlan":
        allocated = sum((allocation.amount for allocation in self.allocations), Decimal("0"))
        if allocated > self.total_budget:
            raise ValueError("budget allocations cannot exceed total budget")
        return self

    @property
    def allocated_budget(self) -> Decimal:
        return sum((allocation.amount for allocation in self.allocations), Decimal("0"))


class RecommendedAction(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    action_id: str = Field(min_length=1, max_length=128)
    title: str = Field(min_length=1, max_length=160)
    description: str = Field(min_length=1, max_length=800)
    owner_role: AgentRole
    priority: int = Field(ge=1, le=5)
    tool_name: str | None = Field(default=None, max_length=120)
    params: dict[str, Any] = Field(default_factory=dict)


class RiskAssessment(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    risk_id: str = Field(min_length=1, max_length=128)
    level: RiskLevel
    description: str = Field(min_length=1, max_length=800)
    mitigation: str = Field(min_length=1, max_length=800)


class SuccessMetric(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    name: str = Field(min_length=1, max_length=120)
    target: str = Field(min_length=1, max_length=120)
    measurement_window: str = Field(min_length=1, max_length=120)


class SourceCitation(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    source_id: str = Field(min_length=1, max_length=160)
    title: str = Field(min_length=1, max_length=240)
    source_type: Literal["mock_tool", "assumption", "rag_document", "historical_case"]
    relevance: float = Field(ge=0, le=1)


class CritiqueIssue(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    severity: RiskLevel
    message: str = Field(min_length=1, max_length=800)
    suggested_fix: str = Field(min_length=1, max_length=800)


class CritiqueReport(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    score: float = Field(ge=0, le=10)
    passed: bool
    issues: list[CritiqueIssue] = Field(default_factory=list)
    required_revisions: list[str] = Field(default_factory=list)
    rationale: str = Field(min_length=1, max_length=1_000)

    @model_validator(mode="after")
    def validate_gate_consistency(self) -> "CritiqueReport":
        if self.passed and self.score < 7:
            raise ValueError("passing critique reports require score >= 7")
        if not self.passed and not self.issues:
            raise ValueError("failed critique reports must include at least one issue")
        return self


class FinalGrowthStrategy(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    strategy_id: str = Field(min_length=1, max_length=128)
    advertiser_id: str = Field(min_length=1, max_length=128)
    objective: CampaignObjective
    summary: str = Field(min_length=1, max_length=1_200)
    audience_strategy: list[str] = Field(min_length=1)
    creative_strategy: list[str] = Field(min_length=1)
    bidding_strategy: str = Field(min_length=1, max_length=800)
    measurement_plan: list[str] = Field(min_length=1)
    budget_plan: BudgetPlan
    actions: list[RecommendedAction] = Field(min_length=1)
    risks: list[RiskAssessment] = Field(default_factory=list)
    assumptions: list[str] = Field(default_factory=list)
    success_metrics: list[SuccessMetric] = Field(min_length=1)
    critique: CritiqueReport
    sources: list[SourceCitation] = Field(default_factory=list)


class GrowthStrategyRequest(BaseModel):
    brief: AdvertiserBrief


class GrowthStrategyResponse(BaseModel):
    strategy: FinalGrowthStrategy
    tool_results: list[ToolResult] = Field(default_factory=list)
