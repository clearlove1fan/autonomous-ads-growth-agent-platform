import pytest

from ads_growth_agent import strategy as strategy_module
from ads_growth_agent.campaign_draft_store_factory import (
    build_configured_campaign_draft_store,
    dispose_cached_campaign_draft_store_engines,
)
from ads_growth_agent.config import Settings
from ads_growth_agent.contracts import (
    AdvertiserBrief,
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
    def __init__(self) -> None:
        self.completed: list[tuple[str, str]] = []

    def record_completed(self, brief, response) -> None:
        self.completed.append((brief.advertiser_id, response.strategy.strategy_id))


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
