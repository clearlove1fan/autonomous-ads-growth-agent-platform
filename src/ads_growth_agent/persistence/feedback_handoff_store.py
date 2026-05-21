from collections.abc import Iterator
from contextlib import contextmanager
from typing import Protocol

import sqlalchemy as sa
from sqlalchemy.engine import Connection, Engine

from ads_growth_agent.contracts import (
    CampaignFeedbackHandoffPackageResponse,
    CampaignFeedbackHandoffRecordListResponse,
    CampaignFeedbackHandoffRecordRequest,
    CampaignFeedbackHandoffRecordResponse,
    FeedbackHandoffOutcome,
)
from ads_growth_agent.feedback_handoff_record import build_feedback_handoff_record
from ads_growth_agent.persistence.partitioning import partition_bucket
from ads_growth_agent.persistence.run_store import DEFAULT_TENANT_ID
from ads_growth_agent.persistence.schema import feedback_handoff_records


class FeedbackHandoffRecordStore(Protocol):
    def record_handoff(
        self,
        handoff_package: CampaignFeedbackHandoffPackageResponse,
        request: CampaignFeedbackHandoffRecordRequest,
    ) -> CampaignFeedbackHandoffRecordResponse:
        """Persist one operator acknowledgement for a manual handoff package."""

    def get_handoff_record(
        self,
        handoff_record_id: str,
    ) -> CampaignFeedbackHandoffRecordResponse | None:
        """Return one handoff acknowledgement record for the configured tenant."""

    def list_handoff_records(
        self,
        *,
        review_id: str | None = None,
        handoff_package_id: str | None = None,
        event_id: str | None = None,
        advertiser_id: str | None = None,
        outcome: FeedbackHandoffOutcome | None = None,
        limit: int = 50,
    ) -> CampaignFeedbackHandoffRecordListResponse:
        """Return recent handoff acknowledgement records for the configured tenant."""


class NoopFeedbackHandoffRecordStore:
    def record_handoff(
        self,
        handoff_package: CampaignFeedbackHandoffPackageResponse,
        request: CampaignFeedbackHandoffRecordRequest,
    ) -> CampaignFeedbackHandoffRecordResponse:
        return build_feedback_handoff_record(handoff_package, request)

    def get_handoff_record(
        self,
        handoff_record_id: str,
    ) -> CampaignFeedbackHandoffRecordResponse | None:
        return None

    def list_handoff_records(
        self,
        *,
        review_id: str | None = None,
        handoff_package_id: str | None = None,
        event_id: str | None = None,
        advertiser_id: str | None = None,
        outcome: FeedbackHandoffOutcome | None = None,
        limit: int = 50,
    ) -> CampaignFeedbackHandoffRecordListResponse:
        return CampaignFeedbackHandoffRecordListResponse(
            items=[],
            count=0,
            limit=limit,
            review_id=review_id,
            handoff_package_id=handoff_package_id,
            event_id=event_id,
            advertiser_id=advertiser_id,
            outcome=outcome,
        )


class PostgresFeedbackHandoffRecordStore:
    def __init__(self, bind: Engine | Connection, *, tenant_id: str = DEFAULT_TENANT_ID) -> None:
        self._bind = bind
        self._tenant_id = tenant_id

    def record_handoff(
        self,
        handoff_package: CampaignFeedbackHandoffPackageResponse,
        request: CampaignFeedbackHandoffRecordRequest,
    ) -> CampaignFeedbackHandoffRecordResponse:
        record = build_feedback_handoff_record(handoff_package, request)
        with _transaction(self._bind) as connection:
            values = {
                "tenant_id": self._tenant_id,
                "handoff_record_id": record.handoff_record_id,
                "handoff_package_id": record.handoff_package_id,
                "review_id": record.review_id,
                "execution_plan_id": record.execution_plan_id,
                "latest_dry_run_id": record.latest_dry_run_id,
                "optimization_draft_id": record.optimization_draft_id,
                "event_id": record.event_id,
                "feedback_id": record.feedback_id,
                "advertiser_id": record.advertiser_id,
                "run_id": record.run_id,
                "campaign_id": record.campaign_id,
                "base_draft_id": record.base_draft_id,
                "strategy_id": record.strategy_id,
                "package_status": record.package_status,
                "outcome": record.outcome.value,
                "operator_id": record.operator_id,
                "notes": record.notes,
                "completed_step_ids": record.completed_step_ids,
                "blocked_step_ids": record.blocked_step_ids,
                "handoff_package_snapshot": handoff_package.model_dump(mode="json"),
                "record_snapshot": record.model_dump(mode="json"),
                "metadata": {"feedback_handoff_persistence": "postgres"},
                "partition_key": record.event_id,
                "partition_bucket": partition_bucket(record.event_id),
                "partition_date": record.created_at.date(),
                "created_at": record.created_at,
                "updated_at": record.created_at,
            }
            connection.execute(feedback_handoff_records.insert().values(values))
        return record

    def get_handoff_record(
        self,
        handoff_record_id: str,
    ) -> CampaignFeedbackHandoffRecordResponse | None:
        with _connection(self._bind) as connection:
            row = connection.execute(
                sa.select(feedback_handoff_records)
                .where(feedback_handoff_records.c.tenant_id == self._tenant_id)
                .where(feedback_handoff_records.c.handoff_record_id == handoff_record_id)
            ).mappings().one_or_none()
        if row is None:
            return None
        return _row_to_record(row)

    def list_handoff_records(
        self,
        *,
        review_id: str | None = None,
        handoff_package_id: str | None = None,
        event_id: str | None = None,
        advertiser_id: str | None = None,
        outcome: FeedbackHandoffOutcome | None = None,
        limit: int = 50,
    ) -> CampaignFeedbackHandoffRecordListResponse:
        with _connection(self._bind) as connection:
            stmt = sa.select(feedback_handoff_records).where(
                feedback_handoff_records.c.tenant_id == self._tenant_id
            )
            if review_id is not None:
                stmt = stmt.where(feedback_handoff_records.c.review_id == review_id)
            if handoff_package_id is not None:
                stmt = stmt.where(
                    feedback_handoff_records.c.handoff_package_id == handoff_package_id
                )
            if event_id is not None:
                stmt = stmt.where(feedback_handoff_records.c.event_id == event_id)
            if advertiser_id is not None:
                stmt = stmt.where(feedback_handoff_records.c.advertiser_id == advertiser_id)
            if outcome is not None:
                stmt = stmt.where(feedback_handoff_records.c.outcome == outcome.value)

            rows = (
                connection.execute(
                    stmt.order_by(
                        feedback_handoff_records.c.created_at.desc(),
                        feedback_handoff_records.c.handoff_record_id.desc(),
                    ).limit(limit)
                )
                .mappings()
                .all()
            )

        items = [_row_to_record(row) for row in rows]
        return CampaignFeedbackHandoffRecordListResponse(
            items=items,
            count=len(items),
            limit=limit,
            review_id=review_id,
            handoff_package_id=handoff_package_id,
            event_id=event_id,
            advertiser_id=advertiser_id,
            outcome=outcome,
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


def _row_to_record(row) -> CampaignFeedbackHandoffRecordResponse:
    return CampaignFeedbackHandoffRecordResponse.model_validate(row["record_snapshot"])
