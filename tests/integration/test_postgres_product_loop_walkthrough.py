import importlib.util
import os
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy.engine import make_url

pytestmark = pytest.mark.integration
SCRIPT_PATH = (
    Path(__file__).resolve().parents[2] / "scripts" / "verify_phase2_mvp.py"
)
SCRIPT_SPEC = importlib.util.spec_from_file_location(
    "verify_phase2_mvp",
    SCRIPT_PATH,
)
if SCRIPT_SPEC is None or SCRIPT_SPEC.loader is None:
    raise RuntimeError(f"Unable to load walkthrough script: {SCRIPT_PATH}")
SCRIPT_MODULE = importlib.util.module_from_spec(SCRIPT_SPEC)
SCRIPT_SPEC.loader.exec_module(SCRIPT_MODULE)
DEFAULT_TEST_DATABASE_URL = str(SCRIPT_MODULE.DEFAULT_TEST_DATABASE_URL)
run_phase2_mvp_acceptance: Any = SCRIPT_MODULE.run_phase2_mvp_acceptance


def test_phase2_mvp_acceptance_verifier() -> None:
    if os.getenv("RUN_POSTGRES_INTEGRATION") != "1":
        pytest.skip("Set RUN_POSTGRES_INTEGRATION=1 to run live PostgreSQL tests.")

    summary = run_phase2_mvp_acceptance(
        make_url(os.getenv("TEST_DATABASE_URL", DEFAULT_TEST_DATABASE_URL)),
    )
    product_loop = summary["product_loop"]
    control_plane = summary["control_plane"]

    assert summary["status"] == "passed"
    assert summary["phase"] == "phase2_functionally_complete"
    assert control_plane["external_job_process"]["completed"] == 1
    assert control_plane["external_job_process"]["final_status"] == "completed"
    assert control_plane["run_lifecycle_cli"]["get_status"] == "failed"
    assert control_plane["run_lifecycle_cli"]["resumed_run_id"].startswith("run_")
    assert control_plane["run_lifecycle_cli"]["retried_run_id"].startswith("run_")
    assert control_plane["run_lifecycle_cli"]["retried_run_id"] != control_plane[
        "run_lifecycle_cli"
    ]["resumed_run_id"]
    assert control_plane["ops_summary_cli"]["failed_run_count"] == 1
    assert control_plane["ops_summary_cli"]["guardrail_count"] >= 1
    assert product_loop["feedback_event"]["advertiser_memory_status"] == "queued"
    assert product_loop["followup_event"]["advertiser_memory_status"] == "queued"
    assert product_loop["feedback_outcome_report"]["outcome_status"] == "improved"
    assert product_loop["feedback_outcome_report"]["followup_event_id"] == product_loop[
        "followup_event"
    ]["event_id"]
    assert product_loop["feedback_outcome_report"]["cli_outcome_status"] == "improved"
    assert product_loop["action_plan"]["first_action_type"] == "adjust_budget"
    assert product_loop["action_plan"]["first_action_status"] == "draft_recommendation"
    assert product_loop["optimization_draft"]["status"] == "draft"
    assert product_loop["optimization_draft"]["first_change_type"] == "budget"
    assert product_loop["review"]["decision"] == "approved"
    assert product_loop["review"]["selected_change_count"] == 1
    assert product_loop["review"]["cli_submitted_decision"] == "needs_revision"
    assert product_loop["revision_draft"]["source_review_id"] == product_loop["review"][
        "cli_submitted_review_id"
    ]
    assert product_loop["revision_draft"]["change_count"] == product_loop["review"][
        "cli_submitted_selected_change_count"
    ]
    assert product_loop["revision_review"]["decision"] == "approved"
    assert product_loop["revision_review"]["optimization_draft_id"] == (
        product_loop["revision_draft"]["revision_draft_id"]
    )
    assert product_loop["revision_review"]["selected_change_count"] == 1
    assert product_loop["revision_review"]["execution_plan_step_count"] == 1
    assert product_loop["revision_review"]["dry_run_status"] == "passed"
    assert product_loop["handoff_package"]["status"] == "ready_for_manual_handoff"
    assert product_loop["handoff_package"]["latest_dry_run_id"] == product_loop["revision_review"][
        "dry_run_id"
    ]
    assert product_loop["handoff_package"]["manual_step_count"] == 1
    assert product_loop["handoff_package"]["first_manual_step_status"] == "validated"
    assert product_loop["handoff_package"]["cli_handoff_package_id"] == product_loop[
        "handoff_package"
    ]["handoff_package_id"]
    assert product_loop["handoff_package"]["cli_status"] == "ready_for_manual_handoff"
    assert product_loop["handoff_record"]["handoff_package_id"] == product_loop[
        "handoff_package"
    ]["handoff_package_id"]
    assert product_loop["handoff_record"]["outcome"] == "applied"
    assert product_loop["handoff_record"]["completed_step_count"] == 1
    assert product_loop["handoff_record"]["requires_follow_up"] is False
    assert product_loop["handoff_record"]["detail_id"] == product_loop["handoff_record"][
        "handoff_record_id"
    ]
    assert product_loop["handoff_record"]["list_count"] == 1
    assert product_loop["handoff_record"]["cli_handoff_record_id"] == product_loop[
        "handoff_record"
    ]["handoff_record_id"]
    assert product_loop["handoff_record"]["cli_list_count"] == 1
    assert product_loop["review_lineage"]["source_review_id"] == product_loop["review"][
        "cli_submitted_review_id"
    ]
    assert product_loop["review_lineage"]["lineage_stage"] == "revision_requested"
    assert product_loop["review_lineage"]["revision_review_count"] == 1
    assert product_loop["review_lineage"]["execution_ready_review_ids"] == [
        product_loop["revision_review"]["review_id"]
    ]
    assert product_loop["review_lineage"]["execution_summary_count"] == 1
    assert product_loop["review_lineage"]["dry_run_count"] == 1
    assert product_loop["review_lineage"]["latest_dry_run_status"] == "passed"
    assert product_loop["review_lineage"]["cli_lineage_stage"] == "revision_review"
    assert product_loop["review_lineage"]["list_count"] == 1
    assert product_loop["review_lineage"]["list_requested_review_id"] == product_loop[
        "revision_review"
    ]["review_id"]
    assert product_loop["review_lineage"]["list_dry_run_id"] == product_loop["revision_review"][
        "dry_run_id"
    ]
    assert product_loop["review_lineage"]["cli_list_count"] == 1
    assert product_loop["feedback_loop_summary"]["current_stage"] == "handoff_applied"
    assert product_loop["feedback_loop_summary"]["review_count"] == 3
    assert product_loop["feedback_loop_summary"]["lineage_count"] == 3
    assert product_loop["feedback_loop_summary"]["dry_run_count"] == 2
    assert product_loop["feedback_loop_summary"]["handoff_record_count"] == 1
    assert product_loop["feedback_loop_summary"]["latest_dry_run_status"] == "passed"
    assert product_loop["feedback_loop_summary"]["latest_handoff_record_id"] == product_loop[
        "handoff_record"
    ]["handoff_record_id"]
    assert product_loop["feedback_loop_summary"]["latest_handoff_outcome"] == "applied"
    assert product_loop["feedback_loop_summary"]["cli_current_stage"] == "handoff_applied"
    assert product_loop["feedback_loop_summary"]["cli_dry_run_count"] == 2
    assert product_loop["feedback_loop_summary"]["cli_handoff_record_count"] == 1
    assert product_loop["feedback_loop_command_center"]["current_stage"] == "outcome_improved"
    assert product_loop["feedback_loop_command_center"]["outcome_status"] == "improved"
    assert product_loop["feedback_loop_command_center"]["followup_event_id"] == product_loop[
        "followup_event"
    ]["event_id"]
    assert product_loop["feedback_loop_command_center"]["primary_command_id"] == (
        "inspect_feedback_outcome_report"
    )
    assert product_loop["feedback_loop_command_center"]["cli_current_stage"] == (
        "outcome_improved"
    )
    assert product_loop["feedback_loop_command_center"]["cli_outcome_status"] == "improved"
    assert product_loop["feedback_loop_chain"]["outcome_status"] == "improved"
    assert product_loop["feedback_loop_chain"]["followup_event_id"] == product_loop[
        "followup_event"
    ]["event_id"]
    assert product_loop["feedback_loop_chain"]["followup_current_stage"] == "review_pending"
    assert product_loop["feedback_loop_chain"]["recommended_focus"] == (
        "monitor_followup_outcome"
    )
    assert product_loop["feedback_loop_chain"]["recommended_command_id"] == (
        "record_next_performance_event"
    )
    assert product_loop["feedback_loop_chain"]["recommended_command_source"] == (
        "baseline_command_center"
    )
    assert product_loop["feedback_loop_chain"]["cli_recommended_focus"] == (
        "monitor_followup_outcome"
    )
    assert product_loop["feedback_loop_chain"]["cli_recommended_command_id"] == (
        "record_next_performance_event"
    )
    assert product_loop["execution_plan"]["execution_mode"] == "dry_run"
    assert product_loop["execution_plan"]["first_tool_name"] == "draft_budget_reallocation"
    assert product_loop["execution_dry_run"]["status"] == "passed"
    assert product_loop["execution_dry_run"]["detail_status"] == "passed"
    assert product_loop["execution_dry_run"]["list_count"] == 1
    assert product_loop["execution_dry_run"]["validated_step_count"] == 1
    assert product_loop["execution_dry_run"]["blocked_step_count"] == 0
    assert product_loop["outbox"]["completed"] == 1
    assert product_loop["handoff_outbox"]["completed"] == 1
    assert product_loop["cli_reads"]["event_count"] == 2
    assert product_loop["cli_reads"]["first_action_type"] == "adjust_budget"
    assert product_loop["cli_reads"]["first_change_type"] == "budget"
    assert product_loop["cli_reads"]["review_count"] == 1
    assert product_loop["cli_reads"]["revision_draft_id"] == product_loop["revision_draft"][
        "revision_draft_id"
    ]
    assert product_loop["cli_reads"]["revision_review_id"] == product_loop["revision_review"][
        "review_id"
    ]
    assert product_loop["cli_reads"]["handoff_package_id"] == product_loop["handoff_package"][
        "handoff_package_id"
    ]
    assert product_loop["cli_reads"]["handoff_record_id"] == product_loop["handoff_record"][
        "handoff_record_id"
    ]
    assert product_loop["cli_reads"]["handoff_record_count"] == 1
    assert product_loop["cli_reads"]["review_lineage_stage"] == "revision_review"
    assert product_loop["cli_reads"]["review_lineage_count"] == 1
    assert product_loop["cli_reads"]["feedback_loop_stage"] == "handoff_applied"
    assert product_loop["cli_reads"]["feedback_outcome_status"] == "improved"
    assert product_loop["cli_reads"]["execution_plan_id"] == product_loop["execution_plan"][
        "execution_plan_id"
    ]
    assert product_loop["cli_reads"]["execution_dry_run_id"] == product_loop["execution_dry_run"][
        "dry_run_id"
    ]
    assert product_loop["cli_reads"]["execution_dry_run_detail_id"] == product_loop[
        "execution_dry_run"
    ]["dry_run_id"]
    assert product_loop["cli_reads"]["execution_dry_run_count"] == 1
    assert product_loop["cli_reads"]["memory_count"] == 2
    assert product_loop["cli_reads"]["handoff_memory_source_id"] == product_loop["memory"][
        "handoff_source_id"
    ]
    assert product_loop["memory"]["handoff_metadata_record_id"] == product_loop["handoff_record"][
        "handoff_record_id"
    ]
    assert product_loop["memory"]["source_id"] in product_loop["later_strategy"][
        "retrieved_memory_source_ids"
    ]
