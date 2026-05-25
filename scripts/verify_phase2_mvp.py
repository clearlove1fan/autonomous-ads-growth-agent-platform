#!/usr/bin/env python3
# ruff: noqa: E402, I001
"""Run and validate the Phase 2 functionally complete MVP."""

from __future__ import annotations

import json
import sys
import tempfile
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from unittest.mock import patch

REPO_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = REPO_ROOT / "src"
SCRIPTS_ROOT = REPO_ROOT / "scripts"
for path in (SRC_ROOT, SCRIPTS_ROOT):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy.engine import URL  # noqa: E402
from typer.testing import CliRunner  # noqa: E402
from verify_persisted_product_loop import (  # noqa: E402
    DEFAULT_ADVERTISER_ID,
    ProductLoopVerificationError,
    run_persisted_product_loop,
)
from verify_persisted_product_loop import (
    DEFAULT_TEST_DATABASE_URL as PERSISTED_DEFAULT_TEST_DATABASE_URL,
)

import ads_growth_agent.cli as cli_module  # noqa: E402
from ads_growth_agent.api import (  # noqa: E402
    app as api_app,
)
from ads_growth_agent.api import (
    get_runtime_settings,
    get_runtime_strategy_job_store,
)
from ads_growth_agent.config import Settings  # noqa: E402
from ads_growth_agent.contracts import (  # noqa: E402
    AdvertiserBrief,
    AgentRunDetailResponse,
    AgentRunStepRecord,
)
from ads_growth_agent.persistence.strategy_job_store import (  # noqa: E402
    InMemoryStrategyJobStore,
)
from ads_growth_agent.strategy import generate_mock_growth_strategy  # noqa: E402

DEFAULT_TENANT_ID = "tenant_phase2_acceptance"
DEFAULT_TEST_DATABASE_URL = PERSISTED_DEFAULT_TEST_DATABASE_URL


class Phase2VerificationError(Exception):
    """Raised when Phase 2 acceptance verification violates the expected contract."""

    def __init__(self, issues: list[str]) -> None:
        super().__init__("\n".join(issues))
        self.issues = issues


def run_phase2_mvp_acceptance(
    base_database_url: URL | str | None = None,
    *,
    tenant_id: str = DEFAULT_TENANT_ID,
    advertiser_id: str = DEFAULT_ADVERTISER_ID,
) -> dict[str, Any]:
    product_loop = run_persisted_product_loop(
        base_database_url,
        tenant_id=tenant_id,
        advertiser_id=advertiser_id,
    )
    _validate_product_loop(product_loop)
    control_plane = _run_control_plane_checks()
    return {
        "status": "passed",
        "phase": "phase2_functionally_complete",
        "tenant_id": tenant_id,
        "database_url": product_loop["database_url"],
        "product_loop": product_loop,
        "control_plane": control_plane,
        "remaining_phase3_boundaries": [
            "native partition migrations",
            "replica-aware read routing",
            "production auth/RBAC and rate limits",
            "external durable queue and DLQ/replay service",
            "SLO dashboards, metrics alerts, load tests, and chaos tests",
            "real ad platform mutation and spend controls",
        ],
    }


def render_summary(summary: dict[str, Any]) -> str:
    product_loop = summary["product_loop"]
    control_plane = summary["control_plane"]
    return "\n".join(
        [
            "Phase 2 MVP acceptance verification passed",
            f"Phase: {summary['phase']}",
            f"Tenant: {summary['tenant_id']}",
            f"Database: {summary['database_url']}",
            (
                "Persisted product loop: "
                f"run={product_loop['first_strategy']['run_id']} "
                f"draft={product_loop['first_strategy']['draft_id']} "
                f"feedback={product_loop['feedback_event']['health_status']} "
                f"outcome={product_loop['feedback_outcome_report']['outcome_status']}"
            ),
            (
                "Feedback operations: "
                f"review={product_loop['review']['decision']} "
                f"revision={product_loop['revision_review']['decision']} "
                f"dry_run={product_loop['execution_dry_run']['status']} "
                f"handoff={product_loop['handoff_record']['outcome']}"
            ),
            (
                "Memory and outbox: "
                f"outbox_completed={product_loop['outbox']['completed']} "
                f"handoff_outbox_completed={product_loop['handoff_outbox']['completed']} "
                f"memories={product_loop['cli_reads']['memory_count']}"
            ),
            (
                "External job process API: "
                f"job={control_plane['external_job_process']['job_id']} "
                f"worker={control_plane['external_job_process']['worker_id']} "
                f"completed={control_plane['external_job_process']['completed']}"
            ),
            (
                "Run lifecycle CLI: "
                f"get={control_plane['run_lifecycle_cli']['get_status']} "
                f"resume_run={control_plane['run_lifecycle_cli']['resumed_run_id']} "
                f"retry_run={control_plane['run_lifecycle_cli']['retried_run_id']}"
            ),
            (
                "Ops summary CLI: "
                f"failed_runs={control_plane['ops_summary_cli']['failed_run_count']} "
                f"guardrails={control_plane['ops_summary_cli']['guardrail_count']}"
            ),
            "Phase 3 boundaries still open: "
            + ", ".join(summary["remaining_phase3_boundaries"]),
        ]
    )


def _run_control_plane_checks() -> dict[str, Any]:
    return {
        "external_job_process": _verify_external_job_process_api(),
        "run_lifecycle_cli": _verify_run_lifecycle_cli(),
        "ops_summary_cli": _verify_ops_summary_cli(),
    }


def _verify_external_job_process_api() -> dict[str, Any]:
    store = InMemoryStrategyJobStore()
    settings = Settings(
        tenant_id="tenant_phase2_jobs",
        strategy_job_backend="memory",
        strategy_job_execution_mode="external",
    )
    api_app.dependency_overrides[get_runtime_settings] = lambda: settings
    api_app.dependency_overrides[get_runtime_strategy_job_store] = lambda: store
    try:
        client = TestClient(api_app)
        accepted = client.post(
            "/growth-strategies/jobs",
            json={"brief": _brief_payload()},
            headers={"X-Tenant-ID": settings.tenant_id},
        )
        process_response = client.post(
            "/growth-strategies/jobs/process",
            params={"limit": "1", "lock_seconds": "60"},
            headers={
                "X-Tenant-ID": settings.tenant_id,
                "X-Worker-ID": "worker_phase2_acceptance",
            },
        )
        detail = client.get(
            accepted.json()["polling_url"],
            headers={"X-Tenant-ID": settings.tenant_id},
        )
    finally:
        api_app.dependency_overrides.clear()

    accepted_payload = _response_json(accepted, label="submit external strategy job")
    process_payload = _response_json(process_response, label="process external strategy job")
    detail_payload = _response_json(detail, label="get processed strategy job")
    _expect(
        accepted.status_code == 202,
        f"external job submission returned {accepted.status_code}",
    )
    _expect(
        accepted.headers.get("strategy-job-execution-mode") == "external",
        "external job submission should advertise external execution mode",
    )
    _expect(process_response.status_code == 200, "external job process API failed")
    _expect(
        process_response.headers.get("strategy-jobs-completed") == "1",
        "external job process API should complete one job",
    )
    _expect(detail_payload["status"] == "completed", "processed job should be completed")
    _expect(detail_payload["result"] is not None, "processed job should include result")
    return {
        "job_id": accepted_payload["job_id"],
        "worker_id": process_payload["worker_id"],
        "claimed": process_payload["claimed"],
        "completed": process_payload["completed"],
        "final_status": detail_payload["status"],
        "run_id": detail_payload["run_id"],
    }


def _verify_run_lifecycle_cli() -> dict[str, Any]:
    growth_response = generate_mock_growth_strategy(
        AdvertiserBrief.model_validate(_brief_payload())
    )
    failed_run = _run_detail_from_growth_response(growth_response, status="failed")
    store = _FakeRunReadStore(failed_run)
    settings = Settings(run_persistence_backend="none", tenant_id="tenant_phase2_runs")

    with patch("ads_growth_agent.cli.get_settings", return_value=settings), patch(
        "ads_growth_agent.cli.build_configured_run_read_store",
        return_value=store,
    ):
        get_payload = _invoke_cli(["get-run", failed_run.run_id])
        resume_payload = _invoke_cli(["resume-run", failed_run.run_id])
        with tempfile.TemporaryDirectory() as tmpdir:
            brief_file = Path(tmpdir) / "brief.json"
            brief_file.write_text(json.dumps({"brief": _brief_payload()}))
            retry_payload = _invoke_cli(["retry-run", failed_run.run_id, str(brief_file)])

    _expect(get_payload["status"] == "failed", "get-run should return failed run")
    _expect(
        resume_payload["run_metadata"]["run_id"] == failed_run.run_id,
        "resume-run should reuse original run_id",
    )
    _expect(
        resume_payload["run_metadata"]["strategy_id"] == failed_run.strategy_id,
        "resume-run should reuse original strategy_id",
    )
    _expect(
        retry_payload["run_metadata"]["run_id"] != failed_run.run_id,
        "retry-run should create a fresh run_id",
    )
    return {
        "requested_run_ids": store.requested_run_ids,
        "get_status": get_payload["status"],
        "resumed_run_id": resume_payload["run_metadata"]["run_id"],
        "resumed_strategy_id": resume_payload["run_metadata"]["strategy_id"],
        "retried_run_id": retry_payload["run_metadata"]["run_id"],
        "retried_strategy_id": retry_payload["run_metadata"]["strategy_id"],
    }


def _verify_ops_summary_cli() -> dict[str, Any]:
    growth_response = generate_mock_growth_strategy(
        AdvertiserBrief.model_validate(_brief_payload())
    )
    failed_run = _run_detail_from_growth_response(growth_response, status="failed")
    settings = Settings(
        run_persistence_backend="postgres",
        strategy_job_backend="memory",
        outbox_backend="postgres",
        performance_event_persistence_backend="postgres",
        tenant_id="tenant_phase2_ops",
    )
    with patch("ads_growth_agent.cli.get_settings", return_value=settings), patch(
        "ads_growth_agent.cli.build_configured_run_read_store",
        return_value=_FakeRunReadStore(failed_run),
    ), patch(
        "ads_growth_agent.cli.build_configured_strategy_job_store",
        return_value=_EmptyStrategyJobStore(),
    ), patch(
        "ads_growth_agent.cli.build_configured_outbox_store",
        return_value=_EmptyOutboxStore(),
    ), patch(
        "ads_growth_agent.cli.build_configured_performance_event_store",
        return_value=_EmptyPerformanceEventStore(),
    ), patch(
        "ads_growth_agent.cli.build_configured_feedback_review_store",
        return_value=object(),
    ), patch(
        "ads_growth_agent.cli.build_configured_feedback_execution_store",
        return_value=object(),
    ), patch(
        "ads_growth_agent.cli.build_configured_feedback_handoff_store",
        return_value=object(),
    ):
        payload = _invoke_cli(["ops-summary", "--limit", "5"])

    _expect(payload["tenant_id"] == settings.tenant_id, "ops summary tenant mismatch")
    _expect(payload["failed_run_count"] == 1, "ops summary should include failed run")
    _expect(
        payload["failed_runs"][0]["run_id"] == failed_run.run_id,
        "ops summary failed run ID mismatch",
    )
    return {
        "tenant_id": payload["tenant_id"],
        "failed_run_count": payload["failed_run_count"],
        "feedback_attention_count": payload["feedback_attention_count"],
        "guardrail_count": len(payload["guardrails"]),
    }


def _validate_product_loop(summary: dict[str, Any]) -> None:
    _expect(summary["status"] == "passed", "persisted product loop did not pass")
    _expect(
        summary["feedback_event"]["advertiser_memory_status"] == "queued",
        "feedback event should queue advertiser memory",
    )
    _expect(
        summary["feedback_outcome_report"]["outcome_status"] == "improved",
        "feedback outcome report should classify the follow-up as improved",
    )
    _expect(
        summary["revision_review"]["dry_run_status"] == "passed",
        "revision review should have a passed dry-run",
    )
    _expect(
        summary["handoff_record"]["outcome"] == "applied",
        "handoff record should capture applied outcome",
    )
    _expect(summary["outbox"]["completed"] == 1, "performance outbox should complete")
    _expect(
        summary["handoff_outbox"]["completed"] == 1,
        "handoff outbox should complete",
    )
    _expect(
        summary["memory"]["source_id"]
        in summary["later_strategy"]["retrieved_memory_source_ids"],
        "later strategy should retrieve learned advertiser memory",
    )


def _invoke_cli(args: list[str]) -> dict[str, Any]:
    result = CliRunner().invoke(cli_module.app, args)
    if result.exit_code != 0:
        stderr = getattr(result, "stderr", "")
        raise Phase2VerificationError(
            [
                f"CLI command failed: ads-growth-agent {' '.join(args)}",
                f"exit_code={result.exit_code}",
                f"stdout={result.stdout.strip()}",
                f"stderr={stderr.strip()}",
                f"exception={result.exception!r}",
            ]
        )
    try:
        return json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        raise Phase2VerificationError(
            [
                f"CLI command returned non-JSON stdout: ads-growth-agent {' '.join(args)}",
                f"stdout={result.stdout.strip()}",
            ]
        ) from exc


def _response_json(response, *, label: str) -> dict[str, Any]:
    try:
        payload = response.json()
    except json.JSONDecodeError as exc:
        raise Phase2VerificationError(
            [f"API call returned non-JSON response: {label}", f"body={response.text}"]
        ) from exc
    if not isinstance(payload, dict):
        raise Phase2VerificationError([f"API call returned non-object JSON: {label}"])
    return payload


def _run_detail_from_growth_response(
    growth_response,
    *,
    status: str = "failed",
) -> AgentRunDetailResponse:
    created_at = datetime.now(UTC)
    return AgentRunDetailResponse(
        run_id=growth_response.run_metadata.run_id,
        execution_id=growth_response.run_metadata.run_id,
        strategy_id=growth_response.strategy.strategy_id,
        advertiser_id=growth_response.strategy.advertiser_id,
        objective=growth_response.strategy.objective,
        status=status,
        trace_id=growth_response.run_metadata.trace_id,
        node_path=growth_response.node_path,
        final_strategy=growth_response.strategy if status == "completed" else None,
        error_summary=[] if status == "completed" else ["phase2 acceptance failed run"],
        metadata={
            "execution_id": growth_response.run_metadata.run_id,
            "advertiser_brief": _brief_payload(),
        },
        steps=[
            AgentRunStepRecord(
                step_index=0,
                node_name="planner",
                status="completed" if status == "completed" else "failed",
                input_json={"run_id": growth_response.run_metadata.run_id},
                output_json={"node_name": "planner"},
                error_json=(
                    {
                        "message": "phase2 acceptance failed run",
                        "tool_name": "planner",
                    }
                    if status == "failed"
                    else None
                ),
                latency_ms=0,
                created_at=created_at,
            )
        ],
        created_at=created_at,
        completed_at=created_at,
    )


class _FakeRunReadStore:
    def __init__(self, run: AgentRunDetailResponse) -> None:
        self._run = run
        self.requested_run_ids: list[str] = []

    def get_run(self, run_id: str) -> AgentRunDetailResponse | None:
        self.requested_run_ids.append(run_id)
        if run_id == self._run.run_id:
            return self._run
        return None

    def list_runs(self, *, status=None, limit: int = 50):
        if status is not None and self._run.status != status:
            return []
        return [self._run][:limit]


class _EmptyStrategyJobStore:
    def list_jobs(self, *, status=None, advertiser_id=None, run_id=None, limit: int = 50):
        return []


class _EmptyOutboxStore:
    def list_events(
        self,
        *,
        status=None,
        event_type=None,
        aggregate_type=None,
        aggregate_id=None,
        limit: int = 50,
    ):
        return []


class _EmptyPerformanceEventStore:
    def list_events(
        self,
        *,
        advertiser_id=None,
        run_id=None,
        campaign_id=None,
        draft_id=None,
        event_type=None,
        limit: int = 50,
    ):
        return []


def _brief_payload() -> dict[str, Any]:
    return {
        "advertiser_id": DEFAULT_ADVERTISER_ID,
        "product_name": "FitTrack Pro",
        "product_category": "fitness app",
        "objective": "registrations",
        "budget": "2000.00",
        "currency": "USD",
        "duration_days": 14,
        "target_market": "United States",
        "primary_kpi": "trial registrations",
        "target_cpa": "20.00",
        "landing_page_url": "https://example.com/fittrack",
        "brand_voice": "motivational and practical",
        "constraints": [
            "Do not make medical claims",
            "Keep all campaign mutations draft-only",
        ],
    }


def _expect(condition: bool, message: str) -> None:
    if not condition:
        raise Phase2VerificationError([message])


def main() -> int:
    try:
        summary = run_phase2_mvp_acceptance()
    except (Phase2VerificationError, ProductLoopVerificationError) as exc:
        print("Phase 2 MVP acceptance verification failed", file=sys.stderr)
        for issue in exc.issues:
            print(f"- {issue}", file=sys.stderr)
        return 1

    print(render_summary(summary))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
