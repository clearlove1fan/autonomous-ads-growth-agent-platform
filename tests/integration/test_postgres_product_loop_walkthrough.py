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
    assert summary["execution_plan"]["execution_mode"] == "dry_run"
    assert summary["execution_plan"]["first_tool_name"] == "draft_budget_reallocation"
    assert summary["execution_dry_run"]["status"] == "passed"
    assert summary["execution_dry_run"]["validated_step_count"] == 1
    assert summary["execution_dry_run"]["blocked_step_count"] == 0
    assert summary["outbox"]["completed"] == 1
    assert summary["cli_reads"]["event_count"] == 1
    assert summary["cli_reads"]["first_action_type"] == "adjust_budget"
    assert summary["cli_reads"]["first_change_type"] == "budget"
    assert summary["cli_reads"]["review_count"] == 1
    assert summary["cli_reads"]["execution_plan_id"] == summary["execution_plan"][
        "execution_plan_id"
    ]
    assert summary["cli_reads"]["execution_dry_run_id"] == summary["execution_dry_run"][
        "dry_run_id"
    ]
    assert summary["cli_reads"]["memory_count"] == 1
    assert summary["memory"]["source_id"] in summary["later_strategy"][
        "retrieved_memory_source_ids"
    ]
