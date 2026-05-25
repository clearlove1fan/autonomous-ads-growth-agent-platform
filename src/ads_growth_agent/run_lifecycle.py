from dataclasses import dataclass
from typing import Any

from pydantic import ValidationError

from ads_growth_agent.contracts import (
    AdvertiserBrief,
    AgentRunDetailResponse,
    CampaignObjective,
    GrowthStrategyRequest,
)
from ads_growth_agent.graph import strategy_id_for_brief


@dataclass(frozen=True)
class RunLifecycleError(ValueError):
    message: str
    error_code: str
    run_id: str
    status: str | None = None
    strategy_id: str | None = None
    advertiser_id: str | None = None
    objective: CampaignObjective | None = None

    def __str__(self) -> str:
        return self.message

    def detail(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "message": self.message,
            "error_code": self.error_code,
            "run_id": self.run_id,
        }
        if self.status is not None:
            payload["status"] = self.status
        if self.strategy_id is not None:
            payload["strategy_id"] = self.strategy_id
        if self.advertiser_id is not None:
            payload["advertiser_id"] = self.advertiser_id
        if self.objective is not None:
            payload["objective"] = self.objective.value
        return payload


def brief_from_run_metadata(run: AgentRunDetailResponse) -> AdvertiserBrief:
    brief_json = run.metadata.get("advertiser_brief")
    if not isinstance(brief_json, dict):
        raise RunLifecycleError(
            "Run does not contain a stored advertiser brief.",
            error_code="RUN_BRIEF_NOT_AVAILABLE",
            run_id=run.run_id,
        )
    try:
        return AdvertiserBrief.model_validate(brief_json)
    except ValidationError as exc:
        raise RunLifecycleError(
            "Stored advertiser brief is no longer valid.",
            error_code="RUN_BRIEF_NOT_AVAILABLE",
            run_id=run.run_id,
        ) from exc


def resumable_brief_from_run(run: AgentRunDetailResponse) -> AdvertiserBrief:
    if run.status == "completed":
        raise RunLifecycleError(
            "Completed runs cannot be resumed.",
            error_code="RUN_NOT_RESUMABLE",
            run_id=run.run_id,
            status=run.status,
        )

    brief = brief_from_run_metadata(run)
    if strategy_id_for_brief(brief) != run.strategy_id:
        raise RunLifecycleError(
            "Stored run brief does not match the original strategy identity.",
            error_code="RUN_BRIEF_MISMATCH",
            run_id=run.run_id,
            strategy_id=run.strategy_id,
        )
    return brief


def validate_retry_request_for_run(
    run: AgentRunDetailResponse,
    request: GrowthStrategyRequest,
) -> None:
    if run.status != "failed":
        raise RunLifecycleError(
            "Only failed runs can be retried.",
            error_code="RUN_NOT_RETRYABLE",
            run_id=run.run_id,
            status=run.status,
        )
    if request.brief.advertiser_id != run.advertiser_id or request.brief.objective != run.objective:
        raise RunLifecycleError(
            "Retry brief must match the original run advertiser and objective.",
            error_code="RETRY_BRIEF_MISMATCH",
            run_id=run.run_id,
            advertiser_id=run.advertiser_id,
            objective=run.objective,
        )
