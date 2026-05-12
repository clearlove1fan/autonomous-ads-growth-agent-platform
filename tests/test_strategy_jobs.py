from decimal import Decimal

from fastapi import HTTPException
from fastapi.testclient import TestClient

from ads_growth_agent import api as api_module
from ads_growth_agent.api import (
    app as api_app,
)
from ads_growth_agent.api import (
    get_runtime_settings,
    get_runtime_strategy_job_store,
)
from ads_growth_agent.config import Settings
from ads_growth_agent.persistence.strategy_job_store import InMemoryStrategyJobStore


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


def test_growth_strategy_job_records_failed_background_execution(monkeypatch) -> None:
    store = InMemoryStrategyJobStore()

    def fail_generation(*args, **kwargs):
        raise HTTPException(
            status_code=502,
            detail={"message": "planner failed", "error_code": "PLANNER_FAILED"},
        )

    monkeypatch.setattr(api_module, "_generate_growth_strategy_response", fail_generation)
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
    assert detail_payload["error"]["status_code"] == 502
    assert detail_payload["error"]["detail"]["error_code"] == "PLANNER_FAILED"
    assert detail_payload["completed_at"] is not None


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
