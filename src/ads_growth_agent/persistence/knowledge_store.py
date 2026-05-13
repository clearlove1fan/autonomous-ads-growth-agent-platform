from collections.abc import Iterator
from contextlib import contextmanager
from time import perf_counter
from typing import Any

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql
from sqlalchemy.engine import Connection, Engine, RowMapping

from ads_growth_agent.knowledge import (
    KnowledgeQuery,
    KnowledgeRetrievalResult,
    RetrievedKnowledge,
)
from ads_growth_agent.outbox import enqueue_advertiser_memory_retrieved
from ads_growth_agent.persistence.outbox_store import PostgresOutboxStore
from ads_growth_agent.persistence.partitioning import partition_bucket
from ads_growth_agent.persistence.schema import retrieval_events

DEFAULT_TENANT_ID = "default"
_DOCUMENT_SOURCE_TYPES = ["rag_document", "historical_case"]


class PostgresKnowledgeStore:
    def __init__(
        self,
        bind: Engine | Connection,
        *,
        tenant_id: str = DEFAULT_TENANT_ID,
        track_memory_usage: bool = False,
    ) -> None:
        self._bind = bind
        self._tenant_id = tenant_id
        self.track_memory_usage = track_memory_usage

    def retrieve(self, query: KnowledgeQuery) -> KnowledgeRetrievalResult:
        started = perf_counter()
        with _transaction(self._bind) as connection:
            candidates = [
                *_retrieve_memories(connection, query, tenant_id=self._tenant_id),
                *_retrieve_documents(connection, query, tenant_id=self._tenant_id),
            ]
            results = _rank_candidates(
                candidates,
                top_k=query.top_k,
                min_relevance=query.min_relevance,
            )
            retrieval = KnowledgeRetrievalResult(query=query, results=results)
            latency_ms = int((perf_counter() - started) * 1000)
            _record_retrieval_event(
                connection,
                query,
                retrieval,
                tenant_id=self._tenant_id,
                latency_ms=latency_ms,
            )
            if self.track_memory_usage:
                _enqueue_memory_usage_events(
                    connection,
                    query,
                    retrieval,
                    tenant_id=self._tenant_id,
                )
            return retrieval


@contextmanager
def _transaction(bind: Engine | Connection) -> Iterator[Connection]:
    if isinstance(bind, Engine):
        with bind.begin() as connection:
            yield connection
    else:
        yield bind


def _retrieve_documents(
    connection: Connection,
    query: KnowledgeQuery,
    *,
    tenant_id: str,
) -> list[RetrievedKnowledge]:
    rows = connection.execute(
        _DOCUMENT_RETRIEVAL_SQL,
        {
            "tenant_id": tenant_id,
            "source_types": _DOCUMENT_SOURCE_TYPES,
            "product_categories": [query.product_category],
            "objectives": [query.objective.value],
            "target_market": query.target_market,
            "query": query.query,
            "limit": query.top_k,
        },
    ).mappings()
    return [_document_row_to_knowledge(row) for row in rows]


def _retrieve_memories(
    connection: Connection,
    query: KnowledgeQuery,
    *,
    tenant_id: str,
) -> list[RetrievedKnowledge]:
    rows = connection.execute(
        _MEMORY_RETRIEVAL_SQL,
        {
            "tenant_id": tenant_id,
            "advertiser_id": query.advertiser_id,
            "product_category": query.product_category,
            "objective": query.objective.value,
            "query": query.query,
            "limit": query.top_k,
        },
    ).mappings()
    return [_memory_row_to_knowledge(row) for row in rows]


def _rank_candidates(
    candidates: list[RetrievedKnowledge],
    *,
    top_k: int,
    min_relevance: float,
) -> list[RetrievedKnowledge]:
    return sorted(
        (candidate for candidate in candidates if candidate.relevance >= min_relevance),
        key=lambda item: (-item.relevance, item.source_type, item.source_id),
    )[:top_k]


def _document_row_to_knowledge(row: RowMapping) -> RetrievedKnowledge:
    metadata = dict(row["metadata"] or {})
    metadata.update(
        {
            "document_id": str(row["document_id"]),
            "chunk_id": str(row["chunk_id"]),
            "product_categories": list(row["product_categories"] or []),
            "objectives": list(row["objectives"] or []),
        }
    )
    return RetrievedKnowledge(
        source_id=row["source_id"],
        title=row["title"],
        source_type=row["source_type"],
        content=row["content"],
        relevance=_bounded_relevance(row["relevance"]),
        match_reason=_match_reason(
            category_match=row["category_match"],
            objective_match=row["objective_match"],
            lexical_match=row["lexical_rank"] > 0,
            advertiser_match=False,
        ),
        metadata=metadata,
    )


def _memory_row_to_knowledge(row: RowMapping) -> RetrievedKnowledge:
    metadata = dict(row["metadata"] or {})
    metadata["memory_id"] = str(row["memory_id"])
    return RetrievedKnowledge(
        source_id=row["source_id"],
        title=row["title"],
        source_type="advertiser_memory",
        content=row["content"],
        relevance=_bounded_relevance(row["relevance"]),
        match_reason=_match_reason(
            category_match=row["category_match"],
            objective_match=row["objective_match"],
            lexical_match=row["lexical_rank"] > 0,
            advertiser_match=True,
        ),
        metadata=metadata,
    )


def _match_reason(
    *,
    category_match: bool,
    objective_match: bool,
    lexical_match: bool,
    advertiser_match: bool,
) -> str:
    reasons: list[str] = []
    if advertiser_match:
        reasons.append("advertiser memory match")
    if category_match:
        reasons.append("product category match")
    if objective_match:
        reasons.append("objective match")
    if lexical_match:
        reasons.append("full-text query match")
    if not reasons:
        reasons.append("metadata fallback match")
    return ", ".join(reasons)


def _bounded_relevance(value: Any) -> float:
    numeric_value = float(value or 0)
    return max(0.0, min(1.0, round(numeric_value, 4)))


def _record_retrieval_event(
    connection: Connection,
    query: KnowledgeQuery,
    retrieval: KnowledgeRetrievalResult,
    *,
    tenant_id: str,
    latency_ms: int,
) -> None:
    run_id = query.run_id or f"ad-hoc:{query.advertiser_id}"
    filters = {
        "source_types": [*_DOCUMENT_SOURCE_TYPES, "advertiser_memory"],
        "product_category": query.product_category,
        "objective": query.objective.value,
        "target_market": query.target_market,
        "min_relevance": query.min_relevance,
    }
    connection.execute(
        retrieval_events.insert().values(
            tenant_id=tenant_id,
            run_id=run_id,
            advertiser_id=query.advertiser_id,
            query=query.query,
            top_k=query.top_k,
            filters=filters,
            results=[result.model_dump(mode="json") for result in retrieval.results],
            latency_ms=latency_ms,
            partition_key=run_id,
            partition_bucket=partition_bucket(run_id),
        )
    )


def _enqueue_memory_usage_events(
    connection: Connection,
    query: KnowledgeQuery,
    retrieval: KnowledgeRetrievalResult,
    *,
    tenant_id: str,
) -> None:
    outbox_store = PostgresOutboxStore(connection, tenant_id=tenant_id)
    for result in retrieval.results:
        if result.source_type != "advertiser_memory":
            continue
        enqueue_advertiser_memory_retrieved(
            outbox_store,
            source_id=result.source_id,
            advertiser_id=query.advertiser_id,
            run_id=query.run_id,
            query=query.query,
            relevance=result.relevance,
        )


_ARRAY_TEXT = postgresql.ARRAY(sa.Text())

_DOCUMENT_RETRIEVAL_SQL = sa.text(
    """
    WITH query_terms AS (
        SELECT plainto_tsquery('english', :query) AS tsq
    )
    SELECT
        d.document_id,
        c.chunk_id,
        d.source_id,
        d.title,
        c.source_type,
        c.content,
        c.product_categories,
        c.objectives,
        c.metadata,
        c.product_categories && :product_categories AS category_match,
        c.objectives && :objectives AS objective_match,
        ts_rank_cd(to_tsvector('english', c.content), query_terms.tsq) AS lexical_rank,
        LEAST(
            1.0,
            0.05
            + CASE WHEN c.product_categories && :product_categories THEN 0.25 ELSE 0 END
            + CASE WHEN c.objectives && :objectives THEN 0.25 ELSE 0 END
            + CASE WHEN LOWER(COALESCE(d.market, '')) = LOWER(:target_market) THEN 0.05 ELSE 0 END
            + LEAST(0.40, ts_rank_cd(to_tsvector('english', c.content), query_terms.tsq))
        ) AS relevance
    FROM knowledge_chunks c
    JOIN knowledge_documents d
        ON d.tenant_id = c.tenant_id
       AND d.document_id = c.document_id
    CROSS JOIN query_terms
    WHERE c.tenant_id = :tenant_id
      AND c.source_type = ANY(:source_types)
      AND (
          c.product_categories && :product_categories
          OR c.objectives && :objectives
          OR to_tsvector('english', c.content) @@ query_terms.tsq
      )
    ORDER BY relevance DESC, d.source_id ASC, c.chunk_index ASC
    LIMIT :limit
    """
).bindparams(
    sa.bindparam("source_types", type_=_ARRAY_TEXT),
    sa.bindparam("product_categories", type_=_ARRAY_TEXT),
    sa.bindparam("objectives", type_=_ARRAY_TEXT),
)

_MEMORY_RETRIEVAL_SQL = sa.text(
    """
    WITH query_terms AS (
        SELECT plainto_tsquery('english', :query) AS tsq
    )
    SELECT
        m.memory_id,
        COALESCE(m.metadata ->> 'source_id', 'memory:' || m.memory_id::text) AS source_id,
        COALESCE(m.metadata ->> 'title', m.summary, 'Advertiser memory') AS title,
        m.content,
        m.metadata,
        COALESCE(m.metadata -> 'product_categories' ? :product_category, false) AS category_match,
        COALESCE(m.metadata -> 'objectives' ? :objective, false) AS objective_match,
        ts_rank_cd(to_tsvector('english', m.content), query_terms.tsq) AS lexical_rank,
        LEAST(
            1.0,
            0.35
            + LEAST(0.30, m.importance_score::float)
            + CASE WHEN COALESCE(m.metadata -> 'product_categories' ? :product_category, false)
                THEN 0.15 ELSE 0 END
            + CASE WHEN COALESCE(m.metadata -> 'objectives' ? :objective, false)
                THEN 0.15 ELSE 0 END
            + LEAST(0.20, ts_rank_cd(to_tsvector('english', m.content), query_terms.tsq))
        ) AS relevance
    FROM advertiser_memories m
    CROSS JOIN query_terms
    WHERE m.tenant_id = :tenant_id
      AND m.advertiser_id = :advertiser_id
      AND (
          COALESCE(m.metadata -> 'product_categories' ? :product_category, false)
          OR COALESCE(m.metadata -> 'objectives' ? :objective, false)
          OR to_tsvector('english', m.content) @@ query_terms.tsq
      )
    ORDER BY relevance DESC, source_id ASC
    LIMIT :limit
    """
)
