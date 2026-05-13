import json
from decimal import Decimal

from fastapi.testclient import TestClient
from typer.testing import CliRunner

from ads_growth_agent import strategy_job_worker as worker_module
from ads_growth_agent.api import (
    app as api_app,
)
from ads_growth_agent.api import (
    get_runtime_settings,
    get_runtime_strategy_job_store,
)
from ads_growth_agent.cli import app as cli_app
from ads_growth_agent.config import Settings
from ads_growth_agent.contracts import GrowthStrategyRequest
from ads_growth_agent.persistence.strategy_job_store import InMemoryStrategyJobStore
from ads_growth_agent.strategy_job_worker import process_strategy_jobs


def test_growth_strategy_job_accepts_request_and_completes_in_background() -> None:
    store = InMemoryStrategyJobStore()
    api_app.dependency_overrides[get_runtime_settings] = lambda: Settings(
        strategy_job_backend="memory"
    )
    api_app.dependency_overrides[get_runtime_strategy_job_store] = lambda: store
    try:
        accepted = TestClient(api_app).post(
            "/growth-strategies/jobs",
            json={"brief": _brief_payload()},
            headers={"X-Tenant-ID": "tenant_jobs"},
        )
    finally:
        api_app.dependency_overrides.clear()

    accepted_payload = accepted.json()
    assert accepted.status_code == 202
    assert accepted.headers["x-tenant-id"] == "tenant_jobs"
    assert accepted.headers["strategy-job-id"] == accepted_payload["job_id"]
    assert accepted.headers["run-id"] == accepted_payload["run_id"]
    assert accepted.headers["location"] == accepted_payload["polling_url"]
    assert accepted.headers["strategy-job-execution-mode"] == "background"
    assert accepted_payload["status"] == "queued"
    assert accepted_payload["advertiser_id"] == "adv_fitness_001"

    api_app.dependency_overrides[get_runtime_strategy_job_store] = lambda: store
    try:
        detail = TestClient(api_app).get(
            accepted_payload["polling_url"],
            headers={"X-Tenant-ID": "tenant_jobs"},
        )
    finally:
        api_app.dependency_overrides.clear()

    detail_payload = detail.json()
    assert detail.status_code == 200
    assert detail.headers["x-tenant-id"] == "tenant_jobs"
    assert detail_payload["job_id"] == accepted_payload["job_id"]
    assert detail_payload["status"] == "completed"
    assert detail_payload["run_id"] == accepted_payload["run_id"]
    assert detail_payload["trace_id"] == accepted_payload["trace_id"]
    assert detail_payload["request"]["brief"]["advertiser_id"] == "adv_fitness_001"
    assert detail_payload["result"]["strategy"]["advertiser_id"] == "adv_fitness_001"
    assert detail_payload["result"]["run_metadata"]["run_id"] == accepted_payload["run_id"]
    assert detail_payload["error"] is None
    assert detail_payload["completed_at"] is not None
    assert detail_payload["attempt_count"] == 1
    assert detail_payload["next_attempt_at"] is None
    assert detail_payload["locked_by"] is None


def test_growth_strategy_job_records_failed_background_execution(monkeypatch) -> None:
    store = InMemoryStrategyJobStore()

    def fail_generation(*args, **kwargs):
        raise RuntimeError("planner failed")

    monkeypatch.setattr(worker_module, "generate_growth_strategy", fail_generation)
    api_app.dependency_overrides[get_runtime_settings] = lambda: Settings(
        strategy_job_backend="memory"
    )
    api_app.dependency_overrides[get_runtime_strategy_job_store] = lambda: store
    try:
        accepted = TestClient(api_app).post(
            "/growth-strategies/jobs",
            json={"brief": _brief_payload()},
        )
        detail = TestClient(api_app).get(accepted.json()["polling_url"])
    finally:
        api_app.dependency_overrides.clear()

    detail_payload = detail.json()
    assert accepted.status_code == 202
    assert detail.status_code == 200
    assert detail_payload["status"] == "failed"
    assert detail_payload["result"] is None
    assert detail_payload["error"]["error_code"] == "STRATEGY_JOB_EXECUTION_FAILED"
    assert detail_payload["error"]["exception_type"] == "RuntimeError"
    assert detail_payload["error"]["detail"] == "planner failed"
    assert detail_payload["error"]["retry_scheduled"] is False
    assert detail_payload["next_attempt_at"] is None
    assert detail_payload["completed_at"] is not None


def test_strategy_job_uses_configured_max_attempts_in_external_mode() -> None:
    store = InMemoryStrategyJobStore()
    settings = Settings(
        strategy_job_backend="memory",
        strategy_job_execution_mode="external",
        strategy_job_max_attempts=5,
    )
    api_app.dependency_overrides[get_runtime_settings] = lambda: settings
    api_app.dependency_overrides[get_runtime_strategy_job_store] = lambda: store
    try:
        accepted = TestClient(api_app).post(
            "/growth-strategies/jobs",
            json={"brief": _brief_payload()},
        )
        detail = TestClient(api_app).get(accepted.json()["polling_url"])
    finally:
        api_app.dependency_overrides.clear()

    detail_payload = detail.json()
    assert accepted.status_code == 202
    assert detail_payload["status"] == "queued"
    assert detail_payload["max_attempts"] == 5
    assert detail_payload["attempt_count"] == 0
    assert detail_payload["next_attempt_at"] is not None


def test_growth_strategy_jobs_list_filters_by_status_and_advertiser() -> None:
    store = InMemoryStrategyJobStore()
    settings = Settings(strategy_job_backend="memory", strategy_job_execution_mode="external")
    api_app.dependency_overrides[get_runtime_settings] = lambda: settings
    api_app.dependency_overrides[get_runtime_strategy_job_store] = lambda: store
    try:
        first = TestClient(api_app).post(
            "/growth-strategies/jobs",
            json={"brief": _brief_payload(advertiser_id="adv_fitness_001")},
            headers={"X-Tenant-ID": "tenant_jobs"},
        )
        second = TestClient(api_app).post(
            "/growth-strategies/jobs",
            json={"brief": _brief_payload(advertiser_id="adv_fitness_002")},
            headers={"X-Tenant-ID": "tenant_jobs"},
        )
        list_response = TestClient(api_app).get(
            "/growth-strategies/jobs",
            params={
                "status": "queued",
                "advertiser_id": "adv_fitness_001",
                "limit": "10",
            },
            headers={"X-Tenant-ID": "tenant_jobs"},
        )
    finally:
        api_app.dependency_overrides.clear()

    payload = list_response.json()
    assert first.status_code == 202
    assert second.status_code == 202
    assert list_response.status_code == 200
    assert list_response.headers["x-tenant-id"] == "tenant_jobs"
    assert payload["count"] == 1
    assert payload["limit"] == 10
    assert payload["status"] == "queued"
    assert payload["advertiser_id"] == "adv_fitness_001"
    assert payload["items"][0]["advertiser_id"] == "adv_fitness_001"
    assert payload["items"][0]["status"] == "queued"
    assert payload["items"][0]["next_attempt_at"] is not None


def test_failed_strategy_job_can_be_manually_retried_via_api(monkeypatch) -> None:
    store = InMemoryStrategyJobStore()

    def fail_generation(*args, **kwargs):
        raise RuntimeError("planner failed")

    monkeypatch.setattr(worker_module, "generate_growth_strategy", fail_generation)
    api_app.dependency_overrides[get_runtime_settings] = lambda: Settings(
        strategy_job_backend="memory",
        strategy_job_max_attempts=4,
    )
    api_app.dependency_overrides[get_runtime_strategy_job_store] = lambda: store
    try:
        accepted = TestClient(api_app).post(
            "/growth-strategies/jobs",
            json={"brief": _brief_payload()},
        )
        retry_response = TestClient(api_app).post(
            f"/growth-strategies/jobs/{accepted.json()['job_id']}/retry",
            headers={"X-Operator-ID": "operator_api"},
        )
    finally:
        api_app.dependency_overrides.clear()

    payload = retry_response.json()
    assert accepted.status_code == 202
    assert retry_response.status_code == 200
    assert retry_response.headers["strategy-job-id"] == accepted.json()["job_id"]
    assert retry_response.headers["strategy-job-status"] == "queued"
    assert payload["status"] == "queued"
    assert payload["attempt_count"] == 0
    assert payload["max_attempts"] == 4
    assert payload["next_attempt_at"] is not None
    assert payload["completed_at"] is None
    assert payload["error"] is None
    assert payload["metadata"]["manual_retry_count"] == 1
    assert payload["metadata"]["last_manual_retry_by"] == "operator_api"
    assert payload["metadata"]["previous_error"]["detail"] == "planner failed"


def test_manual_retry_rejects_non_failed_strategy_job() -> None:
    store = InMemoryStrategyJobStore()
    settings = Settings(strategy_job_backend="memory", strategy_job_execution_mode="external")
    api_app.dependency_overrides[get_runtime_settings] = lambda: settings
    api_app.dependency_overrides[get_runtime_strategy_job_store] = lambda: store
    try:
        accepted = TestClient(api_app).post(
            "/growth-strategies/jobs",
            json={"brief": _brief_payload()},
        )
        retry_response = TestClient(api_app).post(
            f"/growth-strategies/jobs/{accepted.json()['job_id']}/retry"
        )
    finally:
        api_app.dependency_overrides.clear()

    assert accepted.status_code == 202
    assert retry_response.status_code == 409
    assert retry_response.json()["detail"]["error_code"] == "STRATEGY_JOB_NOT_RETRYABLE"


def test_queued_strategy_job_can_be_manually_cancelled_via_api() -> None:
    store = InMemoryStrategyJobStore()
    settings = Settings(strategy_job_backend="memory", strategy_job_execution_mode="external")
    api_app.dependency_overrides[get_runtime_settings] = lambda: settings
    api_app.dependency_overrides[get_runtime_strategy_job_store] = lambda: store
    try:
        accepted = TestClient(api_app).post(
            "/growth-strategies/jobs",
            json={"brief": _brief_payload()},
        )
        cancel_response = TestClient(api_app).post(
            f"/growth-strategies/jobs/{accepted.json()['job_id']}/cancel",
            json={"reason": "duplicate advertiser request"},
            headers={"X-Operator-ID": "operator_api"},
        )
    finally:
        api_app.dependency_overrides.clear()

    payload = cancel_response.json()
    claim_after_cancel = store.claim_queued(limit=1, worker_id="worker_after_cancel")
    assert accepted.status_code == 202
    assert cancel_response.status_code == 200
    assert cancel_response.headers["strategy-job-id"] == accepted.json()["job_id"]
    assert cancel_response.headers["strategy-job-status"] == "cancelled"
    assert payload["status"] == "cancelled"
    assert payload["result"] is None
    assert payload["error"]["error_code"] == "STRATEGY_JOB_CANCELLED"
    assert payload["error"]["cancelled_by"] == "operator_api"
    assert payload["error"]["cancel_reason"] == "duplicate advertiser request"
    assert payload["metadata"]["cancelled_by"] == "operator_api"
    assert payload["metadata"]["cancel_reason"] == "duplicate advertiser request"
    assert payload["metadata"]["cancelled_from_status"] == "queued"
    assert payload["next_attempt_at"] is None
    assert payload["locked_by"] is None
    assert payload["completed_at"] is not None
    assert claim_after_cancel == []


def test_manual_cancel_rejects_terminal_strategy_job() -> None:
    store = InMemoryStrategyJobStore()
    api_app.dependency_overrides[get_runtime_settings] = lambda: Settings(
        strategy_job_backend="memory"
    )
    api_app.dependency_overrides[get_runtime_strategy_job_store] = lambda: store
    try:
        accepted = TestClient(api_app).post(
            "/growth-strategies/jobs",
            json={"brief": _brief_payload()},
        )
        cancel_response = TestClient(api_app).post(
            f"/growth-strategies/jobs/{accepted.json()['job_id']}/cancel"
        )
    finally:
        api_app.dependency_overrides.clear()

    assert accepted.status_code == 202
    assert cancel_response.status_code == 409
    assert cancel_response.json()["detail"]["error_code"] == "STRATEGY_JOB_NOT_CANCELLABLE"
    assert cancel_response.json()["detail"]["status"] == "completed"


def test_external_strategy_job_execution_mode_leaves_job_queued_then_worker_completes() -> None:
    store = InMemoryStrategyJobStore()
    settings = Settings(strategy_job_backend="memory", strategy_job_execution_mode="external")
    api_app.dependency_overrides[get_runtime_settings] = lambda: settings
    api_app.dependency_overrides[get_runtime_strategy_job_store] = lambda: store
    try:
        accepted = TestClient(api_app).post(
            "/growth-strategies/jobs",
            json={"brief": _brief_payload()},
        )
        queued_detail = TestClient(api_app).get(accepted.json()["polling_url"])
    finally:
        api_app.dependency_overrides.clear()

    accepted_payload = accepted.json()
    queued_payload = queued_detail.json()
    assert accepted.status_code == 202
    assert accepted.headers["strategy-job-execution-mode"] == "external"
    assert queued_payload["status"] == "queued"
    assert queued_payload["result"] is None

    report = process_strategy_jobs(
        store,
        settings=settings,
        limit=1,
        worker_id="worker_unit",
    )
    completed = store.get_job(accepted_payload["job_id"])

    assert report.claimed == 1
    assert report.completed == 1
    assert report.retry_scheduled == 0
    assert report.failed == 0
    assert completed is not None
    assert completed.status == "completed"
    assert completed.result is not None


def test_running_strategy_job_cancel_is_not_overwritten_by_worker_completion(
    monkeypatch,
) -> None:
    store = InMemoryStrategyJobStore()
    settings = Settings(strategy_job_backend="memory", strategy_job_execution_mode="external")
    request = GrowthStrategyRequest.model_validate({"brief": _brief_payload()})
    job = store.create_queued(
        request,
        job_id="job_cancel_race",
        strategy_id="strategy_cancel_race",
        run_id="run_cancel_race",
        trace_id="trace_cancel_race",
    )
    original_generate = worker_module.generate_growth_strategy

    def cancel_during_generation(*args, **kwargs):
        cancelled = store.cancel(
            job.job_id,
            requested_by="operator_race",
            reason="operator stopped in-flight job",
        )
        assert cancelled is not None
        assert cancelled.status == "cancelled"
        return original_generate(*args, **kwargs)

    monkeypatch.setattr(worker_module, "generate_growth_strategy", cancel_during_generation)

    report = process_strategy_jobs(
        store,
        settings=settings,
        limit=1,
        worker_id="worker_race",
    )
    terminal = store.get_job(job.job_id)

    assert report.claimed == 1
    assert report.completed == 0
    assert report.failed == 0
    assert report.cancelled == 1
    assert terminal is not None
    assert terminal.status == "cancelled"
    assert terminal.result is None
    assert terminal.error is not None
    assert terminal.error["error_code"] == "STRATEGY_JOB_CANCELLED"
    assert terminal.metadata["cancelled_from_status"] == "running"


def test_external_strategy_job_worker_retries_then_marks_terminal_failed(
    monkeypatch,
) -> None:
    store = InMemoryStrategyJobStore()
    settings = Settings(
        strategy_job_backend="memory",
        strategy_job_execution_mode="external",
        strategy_job_retry_base_delay_seconds=0,
        strategy_job_retry_max_delay_seconds=0,
    )
    request = GrowthStrategyRequest.model_validate({"brief": _brief_payload()})
    job = store.create_queued(
        request,
        job_id="job_retry",
        strategy_id="strategy_retry",
        run_id="run_retry",
        trace_id="trace_retry",
        max_attempts=2,
    )

    def fail_generation(*args, **kwargs):
        raise RuntimeError("temporary planner failure")

    monkeypatch.setattr(worker_module, "generate_growth_strategy", fail_generation)

    first_report = process_strategy_jobs(
        store,
        settings=settings,
        limit=1,
        worker_id="worker_retry",
    )
    scheduled = store.get_job(job.job_id)

    assert first_report.claimed == 1
    assert first_report.completed == 0
    assert first_report.retry_scheduled == 1
    assert first_report.failed == 0
    assert scheduled is not None
    assert scheduled.status == "queued"
    assert scheduled.attempt_count == 1
    assert scheduled.next_attempt_at is not None
    assert scheduled.completed_at is None
    assert scheduled.error is not None
    assert scheduled.error["retry_scheduled"] is True
    assert scheduled.error["retry_delay_seconds"] == 0

    second_report = process_strategy_jobs(
        store,
        settings=settings,
        limit=1,
        worker_id="worker_retry",
    )
    terminal = store.get_job(job.job_id)

    assert second_report.claimed == 1
    assert second_report.completed == 0
    assert second_report.retry_scheduled == 0
    assert second_report.failed == 1
    assert second_report.cancelled == 0
    assert terminal is not None
    assert terminal.status == "failed"
    assert terminal.attempt_count == 2
    assert terminal.next_attempt_at is None
    assert terminal.completed_at is not None
    assert terminal.error is not None
    assert terminal.error["retry_scheduled"] is False
    assert terminal.error["retry_delay_seconds"] is None


def test_in_memory_strategy_job_claim_respects_next_attempt_at() -> None:
    store = InMemoryStrategyJobStore()
    request = GrowthStrategyRequest.model_validate({"brief": _brief_payload()})
    job = store.create_queued(
        request,
        job_id="job_delayed_retry",
        strategy_id="strategy_delayed_retry",
        run_id="run_delayed_retry",
        trace_id="trace_delayed_retry",
        max_attempts=2,
    )
    claimed = store.claim_queued(limit=1, worker_id="worker_retry")
    scheduled = store.mark_attempt_failed(
        claimed[0].job_id,
        error={"message": "temporary failure"},
        retry_delay_seconds=60,
    )
    immediate_retry = store.claim_queued(limit=1, worker_id="worker_retry")

    assert claimed[0].job_id == job.job_id
    assert scheduled is not None
    assert scheduled.status == "queued"
    assert scheduled.next_attempt_at is not None
    assert immediate_retry == []


def test_in_memory_strategy_job_claims_distinct_jobs_per_worker() -> None:
    store = InMemoryStrategyJobStore()
    request = GrowthStrategyRequest.model_validate({"brief": _brief_payload()})
    first = store.create_queued(
        request,
        job_id="job_first",
        strategy_id="strategy_first",
        run_id="run_first",
        trace_id="trace_first",
    )
    second = store.create_queued(
        request,
        job_id="job_second",
        strategy_id="strategy_second",
        run_id="run_second",
        trace_id="trace_second",
    )

    worker_one = store.claim_queued(limit=1, worker_id="worker_one")
    worker_two = store.claim_queued(limit=1, worker_id="worker_two")

    claimed_ids = {worker_one[0].job_id, worker_two[0].job_id}
    assert claimed_ids == {first.job_id, second.job_id}
    assert worker_one[0].locked_by == "worker_one"
    assert worker_two[0].locked_by == "worker_two"
    assert worker_one[0].attempt_count == 1
    assert worker_two[0].attempt_count == 1


def test_list_strategy_jobs_cli_filters_jobs(monkeypatch) -> None:
    store = InMemoryStrategyJobStore()
    request = GrowthStrategyRequest.model_validate({"brief": _brief_payload()})
    store.create_queued(
        request,
        job_id="job_cli_001",
        strategy_id="strategy_cli_001",
        run_id="run_cli_001",
        trace_id="trace_cli_001",
    )

    monkeypatch.setattr(
        "ads_growth_agent.cli.get_settings",
        lambda: Settings(tenant_id="tenant_cli"),
    )
    monkeypatch.setattr(
        "ads_growth_agent.cli.build_configured_strategy_job_store",
        lambda settings: store,
    )

    result = CliRunner().invoke(
        cli_app,
        [
            "list-strategy-jobs",
            "--status",
            "queued",
            "--advertiser-id",
            "adv_fitness_001",
            "--limit",
            "5",
        ],
    )

    assert result.exit_code == 0
    parsed = json.loads(result.stdout)
    assert parsed["count"] == 1
    assert parsed["limit"] == 5
    assert parsed["status"] == "queued"
    assert parsed["advertiser_id"] == "adv_fitness_001"
    assert parsed["items"][0]["job_id"] == "job_cli_001"


def test_retry_strategy_job_cli_requeues_failed_job(monkeypatch) -> None:
    store = InMemoryStrategyJobStore()
    request = GrowthStrategyRequest.model_validate({"brief": _brief_payload()})
    job = store.create_queued(
        request,
        job_id="job_cli_retry",
        strategy_id="strategy_cli_retry",
        run_id="run_cli_retry",
        trace_id="trace_cli_retry",
        max_attempts=1,
    )
    claimed = store.claim_queued(limit=1, worker_id="worker_cli")
    store.mark_attempt_failed(
        claimed[0].job_id,
        error={"message": "permanent failure"},
        retry_delay_seconds=0,
    )

    monkeypatch.setattr(
        "ads_growth_agent.cli.get_settings",
        lambda: Settings(tenant_id="tenant_cli", strategy_job_max_attempts=3),
    )
    monkeypatch.setattr(
        "ads_growth_agent.cli.build_configured_strategy_job_store",
        lambda settings: store,
    )

    result = CliRunner().invoke(
        cli_app,
        ["retry-strategy-job", job.job_id, "--requested-by", "operator_cli"],
    )

    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["job_id"] == job.job_id
    assert payload["status"] == "queued"
    assert payload["attempt_count"] == 0
    assert payload["max_attempts"] == 3
    assert payload["metadata"]["manual_retry_count"] == 1
    assert payload["metadata"]["last_manual_retry_by"] == "operator_cli"
    assert payload["metadata"]["previous_error"]["message"] == "permanent failure"


def test_cancel_strategy_job_cli_cancels_queued_job(monkeypatch) -> None:
    store = InMemoryStrategyJobStore()
    request = GrowthStrategyRequest.model_validate({"brief": _brief_payload()})
    job = store.create_queued(
        request,
        job_id="job_cli_cancel",
        strategy_id="strategy_cli_cancel",
        run_id="run_cli_cancel",
        trace_id="trace_cli_cancel",
    )

    monkeypatch.setattr(
        "ads_growth_agent.cli.get_settings",
        lambda: Settings(tenant_id="tenant_cli"),
    )
    monkeypatch.setattr(
        "ads_growth_agent.cli.build_configured_strategy_job_store",
        lambda settings: store,
    )

    result = CliRunner().invoke(
        cli_app,
        [
            "cancel-strategy-job",
            job.job_id,
            "--requested-by",
            "operator_cli",
            "--reason",
            "bad audience input",
        ],
    )

    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["job_id"] == job.job_id
    assert payload["status"] == "cancelled"
    assert payload["error"]["error_code"] == "STRATEGY_JOB_CANCELLED"
    assert payload["metadata"]["cancelled_by"] == "operator_cli"
    assert payload["metadata"]["cancel_reason"] == "bad audience input"


def test_get_growth_strategy_job_returns_404_for_missing_job() -> None:
    api_app.dependency_overrides[get_runtime_strategy_job_store] = (
        lambda: InMemoryStrategyJobStore()
    )
    try:
        response = TestClient(api_app).get("/growth-strategies/jobs/missing_job")
    finally:
        api_app.dependency_overrides.clear()

    assert response.status_code == 404
    assert response.json()["detail"]["error_code"] == "STRATEGY_JOB_NOT_FOUND"


def _brief_payload(*, advertiser_id: str = "adv_fitness_001") -> dict:
    return {
        "advertiser_id": advertiser_id,
        "product_name": "FitTrack Pro",
        "product_category": "fitness app",
        "objective": "registrations",
        "budget": str(Decimal("2000.00")),
        "currency": "USD",
        "duration_days": 14,
        "target_market": "United States",
        "primary_kpi": "trial registrations",
        "target_cpa": str(Decimal("20.00")),
        "landing_page_url": "https://example.com/fittrack",
        "brand_voice": "motivational and practical",
        "constraints": [
            "Avoid unrealistic body transformation claims",
            "Do not imply medical outcomes",
        ],
        "known_audiences": [
            "Home workout beginners",
            "Wearable fitness tracker users",
        ],
        "historical_context": (
            "Previous organic posts performed best when showing short workout streaks "
            "and beginner-friendly progress tracking."
        ),
    }
