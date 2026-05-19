from collections.abc import Iterator
from contextlib import contextmanager
from typing import Protocol

import sqlalchemy as sa
from sqlalchemy.engine import Connection, Engine

from ads_growth_agent.contracts import (
    CampaignFeedbackOptimizationDraftResponse,
    CampaignFeedbackOptimizationReviewListResponse,
    CampaignFeedbackOptimizationReviewRequest,
    CampaignFeedbackOptimizationReviewResponse,
    FeedbackOptimizationReviewDecision,
)
from ads_growth_agent.feedback import build_campaign_feedback_optimization_review
from ads_growth_agent.persistence.partitioning import partition_bucket
from ads_growth_agent.persistence.run_store import DEFAULT_TENANT_ID
from ads_growth_agent.persistence.schema import feedback_optimization_reviews


class FeedbackOptimizationReviewStore(Protocol):
    def record_review(
        self,
        optimization_draft: CampaignFeedbackOptimizationDraftResponse,
        request: CampaignFeedbackOptimizationReviewRequest,
    ) -> CampaignFeedbackOptimizationReviewResponse:
        """Persist one human review decision for an optimization draft."""

    def get_review(
        self,
        review_id: str,
    ) -> CampaignFeedbackOptimizationReviewResponse | None:
        """Return one persisted optimization review for the configured tenant."""

    def list_reviews(
        self,
        *,
        event_id: str | None = None,
        advertiser_id: str | None = None,
        optimization_draft_id: str | None = None,
        decision: FeedbackOptimizationReviewDecision | None = None,
        limit: int = 50,
    ) -> CampaignFeedbackOptimizationReviewListResponse:
        """Return recent optimization reviews for the configured tenant."""


class NoopFeedbackOptimizationReviewStore:
    def record_review(
        self,
        optimization_draft: CampaignFeedbackOptimizationDraftResponse,
        request: CampaignFeedbackOptimizationReviewRequest,
    ) -> CampaignFeedbackOptimizationReviewResponse:
        raise RuntimeError("feedback optimization review persistence is disabled")

    def get_review(
        self,
        review_id: str,
    ) -> CampaignFeedbackOptimizationReviewResponse | None:
        return None

    def list_reviews(
        self,
        *,
        event_id: str | None = None,
        advertiser_id: str | None = None,
        optimization_draft_id: str | None = None,
        decision: FeedbackOptimizationReviewDecision | None = None,
        limit: int = 50,
    ) -> CampaignFeedbackOptimizationReviewListResponse:
        return CampaignFeedbackOptimizationReviewListResponse(
            items=[],
            count=0,
            limit=limit,
            event_id=event_id,
            advertiser_id=advertiser_id,
            optimization_draft_id=optimization_draft_id,
            decision=decision,
        )


class PostgresFeedbackOptimizationReviewStore:
    def __init__(self, bind: Engine | Connection, *, tenant_id: str = DEFAULT_TENANT_ID) -> None:
        self._bind = bind
        self._tenant_id = tenant_id

    def record_review(
        self,
        optimization_draft: CampaignFeedbackOptimizationDraftResponse,
        request: CampaignFeedbackOptimizationReviewRequest,
    ) -> CampaignFeedbackOptimizationReviewResponse:
        review = build_campaign_feedback_optimization_review(optimization_draft, request)
        with _transaction(self._bind) as connection:
            values = {
                "tenant_id": self._tenant_id,
                "review_id": review.review_id,
                "optimization_draft_id": review.optimization_draft_id,
                "event_id": review.event_id,
                "feedback_id": review.feedback_id,
                "advertiser_id": review.advertiser_id,
                "run_id": review.run_id,
                "campaign_id": review.campaign_id,
                "base_draft_id": review.base_draft_id,
                "strategy_id": review.strategy_id,
                "decision": review.decision.value,
                "reviewer_id": review.reviewer_id,
                "notes": review.notes,
                "selected_change_ids": review.selected_change_ids,
                "draft_snapshot": review.optimization_draft.model_dump(mode="json"),
                "metadata": {"review_persistence": "postgres"},
                "partition_key": review.event_id,
                "partition_bucket": partition_bucket(review.event_id),
                "partition_date": review.created_at.date(),
                "created_at": review.created_at,
                "updated_at": review.created_at,
            }
            connection.execute(feedback_optimization_reviews.insert().values(values))
        return review

    def get_review(
        self,
        review_id: str,
    ) -> CampaignFeedbackOptimizationReviewResponse | None:
        with _connection(self._bind) as connection:
            row = connection.execute(
                sa.select(feedback_optimization_reviews)
                .where(feedback_optimization_reviews.c.tenant_id == self._tenant_id)
                .where(feedback_optimization_reviews.c.review_id == review_id)
            ).mappings().one_or_none()
        if row is None:
            return None
        return _row_to_review(row)

    def list_reviews(
        self,
        *,
        event_id: str | None = None,
        advertiser_id: str | None = None,
        optimization_draft_id: str | None = None,
        decision: FeedbackOptimizationReviewDecision | None = None,
        limit: int = 50,
    ) -> CampaignFeedbackOptimizationReviewListResponse:
        with _connection(self._bind) as connection:
            stmt = sa.select(feedback_optimization_reviews).where(
                feedback_optimization_reviews.c.tenant_id == self._tenant_id
            )
            if event_id is not None:
                stmt = stmt.where(feedback_optimization_reviews.c.event_id == event_id)
            if advertiser_id is not None:
                stmt = stmt.where(
                    feedback_optimization_reviews.c.advertiser_id == advertiser_id
                )
            if optimization_draft_id is not None:
                stmt = stmt.where(
                    feedback_optimization_reviews.c.optimization_draft_id
                    == optimization_draft_id
                )
            if decision is not None:
                stmt = stmt.where(feedback_optimization_reviews.c.decision == decision.value)

            rows = (
                connection.execute(
                    stmt.order_by(
                        feedback_optimization_reviews.c.created_at.desc(),
                        feedback_optimization_reviews.c.review_id.desc(),
                    ).limit(limit)
                )
                .mappings()
                .all()
            )

        items = [_row_to_review(row) for row in rows]
        return CampaignFeedbackOptimizationReviewListResponse(
            items=items,
            count=len(items),
            limit=limit,
            event_id=event_id,
            advertiser_id=advertiser_id,
            optimization_draft_id=optimization_draft_id,
            decision=decision,
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


def _row_to_review(row) -> CampaignFeedbackOptimizationReviewResponse:
    return CampaignFeedbackOptimizationReviewResponse(
        review_id=row["review_id"],
        optimization_draft_id=row["optimization_draft_id"],
        event_id=row["event_id"],
        feedback_id=row["feedback_id"],
        advertiser_id=row["advertiser_id"],
        run_id=row["run_id"],
        campaign_id=row["campaign_id"],
        base_draft_id=row["base_draft_id"],
        strategy_id=row["strategy_id"],
        decision=row["decision"],
        reviewer_id=row["reviewer_id"],
        notes=row["notes"],
        selected_change_ids=list(row["selected_change_ids"] or []),
        optimization_draft=CampaignFeedbackOptimizationDraftResponse.model_validate(
            row["draft_snapshot"]
        ),
        created_at=row["created_at"],
    )
