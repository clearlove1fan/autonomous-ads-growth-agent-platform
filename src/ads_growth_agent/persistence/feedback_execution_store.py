from collections.abc import Iterator
from contextlib import contextmanager
from typing import Literal, Protocol

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.engine import Connection, Engine

from ads_growth_agent.contracts import (
    CampaignFeedbackExecutionDryRunListResponse,
    CampaignFeedbackExecutionDryRunResponse,
    CampaignFeedbackExecutionPlanResponse,
)
from ads_growth_agent.persistence.partitioning import partition_bucket
from ads_growth_agent.persistence.run_store import DEFAULT_TENANT_ID
from ads_growth_agent.persistence.schema import feedback_execution_dry_runs

FeedbackExecutionDryRunStatus = Literal["passed", "failed"]


class FeedbackExecutionDryRunStore(Protocol):
    def record_dry_run(
        self,
        execution_plan: CampaignFeedbackExecutionPlanResponse,
        dry_run: CampaignFeedbackExecutionDryRunResponse,
    ) -> CampaignFeedbackExecutionDryRunResponse:
        """Persist the latest dry-run validation snapshot for one execution plan."""

    def get_dry_run(self, dry_run_id: str) -> CampaignFeedbackExecutionDryRunResponse | None:
        """Return one persisted dry-run validation result for the configured tenant."""

    def list_dry_runs(
        self,
        *,
        review_id: str | None = None,
        execution_plan_id: str | None = None,
        event_id: str | None = None,
        advertiser_id: str | None = None,
        status: FeedbackExecutionDryRunStatus | None = None,
        limit: int = 50,
    ) -> CampaignFeedbackExecutionDryRunListResponse:
        """Return recent persisted dry-run validation results for the configured tenant."""


class NoopFeedbackExecutionDryRunStore:
    def record_dry_run(
        self,
        execution_plan: CampaignFeedbackExecutionPlanResponse,
        dry_run: CampaignFeedbackExecutionDryRunResponse,
    ) -> CampaignFeedbackExecutionDryRunResponse:
        return dry_run

    def get_dry_run(self, dry_run_id: str) -> CampaignFeedbackExecutionDryRunResponse | None:
        return None

    def list_dry_runs(
        self,
        *,
        review_id: str | None = None,
        execution_plan_id: str | None = None,
        event_id: str | None = None,
        advertiser_id: str | None = None,
        status: FeedbackExecutionDryRunStatus | None = None,
        limit: int = 50,
    ) -> CampaignFeedbackExecutionDryRunListResponse:
        return CampaignFeedbackExecutionDryRunListResponse(
            items=[],
            count=0,
            limit=limit,
            review_id=review_id,
            execution_plan_id=execution_plan_id,
            event_id=event_id,
            advertiser_id=advertiser_id,
            status=status,
        )


class PostgresFeedbackExecutionDryRunStore:
    def __init__(self, bind: Engine | Connection, *, tenant_id: str = DEFAULT_TENANT_ID) -> None:
        self._bind = bind
        self._tenant_id = tenant_id

    def record_dry_run(
        self,
        execution_plan: CampaignFeedbackExecutionPlanResponse,
        dry_run: CampaignFeedbackExecutionDryRunResponse,
    ) -> CampaignFeedbackExecutionDryRunResponse:
        with _transaction(self._bind) as connection:
            values = {
                "tenant_id": self._tenant_id,
                "dry_run_id": dry_run.dry_run_id,
                "execution_plan_id": execution_plan.execution_plan_id,
                "review_id": execution_plan.review_id,
                "optimization_draft_id": execution_plan.optimization_draft_id,
                "event_id": execution_plan.event_id,
                "feedback_id": execution_plan.feedback_id,
                "advertiser_id": execution_plan.advertiser_id,
                "run_id": execution_plan.run_id,
                "campaign_id": execution_plan.campaign_id,
                "base_draft_id": execution_plan.base_draft_id,
                "strategy_id": execution_plan.strategy_id,
                "status": dry_run.status,
                "execution_mode": dry_run.execution_mode,
                "validated_step_count": dry_run.validated_step_count,
                "blocked_step_count": dry_run.blocked_step_count,
                "execution_plan_snapshot": execution_plan.model_dump(mode="json"),
                "dry_run_snapshot": dry_run.model_dump(mode="json"),
                "metadata": {"feedback_execution_persistence": "postgres"},
                "partition_key": execution_plan.event_id,
                "partition_bucket": partition_bucket(execution_plan.event_id),
                "partition_date": dry_run.created_at.date(),
                "created_at": dry_run.created_at,
                "updated_at": dry_run.created_at,
            }
            stmt = (
                pg_insert(feedback_execution_dry_runs)
                .values(values)
                .on_conflict_do_update(
                    index_elements=[
                        feedback_execution_dry_runs.c.tenant_id,
                        feedback_execution_dry_runs.c.dry_run_id,
                    ],
                    set_={
                        "status": values["status"],
                        "validated_step_count": values["validated_step_count"],
                        "blocked_step_count": values["blocked_step_count"],
                        "execution_plan_snapshot": values["execution_plan_snapshot"],
                        "dry_run_snapshot": values["dry_run_snapshot"],
                        "metadata": values["metadata"],
                        "partition_date": values["partition_date"],
                        "updated_at": values["updated_at"],
                    },
                )
            )
            connection.execute(stmt)
        return dry_run

    def get_dry_run(self, dry_run_id: str) -> CampaignFeedbackExecutionDryRunResponse | None:
        with _connection(self._bind) as connection:
            row = connection.execute(
                sa.select(feedback_execution_dry_runs)
                .where(feedback_execution_dry_runs.c.tenant_id == self._tenant_id)
                .where(feedback_execution_dry_runs.c.dry_run_id == dry_run_id)
            ).mappings().one_or_none()
        if row is None:
            return None
        return _row_to_dry_run(row)

    def list_dry_runs(
        self,
        *,
        review_id: str | None = None,
        execution_plan_id: str | None = None,
        event_id: str | None = None,
        advertiser_id: str | None = None,
        status: FeedbackExecutionDryRunStatus | None = None,
        limit: int = 50,
    ) -> CampaignFeedbackExecutionDryRunListResponse:
        with _connection(self._bind) as connection:
            stmt = sa.select(feedback_execution_dry_runs).where(
                feedback_execution_dry_runs.c.tenant_id == self._tenant_id
            )
            if review_id is not None:
                stmt = stmt.where(feedback_execution_dry_runs.c.review_id == review_id)
            if execution_plan_id is not None:
                stmt = stmt.where(
                    feedback_execution_dry_runs.c.execution_plan_id == execution_plan_id
                )
            if event_id is not None:
                stmt = stmt.where(feedback_execution_dry_runs.c.event_id == event_id)
            if advertiser_id is not None:
                stmt = stmt.where(
                    feedback_execution_dry_runs.c.advertiser_id == advertiser_id
                )
            if status is not None:
                stmt = stmt.where(feedback_execution_dry_runs.c.status == status)

            rows = (
                connection.execute(
                    stmt.order_by(
                        feedback_execution_dry_runs.c.created_at.desc(),
                        feedback_execution_dry_runs.c.dry_run_id.desc(),
                    ).limit(limit)
                )
                .mappings()
                .all()
            )

        items = [_row_to_dry_run(row) for row in rows]
        return CampaignFeedbackExecutionDryRunListResponse(
            items=items,
            count=len(items),
            limit=limit,
            review_id=review_id,
            execution_plan_id=execution_plan_id,
            event_id=event_id,
            advertiser_id=advertiser_id,
            status=status,
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


def _row_to_dry_run(row) -> CampaignFeedbackExecutionDryRunResponse:
    return CampaignFeedbackExecutionDryRunResponse.model_validate(row["dry_run_snapshot"])
