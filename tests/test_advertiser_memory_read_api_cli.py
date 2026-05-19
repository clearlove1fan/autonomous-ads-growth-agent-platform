import json
from datetime import UTC, datetime
from decimal import Decimal

from fastapi.testclient import TestClient
from typer.testing import CliRunner

from ads_growth_agent.api import (
    app as api_app,
)
from ads_growth_agent.api import (
    get_runtime_advertiser_memory_store,
    get_runtime_settings,
)
from ads_growth_agent.cli import app as cli_app
from ads_growth_agent.config import Settings
from ads_growth_agent.contracts import AdvertiserMemoryDetailResponse, AdvertiserMemoryType


def test_advertiser_memory_api_returns_tenant_scoped_detail_and_list() -> None:
    memory = _memory_detail()
    store = CapturingAdvertiserMemoryReadStore(memories=[memory])

    api_app.dependency_overrides[get_runtime_settings] = lambda: Settings(
        advertiser_memory_persistence_backend="postgres"
    )
    api_app.dependency_overrides[get_runtime_advertiser_memory_store] = lambda: store
    try:
        client = TestClient(api_app)
        detail = client.get(
            f"/advertisers/{memory.advertiser_id}/memories/{memory.source_id}",
            headers={"X-Tenant-ID": "tenant_memory"},
        )
        listing = client.get(
            f"/advertisers/{memory.advertiser_id}/memories",
            params={"memory_type": "historical_performance", "limit": "5"},
            headers={"X-Tenant-ID": "tenant_memory"},
        )
    finally:
        api_app.dependency_overrides.clear()

    detail_payload = detail.json()
    list_payload = listing.json()
    assert detail.status_code == 200
    assert detail.headers["x-tenant-id"] == "tenant_memory"
    assert detail_payload["source_id"] == memory.source_id
    assert detail_payload["memory_type"] == "historical_performance"
    assert detail_payload["usage_count"] == 2
    assert listing.status_code == 200
    assert list_payload["count"] == 1
    assert list_payload["limit"] == 5
    assert list_payload["advertiser_id"] == memory.advertiser_id
    assert list_payload["memory_type"] == "historical_performance"
    assert list_payload["items"][0]["source_id"] == memory.source_id
    assert store.detail_requests == [(memory.advertiser_id, memory.source_id)]
    assert store.list_requests == [
        (memory.advertiser_id, "historical_performance", 5)
    ]


def test_advertiser_memory_api_returns_404_when_missing() -> None:
    api_app.dependency_overrides[get_runtime_advertiser_memory_store] = (
        lambda: CapturingAdvertiserMemoryReadStore()
    )
    try:
        response = TestClient(api_app).get(
            "/advertisers/adv_fitness_001/memories/memory:performance:missing:v1"
        )
    finally:
        api_app.dependency_overrides.clear()

    assert response.status_code == 404
    assert response.json()["detail"]["error_code"] == "ADVERTISER_MEMORY_NOT_FOUND"


def test_get_advertiser_memory_cli_returns_detail(monkeypatch) -> None:
    memory = _memory_detail()
    store = CapturingAdvertiserMemoryReadStore(memories=[memory])

    monkeypatch.setattr(
        "ads_growth_agent.cli.get_settings",
        lambda: Settings(tenant_id="tenant_cli"),
    )
    monkeypatch.setattr(
        "ads_growth_agent.cli.build_configured_advertiser_memory_store",
        lambda settings: store,
    )

    result = CliRunner().invoke(
        cli_app,
        ["get-advertiser-memory", memory.advertiser_id, memory.source_id],
    )

    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["source_id"] == memory.source_id
    assert payload["advertiser_id"] == memory.advertiser_id
    assert payload["metadata"]["event_id"] == "evt_perf_001"


def test_get_advertiser_memory_cli_reports_missing_memory(monkeypatch) -> None:
    monkeypatch.setattr(
        "ads_growth_agent.cli.get_settings",
        lambda: Settings(tenant_id="tenant_cli"),
    )
    monkeypatch.setattr(
        "ads_growth_agent.cli.build_configured_advertiser_memory_store",
        lambda settings: CapturingAdvertiserMemoryReadStore(),
    )

    result = CliRunner().invoke(
        cli_app,
        [
            "get-advertiser-memory",
            "adv_fitness_001",
            "memory:performance:missing:v1",
        ],
    )

    assert result.exit_code == 1
    assert "Advertiser memory not found" in result.stderr


def test_list_advertiser_memories_cli_filters_by_type(monkeypatch) -> None:
    memory = _memory_detail()
    store = CapturingAdvertiserMemoryReadStore(memories=[memory])

    monkeypatch.setattr(
        "ads_growth_agent.cli.get_settings",
        lambda: Settings(tenant_id="tenant_cli"),
    )
    monkeypatch.setattr(
        "ads_growth_agent.cli.build_configured_advertiser_memory_store",
        lambda settings: store,
    )

    result = CliRunner().invoke(
        cli_app,
        [
            "list-advertiser-memories",
            memory.advertiser_id,
            "--memory-type",
            "historical_performance",
            "--limit",
            "5",
        ],
    )

    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["count"] == 1
    assert payload["limit"] == 5
    assert payload["advertiser_id"] == memory.advertiser_id
    assert payload["items"][0]["source_id"] == memory.source_id
    assert store.list_requests == [
        (memory.advertiser_id, "historical_performance", 5)
    ]


def test_list_advertiser_memories_cli_rejects_invalid_type(monkeypatch) -> None:
    monkeypatch.setattr(
        "ads_growth_agent.cli.get_settings",
        lambda: Settings(tenant_id="tenant_cli"),
    )

    result = CliRunner().invoke(
        cli_app,
        [
            "list-advertiser-memories",
            "adv_fitness_001",
            "--memory-type",
            "unknown",
        ],
    )

    assert result.exit_code == 2
    assert "Invalid advertiser memory type" in result.stderr


class CapturingAdvertiserMemoryReadStore:
    def __init__(
        self,
        *,
        memories: list[AdvertiserMemoryDetailResponse] | None = None,
    ) -> None:
        self.memories = memories or []
        self.detail_requests: list[tuple[str, str]] = []
        self.list_requests: list[tuple[str, AdvertiserMemoryType | None, int]] = []

    def get_memory(
        self,
        *,
        advertiser_id: str,
        source_id: str,
    ) -> AdvertiserMemoryDetailResponse | None:
        self.detail_requests.append((advertiser_id, source_id))
        for memory in self.memories:
            if memory.advertiser_id == advertiser_id and memory.source_id == source_id:
                return memory
        return None

    def list_memories(
        self,
        *,
        advertiser_id: str,
        memory_type: AdvertiserMemoryType | None = None,
        limit: int = 50,
    ) -> list[AdvertiserMemoryDetailResponse]:
        self.list_requests.append((advertiser_id, memory_type, limit))
        matches = [
            memory
            for memory in self.memories
            if memory.advertiser_id == advertiser_id
            and (memory_type is None or memory.memory_type == memory_type)
        ]
        return matches[:limit]


def _memory_detail() -> AdvertiserMemoryDetailResponse:
    now = datetime(2026, 5, 12, 12, 0, tzinfo=UTC)
    return AdvertiserMemoryDetailResponse(
        memory_id="5d7f6a71-3b2a-4028-9c16-91770a65f150",
        source_id="memory:performance:test:v1",
        advertiser_id="adv_fitness_001",
        memory_type="historical_performance",
        title="Performance feedback for registrations",
        content="Campaign performance feedback: observed CPA 50.00.",
        summary="underperforming registrations performance feedback",
        importance_score=Decimal("0.850"),
        usage_count=2,
        last_used_at=now,
        metadata={
            "source_id": "memory:performance:test:v1",
            "event_id": "evt_perf_001",
            "feedback_id": "feedback_test",
            "health_status": "underperforming",
        },
        created_at=now,
        updated_at=now,
    )
