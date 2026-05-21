from ads_growth_agent.config import Settings
from ads_growth_agent.contracts import CampaignFeedbackHandoffRecordResponse
from ads_growth_agent.outbox import enqueue_handoff_memory_write
from ads_growth_agent.persistence.advertiser_memory_store import (
    AdvertiserMemoryStore,
    AdvertiserMemoryWriteResult,
)
from ads_growth_agent.persistence.outbox_store import OutboxStore


def schedule_or_record_handoff_memory(
    settings: Settings,
    record: CampaignFeedbackHandoffRecordResponse,
    *,
    memory_store: AdvertiserMemoryStore,
    outbox_store: OutboxStore,
) -> AdvertiserMemoryWriteResult:
    if settings.advertiser_memory_persistence_backend == "none":
        return AdvertiserMemoryWriteResult(persisted=False, status="disabled")
    if settings.outbox_backend == "postgres":
        return enqueue_handoff_memory_write(outbox_store, record)
    return memory_store.record_handoff_memory(record)
