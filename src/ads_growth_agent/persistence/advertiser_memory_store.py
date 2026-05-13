from collections.abc import Iterator
from contextlib import contextmanager
from decimal import Decimal
from typing import Literal, Protocol
from uuid import NAMESPACE_URL, uuid5

import sqlalchemy as sa
from pydantic import BaseModel, Field
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.engine import Connection, Engine

from ads_growth_agent.contracts import (
    CampaignFeedbackAnalysis,
    CampaignPerformanceEventRequest,
    FeedbackHealthStatus,
)
from ads_growth_agent.persistence.partitioning import partition_bucket
from ads_growth_agent.persistence.performance_event_store import (
    hash_campaign_performance_event,
)
from ads_growth_agent.persistence.run_store import DEFAULT_TENANT_ID
from ads_growth_agent.persistence.schema import advertiser_memories, advertisers, tenants

AdvertiserMemoryWriteStatus = Literal["disabled", "queued", "recorded", "failed"]
AdvertiserMemoryType = Literal[
    "profile",
    "constraint",
    "preference",
    "historical_performance",
]


class AdvertiserMemoryConflictError(Exception):
    def __init__(self, event_id: str) -> None:
        super().__init__(
            "Advertiser memory source already exists for a different event payload: "
            f"{event_id}"
        )
        self.event_id = event_id


class AdvertiserMemoryWriteResult(BaseModel):
    persisted: bool
    queued: bool = False
    status: AdvertiserMemoryWriteStatus = "disabled"
    source_id: str | None = Field(default=None, min_length=1, max_length=160)
    memory_type: AdvertiserMemoryType | None = None


class AdvertiserMemoryStore(Protocol):
    def record_feedback_memory(
        self,
        event: CampaignPerformanceEventRequest,
        analysis: CampaignFeedbackAnalysis,
    ) -> AdvertiserMemoryWriteResult:
        """Persist derived long-term memory from campaign feedback."""


class NoopAdvertiserMemoryStore:
    def record_feedback_memory(
        self,
        event: CampaignPerformanceEventRequest,
        analysis: CampaignFeedbackAnalysis,
    ) -> AdvertiserMemoryWriteResult:
        return AdvertiserMemoryWriteResult(persisted=False, status="disabled")


class PostgresAdvertiserMemoryStore:
    def __init__(self, bind: Engine | Connection, *, tenant_id: str = DEFAULT_TENANT_ID) -> None:
        self._bind = bind
        self._tenant_id = tenant_id

    def record_feedback_memory(
        self,
        event: CampaignPerformanceEventRequest,
        analysis: CampaignFeedbackAnalysis,
    ) -> AdvertiserMemoryWriteResult:
        source_id = feedback_memory_source_id(event)
        values = _memory_values(
            event,
            analysis,
            tenant_id=self._tenant_id,
            source_id=source_id,
        )
        event_hash = values["metadata"]["event_hash"]

        with _transaction(self._bind) as connection:
            _upsert_tenant_and_advertiser_from_event(
                connection,
                event,
                tenant_id=self._tenant_id,
            )
            memory_id = _find_memory_id_for_update(
                connection,
                source_id,
                tenant_id=self._tenant_id,
            )
            if memory_id is None:
                connection.execute(advertiser_memories.insert().values(values))
            else:
                existing_hash = _memory_event_hash(
                    connection,
                    memory_id,
                    tenant_id=self._tenant_id,
                )
                if existing_hash is not None and existing_hash != event_hash:
                    raise AdvertiserMemoryConflictError(event.event_id)
                connection.execute(
                    advertiser_memories.update()
                    .where(advertiser_memories.c.tenant_id == self._tenant_id)
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

        return AdvertiserMemoryWriteResult(
            persisted=True,
            status="recorded",
            source_id=source_id,
            memory_type="historical_performance",
        )


def feedback_memory_source_id(event: CampaignPerformanceEventRequest) -> str:
    fingerprint = uuid5(NAMESPACE_URL, f"{event.advertiser_id}:{event.event_id}").hex[:16]
    return f"memory:performance:{fingerprint}:v1"


@contextmanager
def _transaction(bind: Engine | Connection) -> Iterator[Connection]:
    if isinstance(bind, Engine):
        with bind.begin() as connection:
            yield connection
    else:
        yield bind


def _find_memory_id_for_update(
    connection: Connection,
    source_id: str,
    *,
    tenant_id: str,
):
    return connection.execute(
        sa.text(
            "SELECT memory_id "
            "FROM advertiser_memories "
            "WHERE tenant_id = :tenant_id "
            "AND metadata ->> 'source_id' = :source_id "
            "FOR UPDATE"
        ),
        {"tenant_id": tenant_id, "source_id": source_id},
    ).scalar_one_or_none()


def _memory_event_hash(
    connection: Connection,
    memory_id,
    *,
    tenant_id: str,
) -> str | None:
    row = connection.execute(
        sa.select(advertiser_memories.c.metadata)
        .where(advertiser_memories.c.tenant_id == tenant_id)
        .where(advertiser_memories.c.memory_id == memory_id)
    ).mappings().one()
    metadata = dict(row["metadata"] or {})
    event_hash = metadata.get("event_hash")
    return str(event_hash) if event_hash is not None else None


def _memory_values(
    event: CampaignPerformanceEventRequest,
    analysis: CampaignFeedbackAnalysis,
    *,
    tenant_id: str,
    source_id: str,
) -> dict[str, object]:
    health_status = analysis.health_status.value
    action_types = [item.action_type.value for item in analysis.recommendations]
    title = f"Performance feedback for {event.objective.value}"
    metadata = {
        "source_id": source_id,
        "title": title,
        "source_type": "advertiser_memory",
        "memory_origin": "campaign_performance_event",
        "event_hash": hash_campaign_performance_event(event),
        "event_id": event.event_id,
        "feedback_id": analysis.feedback_id,
        "run_id": event.run_id,
        "campaign_id": event.campaign_id,
        "draft_id": event.draft_id,
        "objective": event.objective.value,
        "objectives": [event.objective.value],
        "product_categories": [],
        "event_type": event.event_type.value,
        "health_status": health_status,
        "action_types": action_types,
        "metrics_summary": analysis.metrics_summary,
        "occurred_at": event.occurred_at.isoformat(),
    }
    return {
        "tenant_id": tenant_id,
        "advertiser_id": event.advertiser_id,
        "memory_type": "historical_performance",
        "content": _memory_content(event, analysis, action_types=action_types),
        "summary": f"{health_status} {event.objective.value} performance feedback",
        "importance_score": _importance_score(analysis.health_status),
        "metadata": metadata,
        "partition_key": event.advertiser_id,
        "partition_bucket": partition_bucket(event.advertiser_id),
        "partition_date": event.occurred_at.date(),
    }


def _memory_content(
    event: CampaignPerformanceEventRequest,
    analysis: CampaignFeedbackAnalysis,
    *,
    action_types: list[str],
) -> str:
    summary = analysis.metrics_summary
    references = [
        f"event {event.event_id}",
        f"objective {event.objective.value}",
        f"health status {analysis.health_status.value}",
        f"impressions {summary.get('impressions')}",
        f"clicks {summary.get('clicks')}",
        f"spend {summary.get('spend')}",
        f"conversions {summary.get('conversions')}",
    ]
    cpa = summary.get("cpa")
    target_cpa = summary.get("target_cpa")
    if cpa is not None:
        references.append(f"observed CPA {cpa}")
    if target_cpa is not None:
        references.append(f"target CPA {target_cpa}")
    references.append(f"recommended actions {', '.join(action_types)}")
    return "Campaign performance feedback: " + "; ".join(references) + "."


def _importance_score(health_status: FeedbackHealthStatus) -> Decimal:
    match health_status:
        case FeedbackHealthStatus.UNDERPERFORMING | FeedbackHealthStatus.CREATIVE_FATIGUE:
            return Decimal("0.850")
        case FeedbackHealthStatus.NEEDS_ATTENTION:
            return Decimal("0.750")
        case FeedbackHealthStatus.ON_TRACK:
            return Decimal("0.650")
        case FeedbackHealthStatus.INSUFFICIENT_DATA:
            return Decimal("0.350")


def _upsert_tenant_and_advertiser_from_event(
    connection: Connection,
    event: CampaignPerformanceEventRequest,
    *,
    tenant_id: str,
) -> None:
    tenant_metadata = {"upserted_by": "advertiser_memory_store"}
    connection.execute(
        pg_insert(tenants)
        .values(
            tenant_id=tenant_id,
            display_name="Default Ads Growth Tenant",
            region="us",
            status="active",
            metadata=tenant_metadata,
        )
        .on_conflict_do_update(
            index_elements=[tenants.c.tenant_id],
            set_={
                "status": "active",
                "metadata": tenant_metadata,
                "updated_at": sa.func.now(),
            },
        )
    )

    advertiser_metadata = {
        "upserted_by": "advertiser_memory_store",
        "source": "campaign_performance_event",
    }
    connection.execute(
        pg_insert(advertisers)
        .values(
            tenant_id=tenant_id,
            advertiser_id=event.advertiser_id,
            name=event.advertiser_id,
            industry=event.objective.value,
            target_markets=[],
            status="active",
            metadata=advertiser_metadata,
            partition_key=event.advertiser_id,
            partition_bucket=partition_bucket(event.advertiser_id),
        )
        .on_conflict_do_update(
            index_elements=[advertisers.c.tenant_id, advertisers.c.advertiser_id],
            set_={
                "status": "active",
                "metadata": advertiser_metadata,
                "partition_key": event.advertiser_id,
                "partition_bucket": partition_bucket(event.advertiser_id),
                "updated_at": sa.func.now(),
            },
        )
    )
