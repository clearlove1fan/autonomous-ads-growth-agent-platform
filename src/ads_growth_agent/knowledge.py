import re
from typing import Any, Literal, Protocol

from pydantic import BaseModel, ConfigDict, Field

from ads_growth_agent.contracts import AdvertiserBrief, CampaignObjective

KnowledgeSourceType = Literal["rag_document", "historical_case", "advertiser_memory"]


class KnowledgeDocument(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    source_id: str = Field(min_length=1, max_length=160)
    title: str = Field(min_length=1, max_length=240)
    source_type: KnowledgeSourceType
    content: str = Field(min_length=1, max_length=4_000)
    product_categories: list[str] = Field(default_factory=list)
    objectives: list[CampaignObjective] = Field(default_factory=list)
    advertiser_id: str | None = Field(default=None, min_length=1, max_length=128)
    metadata: dict[str, Any] = Field(default_factory=dict)


class KnowledgeQuery(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    advertiser_id: str = Field(min_length=1, max_length=128)
    query: str = Field(min_length=1, max_length=2_000)
    product_category: str = Field(min_length=1, max_length=120)
    objective: CampaignObjective
    target_market: str = Field(min_length=1, max_length=120)
    top_k: int = Field(default=3, ge=1, le=10)


class RetrievedKnowledge(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    source_id: str = Field(min_length=1, max_length=160)
    title: str = Field(min_length=1, max_length=240)
    source_type: KnowledgeSourceType
    content: str = Field(min_length=1, max_length=4_000)
    relevance: float = Field(ge=0, le=1)
    match_reason: str = Field(min_length=1, max_length=500)
    metadata: dict[str, Any] = Field(default_factory=dict)


class KnowledgeRetrievalResult(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    query: KnowledgeQuery
    results: list[RetrievedKnowledge] = Field(default_factory=list)


class KnowledgeStore(Protocol):
    def retrieve(self, query: KnowledgeQuery) -> KnowledgeRetrievalResult:
        """Return relevant knowledge for the query."""


class InMemoryKnowledgeStore:
    def __init__(self, documents: list[KnowledgeDocument]) -> None:
        self._documents = documents

    def retrieve(self, query: KnowledgeQuery) -> KnowledgeRetrievalResult:
        scored = [
            (document, _score_document(document, query))
            for document in self._documents
        ]
        ranked = sorted(
            ((document, score) for document, score in scored if score > 0),
            key=lambda item: (-item[1], item[0].source_id),
        )
        results = [
            _to_retrieved_knowledge(document, score, query)
            for document, score in ranked[: query.top_k]
        ]
        return KnowledgeRetrievalResult(query=query, results=results)


def build_default_knowledge_store() -> KnowledgeStore:
    return InMemoryKnowledgeStore(_default_documents())


def build_knowledge_query(brief: AdvertiserBrief, *, top_k: int = 3) -> KnowledgeQuery:
    query_parts = [
        brief.product_name,
        brief.product_category,
        brief.objective.value,
        brief.target_market,
        brief.primary_kpi,
        brief.brand_voice or "",
        " ".join(brief.constraints),
        " ".join(brief.known_audiences),
        brief.historical_context or "",
    ]
    return KnowledgeQuery(
        advertiser_id=brief.advertiser_id,
        query=" ".join(part for part in query_parts if part),
        product_category=brief.product_category,
        objective=brief.objective,
        target_market=brief.target_market,
        top_k=top_k,
    )


def _score_document(document: KnowledgeDocument, query: KnowledgeQuery) -> float:
    score = 0.0
    query_tokens = _tokens(
        " ".join(
            [
                query.query,
                query.product_category,
                query.objective.value,
                query.target_market,
            ]
        )
    )
    document_tokens = _tokens(
        " ".join(
            [
                document.title,
                document.content,
                " ".join(document.product_categories),
                " ".join(objective.value for objective in document.objectives),
            ]
        )
    )

    if document.advertiser_id == query.advertiser_id:
        score += 0.35
    elif document.advertiser_id is None:
        score += 0.05
    else:
        score -= 0.15

    if _normalized(query.product_category) in {
        _normalized(category) for category in document.product_categories
    }:
        score += 0.25

    if query.objective in document.objectives:
        score += 0.25

    overlap = query_tokens & document_tokens
    if query_tokens:
        score += min(0.3, len(overlap) / len(query_tokens))

    if _normalized_phrase(query.target_market) in _normalized_phrase(document.content):
        score += 0.1

    return max(0.0, min(1.0, round(score, 4)))


def _to_retrieved_knowledge(
    document: KnowledgeDocument,
    score: float,
    query: KnowledgeQuery,
) -> RetrievedKnowledge:
    reasons: list[str] = []
    if document.advertiser_id == query.advertiser_id:
        reasons.append("advertiser memory match")
    if _normalized(query.product_category) in {
        _normalized(category) for category in document.product_categories
    }:
        reasons.append("product category match")
    if query.objective in document.objectives:
        reasons.append("objective match")
    if not reasons:
        reasons.append("lexical query overlap")

    return RetrievedKnowledge(
        source_id=document.source_id,
        title=document.title,
        source_type=document.source_type,
        content=document.content,
        relevance=score,
        match_reason=", ".join(reasons),
        metadata=document.metadata,
    )


def _tokens(value: str) -> set[str]:
    return {
        token
        for token in re.findall(r"[a-z0-9]+", value.lower())
        if len(token) > 2
    }


def _normalized(value: str) -> str:
    return " ".join(sorted(_tokens(value)))


def _normalized_phrase(value: str) -> str:
    return " ".join(re.findall(r"[a-z0-9]+", value.lower()))


def _default_documents() -> list[KnowledgeDocument]:
    return [
        KnowledgeDocument(
            source_id="rag:playbook:app_registration_learning:v1",
            title="App registration growth learning playbook",
            source_type="rag_document",
            product_categories=["fitness app", "mobile app"],
            objectives=[CampaignObjective.REGISTRATIONS, CampaignObjective.APP_INSTALLS],
            content=(
                "For mobile app registration campaigns, reserve a controlled creative testing "
                "lane before scaling. Use prospecting to discover qualified users, retarget "
                "engaged viewers, and measure registration CPA daily against the target."
            ),
            metadata={"owner": "growth_strategy", "version": "v1"},
        ),
        KnowledgeDocument(
            source_id="case:fitness:trial_registration_creative_loop:v1",
            title="Fitness app trial registration case",
            source_type="historical_case",
            product_categories=["fitness app"],
            objectives=[CampaignObjective.REGISTRATIONS],
            content=(
                "A United States fitness app improved trial registration efficiency by pairing "
                "beginner-friendly creative hooks with retargeting for workout video viewers. "
                "The best-performing plan kept creative learning separate from scale budget."
            ),
            metadata={"market": "United States", "primary_kpi": "trial registrations"},
        ),
        KnowledgeDocument(
            source_id="memory:adv_fitness_001:profile:v1",
            title="FitTrack Pro advertiser memory",
            source_type="advertiser_memory",
            advertiser_id="adv_fitness_001",
            product_categories=["fitness app"],
            objectives=[CampaignObjective.REGISTRATIONS],
            content=(
                "Advertiser prefers motivational but practical messaging, avoids unrealistic "
                "body transformation claims, and uses trial registrations as the primary KPI. "
                "Known high-intent audiences include home workout beginners and wearable "
                "fitness tracker users."
            ),
            metadata={"memory_type": "advertiser_profile"},
        ),
        KnowledgeDocument(
            source_id="rag:playbook:purchase_growth:v1",
            title="Purchase conversion campaign playbook",
            source_type="rag_document",
            product_categories=["skincare", "ecommerce"],
            objectives=[CampaignObjective.PURCHASES],
            content=(
                "For purchase campaigns, build trust with proof-led creative, separate new "
                "customer prospecting from cart or product-page retargeting, and watch blended "
                "CPA before expanding budget."
            ),
            metadata={"owner": "growth_strategy", "version": "v1"},
        ),
        KnowledgeDocument(
            source_id="rag:playbook:b2b_lead_quality:v1",
            title="B2B lead quality measurement playbook",
            source_type="rag_document",
            product_categories=["saas", "b2b software"],
            objectives=[CampaignObjective.LEADS],
            content=(
                "For lead generation, optimize beyond form volume. Track qualified lead rate, "
                "segment by company size or role, and feed sales-quality signals into the next "
                "budget review."
            ),
            metadata={"owner": "growth_strategy", "version": "v1"},
        ),
    ]
