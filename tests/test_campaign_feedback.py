import pytest

from ads_growth_agent.contracts import (
    AgentRole,
    CampaignFeedbackExecutionPlanResponse,
    CampaignFeedbackOptimizationReviewRequest,
    CampaignFeedbackOptimizationReviewResponse,
    CampaignObjective,
    CampaignPerformanceEventDetailResponse,
    CampaignPerformanceEventRequest,
    FeedbackActionType,
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
