import hashlib
import json
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Protocol

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.engine import Connection, Engine

from ads_growth_agent.contracts import (
    CampaignFeedbackAnalysis,
    CampaignPerformanceEventDetailResponse,
    CampaignPerformanceEventRequest,
    PerformanceMetrics,
)
from ads_growth_agent.persistence.partitioning import partition_bucket
from ads_growth_agent.persistence.run_store import DEFAULT_TENANT_ID
from ads_growth_agent.persistence.schema import (
    advertisers,
    campaign_performance_events,
    tenants,
)


class PerformanceEventConflictError(Exception):
    def __init__(self, event_id: str) -> None:
        super().__init__(f"Performance event ID was already used: {event_id}")
        self.event_id = event_id


class CampaignPerformanceEventStore(Protocol):
    def record_analyzed(
        self,
        event: CampaignPerformanceEventRequest,
        analysis: CampaignFeedbackAnalysis,
    ) -> None:
        """Persist a performance event and its analysis."""

    def get_event(self, event_id: str) -> CampaignPerformanceEventDetailResponse | None:
        """Return a persisted performance event for the configured tenant."""


class NoopCampaignPerformanceEventStore:
    def record_analyzed(
        self,
        event: CampaignPerformanceEventRequest,
        analysis: CampaignFeedbackAnalysis,
    ) -> None:
        return None

    def get_event(self, event_id: str) -> CampaignPerformanceEventDetailResponse | None:
        return None


class PostgresCampaignPerformanceEventStore:
    def __init__(self, bind: Engine | Connection, *, tenant_id: str = DEFAULT_TENANT_ID) -> None:
        self._bind = bind
        self._tenant_id = tenant_id

    def record_analyzed(
        self,
        event: CampaignPerformanceEventRequest,
        analysis: CampaignFeedbackAnalysis,
    ) -> None:
        with _transaction(self._bind) as connection:
            event_hash = hash_campaign_performance_event(event)
            existing = _get_event_row_for_update(
                connection,
                event.event_id,
                tenant_id=self._tenant_id,
            )
            if existing is not None:
                existing_hash = dict(existing["metadata"] or {}).get("event_hash")
                if existing_hash != event_hash:
                    raise PerformanceEventConflictError(event.event_id)
                return None

            _upsert_tenant_and_advertiser_from_event(
                connection,
                event,
                tenant_id=self._tenant_id,
            )
            values = {
                "tenant_id": self._tenant_id,
                "event_id": event.event_id,
                "advertiser_id": event.advertiser_id,
                "run_id": event.run_id,
                "campaign_id": event.campaign_id,
                "draft_id": event.draft_id,
                "objective": event.objective.value,
                "event_type": event.event_type.value,
                "occurred_at": event.occurred_at,
                "metrics_json": event.metrics.model_dump(mode="json"),
                "analysis_json": analysis.model_dump(mode="json"),
                "status": "analyzed",
                "metadata": {
                    "event_hash": event_hash,
                    "target_cpa": str(event.target_cpa) if event.target_cpa else None,
                    "attribution_window_days": event.attribution_window_days,
                    "notes": event.notes,
                    "performance_event_persistence": "postgres",
                },
                "partition_key": event.event_id,
                "partition_bucket": partition_bucket(event.event_id),
                "partition_date": event.occurred_at.date(),
            }
            stmt = (
                pg_insert(campaign_performance_events)
                .values(values)
                .on_conflict_do_update(
                    index_elements=[
                        campaign_performance_events.c.tenant_id,
                        campaign_performance_events.c.event_id,
                    ],
                    set_={
                        "advertiser_id": values["advertiser_id"],
                        "run_id": values["run_id"],
                        "campaign_id": values["campaign_id"],
                        "draft_id": values["draft_id"],
                        "objective": values["objective"],
                        "event_type": values["event_type"],
                        "occurred_at": values["occurred_at"],
                        "metrics_json": values["metrics_json"],
                        "analysis_json": values["analysis_json"],
                        "status": values["status"],
                        "metadata": values["metadata"],
                        "partition_key": values["partition_key"],
                        "partition_bucket": values["partition_bucket"],
                        "partition_date": values["partition_date"],
                        "updated_at": sa.func.now(),
                    },
                )
            )
            connection.execute(stmt)

    def get_event(self, event_id: str) -> CampaignPerformanceEventDetailResponse | None:
        with _connection(self._bind) as connection:
            row = connection.execute(
                sa.select(campaign_performance_events)
                .where(campaign_performance_events.c.tenant_id == self._tenant_id)
                .where(campaign_performance_events.c.event_id == event_id)
            ).mappings().one_or_none()
            if row is None:
                return None

        return CampaignPerformanceEventDetailResponse(
            event_id=row["event_id"],
            advertiser_id=row["advertiser_id"],
            run_id=row["run_id"],
            campaign_id=row["campaign_id"],
            draft_id=row["draft_id"],
            objective=row["objective"],
            event_type=row["event_type"],
            occurred_at=row["occurred_at"],
            metrics=PerformanceMetrics.model_validate(row["metrics_json"]),
            status=row["status"],
            metadata=dict(row["metadata"] or {}),
            analysis=CampaignFeedbackAnalysis.model_validate(row["analysis_json"]),
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )


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


def _get_event_row_for_update(
    connection: Connection,
    event_id: str,
    *,
    tenant_id: str,
):
    return connection.execute(
        sa.select(campaign_performance_events)
        .where(campaign_performance_events.c.tenant_id == tenant_id)
        .where(campaign_performance_events.c.event_id == event_id)
        .with_for_update()
    ).mappings().one_or_none()


def hash_campaign_performance_event(event: CampaignPerformanceEventRequest) -> str:
    payload = json.dumps(
        event.model_dump(mode="json"),
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _upsert_tenant_and_advertiser_from_event(
    connection: Connection,
    event: CampaignPerformanceEventRequest,
    *,
    tenant_id: str,
) -> None:
    tenant_metadata = {"upserted_by": "performance_event_store"}
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
        "upserted_by": "performance_event_store",
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
