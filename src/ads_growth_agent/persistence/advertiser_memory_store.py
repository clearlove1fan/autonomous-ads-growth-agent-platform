import hashlib
import json
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from decimal import Decimal
from typing import Literal, Protocol
from uuid import NAMESPACE_URL, uuid5

import sqlalchemy as sa
from pydantic import BaseModel, Field
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.engine import Connection, Engine

from ads_growth_agent.contracts import (
    AdvertiserMemoryDetailResponse,
    AdvertiserMemoryType,
    CampaignFeedbackAnalysis,
    CampaignFeedbackHandoffRecordResponse,
    CampaignPerformanceEventRequest,
    FeedbackHandoffOutcome,
    FeedbackHealthStatus,
)
from ads_growth_agent.persistence.partitioning import partition_bucket
from ads_growth_agent.persistence.performance_event_store import (
    hash_campaign_performance_event,
)
from ads_growth_agent.persistence.run_store import DEFAULT_TENANT_ID
from ads_growth_agent.persistence.schema import advertiser_memories, advertisers, tenants

AdvertiserMemoryWriteStatus = Literal["disabled", "queued", "recorded", "failed"]


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


class AdvertiserMemoryUsageResult(BaseModel):
    recorded: bool
    source_id: str = Field(min_length=1, max_length=160)
    usage_count: int | None = Field(default=None, ge=0)
    last_used_at: datetime | None = None


class AdvertiserMemoryStore(Protocol):
    def record_feedback_memory(
        self,
        event: CampaignPerformanceEventRequest,
        analysis: CampaignFeedbackAnalysis,
    ) -> AdvertiserMemoryWriteResult:
        """Persist derived long-term memory from campaign feedback."""

    def record_handoff_memory(
        self,
        record: CampaignFeedbackHandoffRecordResponse,
    ) -> AdvertiserMemoryWriteResult:
        """Persist derived long-term memory from a manual handoff outcome."""

    def record_retrieval_usage(
        self,
        *,
        source_id: str,
        retrieved_at: datetime | None = None,
    ) -> AdvertiserMemoryUsageResult:
        """Record that a memory source was retrieved."""

    def get_memory(
        self,
        *,
        advertiser_id: str,
        source_id: str,
    ) -> AdvertiserMemoryDetailResponse | None:
        """Return one advertiser memory by public source ID."""

    def list_memories(
        self,
        *,
        advertiser_id: str,
        memory_type: AdvertiserMemoryType | None = None,
        limit: int = 50,
    ) -> list[AdvertiserMemoryDetailResponse]:
        """Return recent advertiser memories for the configured tenant."""


class NoopAdvertiserMemoryStore:
    def record_feedback_memory(
        self,
        event: CampaignPerformanceEventRequest,
        analysis: CampaignFeedbackAnalysis,
    ) -> AdvertiserMemoryWriteResult:
        return AdvertiserMemoryWriteResult(persisted=False, status="disabled")

    def record_handoff_memory(
        self,
        record: CampaignFeedbackHandoffRecordResponse,
    ) -> AdvertiserMemoryWriteResult:
        return AdvertiserMemoryWriteResult(persisted=False, status="disabled")

    def record_retrieval_usage(
        self,
        *,
        source_id: str,
        retrieved_at: datetime | None = None,
    ) -> AdvertiserMemoryUsageResult:
        return AdvertiserMemoryUsageResult(recorded=False, source_id=source_id)

    def get_memory(
        self,
        *,
        advertiser_id: str,
        source_id: str,
    ) -> AdvertiserMemoryDetailResponse | None:
        return None

    def list_memories(
        self,
        *,
        advertiser_id: str,
        memory_type: AdvertiserMemoryType | None = None,
        limit: int = 50,
    ) -> list[AdvertiserMemoryDetailResponse]:
        return []


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

    def record_handoff_memory(
        self,
        record: CampaignFeedbackHandoffRecordResponse,
    ) -> AdvertiserMemoryWriteResult:
        source_id = handoff_memory_source_id(record)
        values = _handoff_memory_values(
            record,
            tenant_id=self._tenant_id,
            source_id=source_id,
        )
        record_hash = values["metadata"]["handoff_record_hash"]

        with _transaction(self._bind) as connection:
            _upsert_tenant_and_advertiser_from_handoff(
                connection,
                record,
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
                existing_hash = _memory_metadata_hash(
                    connection,
                    memory_id,
                    tenant_id=self._tenant_id,
                    metadata_key="handoff_record_hash",
                )
                if existing_hash is not None and existing_hash != record_hash:
                    raise AdvertiserMemoryConflictError(record.handoff_record_id)
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

    def record_retrieval_usage(
        self,
        *,
        source_id: str,
        retrieved_at: datetime | None = None,
    ) -> AdvertiserMemoryUsageResult:
        effective_retrieved_at = retrieved_at or datetime.now(UTC)
        with _transaction(self._bind) as connection:
            row = connection.execute(
                advertiser_memories.update()
                .where(advertiser_memories.c.tenant_id == self._tenant_id)
                .where(advertiser_memories.c.metadata["source_id"].astext == source_id)
                .values(
                    last_used_at=effective_retrieved_at,
                    usage_count=advertiser_memories.c.usage_count + 1,
                    updated_at=sa.func.now(),
                )
                .returning(
                    advertiser_memories.c.usage_count,
                    advertiser_memories.c.last_used_at,
                )
            ).mappings().one_or_none()

        if row is None:
            return AdvertiserMemoryUsageResult(recorded=False, source_id=source_id)
        return AdvertiserMemoryUsageResult(
            recorded=True,
            source_id=source_id,
            usage_count=row["usage_count"],
            last_used_at=row["last_used_at"],
        )

    def get_memory(
        self,
        *,
        advertiser_id: str,
        source_id: str,
    ) -> AdvertiserMemoryDetailResponse | None:
        with _connection(self._bind) as connection:
            row = connection.execute(
                sa.select(advertiser_memories)
                .where(advertiser_memories.c.tenant_id == self._tenant_id)
                .where(advertiser_memories.c.advertiser_id == advertiser_id)
                .where(advertiser_memories.c.metadata["source_id"].astext == source_id)
            ).mappings().one_or_none()
        if row is None:
            return None
        return _row_to_advertiser_memory(row)

    def list_memories(
        self,
        *,
        advertiser_id: str,
        memory_type: AdvertiserMemoryType | None = None,
        limit: int = 50,
    ) -> list[AdvertiserMemoryDetailResponse]:
        with _connection(self._bind) as connection:
            stmt = (
                sa.select(advertiser_memories)
                .where(advertiser_memories.c.tenant_id == self._tenant_id)
                .where(advertiser_memories.c.advertiser_id == advertiser_id)
            )
            if memory_type is not None:
                stmt = stmt.where(advertiser_memories.c.memory_type == memory_type)
            rows = (
                connection.execute(
                    stmt.order_by(
                        advertiser_memories.c.updated_at.desc(),
                        advertiser_memories.c.importance_score.desc(),
                        advertiser_memories.c.memory_id.desc(),
                    ).limit(limit)
                )
                .mappings()
                .all()
            )
        return [_row_to_advertiser_memory(row) for row in rows]


def feedback_memory_source_id(event: CampaignPerformanceEventRequest) -> str:
    fingerprint = uuid5(NAMESPACE_URL, f"{event.advertiser_id}:{event.event_id}").hex[:16]
    return f"memory:performance:{fingerprint}:v1"


def handoff_memory_source_id(record: CampaignFeedbackHandoffRecordResponse) -> str:
    fingerprint = uuid5(
        NAMESPACE_URL,
        f"{record.advertiser_id}:{record.handoff_record_id}",
    ).hex[:16]
    return f"memory:handoff:{fingerprint}:v1"


def hash_feedback_handoff_record(record: CampaignFeedbackHandoffRecordResponse) -> str:
    payload = json.dumps(
        record.model_dump(mode="json"),
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


@contextmanager
def _transaction(bind: Engine | Connection) -> Iterator[Connection]:
    if isinstance(bind, Engine):
        with bind.begin() as connection:
            yield connection
    else:
        yield bind


@contextmanager
def _connection(bind: Engine | Connection) -> Iterator[Connection]:
    if isinstance(bind, Engine):
        with bind.connect() as connection:
            yield connection
    else:
        yield bind


def _row_to_advertiser_memory(row) -> AdvertiserMemoryDetailResponse:
    metadata = dict(row["metadata"] or {})
    source_id = str(metadata.get("source_id") or f"memory:{row['memory_id']}")
    title = metadata.get("title")
    return AdvertiserMemoryDetailResponse(
        memory_id=str(row["memory_id"]),
        source_id=source_id,
        advertiser_id=row["advertiser_id"],
        memory_type=row["memory_type"],
        title=str(title) if title else None,
        content=row["content"],
        summary=row["summary"],
        importance_score=row["importance_score"],
        usage_count=row["usage_count"],
        last_used_at=row["last_used_at"],
        metadata=metadata,
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )


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
    return _memory_metadata_hash(
        connection,
        memory_id,
        tenant_id=tenant_id,
        metadata_key="event_hash",
    )


def _memory_metadata_hash(
    connection: Connection,
    memory_id,
    *,
    tenant_id: str,
    metadata_key: str,
) -> str | None:
    row = connection.execute(
        sa.select(advertiser_memories.c.metadata)
        .where(advertiser_memories.c.tenant_id == tenant_id)
        .where(advertiser_memories.c.memory_id == memory_id)
    ).mappings().one()
    metadata = dict(row["metadata"] or {})
    metadata_hash = metadata.get(metadata_key)
    return str(metadata_hash) if metadata_hash is not None else None


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
        "usage_count": 0,
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


def _handoff_memory_values(
    record: CampaignFeedbackHandoffRecordResponse,
    *,
    tenant_id: str,
    source_id: str,
) -> dict[str, object]:
    title = f"Manual handoff outcome: {record.outcome.value}"
    metadata = {
        "source_id": source_id,
        "title": title,
        "source_type": "advertiser_memory",
        "memory_origin": "feedback_handoff_record",
        "handoff_record_hash": hash_feedback_handoff_record(record),
        "handoff_record_id": record.handoff_record_id,
        "handoff_package_id": record.handoff_package_id,
        "review_id": record.review_id,
        "execution_plan_id": record.execution_plan_id,
        "latest_dry_run_id": record.latest_dry_run_id,
        "optimization_draft_id": record.optimization_draft_id,
        "event_id": record.event_id,
        "feedback_id": record.feedback_id,
        "run_id": record.run_id,
        "campaign_id": record.campaign_id,
        "draft_id": record.base_draft_id,
        "strategy_id": record.strategy_id,
        "outcome": record.outcome.value,
        "operator_id": record.operator_id,
        "package_status": record.package_status,
        "completed_step_ids": record.completed_step_ids,
        "blocked_step_ids": record.blocked_step_ids,
        "requires_follow_up": record.requires_follow_up,
        "created_at": record.created_at.isoformat(),
    }
    return {
        "tenant_id": tenant_id,
        "advertiser_id": record.advertiser_id,
        "memory_type": "historical_performance",
        "content": _handoff_memory_content(record),
        "summary": f"{record.outcome.value} manual handoff for event {record.event_id}",
        "importance_score": _handoff_importance_score(record),
        "usage_count": 0,
        "metadata": metadata,
        "partition_key": record.advertiser_id,
        "partition_bucket": partition_bucket(record.advertiser_id),
        "partition_date": record.created_at.date(),
    }


def _handoff_memory_content(record: CampaignFeedbackHandoffRecordResponse) -> str:
    references = [
        f"event {record.event_id}",
        f"review {record.review_id}",
        f"handoff package {record.handoff_package_id}",
        f"outcome {record.outcome.value}",
        f"package status {record.package_status}",
        f"completed steps {len(record.completed_step_ids)}",
        f"blocked steps {len(record.blocked_step_ids)}",
    ]
    if record.latest_dry_run_id:
        references.append(f"latest dry run {record.latest_dry_run_id}")
    if record.notes:
        references.append(f"operator notes {record.notes}")
    return "Manual feedback handoff outcome: " + "; ".join(references) + "."


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


def _handoff_importance_score(record: CampaignFeedbackHandoffRecordResponse) -> Decimal:
    match record.outcome:
        case FeedbackHandoffOutcome.BLOCKED:
            base = Decimal("0.900")
        case FeedbackHandoffOutcome.APPLIED:
            base = Decimal("0.700")
        case FeedbackHandoffOutcome.SKIPPED:
            base = Decimal("0.500")
    if record.requires_follow_up:
        return min(base + Decimal("0.050"), Decimal("0.950"))
    return base


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


def _upsert_tenant_and_advertiser_from_handoff(
    connection: Connection,
    record: CampaignFeedbackHandoffRecordResponse,
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
        "source": "feedback_handoff_record",
    }
    connection.execute(
        pg_insert(advertisers)
        .values(
            tenant_id=tenant_id,
            advertiser_id=record.advertiser_id,
            name=record.advertiser_id,
            industry="feedback_handoff",
            target_markets=[],
            status="active",
            metadata=advertiser_metadata,
            partition_key=record.advertiser_id,
            partition_bucket=partition_bucket(record.advertiser_id),
        )
        .on_conflict_do_update(
            index_elements=[advertisers.c.tenant_id, advertisers.c.advertiser_id],
            set_={
                "status": "active",
                "metadata": advertiser_metadata,
                "partition_key": record.advertiser_id,
                "partition_bucket": partition_bucket(record.advertiser_id),
                "updated_at": sa.func.now(),
            },
        )
    )
