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
from ads_growth_agent.idempotency_store_factory import (
    dispose_cached_idempotency_store_engines,
)
from ads_growth_agent.run_store_factory import dispose_cached_run_store_engines

pytestmark = pytest.mark.integration

DEFAULT_TEST_DATABASE_URL = (
    "postgresql+psycopg://ads_growth:ads_growth@localhost:5432/ads_growth"
)


def test_growth_strategy_api_replays_postgres_idempotency_key(monkeypatch) -> None:
    base_url = _integration_database_url()
    test_url = _create_temporary_database(base_url)
    engine = sa.create_engine(test_url)
    settings = Settings(
        database_url=test_url.render_as_string(hide_password=False),
        idempotency_backend="postgres",
        run_persistence_backend="postgres",
        tenant_id="default",
        idempotency_ttl_seconds=60,
    )

    try:
        monkeypatch.setenv("DATABASE_URL", test_url.render_as_string(hide_password=False))
        command.upgrade(Config("alembic.ini"), "head")
        api_app.dependency_overrides[get_runtime_settings] = lambda: settings

        client = TestClient(api_app)
        first = client.post(
            "/growth-strategies",
            json={"brief": _brief_payload()},
            headers={"Idempotency-Key": "idem-live-001"},
        )
        second = client.post(
            "/growth-strategies",
            json={"brief": _brief_payload()},
            headers={"Idempotency-Key": "idem-live-001"},
        )
        conflict_payload = {"brief": {**_brief_payload(), "budget": "2500.00"}}
        conflict = client.post(
            "/growth-strategies",
            json=conflict_payload,
            headers={"Idempotency-Key": "idem-live-001"},
        )

        assert first.status_code == 200
        assert first.headers["idempotency-status"] == "created"
        assert second.status_code == 200
        assert second.headers["idempotency-status"] == "replayed"
        assert second.json() == first.json()
        assert conflict.status_code == 409
        assert conflict.json()["detail"]["error_code"] == "IDEMPOTENCY_KEY_REUSED"

        with engine.connect() as connection:
            row = connection.execute(
                sa.text(
                    "SELECT idempotency_key, status, run_id, response_json, partition_bucket "
                    "FROM idempotency_keys WHERE idempotency_key = :key"
                ),
                {"key": "idem-live-001"},
            ).mappings().one()
            row_count = connection.execute(
                sa.text(
                    "SELECT count(*) FROM idempotency_keys WHERE idempotency_key = :key"
                ),
                {"key": "idem-live-001"},
            ).scalar_one()

        assert row_count == 1
        assert row["status"] == "completed"
        assert row["run_id"] == first.json()["run_metadata"]["run_id"]
        assert row["response_json"] == first.json()
        assert 0 <= row["partition_bucket"] < 128
    finally:
        api_app.dependency_overrides.clear()
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
