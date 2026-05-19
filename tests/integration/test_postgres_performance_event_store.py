import os
from uuid import uuid4

import pytest
import sqlalchemy as sa
from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient
from sqlalchemy.engine import URL, make_url

from ads_growth_agent.advertiser_memory_store_factory import (
    dispose_cached_advertiser_memory_store_engines,
)
from ads_growth_agent.api import app as api_app
from ads_growth_agent.api import get_runtime_settings
from ads_growth_agent.config import Settings
from ads_growth_agent.contracts import AdvertiserBrief, CampaignObjective
from ads_growth_agent.knowledge_store_factory import dispose_cached_knowledge_store_engines
from ads_growth_agent.outbox import process_configured_outbox
from ads_growth_agent.outbox_store_factory import dispose_cached_outbox_store_engines
from ads_growth_agent.performance_event_store_factory import (
    dispose_cached_performance_event_store_engines,
)
from ads_growth_agent.strategy import generate_growth_strategy

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
            advertiser_memory_persistence_backend="postgres",
            outbox_backend="postgres",
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
        assert response.headers["performance-event-status"] == "created"
        assert response.headers["advertiser-memory-status"] == "queued"
        assert payload["persisted"] is True
        assert payload["advertiser_memory_persisted"] is False
        assert payload["advertiser_memory_queued"] is True
        assert payload["advertiser_memory_status"] == "queued"
        assert payload["advertiser_memory_source_id"].startswith("memory:performance:")
        assert payload["analysis"]["health_status"] == "underperforming"

        worker_report = process_configured_outbox(
            settings,
            limit=10,
            worker_id="worker_perf_integration",
        )
        memory_list = client.get(
            "/advertisers/adv_fitness_001/memories",
            params={"memory_type": "historical_performance", "limit": "10"},
            headers={"X-Tenant-ID": "tenant_perf"},
        )
        memory_detail = client.get(
            f"/advertisers/adv_fitness_001/memories/{payload['advertiser_memory_source_id']}",
            headers={"X-Tenant-ID": "tenant_perf"},
        )
        memory_missing_from_other_tenant = client.get(
            f"/advertisers/adv_fitness_001/memories/{payload['advertiser_memory_source_id']}",
            headers={"X-Tenant-ID": "tenant_other"},
        )

        conflict_payload = _event_payload()
        conflict_payload["metrics"] = {
            **conflict_payload["metrics"],
            "spend": "900.00",
        }
        conflict = client.post(
            "/campaign-events/performance",
            json=conflict_payload,
            headers={"X-Tenant-ID": "tenant_perf"},
        )
        detail = client.get(
            "/campaign-events/performance/evt_perf_integration",
            headers={"X-Tenant-ID": "tenant_perf"},
        )
        missing_from_other_tenant = client.get(
            "/campaign-events/performance/evt_perf_integration",
            headers={"X-Tenant-ID": "tenant_other"},
        )
        replay = client.post(
            "/campaign-events/performance",
            json=_event_payload(),
            headers={"X-Tenant-ID": "tenant_perf"},
        )

        assert worker_report.claimed == 1
        assert worker_report.completed == 1
        assert worker_report.failed == 0
        assert replay.status_code == 200
        assert replay.headers["performance-event-status"] == "replayed"
        assert replay.headers["advertiser-memory-status"] == "recorded"
        assert replay.json()["analysis"]["feedback_id"] == payload["analysis"]["feedback_id"]
        assert memory_list.status_code == 200
        assert memory_list.json()["count"] == 1
        assert memory_list.json()["items"][0]["source_id"] == payload[
            "advertiser_memory_source_id"
        ]
        assert memory_detail.status_code == 200
        assert memory_detail.json()["source_id"] == payload["advertiser_memory_source_id"]
        assert memory_detail.json()["metadata"]["event_id"] == "evt_perf_integration"
        assert memory_missing_from_other_tenant.status_code == 404
        assert (
            memory_missing_from_other_tenant.json()["detail"]["error_code"]
            == "ADVERTISER_MEMORY_NOT_FOUND"
        )
        assert conflict.status_code == 409
        assert conflict.json()["detail"]["error_code"] == "PERFORMANCE_EVENT_ID_CONFLICT"
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
            memory = connection.execute(
                sa.text(
                    "SELECT memory_type, content, metadata, partition_key, partition_bucket "
                    "FROM advertiser_memories "
                    "WHERE tenant_id = :tenant_id "
                    "AND metadata ->> 'event_id' = :event_id"
                ),
                {"tenant_id": "tenant_perf", "event_id": "evt_perf_integration"},
            ).mappings().one()
            outbox_event = connection.execute(
                sa.text(
                    "SELECT event_type, status, attempt_count, result_json, partition_key, "
                    "partition_bucket FROM outbox_events "
                    "WHERE tenant_id = :tenant_id "
                    "AND aggregate_id = :event_id"
                ),
                {"tenant_id": "tenant_perf", "event_id": "evt_perf_integration"},
            ).mappings().one()

        assert event["tenant_id"] == "tenant_perf"
        assert event["advertiser_id"] == "adv_fitness_001"
        assert event["campaign_id"] == "cmp_fitness_001"
        assert event["objective"] == "registrations"
        assert event["event_type"] == "performance_snapshot"
        assert event["metrics_json"]["spend"] == "1000.00"
        assert event["analysis_json"]["health_status"] == "underperforming"
        assert event["status"] == "analyzed"
        assert event["metadata"]["target_cpa"] == "20.00"
        assert len(event["metadata"]["event_hash"]) == 64
        assert event["partition_key"] == "evt_perf_integration"
        assert 0 <= event["partition_bucket"] < 128
        assert other_tenant_count == 0
        assert memory["memory_type"] == "historical_performance"
        assert memory["metadata"]["source_id"] == payload["advertiser_memory_source_id"]
        assert memory["metadata"]["health_status"] == "underperforming"
        assert memory["metadata"]["objectives"] == ["registrations"]
        assert "observed CPA 50.00" in memory["content"]
        assert memory["partition_key"] == "adv_fitness_001"
        assert 0 <= memory["partition_bucket"] < 128
        assert outbox_event["event_type"] == "campaign_performance_analyzed"
        assert outbox_event["status"] == "completed"
        assert outbox_event["attempt_count"] == 1
        assert outbox_event["result_json"]["source_id"] == payload["advertiser_memory_source_id"]
        assert outbox_event["partition_key"] == "evt_perf_integration"
        assert 0 <= outbox_event["partition_bucket"] < 128

        strategy_settings = Settings(
            database_url=test_url.render_as_string(hide_password=False),
            knowledge_store_backend="postgres",
            tenant_id="tenant_perf",
        )
        strategy = generate_growth_strategy(_fitness_brief(), settings=strategy_settings)
        source_ids = {source.source_id for source in strategy.strategy.sources}
        assert payload["advertiser_memory_source_id"] in source_ids
    finally:
        api_app.dependency_overrides.clear()
        dispose_cached_advertiser_memory_store_engines()
        dispose_cached_knowledge_store_engines()
        dispose_cached_outbox_store_engines()
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


def _fitness_brief() -> AdvertiserBrief:
    return AdvertiserBrief(
        advertiser_id="adv_fitness_001",
        product_name="FitTrack Pro",
        product_category="fitness app",
        objective=CampaignObjective.REGISTRATIONS,
        budget="2000.00",
        currency="USD",
        duration_days=14,
        target_market="United States",
        primary_kpi="trial registrations",
        target_cpa="20.00",
        brand_voice="motivational and practical",
    )


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
