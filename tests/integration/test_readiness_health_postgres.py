import os

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.engine import make_url

from ads_growth_agent.api import app as api_app
from ads_growth_agent.api import get_runtime_settings
from ads_growth_agent.config import Settings

pytestmark = pytest.mark.integration

DEFAULT_TEST_DATABASE_URL = (
    "postgresql+psycopg://ads_growth:ads_growth@localhost:5432/ads_growth"
)


def test_readiness_checks_live_postgres_dependency() -> None:
    settings = Settings(
        database_url=_integration_database_url(),
        run_persistence_backend="postgres",
        use_llm_planner=False,
        use_llm_critic=False,
    )
    api_app.dependency_overrides[get_runtime_settings] = lambda: settings
    try:
        response = TestClient(api_app).get("/health/ready")
    finally:
        api_app.dependency_overrides.clear()

    payload = response.json()
    assert response.status_code == 200
    assert payload["status"] == "ok"
    assert payload["dependencies"][0]["name"] == "postgres"
    assert payload["dependencies"][0]["status"] == "ok"
    assert payload["dependencies"][0]["required"] is True
    assert payload["dependencies"][1]["status"] == "skipped"


def _integration_database_url() -> str:
    if os.getenv("RUN_POSTGRES_INTEGRATION") != "1":
        pytest.skip("Set RUN_POSTGRES_INTEGRATION=1 to run live PostgreSQL tests.")
    return make_url(os.getenv("TEST_DATABASE_URL", DEFAULT_TEST_DATABASE_URL)).render_as_string(
        hide_password=False
    )
