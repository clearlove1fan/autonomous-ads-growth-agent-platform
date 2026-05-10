from ads_growth_agent.contracts import AdvertiserBrief, GrowthStrategyResponse
from ads_growth_agent.graph import StrategyGenerationError, run_growth_strategy_graph
from ads_growth_agent.tools import ToolRegistry

__all__ = ["StrategyGenerationError", "generate_mock_growth_strategy"]


def generate_mock_growth_strategy(
    brief: AdvertiserBrief,
    registry: ToolRegistry | None = None,
) -> GrowthStrategyResponse:
    return run_growth_strategy_graph(brief, registry)
