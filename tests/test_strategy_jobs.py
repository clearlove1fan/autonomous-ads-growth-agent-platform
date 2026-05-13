from decimal import Decimal

from fastapi.testclient import TestClient

from ads_growth_agent import strategy_job_worker as worker_module
from ads_growth_agent.api import (
    app as api_app,
)
from ads_growth_agent.api import (
    get_runtime_settings,
    get_runtime_strategy_job_store,
)
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
    assert detail_payload["completed_at"] is not None


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
    assert report.failed == 0
    assert completed is not None
    assert completed.status == "completed"
    assert completed.result is not None


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


def _brief_payload() -> dict:
    return {
        "advertiser_id": "adv_fitness_001",
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
