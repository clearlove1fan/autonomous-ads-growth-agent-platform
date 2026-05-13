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
from ads_growth_agent.contracts import GrowthStrategyRequest
from ads_growth_agent.persistence.strategy_job_store import PostgresStrategyJobStore
from ads_growth_agent.strategy_job_store_factory import (
    dispose_cached_strategy_job_store_engines,
)

pytestmark = pytest.mark.integration

DEFAULT_TEST_DATABASE_URL = (
    "postgresql+psycopg://ads_growth:ads_growth@localhost:5432/ads_growth"
)


def test_strategy_job_api_persists_completed_job_in_postgres(monkeypatch) -> None:
    base_url = _integration_database_url()
    test_url = _create_temporary_database(base_url)

    try:
        monkeypatch.setenv("DATABASE_URL", test_url.render_as_string(hide_password=False))
        command.upgrade(Config("alembic.ini"), "head")
        settings = Settings(
            database_url=test_url.render_as_string(hide_password=False),
            strategy_job_backend="postgres",
            tenant_id="tenant_jobs",
        )
        api_app.dependency_overrides[get_runtime_settings] = lambda: settings
        try:
            client = TestClient(api_app)
            accepted = client.post(
                "/growth-strategies/jobs",
                json={"brief": _brief_payload()},
            )
            detail = client.get(accepted.json()["polling_url"])
        finally:
            api_app.dependency_overrides.clear()

        accepted_payload = accepted.json()
        detail_payload = detail.json()
        assert accepted.status_code == 202
        assert accepted.headers["x-tenant-id"] == "tenant_jobs"
        assert detail.status_code == 200
        assert detail_payload["status"] == "completed"
        assert detail_payload["job_id"] == accepted_payload["job_id"]
        assert detail_payload["run_id"] == accepted_payload["run_id"]
        assert detail_payload["result"]["strategy"]["advertiser_id"] == "adv_fitness_001"

        engine = sa.create_engine(test_url)
        try:
            with engine.connect() as connection:
                row = connection.execute(
                    sa.text(
                        "SELECT tenant_id, job_id, status, run_id, response_json, "
                        "attempt_count, locked_by, locked_until, partition_bucket "
                        "FROM strategy_jobs WHERE job_id = :job_id"
                    ),
                    {"job_id": accepted_payload["job_id"]},
                ).mappings().one()
        finally:
            engine.dispose()

        assert row["tenant_id"] == "tenant_jobs"
        assert row["status"] == "completed"
        assert row["run_id"] == accepted_payload["run_id"]
        assert row["response_json"]["strategy"]["advertiser_id"] == "adv_fitness_001"
        assert row["attempt_count"] == 1
        assert row["locked_by"] is None
        assert row["locked_until"] is None
        assert 0 <= row["partition_bucket"] < 128
    finally:
        dispose_cached_strategy_job_store_engines()
        _drop_temporary_database(test_url)


def test_postgres_strategy_jobs_claim_distinct_jobs_with_skip_locked(monkeypatch) -> None:
    base_url = _integration_database_url()
    test_url = _create_temporary_database(base_url)
    engine = sa.create_engine(test_url)

    try:
        monkeypatch.setenv("DATABASE_URL", test_url.render_as_string(hide_password=False))
        command.upgrade(Config("alembic.ini"), "head")

        store = PostgresStrategyJobStore(engine, tenant_id="tenant_jobs")
        request = GrowthStrategyRequest.model_validate({"brief": _brief_payload()})
        first = store.create_queued(
            request,
            job_id="job_claim_001",
            strategy_id="strategy_claim_001",
            run_id="run_claim_001",
            trace_id="trace_claim_001",
        )
        second = store.create_queued(
            request,
            job_id="job_claim_002",
            strategy_id="strategy_claim_002",
            run_id="run_claim_002",
            trace_id="trace_claim_002",
        )

        worker_one = store.claim_queued(limit=1, worker_id="worker_one")
        worker_two = store.claim_queued(limit=1, worker_id="worker_two")

        claimed_ids = {worker_one[0].job_id, worker_two[0].job_id}
        assert claimed_ids == {first.job_id, second.job_id}
        assert worker_one[0].status == "running"
        assert worker_two[0].status == "running"
        assert {worker_one[0].locked_by, worker_two[0].locked_by} == {
            "worker_one",
            "worker_two",
        }
        assert worker_one[0].attempt_count == 1
        assert worker_two[0].attempt_count == 1
    finally:
        engine.dispose()
        _drop_temporary_database(test_url)


def _brief_payload() -> dict:
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
