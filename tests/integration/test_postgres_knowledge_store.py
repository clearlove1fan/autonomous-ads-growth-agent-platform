import os
from uuid import uuid4

import pytest
import sqlalchemy as sa
from alembic import command
from alembic.config import Config
from sqlalchemy.engine import URL, make_url

from ads_growth_agent.config import Settings
from ads_growth_agent.contracts import AdvertiserBrief, CampaignObjective
from ads_growth_agent.knowledge import build_knowledge_query
from ads_growth_agent.knowledge_store_factory import dispose_cached_knowledge_store_engines
from ads_growth_agent.outbox import process_configured_outbox
from ads_growth_agent.outbox_store_factory import dispose_cached_outbox_store_engines
from ads_growth_agent.persistence.knowledge_seed import seed_default_knowledge
from ads_growth_agent.persistence.knowledge_store import PostgresKnowledgeStore
from ads_growth_agent.strategy import generate_growth_strategy

pytestmark = pytest.mark.integration

DEFAULT_TEST_DATABASE_URL = (
    "postgresql+psycopg://ads_growth:ads_growth@localhost:5432/ads_growth"
)


def test_postgres_knowledge_store_retrieves_seeded_sources_and_records_event(
    monkeypatch,
) -> None:
    base_url = _integration_database_url()
    test_url = _create_temporary_database(base_url)
    engine = sa.create_engine(test_url)

    try:
        monkeypatch.setenv("DATABASE_URL", test_url.render_as_string(hide_password=False))
        command.upgrade(Config("alembic.ini"), "head")

        seed_default_knowledge(engine)
        seed_default_knowledge(engine)

        query = build_knowledge_query(_fitness_brief(), top_k=3, run_id="strategy_test_run_001")
        result = PostgresKnowledgeStore(engine).retrieve(query)

        source_types = {item.source_type for item in result.results}
        source_ids = {item.source_id for item in result.results}

        assert result.query.run_id == "strategy_test_run_001"
        assert len(result.results) == 3
        assert source_types == {"advertiser_memory", "historical_case", "rag_document"}
        assert "memory:adv_fitness_001:profile:v1" in source_ids
        assert result.results[0].relevance >= result.results[-1].relevance

        with engine.connect() as connection:
            event = connection.execute(
                sa.text(
                    "SELECT run_id, advertiser_id, top_k, results, partition_bucket "
                    "FROM retrieval_events WHERE run_id = :run_id"
                ),
                {"run_id": "strategy_test_run_001"},
            ).mappings().one()
            memory_count = connection.execute(
                sa.text(
                    "SELECT count(*) FROM advertiser_memories "
                    "WHERE metadata ->> 'source_id' = :source_id"
                ),
                {"source_id": "memory:adv_fitness_001:profile:v1"},
            ).scalar_one()

        assert event["advertiser_id"] == "adv_fitness_001"
        assert event["top_k"] == 3
        assert len(event["results"]) == 3
        assert 0 <= event["partition_bucket"] < 128
        assert memory_count == 1
    finally:
        engine.dispose()
        _drop_temporary_database(test_url)


def test_postgres_knowledge_store_filters_low_relevance_lexical_noise(
    monkeypatch,
) -> None:
    base_url = _integration_database_url()
    test_url = _create_temporary_database(base_url)
    engine = sa.create_engine(test_url)

    try:
        monkeypatch.setenv("DATABASE_URL", test_url.render_as_string(hide_password=False))
        command.upgrade(Config("alembic.ini"), "head")
        seed_default_knowledge(engine)

        query = build_knowledge_query(
            _skincare_brief(),
            top_k=3,
            run_id="strategy_test_run_skincare",
        )
        result = PostgresKnowledgeStore(engine).retrieve(query)

        assert [item.source_id for item in result.results] == [
            "rag:playbook:purchase_growth:v1"
        ]
        assert all(item.relevance >= query.min_relevance for item in result.results)

        with engine.connect() as connection:
            event = connection.execute(
                sa.text(
                    "SELECT filters, results "
                    "FROM retrieval_events WHERE run_id = :run_id"
                ),
                {"run_id": "strategy_test_run_skincare"},
            ).mappings().one()

        assert event["filters"]["min_relevance"] == 0.3
        assert [item["source_id"] for item in event["results"]] == [
            "rag:playbook:purchase_growth:v1"
        ]
    finally:
        engine.dispose()
        _drop_temporary_database(test_url)


def test_strategy_generation_can_use_postgres_knowledge_backend(monkeypatch) -> None:
    base_url = _integration_database_url()
    test_url = _create_temporary_database(base_url)
    engine = sa.create_engine(test_url)

    try:
        monkeypatch.setenv("DATABASE_URL", test_url.render_as_string(hide_password=False))
        command.upgrade(Config("alembic.ini"), "head")
        seed_default_knowledge(engine)

        settings = Settings(
            database_url=test_url.render_as_string(hide_password=False),
            knowledge_store_backend="postgres",
            advertiser_memory_persistence_backend="postgres",
            outbox_backend="postgres",
            memory_usage_tracking_backend="outbox",
            tenant_id="default",
        )
        response = generate_growth_strategy(_fitness_brief(), settings=settings)
        worker_report = process_configured_outbox(
            settings,
            limit=10,
            worker_id="worker_memory_usage",
        )

        source_types = {source.source_type for source in response.strategy.sources}
        assert response.node_path == [
            "planner",
            "retriever",
            "tool_executor",
            "critic",
            "finalizer",
        ]
        assert {"advertiser_memory", "historical_case", "rag_document"}.issubset(source_types)

        with engine.connect() as connection:
            event_count = connection.execute(
                sa.text("SELECT count(*) FROM retrieval_events WHERE run_id = :run_id"),
                {"run_id": response.run_metadata.run_id},
            ).scalar_one()
            outbox_count = connection.execute(
                sa.text(
                    "SELECT count(*) FROM outbox_events "
                    "WHERE event_type = 'advertiser_memory_retrieved' "
                    "AND status = 'completed'"
                )
            ).scalar_one()
            memory_usage = connection.execute(
                sa.text(
                    "SELECT usage_count, last_used_at FROM advertiser_memories "
                    "WHERE metadata ->> 'source_id' = :source_id"
                ),
                {"source_id": "memory:adv_fitness_001:profile:v1"},
            ).mappings().one()

        assert event_count == 1
        assert worker_report.claimed == 1
        assert worker_report.completed == 1
        assert outbox_count == 1
        assert memory_usage["usage_count"] == 1
        assert memory_usage["last_used_at"] is not None
    finally:
        dispose_cached_knowledge_store_engines()
        dispose_cached_outbox_store_engines()
        engine.dispose()
        _drop_temporary_database(test_url)


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


def _skincare_brief() -> AdvertiserBrief:
    return AdvertiserBrief(
        advertiser_id="adv_skincare_002",
        product_name="GlowLab Barrier Serum",
        product_category="skincare",
        objective=CampaignObjective.PURCHASES,
        budget="3500.00",
        currency="USD",
        duration_days=21,
        target_market="United States",
        primary_kpi="first purchases",
        target_cpa="35.00",
        brand_voice="clinical and trustworthy",
        constraints=["Avoid medical cure claims"],
        known_audiences=["Sensitive skin shoppers"],
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
