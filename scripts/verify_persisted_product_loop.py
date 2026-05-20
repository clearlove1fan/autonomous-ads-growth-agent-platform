#!/usr/bin/env python3
"""Run and validate the persisted v0.1 product loop."""

from __future__ import annotations

import json
import os
import sys
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any
from unittest.mock import patch
from uuid import uuid4

REPO_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = REPO_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

import sqlalchemy as sa  # noqa: E402
from alembic import command  # noqa: E402
from alembic.config import Config  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy.engine import URL, make_url  # noqa: E402
from typer.testing import CliRunner  # noqa: E402

import ads_growth_agent.cli as cli_module  # noqa: E402
from ads_growth_agent.advertiser_memory_store_factory import (  # noqa: E402
    dispose_cached_advertiser_memory_store_engines,
)
from ads_growth_agent.api import app as api_app  # noqa: E402
from ads_growth_agent.api import get_runtime_settings  # noqa: E402
from ads_growth_agent.campaign_draft_store_factory import (  # noqa: E402
    dispose_cached_campaign_draft_store_engines,
)
from ads_growth_agent.config import Settings  # noqa: E402
from ads_growth_agent.feedback_execution_store_factory import (  # noqa: E402
    dispose_cached_feedback_execution_store_engines,
)
from ads_growth_agent.feedback_review_store_factory import (  # noqa: E402
    dispose_cached_feedback_review_store_engines,
)
from ads_growth_agent.knowledge_store_factory import (  # noqa: E402
    dispose_cached_knowledge_store_engines,
)
from ads_growth_agent.outbox_store_factory import (  # noqa: E402
    dispose_cached_outbox_store_engines,
)
from ads_growth_agent.performance_event_store_factory import (  # noqa: E402
    dispose_cached_performance_event_store_engines,
)
from ads_growth_agent.persistence.knowledge_seed import seed_default_knowledge  # noqa: E402
from ads_growth_agent.run_store_factory import dispose_cached_run_store_engines  # noqa: E402

DEFAULT_TEST_DATABASE_URL = (
    "postgresql+psycopg://ads_growth:ads_growth@localhost:5432/ads_growth"
)
DEFAULT_TENANT_ID = "tenant_product_loop"
DEFAULT_ADVERTISER_ID = "adv_fitness_001"
DEFAULT_BRIEF_TEXT = (
    "I want to use a $2000 budget to promote a fitness app in the United States "
    "and increase trial registrations over 14 days."
)


class ProductLoopVerificationError(Exception):
    """Raised when the persisted product loop violates the expected contract."""

    def __init__(self, issues: list[str]) -> None:
        super().__init__("\n".join(issues))
        self.issues = issues


def run_persisted_product_loop(
    base_database_url: URL | str | None = None,
    *,
    tenant_id: str = DEFAULT_TENANT_ID,
    advertiser_id: str = DEFAULT_ADVERTISER_ID,
) -> dict[str, Any]:
    """Create a temporary PostgreSQL DB and validate the persisted product loop."""

    base_url = _database_url(base_database_url)
    test_url = _create_temporary_database(base_url)
    database_url = test_url.render_as_string(hide_password=False)
    engine = sa.create_engine(test_url, pool_pre_ping=True)
    settings = _walkthrough_settings(database_url, tenant_id=tenant_id)

    try:
        with _temporary_env({"DATABASE_URL": database_url}):
            _upgrade_database()
        seed_default_knowledge(engine, tenant_id=tenant_id)

        api_app.dependency_overrides[get_runtime_settings] = lambda: settings
        with TestClient(api_app) as client:
            first_strategy = _create_strategy(
                client,
                tenant_id=tenant_id,
                advertiser_id=advertiser_id,
            )
            strategy = first_strategy["growth_strategy"]["strategy"]
            run_metadata = first_strategy["growth_strategy"]["run_metadata"]
            run_id = run_metadata["run_id"]
            strategy_id = strategy["strategy_id"]
            draft_id = strategy["campaign_draft"]["draft_id"]

            draft_detail = _api_json(
                client.get(
                    f"/campaign-drafts/{draft_id}",
                    headers=_tenant_headers(tenant_id),
                ),
                label="get campaign draft",
            )
            draft_list = _api_json(
                client.get(
                    "/campaign-drafts",
                    params={"advertiser_id": advertiser_id, "limit": "10"},
                    headers=_tenant_headers(tenant_id),
                ),
                label="list campaign drafts",
            )
            _expect(draft_detail["draft_id"] == draft_id, "draft detail did not match draft_id")
            _expect(draft_list["count"] == 1, "draft list should contain one persisted draft")

            event_payload = _performance_event_payload(
                advertiser_id=advertiser_id,
                run_id=run_id,
                draft_id=draft_id,
                objective=strategy["objective"],
                strategy_context=strategy["feedback_context"],
            )
            event_response = _api_json(
                client.post(
                    "/campaign-events/performance",
                    json=event_payload,
                    headers=_tenant_headers(tenant_id),
                ),
                label="ingest performance event",
            )
            event_id = event_response["event_id"]
            memory_source_id = event_response["advertiser_memory_source_id"]
            _expect(
                event_response["advertiser_memory_status"] == "queued",
                "performance feedback should queue advertiser memory through outbox",
            )
            _expect(
                isinstance(memory_source_id, str) and memory_source_id,
                "performance feedback should expose advertiser_memory_source_id",
            )

            event_list = _api_json(
                client.get(
                    "/campaign-events/performance",
                    params={
                        "advertiser_id": advertiser_id,
                        "run_id": run_id,
                        "draft_id": draft_id,
                        "limit": "10",
                    },
                    headers=_tenant_headers(tenant_id),
                ),
                label="list performance events",
            )
            _expect(event_list["count"] == 1, "event list should contain one feedback event")
            action_plan = _api_json(
                client.get(
                    f"/campaign-events/performance/{event_id}/action-plan",
                    headers=_tenant_headers(tenant_id),
                ),
                label="get feedback action plan",
            )
            _expect(
                action_plan["event_id"] == event_id,
                "action plan should link back to performance event",
            )
            _expect(
                action_plan["steps"][0]["action_type"] == "adjust_budget",
                "action plan should rank budget adjustment first for high CPA feedback",
            )
            _expect(
                action_plan["steps"][0]["status"] == "draft_recommendation",
                "action plan should keep optimization steps draft-only",
            )
            optimization_draft = _api_json(
                client.get(
                    f"/campaign-events/performance/{event_id}/optimization-draft",
                    headers=_tenant_headers(tenant_id),
                ),
                label="get feedback optimization draft",
            )
            _expect(
                optimization_draft["event_id"] == event_id,
                "optimization draft should link back to performance event",
            )
            _expect(
                optimization_draft["status"] == "draft",
                "optimization draft should remain draft-only",
            )
            _expect(
                optimization_draft["changes"][0]["change_type"] == "budget",
                "optimization draft should translate high CPA feedback into a budget change",
            )
            review = _api_json(
                client.post(
                    f"/campaign-events/performance/{event_id}/optimization-draft/reviews",
                    json={
                        "decision": "approved",
                        "reviewer_id": "operator_product_loop",
                        "notes": "Approve the first draft change for the persisted loop demo.",
                        "selected_change_ids": [
                            optimization_draft["changes"][0]["change_id"]
                        ],
                    },
                    headers=_tenant_headers(tenant_id),
                ),
                label="submit feedback optimization review",
                expected_status=201,
            )
            review_id = review["review_id"]
            _expect(
                review["optimization_draft_id"]
                == optimization_draft["optimization_draft_id"],
                "review should link to the reviewed optimization draft",
            )
            _expect(
                review["selected_change_ids"] == [optimization_draft["changes"][0]["change_id"]],
                "review should persist the selected draft change IDs",
            )
            review_detail = _api_json(
                client.get(
                    f"/feedback-optimization-reviews/{review_id}",
                    headers=_tenant_headers(tenant_id),
                ),
                label="get feedback optimization review",
            )
            review_list = _api_json(
                client.get(
                    "/feedback-optimization-reviews",
                    params={
                        "event_id": event_id,
                        "decision": "approved",
                        "limit": "10",
                    },
                    headers=_tenant_headers(tenant_id),
                ),
                label="list feedback optimization reviews",
            )
            _expect(
                review_detail["review_id"] == review_id,
                "review detail should match submitted review",
            )
            _expect(
                review_list["count"] == 1,
                "approved review list should contain one submitted review",
            )
            execution_plan = _api_json(
                client.get(
                    f"/feedback-optimization-reviews/{review_id}/execution-plan",
                    headers=_tenant_headers(tenant_id),
                ),
                label="get feedback execution plan",
            )
            _expect(
                execution_plan["review_id"] == review_id,
                "execution plan should link to approved review",
            )
            _expect(
                execution_plan["execution_mode"] == "dry_run",
                "execution plan should stay in dry-run mode",
            )
            _expect(
                execution_plan["steps"][0]["tool_intent"]["tool_name"]
                == "draft_budget_reallocation",
                "execution plan should map approved budget change to a draft tool intent",
            )
            execution_dry_run = _api_json(
                client.post(
                    f"/feedback-optimization-reviews/{review_id}/execution-plan/dry-run",
                    headers=_tenant_headers(tenant_id),
                ),
                label="dry-run feedback execution plan",
            )
            _expect(
                execution_dry_run["status"] == "passed",
                "execution dry run should pass for approved draft tool intents",
            )
            _expect(
                execution_dry_run["step_results"][0]["tool_result"]["payload"][
                    "mutation_performed"
                ]
                is False,
                "execution dry run should not mutate live campaign state",
            )
            execution_dry_run_detail = _api_json(
                client.get(
                    f"/feedback-execution-dry-runs/{execution_dry_run['dry_run_id']}",
                    headers=_tenant_headers(tenant_id),
                ),
                label="get feedback execution dry run",
            )
            execution_dry_run_list = _api_json(
                client.get(
                    "/feedback-execution-dry-runs",
                    params={
                        "review_id": review_id,
                        "status": "passed",
                        "limit": "10",
                    },
                    headers=_tenant_headers(tenant_id),
                ),
                label="list feedback execution dry runs",
            )
            _expect(
                execution_dry_run_detail["dry_run_id"] == execution_dry_run["dry_run_id"],
                "execution dry-run detail should match submitted dry run",
            )
            _expect(
                execution_dry_run_list["count"] == 1,
                "execution dry-run list should contain one validation result",
            )

            outbox_report = _invoke_cli(
                settings,
                ["process-outbox", "--limit", "10", "--worker-id", "worker_product_loop"],
            )
            _expect(outbox_report["claimed"] == 1, "outbox should claim one memory event")
            _expect(outbox_report["completed"] == 1, "outbox should complete memory event")
            _expect(outbox_report["failed"] == 0, "outbox should not fail memory event")

            memory_list = _api_json(
                client.get(
                    f"/advertisers/{advertiser_id}/memories",
                    params={"memory_type": "historical_performance", "limit": "10"},
                    headers=_tenant_headers(tenant_id),
                ),
                label="list advertiser memories",
            )
            memory_detail = _api_json(
                client.get(
                    f"/advertisers/{advertiser_id}/memories/{memory_source_id}",
                    headers=_tenant_headers(tenant_id),
                ),
                label="get advertiser memory",
            )
            _expect(memory_list["count"] == 1, "memory list should contain one learned memory")
            _expect(
                memory_detail["metadata"]["event_id"] == event_id,
                "memory detail should link back to performance event",
            )

            cli_draft = _invoke_cli(settings, ["get-campaign-draft", draft_id])
            cli_event = _invoke_cli(settings, ["get-performance-event", event_id])
            cli_action_plan = _invoke_cli(
                settings,
                ["get-feedback-action-plan", event_id],
            )
            cli_optimization_draft = _invoke_cli(
                settings,
                ["get-feedback-optimization-draft", event_id],
            )
            cli_review = _invoke_cli(
                settings,
                ["get-feedback-optimization-review", review_id],
            )
            cli_execution_plan = _invoke_cli(
                settings,
                ["get-feedback-execution-plan", review_id],
            )
            cli_execution_dry_run = _invoke_cli(
                settings,
                ["dry-run-feedback-execution-plan", review_id],
            )
            cli_execution_dry_run_detail = _invoke_cli(
                settings,
                ["get-feedback-execution-dry-run", execution_dry_run["dry_run_id"]],
            )
            cli_execution_dry_run_list = _invoke_cli(
                settings,
                [
                    "list-feedback-execution-dry-runs",
                    "--review-id",
                    review_id,
                    "--status",
                    "passed",
                    "--limit",
                    "10",
                ],
            )
            cli_review_list = _invoke_cli(
                settings,
                [
                    "list-feedback-optimization-reviews",
                    "--event-id",
                    event_id,
                    "--decision",
                    "approved",
                    "--limit",
                    "10",
                ],
            )
            cli_submitted_review = _invoke_cli(
                settings,
                [
                    "submit-feedback-optimization-review",
                    event_id,
                    "--decision",
                    "needs_revision",
                    "--reviewer-id",
                    "operator_product_loop_cli",
                    "--notes",
                    "Request a revision before approving every draft change.",
                ],
            )
            revision_draft = _api_json(
                client.get(
                    "/feedback-optimization-reviews/"
                    f"{cli_submitted_review['review_id']}/revision-draft",
                    headers=_tenant_headers(tenant_id),
                ),
                label="get feedback optimization revision draft",
            )
            cli_revision_draft = _invoke_cli(
                settings,
                [
                    "get-feedback-optimization-revision-draft",
                    cli_submitted_review["review_id"],
                ],
            )
            cli_revision_review = _invoke_cli(
                settings,
                [
                    "submit-feedback-optimization-revision-review",
                    cli_submitted_review["review_id"],
                    "--decision",
                    "approved",
                    "--reviewer-id",
                    "operator_product_loop_revision",
                    "--notes",
                    "Approve the first revised draft change.",
                    "--selected-change-id",
                    revision_draft["changes"][0]["change_id"],
                ],
            )
            revision_execution_plan = _api_json(
                client.get(
                    "/feedback-optimization-reviews/"
                    f"{cli_revision_review['review_id']}/execution-plan",
                    headers=_tenant_headers(tenant_id),
                ),
                label="get revision feedback execution plan",
            )
            revision_execution_dry_run = _api_json(
                client.post(
                    "/feedback-optimization-reviews/"
                    f"{cli_revision_review['review_id']}/execution-plan/dry-run",
                    headers=_tenant_headers(tenant_id),
                ),
                label="dry run revision feedback execution plan",
            )
            review_lineage = _api_json(
                client.get(
                    f"/feedback-optimization-reviews/{cli_submitted_review['review_id']}/lineage",
                    headers=_tenant_headers(tenant_id),
                ),
                label="get feedback optimization review lineage",
            )
            cli_review_lineage = _invoke_cli(
                settings,
                [
                    "get-feedback-optimization-review-lineage",
                    cli_revision_review["review_id"],
                ],
            )
            review_lineage_list = _api_json(
                client.get(
                    "/feedback-optimization-review-lineages",
                    params={
                        "event_id": event_id,
                        "decision": "approved",
                        "lineage_stage": "revision_review",
                        "limit": "10",
                    },
                    headers=_tenant_headers(tenant_id),
                ),
                label="list feedback optimization review lineages",
            )
            cli_review_lineage_list = _invoke_cli(
                settings,
                [
                    "list-feedback-optimization-review-lineages",
                    "--event-id",
                    event_id,
                    "--decision",
                    "approved",
                    "--lineage-stage",
                    "revision_review",
                    "--limit",
                    "10",
                ],
            )
            feedback_loop_summary = _api_json(
                client.get(
                    f"/campaign-events/performance/{event_id}/feedback-loop-summary",
                    params={"limit": "10"},
                    headers=_tenant_headers(tenant_id),
                ),
                label="get feedback loop summary",
            )
            cli_feedback_loop_summary = _invoke_cli(
                settings,
                [
                    "get-feedback-loop-summary",
                    event_id,
                    "--limit",
                    "10",
                ],
            )
            cli_event_list = _invoke_cli(
                settings,
                [
                    "list-performance-events",
                    "--advertiser-id",
                    advertiser_id,
                    "--run-id",
                    run_id,
                    "--draft-id",
                    draft_id,
                    "--limit",
                    "10",
                ],
            )
            cli_memory = _invoke_cli(
                settings,
                ["get-advertiser-memory", advertiser_id, memory_source_id],
            )
            cli_memory_list = _invoke_cli(
                settings,
                [
                    "list-advertiser-memories",
                    advertiser_id,
                    "--memory-type",
                    "historical_performance",
                    "--limit",
                    "10",
                ],
            )
            _expect(cli_draft["draft_id"] == draft_id, "CLI draft read did not match draft_id")
            _expect(cli_event["event_id"] == event_id, "CLI event read did not match event_id")
            _expect(
                cli_action_plan["steps"][0]["action_type"]
                == action_plan["steps"][0]["action_type"],
                "CLI action plan should match API action plan",
            )
            _expect(
                cli_optimization_draft["changes"][0]["change_type"]
                == optimization_draft["changes"][0]["change_type"],
                "CLI optimization draft should match API optimization draft",
            )
            _expect(
                cli_review["review_id"] == review_id,
                "CLI review read should match API submitted review",
            )
            _expect(
                cli_execution_plan["execution_plan_id"]
                == execution_plan["execution_plan_id"],
                "CLI execution plan should match API execution plan",
            )
            _expect(
                cli_execution_dry_run["dry_run_id"] == execution_dry_run["dry_run_id"],
                "CLI dry run should match API dry run",
            )
            _expect(
                cli_execution_dry_run_detail["dry_run_id"] == execution_dry_run["dry_run_id"],
                "CLI dry-run detail should match API dry run",
            )
            _expect(
                cli_execution_dry_run_list["count"] == 1,
                "CLI dry-run list should find persisted dry run",
            )
            _expect(
                cli_review_list["count"] == 1,
                "CLI approved review list should find submitted review",
            )
            _expect(
                cli_submitted_review["decision"] == "needs_revision",
                "CLI review submit should persist a revision request",
            )
            _expect(
                revision_draft["source_review_id"] == cli_submitted_review["review_id"],
                "revision draft should link to the needs-revision review",
            )
            _expect(
                len(revision_draft["changes"])
                == len(cli_submitted_review["selected_change_ids"]),
                "revision draft should carry every selected change from the review",
            )
            _expect(
                cli_revision_draft["revision_draft_id"]
                == revision_draft["revision_draft_id"],
                "CLI revision draft should match API revision draft",
            )
            _expect(
                cli_revision_review["decision"] == "approved",
                "CLI revision review should approve the revised draft",
            )
            _expect(
                cli_revision_review["optimization_draft_id"]
                == revision_draft["revision_draft_id"],
                "CLI revision review should review the revision draft",
            )
            _expect(
                cli_revision_review["selected_change_ids"]
                == [revision_draft["changes"][0]["change_id"]],
                "CLI revision review should select the requested revised change",
            )
            _expect(
                revision_execution_plan["review_id"] == cli_revision_review["review_id"],
                "approved revision review should produce an execution plan",
            )
            _expect(
                revision_execution_plan["optimization_draft_id"]
                == revision_draft["revision_draft_id"],
                "revision execution plan should target the revision draft",
            )
            _expect(
                revision_execution_dry_run["execution_plan_id"]
                == revision_execution_plan["execution_plan_id"],
                "revision dry run should validate the revision execution plan",
            )
            _expect(
                review_lineage["revision_draft"]["revision_draft_id"]
                == revision_draft["revision_draft_id"],
                "review lineage should include the revision draft",
            )
            _expect(
                review_lineage["execution_ready_review_ids"]
                == [cli_revision_review["review_id"]],
                "review lineage should identify the approved revision review",
            )
            _expect(
                review_lineage["execution_summaries"][0]["dry_runs"][0]["dry_run_id"]
                == revision_execution_dry_run["dry_run_id"],
                "review lineage should include the persisted revision dry run",
            )
            _expect(
                cli_review_lineage["source_review_id"] == cli_submitted_review["review_id"],
                "CLI lineage should resolve source review from revision review",
            )
            _expect(
                cli_review_lineage["target_review"]["review_id"]
                == cli_revision_review["review_id"],
                "CLI lineage should include the requested revision review",
            )
            _expect(
                cli_review_lineage["execution_summaries"][0]["dry_run_count"] == 1,
                "CLI lineage should include dry-run audit for revision review",
            )
            _expect(
                review_lineage_list["count"] == 1,
                "lineage list should include exactly one approved revision review",
            )
            _expect(
                review_lineage_list["items"][0]["requested_review_id"]
                == cli_revision_review["review_id"],
                "lineage list should return the approved revision review lineage",
            )
            _expect(
                review_lineage_list["items"][0]["execution_summaries"][0]["dry_runs"][0][
                    "dry_run_id"
                ]
                == revision_execution_dry_run["dry_run_id"],
                "lineage list should include persisted revision dry-run audit",
            )
            _expect(
                cli_review_lineage_list["count"] == 1,
                "CLI lineage list should include exactly one approved revision review",
            )
            _expect(
                cli_review_lineage_list["items"][0]["requested_review_id"]
                == cli_revision_review["review_id"],
                "CLI lineage list should return the approved revision review lineage",
            )
            _expect(
                feedback_loop_summary["current_stage"] == "dry_run_passed",
                "feedback loop summary should report the latest loop as dry-run passed",
            )
            _expect(
                feedback_loop_summary["review_count"] == 3,
                "feedback loop summary should include original, revision request, "
                "and revision approval reviews",
            )
            _expect(
                feedback_loop_summary["lineage_count"] == 3,
                "feedback loop summary should include lineage for each review on the event",
            )
            _expect(
                feedback_loop_summary["dry_run_count"] == 2,
                "feedback loop summary should include original and revision dry-run audits",
            )
            _expect(
                cli_feedback_loop_summary["current_stage"]
                == feedback_loop_summary["current_stage"],
                "CLI feedback loop summary should match API current stage",
            )
            _expect(
                cli_feedback_loop_summary["dry_run_count"]
                == feedback_loop_summary["dry_run_count"],
                "CLI feedback loop summary should match API dry-run count",
            )
            _expect(cli_event_list["count"] == 1, "CLI event list should find feedback event")
            _expect(
                cli_memory["source_id"] == memory_source_id,
                "CLI memory read did not match memory source",
            )
            _expect(
                cli_memory_list["count"] == 1,
                "CLI memory list should find learned memory",
            )

            later_strategy = _create_strategy(
                client,
                tenant_id=tenant_id,
                advertiser_id=advertiser_id,
                text=(
                    "Use another $2000 for the same fitness app registration campaign. "
                    "Consider previous performance before recommending the next plan."
                ),
            )
            later_strategy_payload = later_strategy["growth_strategy"]["strategy"]
            later_sources = later_strategy_payload["sources"]
            later_source_ids = {source["source_id"] for source in later_sources}
            _expect(
                memory_source_id in later_source_ids,
                "later strategy should retrieve the learned advertiser memory via RAG",
            )

        return {
            "status": "passed",
            "tenant_id": tenant_id,
            "database_url": _safe_database_url(database_url),
            "first_strategy": {
                "run_id": run_id,
                "strategy_id": strategy_id,
                "draft_id": draft_id,
                "source_types": sorted(
                    {source["source_type"] for source in strategy["sources"]}
                ),
            },
            "feedback_event": {
                "event_id": event_id,
                "feedback_id": event_response["analysis"]["feedback_id"],
                "health_status": event_response["analysis"]["health_status"],
                "advertiser_memory_status": event_response["advertiser_memory_status"],
            },
            "action_plan": {
                "step_count": len(action_plan["steps"]),
                "first_action_type": action_plan["steps"][0]["action_type"],
                "first_action_status": action_plan["steps"][0]["status"],
            },
            "optimization_draft": {
                "optimization_draft_id": optimization_draft["optimization_draft_id"],
                "change_count": len(optimization_draft["changes"]),
                "first_change_type": optimization_draft["changes"][0]["change_type"],
                "status": optimization_draft["status"],
            },
            "review": {
                "review_id": review_id,
                "decision": review["decision"],
                "selected_change_count": len(review["selected_change_ids"]),
                "cli_submitted_decision": cli_submitted_review["decision"],
                "cli_submitted_review_id": cli_submitted_review["review_id"],
                "cli_submitted_selected_change_count": len(
                    cli_submitted_review["selected_change_ids"]
                ),
            },
            "revision_draft": {
                "revision_draft_id": revision_draft["revision_draft_id"],
                "source_review_id": revision_draft["source_review_id"],
                "change_count": len(revision_draft["changes"]),
                "cli_revision_draft_id": cli_revision_draft["revision_draft_id"],
            },
            "revision_review": {
                "review_id": cli_revision_review["review_id"],
                "decision": cli_revision_review["decision"],
                "optimization_draft_id": cli_revision_review["optimization_draft_id"],
                "selected_change_count": len(cli_revision_review["selected_change_ids"]),
                "execution_plan_id": revision_execution_plan["execution_plan_id"],
                "execution_plan_step_count": len(revision_execution_plan["steps"]),
                "dry_run_id": revision_execution_dry_run["dry_run_id"],
                "dry_run_status": revision_execution_dry_run["status"],
            },
            "review_lineage": {
                "requested_review_id": review_lineage["requested_review_id"],
                "source_review_id": review_lineage["source_review_id"],
                "lineage_stage": review_lineage["lineage_stage"],
                "revision_review_count": len(review_lineage["revision_reviews"]),
                "execution_ready_review_ids": review_lineage["execution_ready_review_ids"],
                "execution_summary_count": len(review_lineage["execution_summaries"]),
                "dry_run_count": review_lineage["execution_summaries"][0][
                    "dry_run_count"
                ],
                "latest_dry_run_status": review_lineage["execution_summaries"][0][
                    "latest_dry_run_status"
                ],
                "cli_lineage_stage": cli_review_lineage["lineage_stage"],
                "cli_target_review_id": cli_review_lineage["target_review"]["review_id"],
                "list_count": review_lineage_list["count"],
                "list_requested_review_id": review_lineage_list["items"][0][
                    "requested_review_id"
                ],
                "list_dry_run_id": review_lineage_list["items"][0]["execution_summaries"][0][
                    "dry_runs"
                ][0]["dry_run_id"],
                "cli_list_count": cli_review_lineage_list["count"],
            },
            "feedback_loop_summary": {
                "current_stage": feedback_loop_summary["current_stage"],
                "review_count": feedback_loop_summary["review_count"],
                "lineage_count": feedback_loop_summary["lineage_count"],
                "dry_run_count": feedback_loop_summary["dry_run_count"],
                "latest_review_id": feedback_loop_summary["latest_review_id"],
                "latest_dry_run_status": feedback_loop_summary["latest_dry_run_status"],
                "cli_current_stage": cli_feedback_loop_summary["current_stage"],
                "cli_dry_run_count": cli_feedback_loop_summary["dry_run_count"],
            },
            "execution_plan": {
                "execution_plan_id": execution_plan["execution_plan_id"],
                "execution_mode": execution_plan["execution_mode"],
                "first_tool_name": execution_plan["steps"][0]["tool_intent"]["tool_name"],
                "step_count": len(execution_plan["steps"]),
            },
            "execution_dry_run": {
                "dry_run_id": execution_dry_run["dry_run_id"],
                "status": execution_dry_run["status"],
                "validated_step_count": execution_dry_run["validated_step_count"],
                "blocked_step_count": execution_dry_run["blocked_step_count"],
                "detail_status": execution_dry_run_detail["status"],
                "list_count": execution_dry_run_list["count"],
            },
            "outbox": outbox_report,
            "memory": {
                "source_id": memory_source_id,
                "memory_type": memory_detail["memory_type"],
                "metadata_event_id": memory_detail["metadata"]["event_id"],
            },
            "cli_reads": {
                "draft_id": cli_draft["draft_id"],
                "event_id": cli_event["event_id"],
                "first_action_type": cli_action_plan["steps"][0]["action_type"],
                "first_change_type": cli_optimization_draft["changes"][0]["change_type"],
                "review_id": cli_review["review_id"],
                "review_count": cli_review_list["count"],
                "revision_draft_id": cli_revision_draft["revision_draft_id"],
                "revision_review_id": cli_revision_review["review_id"],
                "review_lineage_stage": cli_review_lineage["lineage_stage"],
                "review_lineage_count": cli_review_lineage_list["count"],
                "feedback_loop_stage": cli_feedback_loop_summary["current_stage"],
                "execution_plan_id": cli_execution_plan["execution_plan_id"],
                "execution_dry_run_id": cli_execution_dry_run["dry_run_id"],
                "execution_dry_run_detail_id": cli_execution_dry_run_detail["dry_run_id"],
                "execution_dry_run_count": cli_execution_dry_run_list["count"],
                "memory_source_id": cli_memory["source_id"],
                "event_count": cli_event_list["count"],
                "memory_count": cli_memory_list["count"],
            },
            "later_strategy": {
                "run_id": later_strategy["growth_strategy"]["run_metadata"]["run_id"],
                "strategy_id": later_strategy_payload["strategy_id"],
                "retrieved_memory_source_ids": sorted(
                    source["source_id"]
                    for source in later_sources
                    if source["source_type"] == "advertiser_memory"
                ),
                "source_types": sorted({source["source_type"] for source in later_sources}),
            },
        }
    finally:
        api_app.dependency_overrides.clear()
        dispose_cached_advertiser_memory_store_engines()
        dispose_cached_campaign_draft_store_engines()
        dispose_cached_feedback_execution_store_engines()
        dispose_cached_feedback_review_store_engines()
        dispose_cached_knowledge_store_engines()
        dispose_cached_outbox_store_engines()
        dispose_cached_performance_event_store_engines()
        dispose_cached_run_store_engines()
        engine.dispose()
        _drop_temporary_database(test_url)


def render_summary(summary: dict[str, Any]) -> str:
    """Render a compact operator-facing summary for the walkthrough script."""

    return "\n".join(
        [
            "Persisted product loop verification passed",
            f"Tenant: {summary['tenant_id']}",
            f"Database: {summary['database_url']}",
            (
                "Strategy draft: "
                f"run={summary['first_strategy']['run_id']} "
                f"strategy={summary['first_strategy']['strategy_id']} "
                f"draft={summary['first_strategy']['draft_id']}"
            ),
            (
                "Feedback event: "
                f"{summary['feedback_event']['event_id']} "
                f"status={summary['feedback_event']['health_status']} "
                f"memory={summary['feedback_event']['advertiser_memory_status']}"
            ),
            (
                "Action plan: "
                f"steps={summary['action_plan']['step_count']} "
                f"first={summary['action_plan']['first_action_type']} "
                f"status={summary['action_plan']['first_action_status']}"
            ),
            (
                "Optimization draft: "
                f"{summary['optimization_draft']['optimization_draft_id']} "
                f"changes={summary['optimization_draft']['change_count']} "
                f"first={summary['optimization_draft']['first_change_type']} "
                f"status={summary['optimization_draft']['status']}"
            ),
            (
                "Review: "
                f"{summary['review']['review_id']} "
                f"decision={summary['review']['decision']} "
                f"selected_changes={summary['review']['selected_change_count']} "
                f"cli_submit={summary['review']['cli_submitted_decision']}"
            ),
            (
                "Revision draft: "
                f"{summary['revision_draft']['revision_draft_id']} "
                f"source_review={summary['revision_draft']['source_review_id']} "
                f"changes={summary['revision_draft']['change_count']}"
            ),
            (
                "Revision review: "
                f"{summary['revision_review']['review_id']} "
                f"decision={summary['revision_review']['decision']} "
                f"selected_changes={summary['revision_review']['selected_change_count']} "
                f"execution_plan={summary['revision_review']['execution_plan_id']} "
                f"dry_run={summary['revision_review']['dry_run_id']}"
            ),
            (
                "Review lineage: "
                f"source={summary['review_lineage']['source_review_id']} "
                f"stage={summary['review_lineage']['lineage_stage']} "
                f"revision_reviews={summary['review_lineage']['revision_review_count']} "
                "execution_ready="
                f"{', '.join(summary['review_lineage']['execution_ready_review_ids'])} "
                f"dry_runs={summary['review_lineage']['dry_run_count']} "
                f"latest={summary['review_lineage']['latest_dry_run_status']} "
                f"list={summary['review_lineage']['list_count']}"
            ),
            (
                "Feedback loop summary: "
                f"stage={summary['feedback_loop_summary']['current_stage']} "
                f"reviews={summary['feedback_loop_summary']['review_count']} "
                f"lineages={summary['feedback_loop_summary']['lineage_count']} "
                f"dry_runs={summary['feedback_loop_summary']['dry_run_count']} "
                f"cli_stage={summary['feedback_loop_summary']['cli_current_stage']}"
            ),
            (
                "Execution plan: "
                f"{summary['execution_plan']['execution_plan_id']} "
                f"mode={summary['execution_plan']['execution_mode']} "
                f"steps={summary['execution_plan']['step_count']} "
                f"first_tool={summary['execution_plan']['first_tool_name']}"
            ),
            (
                "Execution dry run: "
                f"{summary['execution_dry_run']['dry_run_id']} "
                f"status={summary['execution_dry_run']['status']} "
                f"validated={summary['execution_dry_run']['validated_step_count']} "
                f"blocked={summary['execution_dry_run']['blocked_step_count']} "
                f"persisted={summary['execution_dry_run']['list_count']}"
            ),
            (
                "Outbox: "
                f"claimed={summary['outbox']['claimed']} "
                f"completed={summary['outbox']['completed']} "
                f"failed={summary['outbox']['failed']}"
            ),
            (
                "Memory: "
                f"{summary['memory']['source_id']} "
                f"type={summary['memory']['memory_type']}"
            ),
            (
                "Later RAG: "
                f"strategy={summary['later_strategy']['strategy_id']} "
                "retrieved_memories="
                f"{', '.join(summary['later_strategy']['retrieved_memory_source_ids'])}"
            ),
            (
                "CLI reads: "
                f"events={summary['cli_reads']['event_count']} "
                f"first_action={summary['cli_reads']['first_action_type']} "
                f"first_change={summary['cli_reads']['first_change_type']} "
                f"reviews={summary['cli_reads']['review_count']} "
                f"revision_draft={summary['cli_reads']['revision_draft_id']} "
                f"revision_review={summary['cli_reads']['revision_review_id']} "
                f"lineage={summary['cli_reads']['review_lineage_stage']} "
                f"lineage_reads={summary['cli_reads']['review_lineage_count']} "
                f"loop={summary['cli_reads']['feedback_loop_stage']} "
                f"execution_plan={summary['cli_reads']['execution_plan_id']} "
                f"dry_run={summary['cli_reads']['execution_dry_run_id']} "
                f"dry_run_reads={summary['cli_reads']['execution_dry_run_count']} "
                f"memories={summary['cli_reads']['memory_count']}"
            ),
        ]
    )


def _create_strategy(
    client: TestClient,
    *,
    tenant_id: str,
    advertiser_id: str,
    text: str = DEFAULT_BRIEF_TEXT,
) -> dict[str, Any]:
    return _api_json(
        client.post(
            "/growth-strategies/from-text",
            json={
                "text": text,
                "advertiser_id": advertiser_id,
                "default_target_market": "United States",
                "default_currency": "USD",
                "default_duration_days": 14,
            },
            headers=_tenant_headers(tenant_id),
        ),
        label="create growth strategy from text",
    )


def _performance_event_payload(
    *,
    advertiser_id: str,
    run_id: str,
    draft_id: str,
    objective: str,
    strategy_context: dict[str, Any],
) -> dict[str, Any]:
    return {
        "event_id": "evt_product_loop_underperforming",
        "advertiser_id": advertiser_id,
        "run_id": run_id,
        "campaign_id": "cmp_product_loop_001",
        "draft_id": draft_id,
        "objective": objective,
        "event_type": "performance_snapshot",
        "occurred_at": "2026-05-12T12:00:00Z",
        "metrics": {
            "impressions": 10000,
            "clicks": 500,
            "spend": "1000.00",
            "conversions": 20,
        },
        "target_cpa": "20.00",
        "attribution_window_days": 7,
        "strategy_context": strategy_context,
        "notes": "Persisted product loop walkthrough feedback event.",
    }


def _walkthrough_settings(database_url: str, *, tenant_id: str) -> Settings:
    return Settings(
        database_url=database_url,
        tenant_id=tenant_id,
        knowledge_store_backend="postgres",
        campaign_draft_persistence_backend="postgres",
        performance_event_persistence_backend="postgres",
        feedback_review_persistence_backend="postgres",
        feedback_execution_persistence_backend="postgres",
        advertiser_memory_persistence_backend="postgres",
        outbox_backend="postgres",
        idempotency_backend="none",
        run_persistence_backend="none",
        strategy_job_backend="memory",
        graph_checkpointer_backend="none",
        knowledge_top_k=5,
        use_llm_brief_intake=False,
        use_llm_planner=False,
        use_llm_critic=False,
        langsmith_tracing=False,
    )


def _invoke_cli(settings: Settings, args: list[str]) -> dict[str, Any]:
    with patch("ads_growth_agent.cli.get_settings", return_value=settings):
        result = CliRunner().invoke(cli_module.app, args)
    if result.exit_code != 0:
        stderr = getattr(result, "stderr", "")
        raise ProductLoopVerificationError(
            [
                f"CLI command failed: ads-growth-agent {' '.join(args)}",
                f"exit_code={result.exit_code}",
                f"stdout={result.stdout.strip()}",
                f"stderr={stderr.strip()}",
            ]
        )
    try:
        return json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        raise ProductLoopVerificationError(
            [
                f"CLI command returned non-JSON stdout: ads-growth-agent {' '.join(args)}",
                f"stdout={result.stdout.strip()}",
            ]
        ) from exc


def _api_json(response, *, label: str, expected_status: int = 200) -> dict[str, Any]:
    if response.status_code != expected_status:
        raise ProductLoopVerificationError(
            [
                f"API call failed: {label}",
                f"status_code={response.status_code}",
                f"body={response.text}",
            ]
        )
    try:
        payload = response.json()
    except json.JSONDecodeError as exc:
        raise ProductLoopVerificationError(
            [f"API call returned non-JSON response: {label}", f"body={response.text}"]
        ) from exc
    if not isinstance(payload, dict):
        raise ProductLoopVerificationError([f"API call returned non-object JSON: {label}"])
    return payload


def _expect(condition: bool, message: str) -> None:
    if not condition:
        raise ProductLoopVerificationError([message])


def _tenant_headers(tenant_id: str) -> dict[str, str]:
    return {"X-Tenant-ID": tenant_id}


def _database_url(database_url: URL | str | None) -> URL:
    if database_url is None:
        if os.getenv("RUN_POSTGRES_INTEGRATION") != "1":
            raise ProductLoopVerificationError(
                ["Set RUN_POSTGRES_INTEGRATION=1 to run live PostgreSQL walkthrough."]
            )
        database_url = os.getenv("TEST_DATABASE_URL", DEFAULT_TEST_DATABASE_URL)
    if isinstance(database_url, URL):
        return database_url
    return make_url(database_url)


def _upgrade_database() -> None:
    config = Config(str(REPO_ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(REPO_ROOT / "migrations"))
    command.upgrade(config, "head")


def _create_temporary_database(base_url: URL) -> URL:
    database_name = f"ads_growth_test_{uuid4().hex[:12]}"
    admin_url = base_url.set(database="postgres")
    test_url = base_url.set(database=database_name)

    engine = sa.create_engine(admin_url, isolation_level="AUTOCOMMIT")
    try:
        with engine.connect() as connection:
            connection.execute(sa.text(f'CREATE DATABASE "{database_name}"'))
    finally:
        engine.dispose()

    return test_url


def _drop_temporary_database(test_url: URL) -> None:
    database_name = test_url.database
    admin_url = test_url.set(database="postgres")
    engine = sa.create_engine(admin_url, isolation_level="AUTOCOMMIT")
    try:
        with engine.connect() as connection:
            connection.execute(
                sa.text(
                    "SELECT pg_terminate_backend(pid) "
                    "FROM pg_stat_activity "
                    "WHERE datname = :database_name AND pid <> pg_backend_pid()"
                ),
                {"database_name": database_name},
            )
            connection.execute(sa.text(f'DROP DATABASE IF EXISTS "{database_name}"'))
    finally:
        engine.dispose()


@contextmanager
def _temporary_env(values: dict[str, str]) -> Iterator[None]:
    old_values = {key: os.environ.get(key) for key in values}
    os.environ.update(values)
    try:
        yield
    finally:
        for key, old_value in old_values.items():
            if old_value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = old_value


def _safe_database_url(database_url: str) -> str:
    url = make_url(database_url)
    return url.render_as_string(hide_password=True)


def main() -> int:
    try:
        summary = run_persisted_product_loop()
    except ProductLoopVerificationError as exc:
        print("Persisted product loop verification failed", file=sys.stderr)
        for issue in exc.issues:
            print(f"- {issue}", file=sys.stderr)
        return 1

    print(render_summary(summary))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
