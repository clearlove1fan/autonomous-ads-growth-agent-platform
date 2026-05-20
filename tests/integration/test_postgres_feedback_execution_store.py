import os
from uuid import uuid4

import pytest
import sqlalchemy as sa
from alembic import command
from alembic.config import Config
from sqlalchemy.engine import URL, make_url

from ads_growth_agent.contracts import (
    CampaignFeedbackOptimizationReviewRequest,
    CampaignObjective,
    CampaignPerformanceEventRequest,
    FeedbackOptimizationReviewDecision,
    PerformanceMetrics,
)
from ads_growth_agent.feedback import (
    analyze_campaign_performance_event,
    build_campaign_feedback_optimization_draft,
)
from ads_growth_agent.feedback_execution_dry_run import dry_run_feedback_execution_plan
from ads_growth_agent.feedback_execution_plan import build_feedback_execution_plan
from ads_growth_agent.feedback_execution_store_factory import (
    dispose_cached_feedback_execution_store_engines,
)
from ads_growth_agent.feedback_review_store_factory import (
    dispose_cached_feedback_review_store_engines,
)
from ads_growth_agent.performance_event_store_factory import (
    dispose_cached_performance_event_store_engines,
)
from ads_growth_agent.persistence.feedback_execution_store import (
    PostgresFeedbackExecutionDryRunStore,
)
from ads_growth_agent.persistence.feedback_review_store import (
    PostgresFeedbackOptimizationReviewStore,
)
from ads_growth_agent.persistence.performance_event_store import (
    PostgresCampaignPerformanceEventStore,
)

pytestmark = pytest.mark.integration

DEFAULT_TEST_DATABASE_URL = (
    "postgresql+psycopg://ads_growth:ads_growth@localhost:5432/ads_growth"
)


def test_feedback_execution_dry_run_store_persists_and_lists(monkeypatch) -> None:
    base_url = _integration_database_url()
    test_url = _create_temporary_database(base_url)
    engine = sa.create_engine(test_url)

    try:
        monkeypatch.setenv("DATABASE_URL", test_url.render_as_string(hide_password=False))
        command.upgrade(Config("alembic.ini"), "head")

        tenant_id = "tenant_feedback_execution"
        event_store = PostgresCampaignPerformanceEventStore(engine, tenant_id=tenant_id)
        review_store = PostgresFeedbackOptimizationReviewStore(engine, tenant_id=tenant_id)
        execution_store = PostgresFeedbackExecutionDryRunStore(engine, tenant_id=tenant_id)
        event = _event()
        analysis = analyze_campaign_performance_event(event)
        event_store.record_analyzed(event, analysis)
        persisted_event = event_store.get_event(event.event_id)
        assert persisted_event is not None
        optimization_draft = build_campaign_feedback_optimization_draft(persisted_event)
        review = review_store.record_review(
            optimization_draft,
            CampaignFeedbackOptimizationReviewRequest(
                decision=FeedbackOptimizationReviewDecision.APPROVED,
                reviewer_id="operator_feedback_execution",
                selected_change_ids=[optimization_draft.changes[0].change_id],
            ),
        )
        execution_plan = build_feedback_execution_plan(review)
        dry_run = dry_run_feedback_execution_plan(execution_plan)

        execution_store.record_dry_run(execution_plan, dry_run)
        execution_store.record_dry_run(execution_plan, dry_run)

        detail = execution_store.get_dry_run(dry_run.dry_run_id)
        listing = execution_store.list_dry_runs(
            review_id=review.review_id,
            status="passed",
            limit=10,
        )
        other_tenant_detail = PostgresFeedbackExecutionDryRunStore(
            engine,
            tenant_id="tenant_other",
        ).get_dry_run(dry_run.dry_run_id)

        with engine.connect() as connection:
            row = connection.execute(
                sa.text(
                    "SELECT tenant_id, dry_run_id, execution_plan_id, review_id, "
                    "event_id, advertiser_id, status, execution_mode, "
                    "validated_step_count, blocked_step_count, execution_plan_snapshot, "
                    "dry_run_snapshot, metadata, partition_key, partition_bucket "
                    "FROM feedback_execution_dry_runs WHERE dry_run_id = :dry_run_id"
                ),
                {"dry_run_id": dry_run.dry_run_id},
            ).mappings().one()
            row_count = connection.execute(
                sa.text(
                    "SELECT count(*) FROM feedback_execution_dry_runs "
                    "WHERE dry_run_id = :dry_run_id"
                ),
                {"dry_run_id": dry_run.dry_run_id},
            ).scalar_one()

        assert row_count == 1
        assert detail is not None
        assert detail.dry_run_id == dry_run.dry_run_id
        assert detail.status == "passed"
        assert listing.count == 1
        assert listing.items[0].dry_run_id == dry_run.dry_run_id
        assert other_tenant_detail is None
        assert row["tenant_id"] == tenant_id
        assert row["execution_plan_id"] == execution_plan.execution_plan_id
        assert row["review_id"] == review.review_id
        assert row["event_id"] == event.event_id
        assert row["advertiser_id"] == event.advertiser_id
        assert row["status"] == "passed"
        assert row["execution_mode"] == "dry_run"
        assert row["validated_step_count"] == 1
        assert row["blocked_step_count"] == 0
        assert row["execution_plan_snapshot"]["review_id"] == review.review_id
        assert row["dry_run_snapshot"]["dry_run_id"] == dry_run.dry_run_id
        assert row["metadata"]["feedback_execution_persistence"] == "postgres"
        assert row["partition_key"] == event.event_id
        assert 0 <= row["partition_bucket"] < 128
    finally:
        dispose_cached_feedback_execution_store_engines()
        dispose_cached_feedback_review_store_engines()
        dispose_cached_performance_event_store_engines()
        engine.dispose()
        _drop_temporary_database(test_url)


def _event() -> CampaignPerformanceEventRequest:
    return CampaignPerformanceEventRequest(
        event_id="evt_feedback_execution_integration",
        advertiser_id="adv_fitness_001",
        campaign_id="cmp_fitness_001",
        draft_id="draft_fitness_001",
        objective=CampaignObjective.REGISTRATIONS,
        event_type="performance_snapshot",
        occurred_at="2026-05-12T12:00:00Z",
        metrics=PerformanceMetrics(
            impressions=10_000,
            clicks=500,
            spend="1000.00",
            conversions=20,
        ),
        target_cpa="20.00",
        attribution_window_days=7,
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
