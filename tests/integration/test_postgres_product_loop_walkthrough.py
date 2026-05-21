import importlib.util
import os
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy.engine import make_url

pytestmark = pytest.mark.integration
SCRIPT_PATH = (
    Path(__file__).resolve().parents[2] / "scripts" / "verify_persisted_product_loop.py"
)
SCRIPT_SPEC = importlib.util.spec_from_file_location(
    "verify_persisted_product_loop",
    SCRIPT_PATH,
)
if SCRIPT_SPEC is None or SCRIPT_SPEC.loader is None:
    raise RuntimeError(f"Unable to load walkthrough script: {SCRIPT_PATH}")
SCRIPT_MODULE = importlib.util.module_from_spec(SCRIPT_SPEC)
SCRIPT_SPEC.loader.exec_module(SCRIPT_MODULE)
DEFAULT_TEST_DATABASE_URL = str(SCRIPT_MODULE.DEFAULT_TEST_DATABASE_URL)
run_persisted_product_loop: Any = SCRIPT_MODULE.run_persisted_product_loop


def test_persisted_product_loop_walkthrough() -> None:
    if os.getenv("RUN_POSTGRES_INTEGRATION") != "1":
        pytest.skip("Set RUN_POSTGRES_INTEGRATION=1 to run live PostgreSQL tests.")

    summary = run_persisted_product_loop(
        make_url(os.getenv("TEST_DATABASE_URL", DEFAULT_TEST_DATABASE_URL)),
    )

    assert summary["status"] == "passed"
    assert summary["feedback_event"]["advertiser_memory_status"] == "queued"
    assert summary["action_plan"]["first_action_type"] == "adjust_budget"
    assert summary["action_plan"]["first_action_status"] == "draft_recommendation"
    assert summary["optimization_draft"]["status"] == "draft"
    assert summary["optimization_draft"]["first_change_type"] == "budget"
    assert summary["review"]["decision"] == "approved"
    assert summary["review"]["selected_change_count"] == 1
    assert summary["review"]["cli_submitted_decision"] == "needs_revision"
    assert summary["revision_draft"]["source_review_id"] == summary["review"][
        "cli_submitted_review_id"
    ]
    assert summary["revision_draft"]["change_count"] == summary["review"][
        "cli_submitted_selected_change_count"
    ]
    assert summary["revision_review"]["decision"] == "approved"
    assert summary["revision_review"]["optimization_draft_id"] == summary["revision_draft"][
        "revision_draft_id"
    ]
    assert summary["revision_review"]["selected_change_count"] == 1
    assert summary["revision_review"]["execution_plan_step_count"] == 1
    assert summary["revision_review"]["dry_run_status"] == "passed"
    assert summary["handoff_package"]["status"] == "ready_for_manual_handoff"
    assert summary["handoff_package"]["latest_dry_run_id"] == summary["revision_review"][
        "dry_run_id"
    ]
    assert summary["handoff_package"]["manual_step_count"] == 1
    assert summary["handoff_package"]["first_manual_step_status"] == "validated"
    assert summary["handoff_package"]["cli_handoff_package_id"] == summary[
        "handoff_package"
    ]["handoff_package_id"]
    assert summary["handoff_package"]["cli_status"] == "ready_for_manual_handoff"
    assert summary["handoff_record"]["handoff_package_id"] == summary[
        "handoff_package"
    ]["handoff_package_id"]
    assert summary["handoff_record"]["outcome"] == "applied"
    assert summary["handoff_record"]["completed_step_count"] == 1
    assert summary["handoff_record"]["requires_follow_up"] is False
    assert summary["handoff_record"]["detail_id"] == summary["handoff_record"][
        "handoff_record_id"
    ]
    assert summary["handoff_record"]["list_count"] == 1
    assert summary["handoff_record"]["cli_handoff_record_id"] == summary[
        "handoff_record"
    ]["handoff_record_id"]
    assert summary["handoff_record"]["cli_list_count"] == 1
    assert summary["review_lineage"]["source_review_id"] == summary["review"][
        "cli_submitted_review_id"
    ]
    assert summary["review_lineage"]["lineage_stage"] == "revision_requested"
    assert summary["review_lineage"]["revision_review_count"] == 1
    assert summary["review_lineage"]["execution_ready_review_ids"] == [
        summary["revision_review"]["review_id"]
    ]
    assert summary["review_lineage"]["execution_summary_count"] == 1
    assert summary["review_lineage"]["dry_run_count"] == 1
    assert summary["review_lineage"]["latest_dry_run_status"] == "passed"
    assert summary["review_lineage"]["cli_lineage_stage"] == "revision_review"
    assert summary["review_lineage"]["list_count"] == 1
    assert summary["review_lineage"]["list_requested_review_id"] == summary[
        "revision_review"
    ]["review_id"]
    assert summary["review_lineage"]["list_dry_run_id"] == summary["revision_review"][
        "dry_run_id"
    ]
    assert summary["review_lineage"]["cli_list_count"] == 1
    assert summary["feedback_loop_summary"]["current_stage"] == "handoff_applied"
    assert summary["feedback_loop_summary"]["review_count"] == 3
    assert summary["feedback_loop_summary"]["lineage_count"] == 3
    assert summary["feedback_loop_summary"]["dry_run_count"] == 2
    assert summary["feedback_loop_summary"]["handoff_record_count"] == 1
    assert summary["feedback_loop_summary"]["latest_dry_run_status"] == "passed"
    assert summary["feedback_loop_summary"]["latest_handoff_record_id"] == summary[
        "handoff_record"
    ]["handoff_record_id"]
    assert summary["feedback_loop_summary"]["latest_handoff_outcome"] == "applied"
    assert summary["feedback_loop_summary"]["cli_current_stage"] == "handoff_applied"
    assert summary["feedback_loop_summary"]["cli_dry_run_count"] == 2
    assert summary["feedback_loop_summary"]["cli_handoff_record_count"] == 1
    assert summary["execution_plan"]["execution_mode"] == "dry_run"
    assert summary["execution_plan"]["first_tool_name"] == "draft_budget_reallocation"
    assert summary["execution_dry_run"]["status"] == "passed"
    assert summary["execution_dry_run"]["detail_status"] == "passed"
    assert summary["execution_dry_run"]["list_count"] == 1
    assert summary["execution_dry_run"]["validated_step_count"] == 1
    assert summary["execution_dry_run"]["blocked_step_count"] == 0
    assert summary["outbox"]["completed"] == 1
    assert summary["handoff_outbox"]["completed"] == 1
    assert summary["cli_reads"]["event_count"] == 1
    assert summary["cli_reads"]["first_action_type"] == "adjust_budget"
    assert summary["cli_reads"]["first_change_type"] == "budget"
    assert summary["cli_reads"]["review_count"] == 1
    assert summary["cli_reads"]["revision_draft_id"] == summary["revision_draft"][
        "revision_draft_id"
    ]
    assert summary["cli_reads"]["revision_review_id"] == summary["revision_review"][
        "review_id"
    ]
    assert summary["cli_reads"]["handoff_package_id"] == summary["handoff_package"][
        "handoff_package_id"
    ]
    assert summary["cli_reads"]["handoff_record_id"] == summary["handoff_record"][
        "handoff_record_id"
    ]
    assert summary["cli_reads"]["handoff_record_count"] == 1
    assert summary["cli_reads"]["review_lineage_stage"] == "revision_review"
    assert summary["cli_reads"]["review_lineage_count"] == 1
    assert summary["cli_reads"]["feedback_loop_stage"] == "handoff_applied"
    assert summary["cli_reads"]["execution_plan_id"] == summary["execution_plan"][
        "execution_plan_id"
    ]
    assert summary["cli_reads"]["execution_dry_run_id"] == summary["execution_dry_run"][
        "dry_run_id"
    ]
    assert summary["cli_reads"]["execution_dry_run_detail_id"] == summary[
        "execution_dry_run"
    ]["dry_run_id"]
    assert summary["cli_reads"]["execution_dry_run_count"] == 1
    assert summary["cli_reads"]["memory_count"] == 2
    assert summary["cli_reads"]["handoff_memory_source_id"] == summary["memory"][
        "handoff_source_id"
    ]
    assert summary["memory"]["handoff_metadata_record_id"] == summary["handoff_record"][
        "handoff_record_id"
    ]
    assert summary["memory"]["source_id"] in summary["later_strategy"][
        "retrieved_memory_source_ids"
    ]
