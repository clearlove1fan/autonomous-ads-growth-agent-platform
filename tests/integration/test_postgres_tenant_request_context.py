import os
from uuid import uuid4

import pytest
import sqlalchemy as sa
from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient
from sqlalchemy.engine import URL, make_url

from ads_growth_agent.api import app as api_app
from ads_growth_agent.api import get_runtime_settings
from ads_growth_agent.campaign_draft_store_factory import (
    dispose_cached_campaign_draft_store_engines,
)
from ads_growth_agent.config import Settings
from ads_growth_agent.idempotency_store_factory import (
    dispose_cached_idempotency_store_engines,
)
from ads_growth_agent.run_store_factory import dispose_cached_run_store_engines

pytestmark = pytest.mark.integration

DEFAULT_TEST_DATABASE_URL = (
    "postgresql+psycopg://ads_growth:ads_growth@localhost:5432/ads_growth"
)


def test_growth_strategy_api_isolates_postgres_state_by_request_tenant(
    monkeypatch,
) -> None:
    base_url = _integration_database_url()
    test_url = _create_temporary_database(base_url)
    database_url = test_url.render_as_string(hide_password=False)
    engine = sa.create_engine(test_url)
    settings = Settings(
        database_url=database_url,
        idempotency_backend="postgres",
        run_persistence_backend="postgres",
        campaign_draft_persistence_backend="postgres",
        tenant_id="process_default",
        idempotency_ttl_seconds=60,
    )

    try:
        monkeypatch.setenv("DATABASE_URL", database_url)
        command.upgrade(Config("alembic.ini"), "head")
        api_app.dependency_overrides[get_runtime_settings] = lambda: settings

        client = TestClient(api_app)
        first = client.post(
            "/growth-strategies",
            json={"brief": _brief_payload()},
            headers={
                "Idempotency-Key": "idem-shared-key",
                "X-Tenant-ID": "tenant_a",
            },
        )
        second = client.post(
            "/growth-strategies",
            json={"brief": _brief_payload()},
            headers={
                "Idempotency-Key": "idem-shared-key",
                "X-Tenant-ID": "tenant_b",
            },
        )

        assert first.status_code == 200
        assert second.status_code == 200
        assert first.headers["x-tenant-id"] == "tenant_a"
        assert second.headers["x-tenant-id"] == "tenant_b"
        assert first.headers["idempotency-status"] == "created"
        assert second.headers["idempotency-status"] == "created"
        assert first.json()["run_metadata"]["run_id"] != second.json()["run_metadata"]["run_id"]
        assert first.json()["strategy"]["strategy_id"] == second.json()["strategy"]["strategy_id"]

        first_run_id = first.json()["run_metadata"]["run_id"]
        second_run_id = second.json()["run_metadata"]["run_id"]
        strategy_id = first.json()["strategy"]["strategy_id"]
        with engine.connect() as connection:
            idempotency_rows = connection.execute(
                sa.text(
                    "SELECT tenant_id, idempotency_key, status, run_id "
                    "FROM idempotency_keys "
                    "WHERE idempotency_key = :key "
                    "ORDER BY tenant_id"
                ),
                {"key": "idem-shared-key"},
            ).mappings().all()
            run_rows = connection.execute(
                sa.text(
                    "SELECT tenant_id, run_id, strategy_id, status "
                    "FROM agent_runs "
                    "WHERE strategy_id = :strategy_id "
                    "ORDER BY tenant_id"
                ),
                {"strategy_id": strategy_id},
            ).mappings().all()
            draft_rows = connection.execute(
                sa.text(
                    "SELECT tenant_id, created_by_run_id "
                    "FROM campaign_drafts "
                    "WHERE metadata ->> 'strategy_id' = :strategy_id "
                    "ORDER BY tenant_id"
                ),
                {"strategy_id": strategy_id},
            ).mappings().all()

        assert [row["tenant_id"] for row in idempotency_rows] == ["tenant_a", "tenant_b"]
        assert [row["status"] for row in idempotency_rows] == ["completed", "completed"]
        assert [row["run_id"] for row in idempotency_rows] == [first_run_id, second_run_id]
        assert [row["tenant_id"] for row in run_rows] == ["tenant_a", "tenant_b"]
        assert [row["run_id"] for row in run_rows] == [first_run_id, second_run_id]
        assert [row["strategy_id"] for row in run_rows] == [strategy_id, strategy_id]
        assert [row["status"] for row in run_rows] == ["completed", "completed"]
        assert [row["tenant_id"] for row in draft_rows] == ["tenant_a", "tenant_b"]
        assert [row["created_by_run_id"] for row in draft_rows] == [
            first_run_id,
            second_run_id,
        ]
    finally:
        api_app.dependency_overrides.clear()
        dispose_cached_campaign_draft_store_engines()
        dispose_cached_idempotency_store_engines()
        dispose_cached_run_store_engines()
        engine.dispose()
        _drop_temporary_database(test_url)


def _brief_payload() -> dict[str, object]:
    return {
        "advertiser_id": "adv_fitness_001",
        "product_name": "FitTrack Pro",
        "product_category": "fitness app",
        "objective": "registrations",
        "budget": "2000.00",
        "currency": "USD",
        "duration_days": 14,
        "target_market": "United States",
        "primary_kpi": "trial registrations",
        "target_cpa": "20.00",
        "brand_voice": "motivational and practical",
        "constraints": [
            "Avoid unrealistic body transformation claims",
            "Do not imply medical outcomes",
        ],
        "known_audiences": [
            "Home workout beginners",
            "Wearable fitness tracker users",
        ],
    }


def _integration_database_url() -> URL:
    if os.getenv("RUN_POSTGRES_INTEGRATION") != "1":
        pytest.skip("Set RUN_POSTGRES_INTEGRATION=1 to run live PostgreSQL tests.")
    return make_url(os.getenv("TEST_DATABASE_URL", DEFAULT_TEST_DATABASE_URL))


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
