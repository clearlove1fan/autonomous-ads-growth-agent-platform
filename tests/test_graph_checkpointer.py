from langgraph.checkpoint.memory import MemorySaver

from ads_growth_agent.config import Settings
from ads_growth_agent.contracts import AdvertiserBrief, CampaignObjective
from ads_growth_agent.graph_checkpointer import (
    graph_checkpoint_config,
    open_configured_graph_checkpointer,
    psycopg_connection_string,
)
from ads_growth_agent.observability import RunContext
from ads_growth_agent.strategy import generate_growth_strategy


def test_psycopg_connection_string_strips_sqlalchemy_driver_name() -> None:
    conn_string = psycopg_connection_string(
        "postgresql+psycopg://ads_growth:secret@localhost:5432/ads_growth"
    )

    assert conn_string == "postgresql://ads_growth:secret@localhost:5432/ads_growth"


def test_graph_checkpoint_config_uses_run_id_as_thread_id() -> None:
    config = graph_checkpoint_config(
        RunContext(
            run_id="strategy_123",
            trace_id="trace_123",
            langsmith_project="test",
            tracing_enabled=False,
        ),
        enabled=True,
    )

    assert config == {"configurable": {"thread_id": "strategy_123"}}
    assert graph_checkpoint_config(
        RunContext(
            run_id="strategy_123",
            trace_id="trace_123",
            langsmith_project="test",
            tracing_enabled=False,
        ),
        enabled=False,
    ) is None


def test_open_configured_graph_checkpointer_returns_none_for_default_backend() -> None:
    with open_configured_graph_checkpointer(Settings(graph_checkpointer_backend="none")) as saver:
        assert saver is None


def test_open_configured_graph_checkpointer_returns_memory_saver() -> None:
    with open_configured_graph_checkpointer(
        Settings(graph_checkpointer_backend="memory")
    ) as saver:
        assert isinstance(saver, MemorySaver)


def test_strategy_generation_runs_with_memory_checkpointer() -> None:
    response = generate_growth_strategy(
        _fitness_brief(),
        settings=Settings(graph_checkpointer_backend="memory"),
    )

    assert response.node_path == ["planner", "retriever", "tool_executor", "critic", "finalizer"]
    assert response.run_metadata.run_id == response.strategy.strategy_id


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
