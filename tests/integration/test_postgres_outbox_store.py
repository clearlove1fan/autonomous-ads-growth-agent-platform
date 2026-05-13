import os
from uuid import uuid4

import pytest
import sqlalchemy as sa
from alembic import command
from alembic.config import Config
from sqlalchemy.engine import URL, make_url

from ads_growth_agent.persistence.outbox_store import PostgresOutboxStore

pytestmark = pytest.mark.integration

DEFAULT_TEST_DATABASE_URL = (
    "postgresql+psycopg://ads_growth:ads_growth@localhost:5432/ads_growth"
)


def test_postgres_outbox_claims_distinct_events_with_skip_locked(monkeypatch) -> None:
    base_url = _integration_database_url()
    test_url = _create_temporary_database(base_url)
    engine = sa.create_engine(test_url)

    try:
        monkeypatch.setenv("DATABASE_URL", test_url.render_as_string(hide_password=False))
        command.upgrade(Config("alembic.ini"), "head")

        store = PostgresOutboxStore(engine, tenant_id="tenant_outbox")
        first = store.enqueue(
            event_type="campaign_performance_analyzed",
            aggregate_type="campaign_performance_event",
            aggregate_id="evt_outbox_001",
            idempotency_key="idem_outbox_001",
            payload={"event_id": "evt_outbox_001"},
            partition_key="evt_outbox_001",
        )
        second = store.enqueue(
            event_type="campaign_performance_analyzed",
            aggregate_type="campaign_performance_event",
            aggregate_id="evt_outbox_002",
            idempotency_key="idem_outbox_002",
            payload={"event_id": "evt_outbox_002"},
            partition_key="evt_outbox_002",
        )

        worker_one = store.claim_pending(limit=1, worker_id="worker_one")
        worker_two = store.claim_pending(limit=1, worker_id="worker_two")

        claimed_ids = {
            worker_one[0].outbox_event_id,
            worker_two[0].outbox_event_id,
        }
        assert len(worker_one) == 1
        assert len(worker_two) == 1
        assert claimed_ids == {first.outbox_event_id, second.outbox_event_id}
        assert worker_one[0].status == "processing"
        assert worker_two[0].status == "processing"
    finally:
        engine.dispose()
        _drop_temporary_database(test_url)


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
