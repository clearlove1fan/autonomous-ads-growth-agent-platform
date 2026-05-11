from ads_growth_agent.config import Settings, get_settings
from ads_growth_agent.contracts import AdvertiserBrief, GrowthStrategyResponse
from ads_growth_agent.graph import StrategyGenerationError, run_growth_strategy_graph
from ads_growth_agent.knowledge import KnowledgeStore
from ads_growth_agent.knowledge_store_factory import build_configured_knowledge_store
from ads_growth_agent.tools import ToolRegistry

__all__ = ["StrategyGenerationError", "generate_growth_strategy", "generate_mock_growth_strategy"]


def generate_growth_strategy(
    brief: AdvertiserBrief,
    registry: ToolRegistry | None = None,
    *,
    settings: Settings | None = None,
    knowledge_store: KnowledgeStore | None = None,
) -> GrowthStrategyResponse:
    settings = settings or get_settings()
    return run_growth_strategy_graph(
        brief,
        registry,
        settings=settings,
        knowledge_store=knowledge_store or build_configured_knowledge_store(settings),
    )


def generate_mock_growth_strategy(
    brief: AdvertiserBrief,
    registry: ToolRegistry | None = None,
) -> GrowthStrategyResponse:
    return generate_growth_strategy(brief, registry)
