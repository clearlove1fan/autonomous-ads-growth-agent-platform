from datetime import datetime
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


class PerformanceEventType(StrEnum):
    PERFORMANCE_SNAPSHOT = "performance_snapshot"
    BUDGET_PACING = "budget_pacing"
    CREATIVE_FATIGUE = "creative_fatigue"
    CONVERSION_DROP = "conversion_drop"


class FeedbackHealthStatus(StrEnum):
    ON_TRACK = "on_track"
    NEEDS_ATTENTION = "needs_attention"
    UNDERPERFORMING = "underperforming"
    CREATIVE_FATIGUE = "creative_fatigue"
    INSUFFICIENT_DATA = "insufficient_data"


class FeedbackActionType(StrEnum):
    CONTINUE_MONITORING = "continue_monitoring"
    REFRESH_CREATIVE = "refresh_creative"
    ADJUST_BUDGET = "adjust_budget"
    NARROW_AUDIENCE = "narrow_audience"
    INSPECT_TRACKING = "inspect_tracking"


AdvertiserMemoryType = Literal[
    "profile",
    "constraint",
    "preference",
    "historical_performance",
]


class StrategyJobStatus(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


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


class ToolRunSummary(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    tool_name: str = Field(min_length=1, max_length=120)
    success: bool
    latency_ms: int = Field(ge=0)
    error_code: str | None = Field(default=None, max_length=80)


class RunMetadata(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    run_id: str = Field(min_length=1, max_length=128)
    execution_id: str | None = Field(default=None, min_length=1, max_length=128)
    strategy_id: str | None = Field(default=None, min_length=1, max_length=128)
    trace_id: str = Field(min_length=1, max_length=128)
    langsmith_project: str = Field(min_length=1, max_length=200)
    tracing_enabled: bool
    node_path: list[str] = Field(default_factory=list)
    tool_count: int = Field(ge=0)
    failed_tool_count: int = Field(ge=0)
    tool_summaries: list[ToolRunSummary] = Field(default_factory=list)
    error_summary: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def default_execution_id(self) -> "RunMetadata":
        if self.execution_id is None:
            self.execution_id = self.run_id
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


class CampaignObjectivePlan(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    product_name: str = Field(min_length=1, max_length=160)
    product_category: str = Field(min_length=1, max_length=120)
    objective: CampaignObjective
    target_market: str = Field(min_length=1, max_length=120)
    primary_kpi: str = Field(min_length=1, max_length=80)
    budget: Decimal = Field(gt=0, decimal_places=2)
    currency: str = Field(default="USD", min_length=3, max_length=3)
    duration_days: int = Field(ge=1, le=365)
    target_cpa: Decimal | None = Field(default=None, gt=0, decimal_places=2)
    landing_page_url: str | None = Field(default=None, max_length=512)
    summary: str = Field(min_length=1, max_length=800)

    @field_validator("currency")
    @classmethod
    def normalize_currency(cls, value: str) -> str:
        return value.upper()


class AudienceSegmentPlan(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    segment_name: str = Field(min_length=1, max_length=240)
    purpose: Literal["prospecting", "retargeting", "expansion", "exclusion"]
    rationale: str = Field(min_length=1, max_length=800)
    source: str = Field(min_length=1, max_length=160)


class CreativeTestPlan(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    angle: str = Field(min_length=1, max_length=240)
    hook: str = Field(min_length=1, max_length=240)
    format: str = Field(min_length=1, max_length=160)
    call_to_action: str = Field(min_length=1, max_length=160)
    compliance_notes: list[str] = Field(default_factory=list)


class CampaignDraftSummary(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    draft_id: str = Field(min_length=1, max_length=160)
    campaign_name: str = Field(min_length=1, max_length=240)
    status: Literal["draft"]
    total_budget: Decimal = Field(gt=0, decimal_places=2)
    daily_budget: Decimal = Field(gt=0, decimal_places=2)
    currency: str = Field(default="USD", min_length=3, max_length=3)
    safety_note: str = Field(min_length=1, max_length=500)

    @field_validator("currency")
    @classmethod
    def normalize_currency(cls, value: str) -> str:
        return value.upper()


class PerformanceForecast(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    estimated_conversions: int = Field(ge=0)
    estimated_cpa: Decimal = Field(gt=0, decimal_places=2)
    confidence_level: Literal["low", "medium", "high"]
    forecast_window_days: int = Field(ge=1, le=365)
    basis: list[str] = Field(min_length=1)


class MeasurementEventPlan(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    event_name: str = Field(min_length=1, max_length=160)
    event_type: Literal["primary_conversion", "secondary_signal", "guardrail", "diagnostic"]
    success_signal: str = Field(min_length=1, max_length=500)
    review_cadence: str = Field(min_length=1, max_length=160)


class OptimizationRule(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    rule_id: str = Field(min_length=1, max_length=128)
    trigger_metric: str = Field(min_length=1, max_length=120)
    condition: str = Field(min_length=1, max_length=300)
    recommended_action: str = Field(min_length=1, max_length=800)
    owner_role: AgentRole
    priority: int = Field(ge=1, le=5)
    rationale: str = Field(min_length=1, max_length=800)


class FeedbackStrategyContext(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    strategy_id: str = Field(min_length=1, max_length=128)
    draft_id: str | None = Field(default=None, min_length=1, max_length=160)
    target_cpa: Decimal | None = Field(default=None, gt=0, decimal_places=2)
    performance_forecast: PerformanceForecast | None = None
    measurement_events: list[MeasurementEventPlan] = Field(default_factory=list)
    optimization_rules: list[OptimizationRule] = Field(default_factory=list)


class SourceCitation(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    source_id: str = Field(min_length=1, max_length=160)
    title: str = Field(min_length=1, max_length=240)
    source_type: Literal[
        "mock_tool",
        "assumption",
        "rag_document",
        "historical_case",
        "advertiser_memory",
    ]
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
    campaign_objective: CampaignObjectivePlan
    summary: str = Field(min_length=1, max_length=1_200)
    audience_strategy: list[str] = Field(min_length=1)
    audience_segments: list[AudienceSegmentPlan] = Field(min_length=1)
    creative_strategy: list[str] = Field(min_length=1)
    creative_tests: list[CreativeTestPlan] = Field(min_length=1)
    bidding_strategy: str = Field(min_length=1, max_length=800)
    campaign_draft: CampaignDraftSummary
    performance_forecast: PerformanceForecast
    measurement_plan: list[str] = Field(min_length=1)
    measurement_events: list[MeasurementEventPlan] = Field(min_length=1)
    optimization_rules: list[OptimizationRule] = Field(min_length=1)
    feedback_context: FeedbackStrategyContext
    budget_plan: BudgetPlan
    actions: list[RecommendedAction] = Field(min_length=1)
    risks: list[RiskAssessment] = Field(default_factory=list)
    assumptions: list[str] = Field(default_factory=list)
    success_metrics: list[SuccessMetric] = Field(min_length=1)
    critique: CritiqueReport
    sources: list[SourceCitation] = Field(default_factory=list)


class CampaignDraftDetailResponse(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    draft_id: str = Field(min_length=1, max_length=160)
    advertiser_id: str = Field(min_length=1, max_length=128)
    objective: CampaignObjective
    status: Literal["draft"]
    budget: Decimal = Field(gt=0, decimal_places=2)
    currency: str = Field(default="USD", min_length=3, max_length=3)
    campaign_name: str | None = Field(default=None, max_length=240)
    daily_budget: Decimal | None = Field(default=None, gt=0, decimal_places=2)
    safety_note: str | None = Field(default=None, max_length=500)
    created_by_run_id: str | None = Field(default=None, min_length=1, max_length=128)
    strategy: FinalGrowthStrategy
    metadata: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime
    updated_at: datetime

    @field_validator("currency")
    @classmethod
    def normalize_currency(cls, value: str) -> str:
        return value.upper()


class CampaignDraftListResponse(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    items: list[CampaignDraftDetailResponse] = Field(default_factory=list)
    count: int = Field(ge=0)
    limit: int = Field(ge=1, le=100)
    advertiser_id: str | None = Field(default=None, min_length=1, max_length=128)


class AdvertiserMemoryDetailResponse(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    memory_id: str = Field(min_length=1, max_length=80)
    source_id: str = Field(min_length=1, max_length=160)
    advertiser_id: str = Field(min_length=1, max_length=128)
    memory_type: AdvertiserMemoryType
    title: str | None = Field(default=None, min_length=1, max_length=240)
    content: str = Field(min_length=1, max_length=5_000)
    summary: str | None = Field(default=None, min_length=1, max_length=1_000)
    importance_score: Decimal = Field(ge=0, le=1, decimal_places=3)
    usage_count: int = Field(ge=0)
    last_used_at: datetime | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime
    updated_at: datetime


class AdvertiserMemoryListResponse(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    items: list[AdvertiserMemoryDetailResponse] = Field(default_factory=list)
    count: int = Field(ge=0)
    limit: int = Field(ge=1, le=100)
    advertiser_id: str = Field(min_length=1, max_length=128)
    memory_type: AdvertiserMemoryType | None = None


class AgentRunStepRecord(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    step_index: int = Field(ge=0)
    node_name: str = Field(min_length=1, max_length=120)
    status: Literal["started", "completed", "failed"]
    input_json: dict[str, Any] = Field(default_factory=dict)
    output_json: dict[str, Any] = Field(default_factory=dict)
    error_json: dict[str, Any] | None = None
    latency_ms: int = Field(ge=0)
    created_at: datetime


class AgentRunDetailResponse(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    run_id: str = Field(min_length=1, max_length=128)
    execution_id: str = Field(min_length=1, max_length=128)
    strategy_id: str = Field(min_length=1, max_length=128)
    advertiser_id: str = Field(min_length=1, max_length=128)
    objective: CampaignObjective
    status: Literal["running", "completed", "failed"]
    trace_id: str = Field(min_length=1, max_length=128)
    node_path: list[str] = Field(default_factory=list)
    final_strategy: FinalGrowthStrategy | None = None
    error_summary: list[str] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)
    steps: list[AgentRunStepRecord] = Field(default_factory=list)
    created_at: datetime
    completed_at: datetime | None = None


class GrowthStrategyRequest(BaseModel):
    brief: AdvertiserBrief


class GrowthStrategyResponse(BaseModel):
    strategy: FinalGrowthStrategy
    tool_results: list[ToolResult] = Field(default_factory=list)
    node_path: list[str] = Field(default_factory=list)
    run_metadata: RunMetadata


class AdvertiserBriefIntakeRequest(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    text: str = Field(min_length=10, max_length=5_000)
    advertiser_id: str | None = Field(default=None, min_length=1, max_length=128)
    default_currency: str = Field(default="USD", min_length=3, max_length=3)
    default_target_market: str = Field(default="United States", min_length=1, max_length=120)
    default_duration_days: int = Field(default=14, ge=1, le=365)

    @field_validator("default_currency")
    @classmethod
    def normalize_default_currency(cls, value: str) -> str:
        return value.upper()


class AdvertiserBriefIntakeResponse(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    source_text: str = Field(min_length=10, max_length=5_000)
    brief: AdvertiserBrief
    mode: Literal["heuristic", "llm", "llm_fallback"]
    confidence: float = Field(ge=0, le=1)
    assumptions: list[str] = Field(default_factory=list)
    extraction_errors: list[str] = Field(default_factory=list)


class GrowthStrategyFromTextRequest(AdvertiserBriefIntakeRequest):
    pass


class GrowthStrategyFromTextResponse(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    intake: AdvertiserBriefIntakeResponse
    growth_strategy: GrowthStrategyResponse


class StrategyJobAcceptedResponse(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    job_id: str = Field(min_length=1, max_length=128)
    status: StrategyJobStatus
    strategy_id: str = Field(min_length=1, max_length=128)
    advertiser_id: str = Field(min_length=1, max_length=128)
    objective: CampaignObjective
    run_id: str = Field(min_length=1, max_length=128)
    trace_id: str = Field(min_length=1, max_length=128)
    polling_url: str = Field(min_length=1, max_length=240)
    created_at: datetime


class StrategyJobFromTextResponse(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    intake: AdvertiserBriefIntakeResponse
    job: StrategyJobAcceptedResponse


class StrategyJobDetailResponse(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    job_id: str = Field(min_length=1, max_length=128)
    status: StrategyJobStatus
    strategy_id: str = Field(min_length=1, max_length=128)
    advertiser_id: str = Field(min_length=1, max_length=128)
    objective: CampaignObjective
    run_id: str = Field(min_length=1, max_length=128)
    trace_id: str = Field(min_length=1, max_length=128)
    request: GrowthStrategyRequest
    result: GrowthStrategyResponse | None = None
    error: dict[str, Any] | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)
    attempt_count: int = Field(default=0, ge=0)
    max_attempts: int = Field(default=3, gt=0)
    next_attempt_at: datetime | None = None
    locked_by: str | None = Field(default=None, min_length=1, max_length=160)
    locked_until: datetime | None = None
    created_at: datetime
    updated_at: datetime
    completed_at: datetime | None = None


class StrategyJobListResponse(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    items: list[StrategyJobDetailResponse] = Field(default_factory=list)
    count: int = Field(ge=0)
    limit: int = Field(ge=1, le=100)
    status: StrategyJobStatus | None = None
    advertiser_id: str | None = Field(default=None, min_length=1, max_length=128)
    run_id: str | None = Field(default=None, min_length=1, max_length=128)


class StrategyJobCancelRequest(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    reason: str | None = Field(default=None, min_length=1, max_length=500)


class PerformanceMetrics(BaseModel):
    impressions: int = Field(ge=0)
    clicks: int = Field(ge=0)
    spend: Decimal = Field(ge=0, decimal_places=2)
    conversions: int = Field(ge=0)
    revenue: Decimal | None = Field(default=None, ge=0, decimal_places=2)

    @model_validator(mode="after")
    def validate_metric_order(self) -> "PerformanceMetrics":
        if self.clicks > self.impressions:
            raise ValueError("clicks cannot exceed impressions")
        if self.conversions > self.clicks:
            raise ValueError("conversions cannot exceed clicks")
        return self


class CampaignPerformanceEventRequest(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    event_id: str = Field(min_length=1, max_length=128)
    advertiser_id: str = Field(min_length=1, max_length=128)
    run_id: str | None = Field(default=None, min_length=1, max_length=128)
    campaign_id: str | None = Field(default=None, min_length=1, max_length=128)
    draft_id: str | None = Field(default=None, min_length=1, max_length=128)
    objective: CampaignObjective
    event_type: PerformanceEventType = PerformanceEventType.PERFORMANCE_SNAPSHOT
    occurred_at: datetime
    metrics: PerformanceMetrics
    target_cpa: Decimal | None = Field(default=None, gt=0, decimal_places=2)
    attribution_window_days: int = Field(default=7, ge=1, le=90)
    strategy_context: FeedbackStrategyContext | None = None
    notes: str | None = Field(default=None, max_length=1_000)

    @model_validator(mode="after")
    def require_campaign_or_run_reference(self) -> "CampaignPerformanceEventRequest":
        if not (self.run_id or self.campaign_id or self.draft_id):
            raise ValueError("performance events require run_id, campaign_id, or draft_id")
        return self


class FeedbackRecommendation(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    recommendation_id: str = Field(min_length=1, max_length=160)
    action_type: FeedbackActionType
    title: str = Field(min_length=1, max_length=160)
    rationale: str = Field(min_length=1, max_length=800)
    priority: int = Field(ge=1, le=5)
    risk_level: RiskLevel = RiskLevel.LOW
    requires_human_approval: bool = True
    params: dict[str, Any] = Field(default_factory=dict)


class StrategyRuleMatch(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    rule_id: str = Field(min_length=1, max_length=128)
    trigger_metric: str = Field(min_length=1, max_length=120)
    recommended_action: str = Field(min_length=1, max_length=800)
    owner_role: AgentRole
    priority: int = Field(ge=1, le=5)
    match_reason: str = Field(min_length=1, max_length=500)


class CampaignFeedbackAnalysis(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    feedback_id: str = Field(min_length=1, max_length=160)
    event_id: str = Field(min_length=1, max_length=128)
    advertiser_id: str = Field(min_length=1, max_length=128)
    run_id: str | None = Field(default=None, min_length=1, max_length=128)
    strategy_id: str | None = Field(default=None, min_length=1, max_length=128)
    draft_id: str | None = Field(default=None, min_length=1, max_length=160)
    health_status: FeedbackHealthStatus
    metrics_summary: dict[str, Any] = Field(default_factory=dict)
    recommendations: list[FeedbackRecommendation] = Field(min_length=1)
    matched_strategy_rules: list[StrategyRuleMatch] = Field(default_factory=list)
    guardrails: list[str] = Field(default_factory=list)
    created_at: datetime


class CampaignPerformanceEventResponse(BaseModel):
    event_id: str = Field(min_length=1, max_length=128)
    advertiser_id: str = Field(min_length=1, max_length=128)
    run_id: str | None = Field(default=None, min_length=1, max_length=128)
    status: Literal["analyzed"]
    persisted: bool
    advertiser_memory_persisted: bool = False
    advertiser_memory_queued: bool = False
    advertiser_memory_status: Literal["disabled", "queued", "recorded", "failed"] = "disabled"
    advertiser_memory_source_id: str | None = Field(default=None, min_length=1, max_length=160)
    analysis: CampaignFeedbackAnalysis


class CampaignPerformanceEventDetailResponse(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    event_id: str = Field(min_length=1, max_length=128)
    advertiser_id: str = Field(min_length=1, max_length=128)
    run_id: str | None = Field(default=None, min_length=1, max_length=128)
    campaign_id: str | None = Field(default=None, min_length=1, max_length=128)
    draft_id: str | None = Field(default=None, min_length=1, max_length=128)
    objective: CampaignObjective
    event_type: PerformanceEventType
    occurred_at: datetime
    metrics: PerformanceMetrics
    status: Literal["analyzed", "ignored", "failed"]
    metadata: dict[str, Any] = Field(default_factory=dict)
    analysis: CampaignFeedbackAnalysis
    created_at: datetime
    updated_at: datetime


class CampaignPerformanceEventListResponse(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    items: list[CampaignPerformanceEventDetailResponse] = Field(default_factory=list)
    count: int = Field(ge=0)
    limit: int = Field(ge=1, le=100)
    advertiser_id: str | None = Field(default=None, min_length=1, max_length=128)
    run_id: str | None = Field(default=None, min_length=1, max_length=128)
    campaign_id: str | None = Field(default=None, min_length=1, max_length=128)
    draft_id: str | None = Field(default=None, min_length=1, max_length=128)
    event_type: PerformanceEventType | None = None
