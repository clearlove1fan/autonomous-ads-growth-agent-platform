from typing import Any
from uuid import uuid4

from pydantic import BaseModel, Field, ValidationError

from ads_growth_agent.advertiser_memory_store_factory import (
    build_configured_advertiser_memory_store,
)
from ads_growth_agent.config import Settings
from ads_growth_agent.contracts import (
    CampaignFeedbackAnalysis,
    CampaignPerformanceEventRequest,
)
from ads_growth_agent.outbox_store_factory import build_configured_outbox_store
from ads_growth_agent.persistence.advertiser_memory_store import (
    AdvertiserMemoryWriteResult,
    feedback_memory_source_id,
)
from ads_growth_agent.persistence.outbox_store import OutboxEventRecord, OutboxStore

CAMPAIGN_PERFORMANCE_ANALYZED_EVENT = "campaign_performance_analyzed"
ADVERTISER_MEMORY_HANDLER = "advertiser_memory_write"


class OutboxProcessingReport(BaseModel):
    worker_id: str = Field(min_length=1)
    claimed: int = Field(ge=0)
    completed: int = Field(ge=0)
    failed: int = Field(ge=0)
    events: list[dict[str, Any]] = Field(default_factory=list)


def enqueue_advertiser_memory_write(
    outbox_store: OutboxStore,
    event: CampaignPerformanceEventRequest,
    analysis: CampaignFeedbackAnalysis,
) -> AdvertiserMemoryWriteResult:
    source_id = feedback_memory_source_id(event)
    record = outbox_store.enqueue(
        event_type=CAMPAIGN_PERFORMANCE_ANALYZED_EVENT,
        aggregate_type="campaign_performance_event",
        aggregate_id=event.event_id,
        idempotency_key=_memory_idempotency_key(event),
        payload={
            "event": event.model_dump(mode="json"),
            "analysis": analysis.model_dump(mode="json"),
        },
        metadata={
            "handler": ADVERTISER_MEMORY_HANDLER,
            "advertiser_id": event.advertiser_id,
            "advertiser_memory_source_id": source_id,
        },
        partition_key=event.event_id,
        partition_date=event.occurred_at,
    )
    return _write_result_from_outbox_record(record, source_id=source_id)


def process_configured_outbox(
    settings: Settings,
    *,
    limit: int = 100,
    worker_id: str | None = None,
) -> OutboxProcessingReport:
    return process_outbox_events(
        build_configured_outbox_store(settings),
        build_configured_advertiser_memory_store(settings),
        limit=limit,
        worker_id=worker_id,
    )


def process_outbox_events(
    outbox_store: OutboxStore,
    advertiser_memory_store,
    *,
    limit: int = 100,
    worker_id: str | None = None,
) -> OutboxProcessingReport:
    effective_worker_id = worker_id or f"worker_{uuid4().hex[:12]}"
    claimed = outbox_store.claim_pending(limit=limit, worker_id=effective_worker_id)
    completed = 0
    failed = 0
    events: list[dict[str, Any]] = []
    for record in claimed:
        try:
            if record.event_type != CAMPAIGN_PERFORMANCE_ANALYZED_EVENT:
                raise ValueError(f"unsupported outbox event type: {record.event_type}")
            result = _handle_campaign_performance_analyzed(record, advertiser_memory_store)
        except Exception as exc:
            failed += 1
            outbox_store.mark_failed(
                record.outbox_event_id,
                error={"type": type(exc).__name__, "message": str(exc)},
            )
            events.append(
                {
                    "outbox_event_id": record.outbox_event_id,
                    "event_type": record.event_type,
                    "status": "failed",
                    "error_type": type(exc).__name__,
                }
            )
            continue

        completed += 1
        outbox_store.mark_completed(
            record.outbox_event_id,
            result=result.model_dump(mode="json"),
        )
        events.append(
            {
                "outbox_event_id": record.outbox_event_id,
                "event_type": record.event_type,
                "status": "completed",
                "advertiser_memory_source_id": result.source_id,
            }
        )

    return OutboxProcessingReport(
        worker_id=effective_worker_id,
        claimed=len(claimed),
        completed=completed,
        failed=failed,
        events=events,
    )


def _handle_campaign_performance_analyzed(
    record: OutboxEventRecord,
    advertiser_memory_store,
) -> AdvertiserMemoryWriteResult:
    try:
        event = CampaignPerformanceEventRequest.model_validate(record.payload["event"])
        analysis = CampaignFeedbackAnalysis.model_validate(record.payload["analysis"])
    except (KeyError, TypeError, ValidationError) as exc:
        raise ValueError("invalid campaign performance analyzed payload") from exc
    return advertiser_memory_store.record_feedback_memory(event, analysis)


def _write_result_from_outbox_record(
    record: OutboxEventRecord,
    *,
    source_id: str,
) -> AdvertiserMemoryWriteResult:
    if record.status == "completed":
        result = record.result_json or {}
        return AdvertiserMemoryWriteResult(
            persisted=True,
            queued=False,
            status="recorded",
            source_id=str(result.get("source_id") or source_id),
            memory_type="historical_performance",
        )
    if record.status == "failed":
        return AdvertiserMemoryWriteResult(
            persisted=False,
            queued=False,
            status="failed",
            source_id=source_id,
            memory_type="historical_performance",
        )
    return AdvertiserMemoryWriteResult(
        persisted=False,
        queued=True,
        status="queued",
        source_id=source_id,
        memory_type="historical_performance",
    )


def _memory_idempotency_key(event: CampaignPerformanceEventRequest) -> str:
    return f"advertiser-memory:{event.advertiser_id}:{event.event_id}:v1"
