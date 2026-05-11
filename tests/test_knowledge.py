from ads_growth_agent.contracts import AdvertiserBrief, CampaignObjective
from ads_growth_agent.knowledge import (
    InMemoryKnowledgeStore,
    KnowledgeDocument,
    build_default_knowledge_store,
    build_knowledge_query,
)


def test_default_knowledge_store_retrieves_advertiser_memory_and_rag_docs() -> None:
    query = build_knowledge_query(_fitness_brief())
    result = build_default_knowledge_store().retrieve(query)

    source_types = [item.source_type for item in result.results]
    source_ids = [item.source_id for item in result.results]

    assert result.query.advertiser_id == "adv_fitness_001"
    assert len(result.results) == 3
    assert "advertiser_memory" in source_types
    assert "rag_document" in source_types
    assert "historical_case" in source_types
    assert "memory:adv_fitness_001:profile:v1" in source_ids
    assert result.results[0].relevance >= result.results[-1].relevance


def test_in_memory_store_penalizes_other_advertiser_memory() -> None:
    store = InMemoryKnowledgeStore(
        [
            KnowledgeDocument(
                source_id="memory:other",
                title="Other advertiser memory",
                source_type="advertiser_memory",
                advertiser_id="adv_other",
                product_categories=["fitness app"],
                objectives=[CampaignObjective.REGISTRATIONS],
                content="Other advertiser prefers aggressive discount claims.",
            ),
            KnowledgeDocument(
                source_id="rag:fitness",
                title="Fitness registration playbook",
                source_type="rag_document",
                product_categories=["fitness app"],
                objectives=[CampaignObjective.REGISTRATIONS],
                content="Fitness registration plans should use beginner-friendly onboarding.",
            ),
        ]
    )

    result = store.retrieve(build_knowledge_query(_fitness_brief(), top_k=2))

    assert [item.source_id for item in result.results] == ["rag:fitness", "memory:other"]
    assert result.results[0].relevance > result.results[1].relevance


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
