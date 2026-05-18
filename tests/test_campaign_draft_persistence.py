import json
from datetime import UTC, datetime

import pytest
from fastapi.testclient import TestClient
from typer.testing import CliRunner

from ads_growth_agent import strategy as strategy_module
from ads_growth_agent.api import (
    app as api_app,
)
from ads_growth_agent.api import (
    get_runtime_campaign_draft_store,
    get_runtime_settings,
)
from ads_growth_agent.campaign_draft_store_factory import (
    build_configured_campaign_draft_store,
    dispose_cached_campaign_draft_store_engines,
)
from ads_growth_agent.cli import app as cli_app
from ads_growth_agent.config import Settings
from ads_growth_agent.contracts import (
    AdvertiserBrief,
    CampaignDraftDetailResponse,
    CampaignObjective,
    RunMetadata,
    ToolError,
    ToolResult,
)
from ads_growth_agent.graph import StrategyGenerationError
from ads_growth_agent.persistence.campaign_draft_store import (
    NoopCampaignDraftStore,
    PostgresCampaignDraftStore,
)


def test_campaign_draft_store_factory_defaults_to_noop_store() -> None:
    store = build_configured_campaign_draft_store(
        Settings(campaign_draft_persistence_backend="none")
    )

    assert isinstance(store, NoopCampaignDraftStore)
    assert store.get_draft("missing_draft") is None
    assert store.list_drafts(limit=10) == []


def test_campaign_draft_store_factory_builds_cached_postgres_store(monkeypatch) -> None:
    created: list[tuple[str, dict[str, object]]] = []

    class FakeEngine:
        def dispose(self) -> None:
            pass

    def fake_create_engine(database_url: str, **kwargs: object) -> FakeEngine:
        created.append((database_url, kwargs))
        return FakeEngine()

    monkeypatch.setattr(
        "ads_growth_agent.campaign_draft_store_factory.sa.create_engine",
        fake_create_engine,
    )
    dispose_cached_campaign_draft_store_engines()

    settings = Settings(
        database_url="postgresql+psycopg://ads_growth:ads_growth@localhost:5432/ads_growth",
        campaign_draft_persistence_backend="postgres",
        tenant_id="tenant_a",
    )
    first = build_configured_campaign_draft_store(settings)
    second = build_configured_campaign_draft_store(settings)

    assert isinstance(first, PostgresCampaignDraftStore)
    assert isinstance(second, PostgresCampaignDraftStore)
    assert created == [
        (
            "postgresql+psycopg://ads_growth:ads_growth@localhost:5432/ads_growth",
            {"pool_pre_ping": True},
        )
    ]

    dispose_cached_campaign_draft_store_engines()


def test_generate_growth_strategy_records_campaign_draft_with_injected_store() -> None:
    draft_store = CapturingCampaignDraftStore()

    response = strategy_module.generate_growth_strategy(
        _fitness_brief(),
        settings=Settings(campaign_draft_persistence_backend="none"),
        campaign_draft_store=draft_store,
    )

    assert draft_store.completed == [
        (_fitness_brief().advertiser_id, response.strategy.strategy_id)
    ]


def test_campaign_draft_api_returns_tenant_scoped_detail_and_list() -> None:
    draft = _draft_detail()
    store = CapturingCampaignDraftStore(drafts=[draft])

    api_app.dependency_overrides[get_runtime_settings] = lambda: Settings(
        campaign_draft_persistence_backend="postgres"
    )
    api_app.dependency_overrides[get_runtime_campaign_draft_store] = lambda: store
    try:
        detail = TestClient(api_app).get(
            f"/campaign-drafts/{draft.draft_id}",
            headers={"X-Tenant-ID": "tenant_drafts"},
        )
        listing = TestClient(api_app).get(
            "/campaign-drafts",
            params={"advertiser_id": draft.advertiser_id, "limit": "10"},
            headers={"X-Tenant-ID": "tenant_drafts"},
        )
    finally:
        api_app.dependency_overrides.clear()

    detail_payload = detail.json()
    list_payload = listing.json()
    assert detail.status_code == 200
    assert detail.headers["x-tenant-id"] == "tenant_drafts"
    assert detail_payload["draft_id"] == draft.draft_id
    assert detail_payload["status"] == "draft"
    assert detail_payload["strategy"]["strategy_id"] == draft.strategy.strategy_id
    assert listing.status_code == 200
    assert list_payload["count"] == 1
    assert list_payload["limit"] == 10
    assert list_payload["advertiser_id"] == draft.advertiser_id
    assert list_payload["items"][0]["draft_id"] == draft.draft_id


def test_campaign_draft_api_returns_404_when_missing() -> None:
    api_app.dependency_overrides[get_runtime_campaign_draft_store] = (
        lambda: CapturingCampaignDraftStore()
    )
    try:
        response = TestClient(api_app).get("/campaign-drafts/missing_draft")
    finally:
        api_app.dependency_overrides.clear()

    assert response.status_code == 404
    assert response.json()["detail"]["error_code"] == "CAMPAIGN_DRAFT_NOT_FOUND"


def test_get_campaign_draft_cli_returns_detail(monkeypatch) -> None:
    draft = _draft_detail()
    store = CapturingCampaignDraftStore(drafts=[draft])

    monkeypatch.setattr(
        "ads_growth_agent.cli.get_settings",
        lambda: Settings(tenant_id="tenant_cli"),
    )
    monkeypatch.setattr(
        "ads_growth_agent.cli.build_configured_campaign_draft_store",
        lambda settings: store,
    )

    result = CliRunner().invoke(cli_app, ["get-campaign-draft", draft.draft_id])

    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["draft_id"] == draft.draft_id
    assert payload["advertiser_id"] == draft.advertiser_id
    assert payload["strategy"]["strategy_id"] == draft.strategy.strategy_id


def test_get_campaign_draft_cli_reports_missing_draft(monkeypatch) -> None:
    monkeypatch.setattr(
        "ads_growth_agent.cli.get_settings",
        lambda: Settings(tenant_id="tenant_cli"),
    )
    monkeypatch.setattr(
        "ads_growth_agent.cli.build_configured_campaign_draft_store",
        lambda settings: CapturingCampaignDraftStore(),
    )

    result = CliRunner().invoke(cli_app, ["get-campaign-draft", "missing_draft"])

    assert result.exit_code == 1
    assert "Campaign draft not found: missing_draft" in result.stderr


def test_list_campaign_drafts_cli_filters_by_advertiser(monkeypatch) -> None:
    draft = _draft_detail()
    store = CapturingCampaignDraftStore(drafts=[draft])

    monkeypatch.setattr(
        "ads_growth_agent.cli.get_settings",
        lambda: Settings(tenant_id="tenant_cli"),
    )
    monkeypatch.setattr(
        "ads_growth_agent.cli.build_configured_campaign_draft_store",
        lambda settings: store,
    )

    result = CliRunner().invoke(
        cli_app,
        [
            "list-campaign-drafts",
            "--advertiser-id",
            draft.advertiser_id,
            "--limit",
            "5",
        ],
    )

    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["count"] == 1
    assert payload["limit"] == 5
    assert payload["advertiser_id"] == draft.advertiser_id
    assert payload["items"][0]["draft_id"] == draft.draft_id


def test_generate_growth_strategy_does_not_record_campaign_draft_on_failure(
    monkeypatch,
) -> None:
    draft_store = CapturingCampaignDraftStore()
    failure_result = ToolResult(
        tool_name="llm_planner",
        success=False,
        payload={},
        error=ToolError(code="PLANNER_FAILED", message="planner failed", retryable=False),
        latency_ms=0,
    )
    run_metadata = RunMetadata(
        run_id="strategy_failure",
        trace_id="trace_failure",
        langsmith_project="test",
        tracing_enabled=False,
        node_path=["planner"],
        tool_count=1,
        failed_tool_count=1,
        error_summary=["planner failed"],
    )

    def fake_run_growth_strategy_graph(*args: object, **kwargs: object):
        raise StrategyGenerationError(
            "planner failed",
            failure_result,
            run_metadata=run_metadata,
            tool_results=[failure_result],
            node_path=["planner"],
        )

    monkeypatch.setattr(
        strategy_module,
        "run_growth_strategy_graph",
        fake_run_growth_strategy_graph,
    )

    with pytest.raises(StrategyGenerationError):
        strategy_module.generate_growth_strategy(
            _fitness_brief(),
            settings=Settings(campaign_draft_persistence_backend="none"),
            campaign_draft_store=draft_store,
        )

    assert draft_store.completed == []


class CapturingCampaignDraftStore:
    def __init__(self, drafts: list[CampaignDraftDetailResponse] | None = None) -> None:
        self.completed: list[tuple[str, str]] = []
        self.drafts = {draft.draft_id: draft for draft in drafts or []}

    def record_completed(self, brief, response) -> None:
        self.completed.append((brief.advertiser_id, response.strategy.strategy_id))

    def get_draft(self, draft_id: str) -> CampaignDraftDetailResponse | None:
        return self.drafts.get(draft_id)

    def list_drafts(
        self,
        *,
        advertiser_id: str | None = None,
        limit: int = 50,
    ) -> list[CampaignDraftDetailResponse]:
        drafts = [
            draft
            for draft in self.drafts.values()
            if advertiser_id is None or draft.advertiser_id == advertiser_id
        ]
        return drafts[:limit]


def _draft_detail() -> CampaignDraftDetailResponse:
    response = strategy_module.generate_growth_strategy(
        _fitness_brief(),
        settings=Settings(campaign_draft_persistence_backend="none"),
    )
    draft = response.strategy.campaign_draft
    now = datetime.now(UTC)
    return CampaignDraftDetailResponse(
        draft_id=draft.draft_id,
        advertiser_id=response.strategy.advertiser_id,
        objective=response.strategy.objective,
        status=draft.status,
        budget=draft.total_budget,
        currency=draft.currency,
        campaign_name=draft.campaign_name,
        daily_budget=draft.daily_budget,
        safety_note=draft.safety_note,
        created_by_run_id=response.run_metadata.run_id,
        strategy=response.strategy,
        metadata={
            "campaign_name": draft.campaign_name,
            "daily_budget": str(draft.daily_budget),
            "safety_note": draft.safety_note,
            "strategy_id": response.strategy.strategy_id,
        },
        created_at=now,
        updated_at=now,
    )


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
