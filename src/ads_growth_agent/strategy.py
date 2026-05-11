from ads_growth_agent.campaign_draft_store_factory import (
    build_configured_campaign_draft_store,
)
from ads_growth_agent.config import Settings, get_settings
from ads_growth_agent.contracts import AdvertiserBrief, GrowthStrategyResponse
from ads_growth_agent.graph import StrategyGenerationError, run_growth_strategy_graph
from ads_growth_agent.knowledge import KnowledgeStore
from ads_growth_agent.knowledge_store_factory import build_configured_knowledge_store
from ads_growth_agent.persistence.campaign_draft_store import CampaignDraftStore
from ads_growth_agent.persistence.run_store import AgentRunStore
from ads_growth_agent.run_store_factory import build_configured_run_store
from ads_growth_agent.tools import ToolRegistry

__all__ = ["StrategyGenerationError", "generate_growth_strategy", "generate_mock_growth_strategy"]


def generate_growth_strategy(
    brief: AdvertiserBrief,
    registry: ToolRegistry | None = None,
    *,
    settings: Settings | None = None,
    knowledge_store: KnowledgeStore | None = None,
    run_store: AgentRunStore | None = None,
    campaign_draft_store: CampaignDraftStore | None = None,
) -> GrowthStrategyResponse:
    settings = settings or get_settings()
    run_store = run_store or build_configured_run_store(settings)
    campaign_draft_store = campaign_draft_store or build_configured_campaign_draft_store(
        settings
    )
    try:
        response = run_growth_strategy_graph(
            brief,
            registry,
            settings=settings,
            knowledge_store=knowledge_store or build_configured_knowledge_store(settings),
        )
    except StrategyGenerationError as exc:
        if exc.run_metadata is not None:
            run_store.record_failed(
                brief,
                exc.run_metadata,
                tool_results=exc.tool_results,
                error_message=str(exc),
            )
        raise

    run_store.record_completed(brief, response)
    campaign_draft_store.record_completed(brief, response)
    return response


def generate_mock_growth_strategy(
    brief: AdvertiserBrief,
    registry: ToolRegistry | None = None,
) -> GrowthStrategyResponse:
    return generate_growth_strategy(brief, registry)
