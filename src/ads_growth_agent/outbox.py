from datetime import UTC, datetime
from typing import Any
from uuid import NAMESPACE_URL, uuid4, uuid5

from pydantic import BaseModel, Field, ValidationError

from ads_growth_agent.advertiser_memory_store_factory import (
    build_configured_advertiser_memory_store,
)
from ads_growth_agent.config import Settings
from ads_growth_agent.contracts import (
    CampaignFeedbackAnalysis,
    CampaignFeedbackHandoffRecordResponse,
    CampaignPerformanceEventRequest,
)
from ads_growth_agent.outbox_store_factory import build_configured_outbox_store
from ads_growth_agent.persistence.advertiser_memory_store import (
    AdvertiserMemoryUsageResult,
    AdvertiserMemoryWriteResult,
    feedback_memory_source_id,
    handoff_memory_source_id,
)
from ads_growth_agent.persistence.outbox_store import OutboxEventRecord, OutboxStore

CAMPAIGN_PERFORMANCE_ANALYZED_EVENT = "campaign_performance_analyzed"
FEEDBACK_HANDOFF_RECORDED_EVENT = "feedback_handoff_recorded"
ADVERTISER_MEMORY_RETRIEVED_EVENT = "advertiser_memory_retrieved"
ADVERTISER_MEMORY_HANDLER = "advertiser_memory_write"
HANDOFF_MEMORY_HANDLER = "feedback_handoff_memory_write"
ADVERTISER_MEMORY_USAGE_HANDLER = "advertiser_memory_usage"


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


def enqueue_handoff_memory_write(
    outbox_store: OutboxStore,
    record: CampaignFeedbackHandoffRecordResponse,
) -> AdvertiserMemoryWriteResult:
    source_id = handoff_memory_source_id(record)
    outbox_record = outbox_store.enqueue(
        event_type=FEEDBACK_HANDOFF_RECORDED_EVENT,
        aggregate_type="feedback_handoff_record",
        aggregate_id=record.handoff_record_id,
        idempotency_key=_handoff_memory_idempotency_key(record),
        payload={"handoff_record": record.model_dump(mode="json")},
        metadata={
            "handler": HANDOFF_MEMORY_HANDLER,
            "advertiser_id": record.advertiser_id,
            "advertiser_memory_source_id": source_id,
            "handoff_outcome": record.outcome.value,
        },
        partition_key=record.handoff_record_id,
        partition_date=record.created_at,
    )
    return _write_result_from_outbox_record(outbox_record, source_id=source_id)


def enqueue_advertiser_memory_retrieved(
    outbox_store: OutboxStore,
    *,
    source_id: str,
    advertiser_id: str,
    run_id: str | None,
    query: str,
    relevance: float,
    retrieved_at: datetime | None = None,
) -> OutboxEventRecord:
    effective_retrieved_at = retrieved_at or datetime.now(UTC)
    return outbox_store.enqueue(
        event_type=ADVERTISER_MEMORY_RETRIEVED_EVENT,
        aggregate_type="advertiser_memory",
        aggregate_id=source_id,
        idempotency_key=_usage_idempotency_key(run_id=run_id, source_id=source_id),
        payload={
            "source_id": source_id,
            "advertiser_id": advertiser_id,
            "run_id": run_id,
            "query": query,
            "relevance": relevance,
            "retrieved_at": effective_retrieved_at.isoformat(),
        },
        metadata={
            "handler": ADVERTISER_MEMORY_USAGE_HANDLER,
            "advertiser_id": advertiser_id,
            "run_id": run_id,
        },
        partition_key=source_id,
        partition_date=effective_retrieved_at,
    )


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
            if record.event_type == CAMPAIGN_PERFORMANCE_ANALYZED_EVENT:
                result = _handle_campaign_performance_analyzed(record, advertiser_memory_store)
            elif record.event_type == FEEDBACK_HANDOFF_RECORDED_EVENT:
                result = _handle_feedback_handoff_recorded(record, advertiser_memory_store)
            elif record.event_type == ADVERTISER_MEMORY_RETRIEVED_EVENT:
                result = _handle_advertiser_memory_retrieved(record, advertiser_memory_store)
            else:
                raise ValueError(f"unsupported outbox event type: {record.event_type}")
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
                "advertiser_memory_source_id": getattr(result, "source_id", None),
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


def _handle_feedback_handoff_recorded(
    record: OutboxEventRecord,
    advertiser_memory_store,
) -> AdvertiserMemoryWriteResult:
    try:
        handoff_record = CampaignFeedbackHandoffRecordResponse.model_validate(
            record.payload["handoff_record"]
        )
    except (KeyError, TypeError, ValidationError) as exc:
        raise ValueError("invalid feedback handoff recorded payload") from exc
    return advertiser_memory_store.record_handoff_memory(handoff_record)


def _handle_advertiser_memory_retrieved(
    record: OutboxEventRecord,
    advertiser_memory_store,
) -> AdvertiserMemoryUsageResult:
    try:
        source_id = str(record.payload["source_id"])
        retrieved_at = datetime.fromisoformat(str(record.payload["retrieved_at"]))
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError("invalid advertiser memory retrieved payload") from exc
    return advertiser_memory_store.record_retrieval_usage(
        source_id=source_id,
        retrieved_at=retrieved_at,
    )


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


def _handoff_memory_idempotency_key(record: CampaignFeedbackHandoffRecordResponse) -> str:
    return f"advertiser-memory:handoff:{record.advertiser_id}:{record.handoff_record_id}:v1"


def _usage_idempotency_key(*, run_id: str | None, source_id: str) -> str:
    run_component = run_id or f"ad-hoc:{uuid4().hex}"
    fingerprint = uuid5(NAMESPACE_URL, f"{run_component}:{source_id}").hex[:20]
    return f"advertiser-memory-usage:{fingerprint}:v1"
