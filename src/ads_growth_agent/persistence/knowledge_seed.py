from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from uuid import UUID

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.engine import Connection, Engine

from ads_growth_agent.knowledge import KnowledgeDocument, default_knowledge_documents
from ads_growth_agent.persistence.partitioning import partition_bucket
from ads_growth_agent.persistence.schema import (
    advertiser_memories,
    advertisers,
    knowledge_chunks,
    knowledge_documents,
    tenants,
)

DEFAULT_TENANT_ID = "default"

_MEMORY_TYPE_BY_SEED_VALUE = {
    "advertiser_profile": "profile",
    "profile": "profile",
    "constraint": "constraint",
    "preference": "preference",
    "historical_performance": "historical_performance",
}


def seed_default_knowledge(
    bind: Engine | Connection,
    *,
    tenant_id: str = DEFAULT_TENANT_ID,
) -> None:
    seed_knowledge_documents(bind, default_knowledge_documents(), tenant_id=tenant_id)


def seed_knowledge_documents(
    bind: Engine | Connection,
    documents: Sequence[KnowledgeDocument],
    *,
    tenant_id: str = DEFAULT_TENANT_ID,
) -> None:
    with _transaction(bind) as connection:
        _upsert_tenant(connection, tenant_id)
        for document in documents:
            if document.source_type == "advertiser_memory":
                _upsert_memory(connection, document, tenant_id=tenant_id)
            else:
                _upsert_document_and_chunk(connection, document, tenant_id=tenant_id)


@contextmanager
def _transaction(bind: Engine | Connection) -> Iterator[Connection]:
    if isinstance(bind, Engine):
        with bind.begin() as connection:
            yield connection
    else:
        yield bind


def _upsert_tenant(connection: Connection, tenant_id: str) -> None:
    stmt = (
        pg_insert(tenants)
        .values(
            tenant_id=tenant_id,
            display_name="Default Ads Growth Tenant",
            region="us",
            status="active",
            metadata={"seeded_by": "ads_growth_agent"},
        )
        .on_conflict_do_update(
            index_elements=[tenants.c.tenant_id],
            set_={
                "display_name": "Default Ads Growth Tenant",
                "status": "active",
                "metadata": {"seeded_by": "ads_growth_agent"},
            },
        )
    )
    connection.execute(stmt)


def _upsert_memory(
    connection: Connection,
    document: KnowledgeDocument,
    *,
    tenant_id: str,
) -> None:
    if document.advertiser_id is None:
        raise ValueError(f"Advertiser memory {document.source_id} requires advertiser_id")

    _upsert_advertiser(connection, document, tenant_id=tenant_id)
    memory_id = _find_memory_id(connection, document, tenant_id=tenant_id)
    values = _memory_values(document, tenant_id=tenant_id)

    if memory_id is None:
        connection.execute(advertiser_memories.insert().values(values))
        return

    connection.execute(
        advertiser_memories.update()
        .where(advertiser_memories.c.tenant_id == tenant_id)
        .where(advertiser_memories.c.memory_id == memory_id)
        .values(
            memory_type=values["memory_type"],
            content=values["content"],
            summary=values["summary"],
            importance_score=values["importance_score"],
            metadata=values["metadata"],
            partition_key=values["partition_key"],
            partition_bucket=values["partition_bucket"],
            updated_at=sa.func.now(),
        )
    )


def _upsert_advertiser(
    connection: Connection,
    document: KnowledgeDocument,
    *,
    tenant_id: str,
) -> None:
    advertiser_id = document.advertiser_id
    if advertiser_id is None:
        return

    market = document.metadata.get("market")
    target_markets = [market] if isinstance(market, str) and market else []
    stmt = (
        pg_insert(advertisers)
        .values(
            tenant_id=tenant_id,
            advertiser_id=advertiser_id,
            name=document.title,
            industry=document.product_categories[0] if document.product_categories else None,
            target_markets=target_markets,
            status="active",
            metadata={"seeded_from_source_id": document.source_id},
            partition_key=advertiser_id,
            partition_bucket=partition_bucket(advertiser_id),
        )
        .on_conflict_do_update(
            index_elements=[advertisers.c.tenant_id, advertisers.c.advertiser_id],
            set_={
                "name": document.title,
                "industry": document.product_categories[0]
                if document.product_categories
                else None,
                "target_markets": target_markets,
                "status": "active",
                "metadata": {"seeded_from_source_id": document.source_id},
                "partition_key": advertiser_id,
                "partition_bucket": partition_bucket(advertiser_id),
                "updated_at": sa.func.now(),
            },
        )
    )
    connection.execute(stmt)


def _find_memory_id(
    connection: Connection,
    document: KnowledgeDocument,
    *,
    tenant_id: str,
) -> UUID | None:
    return connection.execute(
        sa.text(
            "SELECT memory_id "
            "FROM advertiser_memories "
            "WHERE tenant_id = :tenant_id "
            "AND advertiser_id = :advertiser_id "
            "AND metadata ->> 'source_id' = :source_id"
        ),
        {
            "tenant_id": tenant_id,
            "advertiser_id": document.advertiser_id,
            "source_id": document.source_id,
        },
    ).scalar_one_or_none()


def _memory_values(document: KnowledgeDocument, *, tenant_id: str) -> dict[str, object]:
    if document.advertiser_id is None:
        raise ValueError(f"Advertiser memory {document.source_id} requires advertiser_id")

    memory_type = _MEMORY_TYPE_BY_SEED_VALUE.get(str(document.metadata.get("memory_type", "")))
    if memory_type is None:
        memory_type = "profile"

    metadata = {
        **document.metadata,
        "source_id": document.source_id,
        "title": document.title,
        "source_type": document.source_type,
        "product_categories": document.product_categories,
        "objectives": [objective.value for objective in document.objectives],
    }
    return {
        "tenant_id": tenant_id,
        "advertiser_id": document.advertiser_id,
        "memory_type": memory_type,
        "content": document.content,
        "summary": document.title,
        "importance_score": document.metadata.get("importance_score", 0.5),
        "metadata": metadata,
        "partition_key": document.advertiser_id,
        "partition_bucket": partition_bucket(document.advertiser_id),
    }


def _upsert_document_and_chunk(
    connection: Connection,
    document: KnowledgeDocument,
    *,
    tenant_id: str,
) -> None:
    document_id = _upsert_document(connection, document, tenant_id=tenant_id)
    _upsert_chunk(connection, document, document_id, tenant_id=tenant_id)


def _upsert_document(
    connection: Connection,
    document: KnowledgeDocument,
    *,
    tenant_id: str,
) -> UUID:
    stmt = (
        pg_insert(knowledge_documents)
        .values(
            tenant_id=tenant_id,
            source_id=document.source_id,
            title=document.title,
            source_type=document.source_type,
            product_categories=document.product_categories,
            objectives=[objective.value for objective in document.objectives],
            market=document.metadata.get("market"),
            metadata=document.metadata,
            partition_key=document.source_id,
            partition_bucket=partition_bucket(document.source_id),
        )
        .on_conflict_do_update(
            index_elements=[knowledge_documents.c.tenant_id, knowledge_documents.c.source_id],
            set_={
                "title": document.title,
                "source_type": document.source_type,
                "product_categories": document.product_categories,
                "objectives": [objective.value for objective in document.objectives],
                "market": document.metadata.get("market"),
                "metadata": document.metadata,
                "partition_key": document.source_id,
                "partition_bucket": partition_bucket(document.source_id),
                "updated_at": sa.func.now(),
            },
        )
        .returning(knowledge_documents.c.document_id)
    )
    return connection.execute(stmt).scalar_one()


def _upsert_chunk(
    connection: Connection,
    document: KnowledgeDocument,
    document_id: UUID,
    *,
    tenant_id: str,
) -> None:
    metadata = {
        **document.metadata,
        "source_id": document.source_id,
        "title": document.title,
    }
    stmt = (
        pg_insert(knowledge_chunks)
        .values(
            tenant_id=tenant_id,
            document_id=document_id,
            chunk_index=0,
            content=document.content,
            token_count=max(1, len(document.content.split())),
            source_type=document.source_type,
            product_categories=document.product_categories,
            objectives=[objective.value for objective in document.objectives],
            metadata=metadata,
            partition_key=document.source_id,
            partition_bucket=partition_bucket(document.source_id),
        )
        .on_conflict_do_update(
            index_elements=[
                knowledge_chunks.c.tenant_id,
                knowledge_chunks.c.document_id,
                knowledge_chunks.c.chunk_index,
            ],
            set_={
                "content": document.content,
                "token_count": max(1, len(document.content.split())),
                "source_type": document.source_type,
                "product_categories": document.product_categories,
                "objectives": [objective.value for objective in document.objectives],
                "metadata": metadata,
                "partition_key": document.source_id,
                "partition_bucket": partition_bucket(document.source_id),
            },
        )
    )
    connection.execute(stmt)
