import pytest

from ads_growth_agent.contracts import (
    AgentRole,
    CampaignFeedbackExecutionDryRunListResponse,
    CampaignFeedbackExecutionDryRunResponse,
    CampaignFeedbackExecutionPlanResponse,
    CampaignFeedbackHandoffRecordListResponse,
    CampaignFeedbackHandoffRecordRequest,
    CampaignFeedbackOptimizationReviewListResponse,
    CampaignFeedbackOptimizationReviewRequest,
    CampaignFeedbackOptimizationReviewResponse,
    CampaignObjective,
    CampaignPerformanceEventDetailResponse,
    CampaignPerformanceEventRequest,
    FeedbackActionType,
    FeedbackHandoffOutcome,
    FeedbackHealthStatus,
    FeedbackOptimizationReviewDecision,
    FeedbackStrategyContext,
    OptimizationRule,
    PerformanceMetrics,
)
from ads_growth_agent.feedback import (
    FeedbackRevisionDraftNotRequestedError,
    analyze_campaign_performance_event,
    build_campaign_feedback_action_plan,
    build_campaign_feedback_optimization_draft,
    build_campaign_feedback_optimization_review,
    build_campaign_feedback_optimization_revision_draft,
    build_campaign_feedback_revision_reviewable_draft,
)
from ads_growth_agent.feedback_execution_dry_run import dry_run_feedback_execution_plan
from ads_growth_agent.feedback_execution_plan import (
    FeedbackExecutionPlanNotApprovedError,
    build_feedback_execution_plan,
)
from ads_growth_agent.feedback_handoff_package import build_feedback_handoff_package
from ads_growth_agent.feedback_handoff_record import (
    FeedbackHandoffRecordNotReadyError,
    FeedbackHandoffRecordStepMismatchError,
    build_feedback_handoff_record,
)
from ads_growth_agent.feedback_lineage import (
    build_feedback_optimization_review_lineage,
    list_feedback_optimization_review_lineages,
)
from ads_growth_agent.feedback_loop_summary import build_campaign_feedback_loop_summary


def _approved_feedback_execution_plan(
    review_id: str = "feedback_review_dry_run_001",
) -> CampaignFeedbackExecutionPlanResponse:
    event = CampaignPerformanceEventRequest(
        event_id=f"evt_{review_id}",
        advertiser_id="adv_fitness_001",
        run_id="run_001",
        campaign_id="cmp_fittrack",
        draft_id="draft_fittrack",
        objective=CampaignObjective.REGISTRATIONS,
        occurred_at="2026-05-12T12:00:00Z",
        metrics=PerformanceMetrics(
            impressions=10_000,
            clicks=500,
            spend="1000.00",
            conversions=20,
        ),
        target_cpa="20.00",
    )
    analysis = analyze_campaign_performance_event(event)
    detail = CampaignPerformanceEventDetailResponse(
        event_id=event.event_id,
        advertiser_id=event.advertiser_id,
        run_id=event.run_id,
        campaign_id=event.campaign_id,
        draft_id=event.draft_id,
        objective=event.objective,
        event_type=event.event_type,
        occurred_at=event.occurred_at,
        metrics=event.metrics,
        status="analyzed",
        metadata={},
        analysis=analysis,
        created_at=analysis.created_at,
        updated_at=analysis.created_at,
    )
    optimization_draft = build_campaign_feedback_optimization_draft(detail)
    review = build_campaign_feedback_optimization_review(
        optimization_draft,
        CampaignFeedbackOptimizationReviewRequest(
            decision=FeedbackOptimizationReviewDecision.APPROVED,
            reviewer_id="operator_001",
            selected_change_ids=[optimization_draft.changes[0].change_id],
        ),
        review_id=review_id,
    )
    return build_feedback_execution_plan(review)


def _feedback_optimization_review(
    *,
    decision: FeedbackOptimizationReviewDecision,
    review_id: str,
    notes: str | None = None,
) -> CampaignFeedbackOptimizationReviewResponse:
    event = CampaignPerformanceEventRequest(
        event_id=f"evt_{review_id}",
        advertiser_id="adv_fitness_001",
        run_id="run_001",
        campaign_id="cmp_fittrack",
        draft_id="draft_fittrack",
        objective=CampaignObjective.REGISTRATIONS,
        occurred_at="2026-05-12T12:00:00Z",
        metrics=PerformanceMetrics(
            impressions=10_000,
            clicks=500,
            spend="1000.00",
            conversions=20,
        ),
        target_cpa="20.00",
    )
    analysis = analyze_campaign_performance_event(event)
    detail = CampaignPerformanceEventDetailResponse(
        event_id=event.event_id,
        advertiser_id=event.advertiser_id,
        run_id=event.run_id,
        campaign_id=event.campaign_id,
        draft_id=event.draft_id,
        objective=event.objective,
        event_type=event.event_type,
        occurred_at=event.occurred_at,
        metrics=event.metrics,
        status="analyzed",
        metadata={},
        analysis=analysis,
        created_at=analysis.created_at,
        updated_at=analysis.created_at,
    )
    optimization_draft = build_campaign_feedback_optimization_draft(detail)
    return build_campaign_feedback_optimization_review(
        optimization_draft,
        CampaignFeedbackOptimizationReviewRequest(
            decision=decision,
            reviewer_id="operator_001",
            notes=notes,
            selected_change_ids=[optimization_draft.changes[0].change_id],
        ),
        review_id=review_id,
    )


class _ReviewLineageStore:
    def __init__(
        self,
        reviews: list[CampaignFeedbackOptimizationReviewResponse],
    ) -> None:
        self._reviews = reviews

    def get_review(self, review_id: str) -> CampaignFeedbackOptimizationReviewResponse | None:
        for review in self._reviews:
            if review.review_id == review_id:
                return review
        return None

    def list_reviews(
        self,
        *,
        event_id: str | None = None,
        advertiser_id: str | None = None,
        optimization_draft_id: str | None = None,
        decision: FeedbackOptimizationReviewDecision | None = None,
        limit: int = 50,
    ):
        items = [
            review
            for review in self._reviews
            if (event_id is None or review.event_id == event_id)
            and (advertiser_id is None or review.advertiser_id == advertiser_id)
            and (
                optimization_draft_id is None
                or review.optimization_draft_id == optimization_draft_id
            )
            and (decision is None or review.decision == decision)
        ][:limit]
        return CampaignFeedbackOptimizationReviewListResponse(
            items=items,
            count=len(items),
            limit=limit,
            event_id=event_id,
            advertiser_id=advertiser_id,
            optimization_draft_id=optimization_draft_id,
            decision=decision,
        )


class _ExecutionLineageStore:
    def __init__(self, dry_runs: list[CampaignFeedbackExecutionDryRunResponse]) -> None:
        self._dry_runs = dry_runs

    def list_dry_runs(
        self,
        *,
        review_id: str | None = None,
        execution_plan_id: str | None = None,
        event_id: str | None = None,
        advertiser_id: str | None = None,
        status: str | None = None,
        limit: int = 50,
    ) -> CampaignFeedbackExecutionDryRunListResponse:
        items = [
            dry_run
            for dry_run in self._dry_runs
            if (review_id is None or dry_run.review_id == review_id)
            and (execution_plan_id is None or dry_run.execution_plan_id == execution_plan_id)
            and (event_id is None or dry_run.event_id == event_id)
            and (advertiser_id is None or dry_run.advertiser_id == advertiser_id)
            and (status is None or dry_run.status == status)
        ][:limit]
        return CampaignFeedbackExecutionDryRunListResponse(
            items=items,
            count=len(items),
            limit=limit,
            review_id=review_id,
            execution_plan_id=execution_plan_id,
            event_id=event_id,
            advertiser_id=advertiser_id,
            status=status,
        )


class _HandoffSummaryStore:
    def __init__(self, records) -> None:
        self._records = records

    def list_handoff_records(
        self,
        *,
        review_id: str | None = None,
        handoff_package_id: str | None = None,
        event_id: str | None = None,
        advertiser_id: str | None = None,
        outcome=None,
        limit: int = 50,
    ) -> CampaignFeedbackHandoffRecordListResponse:
        items = [
            record
            for record in self._records
            if (review_id is None or record.review_id == review_id)
            and (handoff_package_id is None or record.handoff_package_id == handoff_package_id)
            and (event_id is None or record.event_id == event_id)
            and (advertiser_id is None or record.advertiser_id == advertiser_id)
            and (outcome is None or record.outcome == outcome)
        ][:limit]
        return CampaignFeedbackHandoffRecordListResponse(
            items=items,
            count=len(items),
            limit=limit,
            review_id=review_id,
            handoff_package_id=handoff_package_id,
            event_id=event_id,
            advertiser_id=advertiser_id,
            outcome=outcome,
        )


def test_feedback_analysis_flags_underperforming_cpa() -> None:
    analysis = analyze_campaign_performance_event(
        CampaignPerformanceEventRequest(
            event_id="evt_underperforming_cpa",
            advertiser_id="adv_fitness_001",
            run_id="run_001",
            objective=CampaignObjective.REGISTRATIONS,
            occurred_at="2026-05-12T12:00:00Z",
            metrics=PerformanceMetrics(
                impressions=10_000,
                clicks=500,
                spend="1000.00",
                conversions=20,
            ),
            target_cpa="20.00",
        )
    )

    assert analysis.health_status == FeedbackHealthStatus.UNDERPERFORMING
    assert analysis.feedback_id.startswith("feedback_")
    assert analysis.metrics_summary["cpa"] == "50.00"
    assert [item.action_type for item in analysis.recommendations] == [
        FeedbackActionType.ADJUST_BUDGET,
        FeedbackActionType.REFRESH_CREATIVE,
    ]
    assert all(item.requires_human_approval for item in analysis.recommendations)
    assert "do not mutate live campaign spend" in analysis.guardrails[0]


def test_feedback_analysis_matches_strategy_optimization_rules() -> None:
    analysis = analyze_campaign_performance_event(
        CampaignPerformanceEventRequest(
            event_id="evt_strategy_context",
            advertiser_id="adv_fitness_001",
            run_id="run_001",
            draft_id="draft_fittrack",
            objective=CampaignObjective.REGISTRATIONS,
            occurred_at="2026-05-12T12:00:00Z",
            metrics=PerformanceMetrics(
                impressions=10_000,
                clicks=500,
                spend="1000.00",
                conversions=20,
            ),
            strategy_context=FeedbackStrategyContext(
                strategy_id="strategy_001",
                draft_id="draft_fittrack",
                target_cpa="20.00",
                optimization_rules=[
                    OptimizationRule(
                        rule_id="strategy_001:rule:cpa_guardrail",
                        trigger_metric="cost_per_result",
                        condition="Observed CPA exceeds target by more than 20%.",
                        recommended_action="Shift budget toward the best converting lane.",
                        owner_role=AgentRole.BUDGET_OPTIMIZER,
                        priority=1,
                        rationale="CPA is the primary efficiency guardrail.",
                    ),
                    OptimizationRule(
                        rule_id="strategy_001:rule:creative_learning",
                        trigger_metric="creative_cell_conversions",
                        condition="One creative angle wins.",
                        recommended_action="Generate close variants of the winning hook.",
                        owner_role=AgentRole.CREATIVE_STRATEGIST,
                        priority=2,
                        rationale="Creative learning should happen before broad scaling.",
                    ),
                ],
            ),
        )
    )

    assert analysis.health_status == FeedbackHealthStatus.UNDERPERFORMING
    assert analysis.strategy_id == "strategy_001"
    assert analysis.draft_id == "draft_fittrack"
    assert analysis.metrics_summary["target_cpa"] == "20.00"
    assert [match.rule_id for match in analysis.matched_strategy_rules] == [
        "strategy_001:rule:cpa_guardrail"
    ]
    assert analysis.recommendations[0].params["strategy_id"] == "strategy_001"
    assert analysis.recommendations[0].params["draft_id"] == "draft_fittrack"
    assert analysis.recommendations[0].params["matched_strategy_rule_ids"] == [
        "strategy_001:rule:cpa_guardrail"
    ]


def test_feedback_analysis_continues_monitoring_for_low_signal_event() -> None:
    analysis = analyze_campaign_performance_event(
        CampaignPerformanceEventRequest(
            event_id="evt_low_signal",
            advertiser_id="adv_fitness_001",
            draft_id="draft_001",
            objective=CampaignObjective.REGISTRATIONS,
            occurred_at="2026-05-12T12:00:00Z",
            metrics=PerformanceMetrics(
                impressions=25,
                clicks=2,
                spend="0.00",
                conversions=0,
            ),
            target_cpa="20.00",
        )
    )

    assert analysis.health_status == FeedbackHealthStatus.INSUFFICIENT_DATA
    assert analysis.recommendations[0].action_type == FeedbackActionType.CONTINUE_MONITORING
    assert analysis.recommendations[0].requires_human_approval is False


def test_feedback_analysis_flags_zero_conversion_attention() -> None:
    analysis = analyze_campaign_performance_event(
        CampaignPerformanceEventRequest(
            event_id="evt_zero_conversions",
            advertiser_id="adv_fitness_001",
            campaign_id="cmp_001",
            objective=CampaignObjective.REGISTRATIONS,
            occurred_at="2026-05-12T12:00:00Z",
            metrics=PerformanceMetrics(
                impressions=5_000,
                clicks=200,
                spend="350.00",
                conversions=0,
            ),
            target_cpa="20.00",
        )
    )

    assert analysis.health_status == FeedbackHealthStatus.NEEDS_ATTENTION
    assert [item.action_type for item in analysis.recommendations] == [
        FeedbackActionType.INSPECT_TRACKING,
        FeedbackActionType.NARROW_AUDIENCE,
    ]


def test_feedback_action_plan_ranks_draft_only_next_steps() -> None:
    event = CampaignPerformanceEventRequest(
        event_id="evt_action_plan",
        advertiser_id="adv_fitness_001",
        run_id="run_001",
        campaign_id="cmp_fittrack",
        draft_id="draft_fittrack",
        objective=CampaignObjective.REGISTRATIONS,
        occurred_at="2026-05-12T12:00:00Z",
        metrics=PerformanceMetrics(
            impressions=10_000,
            clicks=500,
            spend="1000.00",
            conversions=20,
        ),
        strategy_context=FeedbackStrategyContext(
            strategy_id="strategy_001",
            draft_id="draft_fittrack",
            target_cpa="20.00",
            optimization_rules=[
                OptimizationRule(
                    rule_id="strategy_001:rule:cpa_guardrail",
                    trigger_metric="cost_per_result",
                    condition="Observed CPA exceeds target by more than 20%.",
                    recommended_action="Shift budget toward the best converting lane.",
                    owner_role=AgentRole.BUDGET_OPTIMIZER,
                    priority=1,
                    rationale="CPA is the primary efficiency guardrail.",
                ),
            ],
        ),
    )
    analysis = analyze_campaign_performance_event(event)
    detail = CampaignPerformanceEventDetailResponse(
        event_id=event.event_id,
        advertiser_id=event.advertiser_id,
        run_id=event.run_id,
        campaign_id=event.campaign_id,
        draft_id=event.draft_id,
        objective=event.objective,
        event_type=event.event_type,
        occurred_at=event.occurred_at,
        metrics=event.metrics,
        status="analyzed",
        metadata={},
        analysis=analysis,
        created_at=analysis.created_at,
        updated_at=analysis.created_at,
    )

    action_plan = build_campaign_feedback_action_plan(detail)

    assert action_plan.event_id == "evt_action_plan"
    assert action_plan.feedback_id == analysis.feedback_id
    assert action_plan.strategy_id == "strategy_001"
    assert action_plan.draft_id == "draft_fittrack"
    assert action_plan.health_status == FeedbackHealthStatus.UNDERPERFORMING
    assert "Observed CPA is 50.00 against target CPA 20.00" in action_plan.summary
    assert [step.action_type for step in action_plan.steps] == [
        FeedbackActionType.ADJUST_BUDGET,
        FeedbackActionType.REFRESH_CREATIVE,
    ]
    assert action_plan.steps[0].owner_role == AgentRole.BUDGET_OPTIMIZER
    assert action_plan.steps[0].tool_name == "optimize_budget"
    assert action_plan.steps[0].recommended_action == (
        "Shift budget toward the best converting lane."
    )
    assert action_plan.steps[0].matched_strategy_rule_ids == [
        "strategy_001:rule:cpa_guardrail"
    ]
    assert action_plan.steps[0].requires_human_approval is True
    assert action_plan.steps[0].status == "draft_recommendation"
    assert action_plan.steps[0].params["event_id"] == "evt_action_plan"
    assert action_plan.steps[1].owner_role == AgentRole.CREATIVE_STRATEGIST
    assert action_plan.steps[1].tool_name == "generate_creative_brief"
    assert action_plan.guardrails[-1].startswith("Action plan steps are recommendations")


def test_feedback_optimization_draft_maps_action_steps_to_draft_changes() -> None:
    event = CampaignPerformanceEventRequest(
        event_id="evt_optimization_draft",
        advertiser_id="adv_fitness_001",
        run_id="run_001",
        campaign_id="cmp_fittrack",
        draft_id="draft_fittrack",
        objective=CampaignObjective.REGISTRATIONS,
        occurred_at="2026-05-12T12:00:00Z",
        metrics=PerformanceMetrics(
            impressions=10_000,
            clicks=500,
            spend="1000.00",
            conversions=20,
        ),
        target_cpa="20.00",
    )
    analysis = analyze_campaign_performance_event(event)
    detail = CampaignPerformanceEventDetailResponse(
        event_id=event.event_id,
        advertiser_id=event.advertiser_id,
        run_id=event.run_id,
        campaign_id=event.campaign_id,
        draft_id=event.draft_id,
        objective=event.objective,
        event_type=event.event_type,
        occurred_at=event.occurred_at,
        metrics=event.metrics,
        status="analyzed",
        metadata={},
        analysis=analysis,
        created_at=analysis.created_at,
        updated_at=analysis.created_at,
    )

    optimization_draft = build_campaign_feedback_optimization_draft(detail)

    assert optimization_draft.optimization_draft_id.startswith("optimization_draft_")
    assert optimization_draft.event_id == "evt_optimization_draft"
    assert optimization_draft.base_draft_id == "draft_fittrack"
    assert optimization_draft.status == "draft"
    assert optimization_draft.requires_human_approval is True
    assert "2 reviewable change(s)" in optimization_draft.summary
    assert [change.change_type for change in optimization_draft.changes] == [
        "budget",
        "creative",
    ]
    assert optimization_draft.changes[0].status == "draft_change"
    assert optimization_draft.changes[0].requires_human_approval is True
    assert optimization_draft.changes[0].params["budget_guardrail"].startswith("Do not")
    assert optimization_draft.changes[1].params["creative_refresh_focus"] == [
        "conversion proof",
        "first useful product moment",
        "clearer value proposition",
    ]
    assert optimization_draft.guardrails[-1].startswith("Optimization draft is review-only")


def test_feedback_optimization_review_defaults_to_all_draft_changes() -> None:
    event = CampaignPerformanceEventRequest(
        event_id="evt_optimization_review",
        advertiser_id="adv_fitness_001",
        run_id="run_001",
        campaign_id="cmp_fittrack",
        draft_id="draft_fittrack",
        objective=CampaignObjective.REGISTRATIONS,
        occurred_at="2026-05-12T12:00:00Z",
        metrics=PerformanceMetrics(
            impressions=10_000,
            clicks=500,
            spend="1000.00",
            conversions=20,
        ),
        target_cpa="20.00",
    )
    analysis = analyze_campaign_performance_event(event)
    detail = CampaignPerformanceEventDetailResponse(
        event_id=event.event_id,
        advertiser_id=event.advertiser_id,
        run_id=event.run_id,
        campaign_id=event.campaign_id,
        draft_id=event.draft_id,
        objective=event.objective,
        event_type=event.event_type,
        occurred_at=event.occurred_at,
        metrics=event.metrics,
        status="analyzed",
        metadata={},
        analysis=analysis,
        created_at=analysis.created_at,
        updated_at=analysis.created_at,
    )
    optimization_draft = build_campaign_feedback_optimization_draft(detail)

    review = build_campaign_feedback_optimization_review(
        optimization_draft,
        CampaignFeedbackOptimizationReviewRequest(
            decision=FeedbackOptimizationReviewDecision.APPROVED,
            reviewer_id="operator_001",
            notes="Approve the safe draft changes.",
        ),
    )

    assert review.review_id.startswith("feedback_review_")
    assert review.optimization_draft_id == optimization_draft.optimization_draft_id
    assert review.event_id == event.event_id
    assert review.decision == FeedbackOptimizationReviewDecision.APPROVED
    assert review.reviewer_id == "operator_001"
    assert review.selected_change_ids == [
        change.change_id for change in optimization_draft.changes
    ]
    assert review.optimization_draft == optimization_draft


def test_feedback_optimization_review_rejects_unknown_selected_change_id() -> None:
    event = CampaignPerformanceEventRequest(
        event_id="evt_unknown_change_review",
        advertiser_id="adv_fitness_001",
        run_id="run_001",
        draft_id="draft_fittrack",
        objective=CampaignObjective.REGISTRATIONS,
        occurred_at="2026-05-12T12:00:00Z",
        metrics=PerformanceMetrics(
            impressions=10_000,
            clicks=500,
            spend="1000.00",
            conversions=20,
        ),
        target_cpa="20.00",
    )
    analysis = analyze_campaign_performance_event(event)
    detail = CampaignPerformanceEventDetailResponse(
        event_id=event.event_id,
        advertiser_id=event.advertiser_id,
        run_id=event.run_id,
        campaign_id=event.campaign_id,
        draft_id=event.draft_id,
        objective=event.objective,
        event_type=event.event_type,
        occurred_at=event.occurred_at,
        metrics=event.metrics,
        status="analyzed",
        metadata={},
        analysis=analysis,
        created_at=analysis.created_at,
        updated_at=analysis.created_at,
    )
    optimization_draft = build_campaign_feedback_optimization_draft(detail)

    with pytest.raises(ValueError, match="unknown change IDs"):
        build_campaign_feedback_optimization_review(
            optimization_draft,
            CampaignFeedbackOptimizationReviewRequest(
                decision=FeedbackOptimizationReviewDecision.NEEDS_REVISION,
                reviewer_id="operator_001",
                selected_change_ids=["missing_change"],
            ),
        )


def test_feedback_revision_draft_uses_reviewer_notes_for_selected_changes() -> None:
    review = _feedback_optimization_review(
        decision=FeedbackOptimizationReviewDecision.NEEDS_REVISION,
        review_id="feedback_review_revision_001",
        notes="Reduce budget movement and explain why creative is not changed.",
    )

    revision_draft = build_campaign_feedback_optimization_revision_draft(review)

    assert revision_draft.revision_draft_id.startswith("feedback_revision_draft_")
    assert revision_draft.source_review_id == review.review_id
    assert revision_draft.original_optimization_draft_id == review.optimization_draft_id
    assert revision_draft.status == "draft"
    assert revision_draft.requires_human_approval is True
    assert revision_draft.reviewer_notes == review.notes
    assert len(revision_draft.changes) == 1
    revised_change = revision_draft.changes[0]
    assert revised_change.change_id.startswith("feedback_revision_change_")
    assert revised_change.status == "draft_change"
    assert revised_change.requires_human_approval is True
    assert revised_change.params["revision_source_review_id"] == review.review_id
    assert revised_change.params["original_change_id"] == review.selected_change_ids[0]
    assert "Reduce budget movement" in revised_change.description
    assert revision_draft.guardrails[-1].startswith("Revision draft generation")


def test_feedback_revision_draft_requires_needs_revision_review() -> None:
    review = _feedback_optimization_review(
        decision=FeedbackOptimizationReviewDecision.APPROVED,
        review_id="feedback_review_revision_blocked",
    )

    with pytest.raises(FeedbackRevisionDraftNotRequestedError):
        build_campaign_feedback_optimization_revision_draft(review)


def test_feedback_revision_reviewable_draft_can_be_approved_for_execution_plan() -> None:
    source_review = _feedback_optimization_review(
        decision=FeedbackOptimizationReviewDecision.NEEDS_REVISION,
        review_id="feedback_review_revision_reviewable",
        notes="Make the revised budget change more conservative.",
    )
    reviewable_draft = build_campaign_feedback_revision_reviewable_draft(source_review)

    approved_revision_review = build_campaign_feedback_optimization_review(
        reviewable_draft,
        CampaignFeedbackOptimizationReviewRequest(
            decision=FeedbackOptimizationReviewDecision.APPROVED,
            reviewer_id="operator_002",
            selected_change_ids=[reviewable_draft.changes[0].change_id],
        ),
        review_id="feedback_review_revision_approved",
    )
    execution_plan = build_feedback_execution_plan(approved_revision_review)

    assert reviewable_draft.optimization_draft_id.startswith("feedback_revision_draft_")
    assert reviewable_draft.health_status == source_review.optimization_draft.health_status
    assert reviewable_draft.changes[0].params["revision_source_review_id"] == (
        source_review.review_id
    )
    assert approved_revision_review.optimization_draft_id == (
        reviewable_draft.optimization_draft_id
    )
    assert execution_plan.review_id == approved_revision_review.review_id
    assert execution_plan.optimization_draft_id == reviewable_draft.optimization_draft_id
    assert execution_plan.steps[0].change_id == reviewable_draft.changes[0].change_id


def test_feedback_review_lineage_links_source_revision_and_execution_ready_review() -> None:
    source_review = _feedback_optimization_review(
        decision=FeedbackOptimizationReviewDecision.NEEDS_REVISION,
        review_id="feedback_review_lineage_source",
        notes="Revise budget movement before approval.",
    )
    reviewable_draft = build_campaign_feedback_revision_reviewable_draft(source_review)
    approved_revision_review = build_campaign_feedback_optimization_review(
        reviewable_draft,
        CampaignFeedbackOptimizationReviewRequest(
            decision=FeedbackOptimizationReviewDecision.APPROVED,
            reviewer_id="operator_002",
            selected_change_ids=[reviewable_draft.changes[0].change_id],
        ),
        review_id="feedback_review_lineage_revision_approved",
    )
    store = _ReviewLineageStore([source_review, approved_revision_review])
    execution_plan = build_feedback_execution_plan(approved_revision_review)
    dry_run = dry_run_feedback_execution_plan(execution_plan)
    execution_store = _ExecutionLineageStore([dry_run])

    lineage = build_feedback_optimization_review_lineage(
        source_review,
        store,
        execution_store,
    )

    assert lineage.requested_review_id == source_review.review_id
    assert lineage.lineage_stage == "revision_requested"
    assert lineage.source_review_id == source_review.review_id
    assert lineage.revision_draft is not None
    assert lineage.revision_draft.revision_draft_id == reviewable_draft.optimization_draft_id
    assert [review.review_id for review in lineage.revision_reviews] == [
        approved_revision_review.review_id
    ]
    assert lineage.execution_ready_review_ids == [approved_revision_review.review_id]
    assert len(lineage.execution_summaries) == 1
    execution_summary = lineage.execution_summaries[0]
    assert execution_summary.review_id == approved_revision_review.review_id
    assert execution_summary.execution_plan_id == execution_plan.execution_plan_id
    assert execution_summary.step_count == 1
    assert execution_summary.dry_run_count == 1
    assert execution_summary.latest_dry_run_status == "passed"
    assert execution_summary.dry_runs[0].dry_run_id == dry_run.dry_run_id


def test_feedback_review_lineage_resolves_source_from_revision_review() -> None:
    source_review = _feedback_optimization_review(
        decision=FeedbackOptimizationReviewDecision.NEEDS_REVISION,
        review_id="feedback_review_lineage_source_from_revision",
    )
    reviewable_draft = build_campaign_feedback_revision_reviewable_draft(source_review)
    approved_revision_review = build_campaign_feedback_optimization_review(
        reviewable_draft,
        CampaignFeedbackOptimizationReviewRequest(
            decision=FeedbackOptimizationReviewDecision.APPROVED,
            reviewer_id="operator_002",
            selected_change_ids=[reviewable_draft.changes[0].change_id],
        ),
        review_id="feedback_review_lineage_revision_target",
    )
    store = _ReviewLineageStore([source_review, approved_revision_review])

    lineage = build_feedback_optimization_review_lineage(approved_revision_review, store)

    assert lineage.requested_review_id == approved_revision_review.review_id
    assert lineage.lineage_stage == "revision_review"
    assert lineage.source_review_id == source_review.review_id
    assert lineage.target_review.review_id == approved_revision_review.review_id
    assert lineage.source_review.review_id == source_review.review_id
    assert lineage.approved_review_ids == [approved_revision_review.review_id]


def test_feedback_review_lineage_list_filters_revision_reviews_with_execution_audit() -> None:
    source_review = _feedback_optimization_review(
        decision=FeedbackOptimizationReviewDecision.NEEDS_REVISION,
        review_id="feedback_review_lineage_list_source",
    )
    reviewable_draft = build_campaign_feedback_revision_reviewable_draft(source_review)
    approved_revision_review = build_campaign_feedback_optimization_review(
        reviewable_draft,
        CampaignFeedbackOptimizationReviewRequest(
            decision=FeedbackOptimizationReviewDecision.APPROVED,
            reviewer_id="operator_002",
            selected_change_ids=[reviewable_draft.changes[0].change_id],
        ),
        review_id="feedback_review_lineage_list_revision",
    )
    store = _ReviewLineageStore([source_review, approved_revision_review])
    execution_plan = build_feedback_execution_plan(approved_revision_review)
    dry_run = dry_run_feedback_execution_plan(execution_plan)
    execution_store = _ExecutionLineageStore([dry_run])

    lineages = list_feedback_optimization_review_lineages(
        store,
        execution_store,
        decision=FeedbackOptimizationReviewDecision.APPROVED,
        lineage_stage="revision_review",
        limit=10,
    )

    assert lineages.count == 1
    assert lineages.limit == 10
    assert lineages.decision == FeedbackOptimizationReviewDecision.APPROVED
    assert lineages.lineage_stage == "revision_review"
    lineage = lineages.items[0]
    assert lineage.requested_review_id == approved_revision_review.review_id
    assert lineage.source_review_id == source_review.review_id
    assert lineage.execution_summaries[0].dry_run_count == 1
    assert lineage.execution_summaries[0].dry_runs[0].dry_run_id == dry_run.dry_run_id


def test_feedback_loop_summary_reports_current_operator_stage() -> None:
    event = CampaignPerformanceEventRequest(
        event_id="evt_feedback_loop_summary",
        advertiser_id="adv_fitness_001",
        run_id="run_001",
        campaign_id="cmp_fittrack",
        draft_id="draft_fittrack",
        objective=CampaignObjective.REGISTRATIONS,
        occurred_at="2026-05-12T12:00:00Z",
        metrics=PerformanceMetrics(
            impressions=10_000,
            clicks=500,
            spend="1000.00",
            conversions=20,
        ),
        target_cpa="20.00",
    )
    analysis = analyze_campaign_performance_event(event)
    detail = CampaignPerformanceEventDetailResponse(
        event_id=event.event_id,
        advertiser_id=event.advertiser_id,
        run_id=event.run_id,
        campaign_id=event.campaign_id,
        draft_id=event.draft_id,
        objective=event.objective,
        event_type=event.event_type,
        occurred_at=event.occurred_at,
        metrics=event.metrics,
        status="analyzed",
        metadata={},
        analysis=analysis,
        created_at=analysis.created_at,
        updated_at=analysis.created_at,
    )
    optimization_draft = build_campaign_feedback_optimization_draft(detail)
    source_review = build_campaign_feedback_optimization_review(
        optimization_draft,
        CampaignFeedbackOptimizationReviewRequest(
            decision=FeedbackOptimizationReviewDecision.NEEDS_REVISION,
            reviewer_id="operator_001",
            selected_change_ids=[optimization_draft.changes[0].change_id],
        ),
        review_id="feedback_review_summary_source",
    )
    revision_draft = build_campaign_feedback_revision_reviewable_draft(source_review)
    approved_revision_review = build_campaign_feedback_optimization_review(
        revision_draft,
        CampaignFeedbackOptimizationReviewRequest(
            decision=FeedbackOptimizationReviewDecision.APPROVED,
            reviewer_id="operator_002",
            selected_change_ids=[revision_draft.changes[0].change_id],
        ),
        review_id="feedback_review_summary_revision",
    )
    execution_plan = build_feedback_execution_plan(approved_revision_review)
    dry_run = dry_run_feedback_execution_plan(execution_plan)
    review_store = _ReviewLineageStore([source_review, approved_revision_review])
    execution_store = _ExecutionLineageStore([dry_run])

    summary = build_campaign_feedback_loop_summary(
        detail,
        review_store,
        execution_store,
        review_persistence_enabled=True,
        execution_persistence_enabled=True,
    )

    assert summary.event_id == event.event_id
    assert summary.current_stage == "dry_run_passed"
    assert summary.review_count == 2
    assert summary.lineage_count == 2
    assert summary.dry_run_count == 1
    assert summary.latest_review_id == approved_revision_review.review_id
    assert summary.latest_dry_run_id == dry_run.dry_run_id
    assert summary.approved_review_ids == [approved_revision_review.review_id]
    assert summary.execution_ready_review_ids == [approved_revision_review.review_id]
    assert summary.action_plan.event_id == event.event_id
    assert summary.optimization_draft.optimization_draft_id == (
        optimization_draft.optimization_draft_id
    )
    assert "manual campaign-platform handoff" in summary.next_operator_actions[0]


def test_feedback_loop_summary_reports_latest_handoff_outcome_stage() -> None:
    event = CampaignPerformanceEventRequest(
        event_id="evt_feedback_loop_summary_handoff",
        advertiser_id="adv_fitness_001",
        run_id="run_001",
        campaign_id="cmp_fittrack",
        draft_id="draft_fittrack",
        objective=CampaignObjective.REGISTRATIONS,
        occurred_at="2026-05-12T12:00:00Z",
        metrics=PerformanceMetrics(
            impressions=10_000,
            clicks=500,
            spend="1000.00",
            conversions=20,
        ),
        target_cpa="20.00",
    )
    analysis = analyze_campaign_performance_event(event)
    detail = CampaignPerformanceEventDetailResponse(
        event_id=event.event_id,
        advertiser_id=event.advertiser_id,
        run_id=event.run_id,
        campaign_id=event.campaign_id,
        draft_id=event.draft_id,
        objective=event.objective,
        event_type=event.event_type,
        occurred_at=event.occurred_at,
        metrics=event.metrics,
        status="analyzed",
        metadata={},
        analysis=analysis,
        created_at=analysis.created_at,
        updated_at=analysis.created_at,
    )
    optimization_draft = build_campaign_feedback_optimization_draft(detail)
    review = build_campaign_feedback_optimization_review(
        optimization_draft,
        CampaignFeedbackOptimizationReviewRequest(
            decision=FeedbackOptimizationReviewDecision.APPROVED,
            reviewer_id="operator_001",
            selected_change_ids=[optimization_draft.changes[0].change_id],
        ),
        review_id="feedback_review_summary_handoff",
    )
    execution_plan = build_feedback_execution_plan(review)
    dry_run = dry_run_feedback_execution_plan(execution_plan)
    execution_store = _ExecutionLineageStore([dry_run])
    handoff_package = build_feedback_handoff_package(review, execution_store)
    handoff_record = build_feedback_handoff_record(
        handoff_package,
        CampaignFeedbackHandoffRecordRequest(
            outcome=FeedbackHandoffOutcome.APPLIED,
            operator_id="operator_001",
            completed_step_ids=[step.step_id for step in handoff_package.manual_steps],
        ),
    )

    summary = build_campaign_feedback_loop_summary(
        detail,
        _ReviewLineageStore([review]),
        execution_store,
        _HandoffSummaryStore([handoff_record]),
        review_persistence_enabled=True,
        execution_persistence_enabled=True,
        handoff_persistence_enabled=True,
    )

    assert summary.current_stage == "handoff_applied"
    assert summary.handoff_persistence_enabled is True
    assert summary.handoff_record_count == 1
    assert summary.latest_handoff_record_id == handoff_record.handoff_record_id
    assert summary.latest_handoff_outcome == FeedbackHandoffOutcome.APPLIED
    assert summary.handoff_records.items[0].handoff_record_id == (
        handoff_record.handoff_record_id
    )
    assert "Monitor the manually applied changes" in summary.next_operator_actions[0]
    assert "handoffs=1" in summary.summary


def test_feedback_execution_plan_maps_approved_review_to_dry_run_tool_intents() -> None:
    event = CampaignPerformanceEventRequest(
        event_id="evt_execution_plan",
        advertiser_id="adv_fitness_001",
        run_id="run_001",
        campaign_id="cmp_fittrack",
        draft_id="draft_fittrack",
        objective=CampaignObjective.REGISTRATIONS,
        occurred_at="2026-05-12T12:00:00Z",
        metrics=PerformanceMetrics(
            impressions=10_000,
            clicks=500,
            spend="1000.00",
            conversions=20,
        ),
        target_cpa="20.00",
    )
    analysis = analyze_campaign_performance_event(event)
    detail = CampaignPerformanceEventDetailResponse(
        event_id=event.event_id,
        advertiser_id=event.advertiser_id,
        run_id=event.run_id,
        campaign_id=event.campaign_id,
        draft_id=event.draft_id,
        objective=event.objective,
        event_type=event.event_type,
        occurred_at=event.occurred_at,
        metrics=event.metrics,
        status="analyzed",
        metadata={},
        analysis=analysis,
        created_at=analysis.created_at,
        updated_at=analysis.created_at,
    )
    optimization_draft = build_campaign_feedback_optimization_draft(detail)
    selected_change_id = optimization_draft.changes[0].change_id
    review = build_campaign_feedback_optimization_review(
        optimization_draft,
        CampaignFeedbackOptimizationReviewRequest(
            decision=FeedbackOptimizationReviewDecision.APPROVED,
            reviewer_id="operator_001",
            selected_change_ids=[selected_change_id],
        ),
        review_id="feedback_review_execution_001",
    )

    execution_plan = build_feedback_execution_plan(review)

    assert execution_plan.execution_plan_id.startswith("feedback_execution_plan_")
    assert execution_plan.review_id == "feedback_review_execution_001"
    assert execution_plan.execution_mode == "dry_run"
    assert execution_plan.status == "ready"
    assert len(execution_plan.steps) == 1
    step = execution_plan.steps[0]
    assert step.change_id == selected_change_id
    assert step.tool_intent.tool_name == "draft_budget_reallocation"
    assert step.tool_intent.params["dry_run"] is True
    assert step.tool_intent.params["approval_reference_id"] == review.review_id
    assert step.tool_intent.params["campaign_id"] == "cmp_fittrack"
    assert "second live-execution gate" in step.preconditions[-1]
    assert step.rollback_plan.startswith("Discard the draft budget")
    assert execution_plan.guardrails[0].startswith("Execution mode is dry_run")


def test_feedback_handoff_package_marks_passed_dry_run_ready_for_manual_handoff() -> None:
    review = _feedback_optimization_review(
        decision=FeedbackOptimizationReviewDecision.APPROVED,
        review_id="feedback_review_handoff_ready",
    )
    execution_plan = build_feedback_execution_plan(review)
    dry_run = dry_run_feedback_execution_plan(execution_plan)
    execution_store = _ExecutionLineageStore([dry_run])

    package = build_feedback_handoff_package(review, execution_store)

    assert package.handoff_package_id.startswith("feedback_handoff_")
    assert package.status == "ready_for_manual_handoff"
    assert package.review_id == review.review_id
    assert package.execution_plan_id == execution_plan.execution_plan_id
    assert package.latest_dry_run_id == dry_run.dry_run_id
    assert package.latest_dry_run_status == "passed"
    assert package.step_count == 1
    assert package.validated_step_count == 1
    assert package.blocked_step_count == 0
    assert package.manual_steps[0].dry_run_status == "validated"
    assert package.manual_steps[0].source_params["approval_reference_id"] == review.review_id
    assert "manual campaign-platform handoff" in package.operator_checklist[-1]


def test_feedback_handoff_package_marks_missing_validation_when_no_dry_run() -> None:
    review = _feedback_optimization_review(
        decision=FeedbackOptimizationReviewDecision.APPROVED,
        review_id="feedback_review_handoff_missing_validation",
    )

    package = build_feedback_handoff_package(review)

    assert package.status == "validation_missing"
    assert package.latest_dry_run_id is None
    assert package.validated_step_count == 0
    assert package.blocked_step_count == 0
    assert package.manual_steps[0].dry_run_status == "not_validated"
    assert "Run dry-run execution validation" in package.operator_checklist[-1]


def test_feedback_handoff_record_marks_ready_package_applied() -> None:
    review = _feedback_optimization_review(
        decision=FeedbackOptimizationReviewDecision.APPROVED,
        review_id="feedback_review_handoff_record_applied",
    )
    execution_plan = build_feedback_execution_plan(review)
    dry_run = dry_run_feedback_execution_plan(execution_plan)
    package = build_feedback_handoff_package(review, _ExecutionLineageStore([dry_run]))
    completed_step_ids = [step.step_id for step in package.manual_steps]

    record = build_feedback_handoff_record(
        package,
        CampaignFeedbackHandoffRecordRequest(
            outcome=FeedbackHandoffOutcome.APPLIED,
            operator_id="operator_001",
            notes="Manually applied approved change in ads workspace.",
            completed_step_ids=completed_step_ids,
        ),
        handoff_record_id="feedback_handoff_record_test",
    )

    assert record.handoff_record_id == "feedback_handoff_record_test"
    assert record.handoff_package_id == package.handoff_package_id
    assert record.review_id == review.review_id
    assert record.latest_dry_run_id == dry_run.dry_run_id
    assert record.outcome == FeedbackHandoffOutcome.APPLIED
    assert record.completed_step_ids == completed_step_ids
    assert record.requires_follow_up is False
    assert record.handoff_package.status == "ready_for_manual_handoff"
    assert "does not execute live campaign changes" in record.guardrails[0]


def test_feedback_handoff_record_blocks_applied_without_ready_package() -> None:
    review = _feedback_optimization_review(
        decision=FeedbackOptimizationReviewDecision.APPROVED,
        review_id="feedback_review_handoff_record_not_ready",
    )
    package = build_feedback_handoff_package(review)

    with pytest.raises(FeedbackHandoffRecordNotReadyError):
        build_feedback_handoff_record(
            package,
            CampaignFeedbackHandoffRecordRequest(
                outcome=FeedbackHandoffOutcome.APPLIED,
                operator_id="operator_001",
                completed_step_ids=[package.manual_steps[0].step_id],
            ),
        )


def test_feedback_handoff_record_rejects_unknown_completed_step() -> None:
    review = _feedback_optimization_review(
        decision=FeedbackOptimizationReviewDecision.APPROVED,
        review_id="feedback_review_handoff_record_unknown_step",
    )
    execution_plan = build_feedback_execution_plan(review)
    dry_run = dry_run_feedback_execution_plan(execution_plan)
    package = build_feedback_handoff_package(review, _ExecutionLineageStore([dry_run]))

    with pytest.raises(FeedbackHandoffRecordStepMismatchError):
        build_feedback_handoff_record(
            package,
            CampaignFeedbackHandoffRecordRequest(
                outcome=FeedbackHandoffOutcome.APPLIED,
                operator_id="operator_001",
                completed_step_ids=["unknown_step"],
            ),
        )


def test_feedback_execution_plan_requires_approved_review() -> None:
    event = CampaignPerformanceEventRequest(
        event_id="evt_execution_plan_not_approved",
        advertiser_id="adv_fitness_001",
        run_id="run_001",
        draft_id="draft_fittrack",
        objective=CampaignObjective.REGISTRATIONS,
        occurred_at="2026-05-12T12:00:00Z",
        metrics=PerformanceMetrics(
            impressions=10_000,
            clicks=500,
            spend="1000.00",
            conversions=20,
        ),
        target_cpa="20.00",
    )
    analysis = analyze_campaign_performance_event(event)
    detail = CampaignPerformanceEventDetailResponse(
        event_id=event.event_id,
        advertiser_id=event.advertiser_id,
        run_id=event.run_id,
        campaign_id=event.campaign_id,
        draft_id=event.draft_id,
        objective=event.objective,
        event_type=event.event_type,
        occurred_at=event.occurred_at,
        metrics=event.metrics,
        status="analyzed",
        metadata={},
        analysis=analysis,
        created_at=analysis.created_at,
        updated_at=analysis.created_at,
    )
    optimization_draft = build_campaign_feedback_optimization_draft(detail)
    review = build_campaign_feedback_optimization_review(
        optimization_draft,
        CampaignFeedbackOptimizationReviewRequest(
            decision=FeedbackOptimizationReviewDecision.NEEDS_REVISION,
            reviewer_id="operator_001",
        ),
        review_id="feedback_review_execution_blocked",
    )

    with pytest.raises(FeedbackExecutionPlanNotApprovedError):
        build_feedback_execution_plan(review)


def test_feedback_execution_dry_run_validates_draft_tool_intents() -> None:
    execution_plan = _approved_feedback_execution_plan()

    dry_run = dry_run_feedback_execution_plan(execution_plan)

    assert dry_run.dry_run_id.startswith("feedback_dry_run_")
    assert dry_run.execution_plan_id == execution_plan.execution_plan_id
    assert dry_run.status == "passed"
    assert dry_run.validated_step_count == 1
    assert dry_run.blocked_step_count == 0
    assert dry_run.step_results[0].status == "validated"
    assert dry_run.step_results[0].tool_result.success is True
    assert dry_run.step_results[0].tool_result.payload["mutation_performed"] is False


def test_feedback_execution_dry_run_blocks_mismatched_tool_identity() -> None:
    execution_plan = _approved_feedback_execution_plan("feedback_review_identity_001")
    step = execution_plan.steps[0]
    mismatched_intent = step.tool_intent.model_copy(
        update={
            "params": {
                **step.tool_intent.params,
                "advertiser_id": "adv_other",
            }
        }
    )
    mismatched_plan = execution_plan.model_copy(
        update={
            "steps": [
                step.model_copy(update={"tool_intent": mismatched_intent}),
            ]
        }
    )

    dry_run = dry_run_feedback_execution_plan(mismatched_plan)

    assert dry_run.status == "failed"
    assert dry_run.validated_step_count == 0
    assert dry_run.blocked_step_count == 1
    assert dry_run.step_results[0].status == "blocked"
    assert (
        dry_run.step_results[0].tool_result.error is not None
        and dry_run.step_results[0].tool_result.error.code == "EXECUTION_CONTEXT_MISMATCH"
    )
    assert "tool_registry_validation_skipped" in dry_run.step_results[0].safety_checks[-1]
