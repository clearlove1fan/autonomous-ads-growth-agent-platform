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
from ads_growth_agent.contracts import AdvertiserBrief, CampaignObjective, ToolError, ToolResult
from ads_growth_agent.observability import build_run_metadata, create_run_context
from ads_growth_agent.persistence.run_store import PostgresAgentRunStore
from ads_growth_agent.run_store_factory import dispose_cached_run_store_engines
from ads_growth_agent.strategy import generate_growth_strategy

pytestmark = pytest.mark.integration

DEFAULT_TEST_DATABASE_URL = (
    "postgresql+psycopg://ads_growth:ads_growth@localhost:5432/ads_growth"
)


def test_strategy_generation_persists_agent_run_and_steps(monkeypatch) -> None:
    base_url = _integration_database_url()
    test_url = _create_temporary_database(base_url)
    engine = sa.create_engine(test_url)

    try:
        monkeypatch.setenv("DATABASE_URL", test_url.render_as_string(hide_password=False))
        command.upgrade(Config("alembic.ini"), "head")

        settings = Settings(
            database_url=test_url.render_as_string(hide_password=False),
            run_persistence_backend="postgres",
            tenant_id="default",
        )
        lifecycle_context = create_run_context(
            strategy_id="strategy_lifecycle",
            settings=settings,
        )
        PostgresAgentRunStore(engine, tenant_id="default").record_started(
            _fitness_brief(),
            build_run_metadata(lifecycle_context, node_path=[], tool_results=[]),
        )
        response = generate_growth_strategy(_fitness_brief(), settings=settings)
        response_again = generate_growth_strategy(_fitness_brief(), settings=settings)

        assert response_again.run_metadata.run_id != response.run_metadata.run_id
        assert response_again.strategy.strategy_id == response.strategy.strategy_id
        assert response_again.run_metadata.strategy_id == response.strategy.strategy_id

        with engine.connect() as connection:
            run = connection.execute(
                sa.text(
                    "SELECT run_id, strategy_id, advertiser_id, status, trace_id, node_path, "
                    "final_strategy_json, error_summary, partition_bucket "
                    "FROM agent_runs WHERE run_id = :run_id"
                ),
                {"run_id": response_again.run_metadata.run_id},
            ).mappings().one()
            steps = list(
                connection.execute(
                    sa.text(
                        "SELECT step_index, node_name, status, input_json, output_json, error_json "
                        "FROM agent_run_steps WHERE run_id = :run_id "
                        "ORDER BY step_index ASC"
                    ),
                    {"run_id": response_again.run_metadata.run_id},
                ).mappings()
            )
            run_count = connection.execute(
                sa.text("SELECT count(*) FROM agent_runs WHERE run_id = :run_id"),
                {"run_id": response_again.run_metadata.run_id},
            ).scalar_one()
            strategy_run_count = connection.execute(
                sa.text("SELECT count(*) FROM agent_runs WHERE strategy_id = :strategy_id"),
                {"strategy_id": response.strategy.strategy_id},
            ).scalar_one()
            started_run = connection.execute(
                sa.text(
                    "SELECT run_id, strategy_id, status, completed_at, final_strategy_json "
                    "FROM agent_runs WHERE run_id = :run_id"
                ),
                {"run_id": lifecycle_context.run_id},
            ).mappings().one()

        assert started_run["run_id"] == lifecycle_context.run_id
        assert started_run["strategy_id"] == "strategy_lifecycle"
        assert started_run["status"] == "running"
        assert started_run["completed_at"] is None
        assert started_run["final_strategy_json"] is None
        assert run_count == 1
        assert strategy_run_count == 2
        assert run["run_id"] == response_again.run_metadata.run_id
        assert run["strategy_id"] == response.strategy.strategy_id
        assert run["advertiser_id"] == "adv_fitness_001"
        assert run["status"] == "completed"
        assert run["trace_id"] == response_again.run_metadata.trace_id
        assert run["node_path"] == response_again.node_path
        assert run["final_strategy_json"]["strategy_id"] == response.strategy.strategy_id
        assert run["error_summary"] == []
        assert 0 <= run["partition_bucket"] < 128

        assert [step["node_name"] for step in steps] == response.node_path
        assert [step["step_index"] for step in steps] == list(range(len(response.node_path)))
        assert {step["status"] for step in steps} == {"completed"}
        assert all(step["error_json"] is None for step in steps)
        assert steps[0]["input_json"]["execution_id"] == response_again.run_metadata.run_id
        assert steps[0]["input_json"]["strategy_id"] == response.strategy.strategy_id
        assert steps[-1]["output_json"]["strategy_id"] == response.strategy.strategy_id

        api_app.dependency_overrides[get_runtime_settings] = lambda: settings
        client = TestClient(api_app)
        detail = client.get(f"/runs/{response_again.run_metadata.run_id}")
        missing_from_other_tenant = client.get(
            f"/runs/{response_again.run_metadata.run_id}",
            headers={"X-Tenant-ID": "tenant_other"},
        )

        assert detail.status_code == 200
        assert detail.headers["x-tenant-id"] == "default"
        detail_payload = detail.json()
        assert detail_payload["run_id"] == response_again.run_metadata.run_id
        assert detail_payload["execution_id"] == response_again.run_metadata.run_id
        assert detail_payload["strategy_id"] == response.strategy.strategy_id
        assert detail_payload["status"] == "completed"
        assert detail_payload["final_strategy"]["strategy_id"] == response.strategy.strategy_id
        assert [step["node_name"] for step in detail_payload["steps"]] == response.node_path
        assert missing_from_other_tenant.status_code == 404
        assert missing_from_other_tenant.json()["detail"]["error_code"] == "RUN_NOT_FOUND"

        failure_result = ToolResult(
            tool_name="llm_planner",
            success=False,
            payload={},
            error=ToolError(code="PLANNER_FAILED", message="planner failed", retryable=False),
            latency_ms=0,
        )
        failed_context = create_run_context(
            strategy_id=response.strategy.strategy_id,
            settings=settings,
        )
        PostgresAgentRunStore(engine, tenant_id="default").record_failed(
            _fitness_brief(),
            build_run_metadata(
                failed_context,
                node_path=["planner"],
                tool_results=[failure_result],
                error_summary=["planner failed"],
            ),
            tool_results=[failure_result],
            error_message="planner failed",
        )
        retry = client.post(
            f"/runs/{failed_context.run_id}/retry",
            json={"brief": _brief_payload()},
        )
        retry_payload = retry.json()

        assert retry.status_code == 200
        assert retry.headers["retried-run-id"] == failed_context.run_id
        assert retry_payload["run_metadata"]["run_id"] != failed_context.run_id
        assert retry_payload["run_metadata"]["strategy_id"] == response.strategy.strategy_id

        with engine.connect() as connection:
            retry_run_status = connection.execute(
                sa.text("SELECT status FROM agent_runs WHERE run_id = :run_id"),
                {"run_id": retry_payload["run_metadata"]["run_id"]},
            ).scalar_one()

        assert retry_run_status == "completed"
    finally:
        api_app.dependency_overrides.clear()
        dispose_cached_run_store_engines()
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
        "brand_voice": "motivational and practical",
        "constraints": ["Avoid unrealistic body transformation claims"],
        "known_audiences": ["Home workout beginners"],
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
