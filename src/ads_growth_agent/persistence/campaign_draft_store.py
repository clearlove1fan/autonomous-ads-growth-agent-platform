from collections.abc import Iterator
from contextlib import contextmanager
from typing import Protocol

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.engine import Connection, Engine

from ads_growth_agent.contracts import (
    AdvertiserBrief,
    CampaignDraftDetailResponse,
    FinalGrowthStrategy,
    GrowthStrategyResponse,
    ToolResult,
)
from ads_growth_agent.persistence.identity import upsert_tenant_and_advertiser
from ads_growth_agent.persistence.partitioning import partition_bucket
from ads_growth_agent.persistence.schema import campaign_drafts
from ads_growth_agent.tools import CampaignDraftOutput

DEFAULT_TENANT_ID = "default"


class CampaignDraftStore(Protocol):
    def record_completed(self, brief: AdvertiserBrief, response: GrowthStrategyResponse) -> None:
        """Persist draft artifacts from a completed strategy run."""

    def get_draft(self, draft_id: str) -> CampaignDraftDetailResponse | None:
        """Return one campaign draft for the configured tenant."""

    def list_drafts(
        self,
        *,
        advertiser_id: str | None = None,
        limit: int = 50,
    ) -> list[CampaignDraftDetailResponse]:
        """Return recent campaign drafts for the configured tenant."""


class NoopCampaignDraftStore:
    def record_completed(self, brief: AdvertiserBrief, response: GrowthStrategyResponse) -> None:
        return None

    def get_draft(self, draft_id: str) -> CampaignDraftDetailResponse | None:
        return None

    def list_drafts(
        self,
        *,
        advertiser_id: str | None = None,
        limit: int = 50,
    ) -> list[CampaignDraftDetailResponse]:
        return []


class PostgresCampaignDraftStore:
    def __init__(self, bind: Engine | Connection, *, tenant_id: str = DEFAULT_TENANT_ID) -> None:
        self._bind = bind
        self._tenant_id = tenant_id

    def record_completed(self, brief: AdvertiserBrief, response: GrowthStrategyResponse) -> None:
        draft = _campaign_draft_from_tool_results(response.tool_results)
        if draft is None:
            return

        with _transaction(self._bind) as connection:
            upsert_tenant_and_advertiser(
                connection,
                brief,
                tenant_id=self._tenant_id,
                upserted_by="campaign_draft_store",
            )
            _upsert_campaign_draft(connection, brief, response, draft, tenant_id=self._tenant_id)

    def get_draft(self, draft_id: str) -> CampaignDraftDetailResponse | None:
        with _connection(self._bind) as connection:
            row = connection.execute(
                sa.select(campaign_drafts)
                .where(campaign_drafts.c.tenant_id == self._tenant_id)
                .where(campaign_drafts.c.draft_id == draft_id)
            ).mappings().one_or_none()
        if row is None:
            return None
        return _row_to_campaign_draft(row)

    def list_drafts(
        self,
        *,
        advertiser_id: str | None = None,
        limit: int = 50,
    ) -> list[CampaignDraftDetailResponse]:
        with _connection(self._bind) as connection:
            stmt = sa.select(campaign_drafts).where(
                campaign_drafts.c.tenant_id == self._tenant_id
            )
            if advertiser_id is not None:
                stmt = stmt.where(campaign_drafts.c.advertiser_id == advertiser_id)
            rows = (
                connection.execute(
                    stmt.order_by(
                        campaign_drafts.c.updated_at.desc(),
                        campaign_drafts.c.draft_id.desc(),
                    ).limit(limit)
                )
                .mappings()
                .all()
            )
        return [_row_to_campaign_draft(row) for row in rows]


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


def _campaign_draft_from_tool_results(
    tool_results: list[ToolResult],
) -> CampaignDraftOutput | None:
    for result in reversed(tool_results):
        if result.tool_name == "create_campaign_draft" and result.success:
            return CampaignDraftOutput.model_validate(result.payload)
    return None


def _upsert_campaign_draft(
    connection: Connection,
    brief: AdvertiserBrief,
    response: GrowthStrategyResponse,
    draft: CampaignDraftOutput,
    *,
    tenant_id: str,
) -> None:
    metadata = {
        "campaign_name": draft.campaign_name,
        "daily_budget": str(draft.daily_budget),
        "audience_segments": draft.audience_segments,
        "creative_angles": draft.creative_angles,
        "safety_note": draft.safety_note,
        "source_id": draft.source_id,
        "product_name": brief.product_name,
        "duration_days": brief.duration_days,
        "run_id": response.run_metadata.run_id,
        "execution_id": response.run_metadata.execution_id or response.run_metadata.run_id,
        "strategy_id": response.strategy.strategy_id,
        "draft_persistence": "postgres",
    }
    values = {
        "tenant_id": tenant_id,
        "draft_id": draft.draft_id,
        "advertiser_id": brief.advertiser_id,
        "objective": draft.objective.value,
        "status": draft.status,
        "budget": draft.total_budget,
        "currency": brief.currency,
        "strategy_json": response.strategy.model_dump(mode="json"),
        "created_by_run_id": response.run_metadata.run_id,
        "metadata": metadata,
        "partition_key": brief.advertiser_id,
        "partition_bucket": partition_bucket(brief.advertiser_id),
    }
    stmt = (
        pg_insert(campaign_drafts)
        .values(values)
        .on_conflict_do_update(
            index_elements=[campaign_drafts.c.tenant_id, campaign_drafts.c.draft_id],
            set_={
                "advertiser_id": values["advertiser_id"],
                "objective": values["objective"],
                "status": values["status"],
                "budget": values["budget"],
                "currency": values["currency"],
                "strategy_json": values["strategy_json"],
                "created_by_run_id": values["created_by_run_id"],
                "metadata": values["metadata"],
                "partition_key": values["partition_key"],
                "partition_bucket": values["partition_bucket"],
                "updated_at": sa.func.now(),
            },
        )
    )
    connection.execute(stmt)


def _row_to_campaign_draft(row) -> CampaignDraftDetailResponse:
    metadata = dict(row["metadata"] or {})
    return CampaignDraftDetailResponse(
        draft_id=row["draft_id"],
        advertiser_id=row["advertiser_id"],
        objective=row["objective"],
        status=row["status"],
        budget=row["budget"],
        currency=row["currency"],
        campaign_name=metadata.get("campaign_name"),
        daily_budget=metadata.get("daily_budget"),
        safety_note=metadata.get("safety_note"),
        created_by_run_id=row["created_by_run_id"],
        strategy=FinalGrowthStrategy.model_validate(row["strategy_json"]),
        metadata=metadata,
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )
