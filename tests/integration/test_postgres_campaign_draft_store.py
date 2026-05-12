import os
from decimal import Decimal
from uuid import uuid4

import pytest
import sqlalchemy as sa
from alembic import command
from alembic.config import Config
from sqlalchemy.engine import URL, make_url

from ads_growth_agent.campaign_draft_store_factory import (
    dispose_cached_campaign_draft_store_engines,
)
from ads_growth_agent.config import Settings
from ads_growth_agent.contracts import AdvertiserBrief, CampaignObjective
from ads_growth_agent.strategy import generate_growth_strategy

pytestmark = pytest.mark.integration

DEFAULT_TEST_DATABASE_URL = (
    "postgresql+psycopg://ads_growth:ads_growth@localhost:5432/ads_growth"
)


def test_strategy_generation_persists_campaign_draft(monkeypatch) -> None:
    base_url = _integration_database_url()
    test_url = _create_temporary_database(base_url)
    engine = sa.create_engine(test_url)

    try:
        monkeypatch.setenv("DATABASE_URL", test_url.render_as_string(hide_password=False))
        command.upgrade(Config("alembic.ini"), "head")

        settings = Settings(
            database_url=test_url.render_as_string(hide_password=False),
            campaign_draft_persistence_backend="postgres",
            tenant_id="default",
        )
        response = generate_growth_strategy(_fitness_brief(), settings=settings)
        response_again = generate_growth_strategy(_fitness_brief(), settings=settings)
        draft_id = _draft_id(response)

        assert response_again.run_metadata.run_id != response.run_metadata.run_id
        assert response_again.strategy.strategy_id == response.strategy.strategy_id

        with engine.connect() as connection:
            draft = connection.execute(
                sa.text(
                    "SELECT draft_id, advertiser_id, objective, status, budget, currency, "
                    "strategy_json, created_by_run_id, metadata, partition_bucket "
                    "FROM campaign_drafts WHERE draft_id = :draft_id"
                ),
                {"draft_id": draft_id},
            ).mappings().one()
            draft_count = connection.execute(
                sa.text("SELECT count(*) FROM campaign_drafts WHERE draft_id = :draft_id"),
                {"draft_id": draft_id},
            ).scalar_one()

        assert response_again.strategy.strategy_id == response.strategy.strategy_id
        assert draft_count == 1
        assert draft["advertiser_id"] == "adv_fitness_001"
        assert draft["objective"] == "registrations"
        assert draft["status"] == "draft"
        assert draft["budget"] == Decimal("2000.00")
        assert draft["currency"] == "USD"
        assert draft["strategy_json"]["strategy_id"] == response.strategy.strategy_id
        assert draft["created_by_run_id"] == response_again.run_metadata.run_id
        assert draft["metadata"]["execution_id"] == response_again.run_metadata.run_id
        assert draft["metadata"]["strategy_id"] == response.strategy.strategy_id
        assert draft["metadata"]["safety_note"].startswith("Draft only.")
        assert draft["metadata"]["draft_persistence"] == "postgres"
        assert len(draft["metadata"]["audience_segments"]) >= 1
        assert len(draft["metadata"]["creative_angles"]) >= 1
        assert 0 <= draft["partition_bucket"] < 128
    finally:
        dispose_cached_campaign_draft_store_engines()
        engine.dispose()
        _drop_temporary_database(test_url)


def _draft_id(response) -> str:
    for result in response.tool_results:
        if result.tool_name == "create_campaign_draft":
            return str(result.payload["draft_id"])
    raise AssertionError("create_campaign_draft result not found")


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
        brand_voice="motivational and practical",
        constraints=["Avoid unrealistic body transformation claims"],
        known_audiences=["Home workout beginners"],
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
