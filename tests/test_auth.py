from fastapi.routing import APIRoute
from fastapi.testclient import TestClient

from ads_growth_agent.api import app, get_runtime_settings, require_api_auth
from ads_growth_agent.config import Settings

PROTECTED_PATHS = {
    "/advertiser-briefs/parse",
    "/advertisers/{advertiser_id}/memories",
    "/advertisers/{advertiser_id}/memories/{source_id}",
    "/campaign-drafts",
    "/campaign-drafts/{draft_id}",
    "/campaign-events/performance",
    "/campaign-events/performance/{event_id}/action-plan",
    "/campaign-events/performance/{event_id}/feedback-loop-summary",
    "/campaign-events/performance/{event_id}/optimization-draft",
    "/campaign-events/performance/{event_id}/optimization-draft/reviews",
    "/campaign-events/performance/{event_id}",
    "/feedback-optimization-reviews",
    "/feedback-optimization-review-lineages",
    "/feedback-optimization-reviews/{review_id}",
    "/feedback-optimization-reviews/{review_id}/lineage",
    "/feedback-optimization-reviews/{review_id}/revision-draft",
    "/feedback-optimization-reviews/{review_id}/revision-draft/reviews",
    "/feedback-optimization-reviews/{review_id}/execution-plan",
    "/feedback-optimization-reviews/{review_id}/execution-plan/dry-run",
    "/feedback-execution-dry-runs",
    "/feedback-execution-dry-runs/{dry_run_id}",
    "/growth-strategies",
    "/growth-strategies/from-text",
    "/growth-strategies/jobs",
    "/growth-strategies/jobs/from-text",
    "/growth-strategies/jobs/{job_id}",
    "/growth-strategies/jobs/{job_id}/cancel",
    "/growth-strategies/jobs/{job_id}/retry",
    "/runs/{run_id}",
    "/runs/{run_id}/resume",
    "/runs/{run_id}/retry",
}


def test_health_endpoints_remain_public_when_api_key_auth_is_enabled() -> None:
    app.dependency_overrides[get_runtime_settings] = lambda: Settings(
        auth_mode="api_key",
        ads_growth_api_key="local-secret",
    )
    try:
        response = TestClient(app).get("/health/live")
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200
    assert response.json()["status"] == "ok"


def test_protected_api_allows_requests_when_auth_is_disabled() -> None:
    app.dependency_overrides[get_runtime_settings] = lambda: Settings(auth_mode="none")
    try:
        response = TestClient(app).post(
            "/advertiser-briefs/parse",
            json=_brief_intake_payload(),
        )
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200
    assert response.json()["brief"]["advertiser_id"] == "adv_auth_demo"


def test_api_key_auth_requires_credentials() -> None:
    app.dependency_overrides[get_runtime_settings] = lambda: Settings(
        auth_mode="api_key",
        ads_growth_api_key="local-secret",
    )
    try:
        response = TestClient(app).post(
            "/advertiser-briefs/parse",
            json=_brief_intake_payload(),
        )
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 401
    assert response.headers["www-authenticate"] == "Bearer"
    assert response.json()["detail"]["error_code"] == "AUTH_REQUIRED"


def test_api_key_auth_rejects_invalid_credentials() -> None:
    app.dependency_overrides[get_runtime_settings] = lambda: Settings(
        auth_mode="api_key",
        ads_growth_api_key="local-secret",
    )
    try:
        response = TestClient(app).post(
            "/advertiser-briefs/parse",
            json=_brief_intake_payload(),
            headers={"X-API-Key": "wrong-secret"},
        )
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 403
    assert response.json()["detail"]["error_code"] == "AUTH_FORBIDDEN"


def test_api_key_auth_accepts_x_api_key_header() -> None:
    app.dependency_overrides[get_runtime_settings] = lambda: Settings(
        auth_mode="api_key",
        ads_growth_api_key="local-secret",
    )
    try:
        response = TestClient(app).post(
            "/advertiser-briefs/parse",
            json=_brief_intake_payload(),
            headers={"X-API-Key": "local-secret"},
        )
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200
    assert response.json()["brief"]["advertiser_id"] == "adv_auth_demo"


def test_api_key_auth_accepts_bearer_token() -> None:
    app.dependency_overrides[get_runtime_settings] = lambda: Settings(
        auth_mode="api_key",
        ads_growth_api_key="local-secret",
    )
    try:
        response = TestClient(app).post(
            "/advertiser-briefs/parse",
            json=_brief_intake_payload(),
            headers={"Authorization": "Bearer local-secret"},
        )
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200
    assert response.json()["brief"]["advertiser_id"] == "adv_auth_demo"


def test_api_key_auth_reports_misconfiguration() -> None:
    app.dependency_overrides[get_runtime_settings] = lambda: Settings(
        auth_mode="api_key",
        ads_growth_api_key=None,
    )
    try:
        response = TestClient(app).post(
            "/advertiser-briefs/parse",
            json=_brief_intake_payload(),
            headers={"X-API-Key": "local-secret"},
        )
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 503
    assert response.json()["detail"]["error_code"] == "AUTH_NOT_CONFIGURED"


def test_product_routes_are_registered_with_auth_dependency() -> None:
    routes = {
        route.path: route
        for route in app.routes
        if isinstance(route, APIRoute)
    }

    for path in PROTECTED_PATHS:
        assert path in routes
        assert any(
            dependency.dependency is require_api_auth
            for dependency in routes[path].dependencies
        ), path

    for path in {"/health", "/health/live", "/health/ready"}:
        assert path in routes
        assert not any(
            dependency.dependency is require_api_auth
            for dependency in routes[path].dependencies
        ), path


def _brief_intake_payload() -> dict[str, str]:
    return {
        "text": "Use $2000 to promote a fitness app and improve registrations.",
        "advertiser_id": "adv_auth_demo",
    }
