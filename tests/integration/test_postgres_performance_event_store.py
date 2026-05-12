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
from ads_growth_agent.config import Settings
from ads_growth_agent.performance_event_store_factory import (
    dispose_cached_performance_event_store_engines,
)

pytestmark = pytest.mark.integration

DEFAULT_TEST_DATABASE_URL = (
    "postgresql+psycopg://ads_growth:ads_growth@localhost:5432/ads_growth"
)


def test_performance_event_api_persists_analysis_to_postgres(monkeypatch) -> None:
    base_url = _integration_database_url()
    test_url = _create_temporary_database(base_url)
    engine = sa.create_engine(test_url)

    try:
        monkeypatch.setenv("DATABASE_URL", test_url.render_as_string(hide_password=False))
        command.upgrade(Config("alembic.ini"), "head")

        settings = Settings(
            database_url=test_url.render_as_string(hide_password=False),
            performance_event_persistence_backend="postgres",
            tenant_id="tenant_perf",
        )
        api_app.dependency_overrides[get_runtime_settings] = lambda: settings
        client = TestClient(api_app)

        response = client.post(
            "/campaign-events/performance",
            json=_event_payload(),
            headers={"X-Tenant-ID": "tenant_perf"},
        )
        payload = response.json()

        assert response.status_code == 200
        assert response.headers["performance-event-id"] == "evt_perf_integration"
        assert payload["persisted"] is True
        assert payload["analysis"]["health_status"] == "underperforming"
        detail = client.get(
            "/campaign-events/performance/evt_perf_integration",
            headers={"X-Tenant-ID": "tenant_perf"},
        )
        missing_from_other_tenant = client.get(
            "/campaign-events/performance/evt_perf_integration",
            headers={"X-Tenant-ID": "tenant_other"},
        )

        assert detail.status_code == 200
        detail_payload = detail.json()
        assert detail_payload["event_id"] == "evt_perf_integration"
        assert detail_payload["metrics"]["spend"] == "1000.00"
        assert detail_payload["analysis"]["health_status"] == "underperforming"
        assert detail_payload["metadata"]["target_cpa"] == "20.00"
        assert missing_from_other_tenant.status_code == 404
        assert (
            missing_from_other_tenant.json()["detail"]["error_code"]
            == "PERFORMANCE_EVENT_NOT_FOUND"
        )

        with engine.connect() as connection:
            event = connection.execute(
                sa.text(
                    "SELECT tenant_id, event_id, advertiser_id, campaign_id, objective, "
                    "event_type, metrics_json, analysis_json, status, metadata, "
                    "partition_key, partition_bucket "
                    "FROM campaign_performance_events WHERE event_id = :event_id"
                ),
                {"event_id": "evt_perf_integration"},
            ).mappings().one()
            other_tenant_count = connection.execute(
                sa.text(
                    "SELECT count(*) FROM campaign_performance_events "
                    "WHERE tenant_id = :tenant_id"
                ),
                {"tenant_id": "tenant_other"},
            ).scalar_one()

        assert event["tenant_id"] == "tenant_perf"
        assert event["advertiser_id"] == "adv_fitness_001"
        assert event["campaign_id"] == "cmp_fitness_001"
        assert event["objective"] == "registrations"
        assert event["event_type"] == "performance_snapshot"
        assert event["metrics_json"]["spend"] == "1000.00"
        assert event["analysis_json"]["health_status"] == "underperforming"
        assert event["status"] == "analyzed"
        assert event["metadata"]["target_cpa"] == "20.00"
        assert event["partition_key"] == "evt_perf_integration"
        assert 0 <= event["partition_bucket"] < 128
        assert other_tenant_count == 0
    finally:
        api_app.dependency_overrides.clear()
        dispose_cached_performance_event_store_engines()
        engine.dispose()
        _drop_temporary_database(test_url)


def _event_payload() -> dict[str, object]:
    return {
        "event_id": "evt_perf_integration",
        "advertiser_id": "adv_fitness_001",
        "campaign_id": "cmp_fitness_001",
        "objective": "registrations",
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
