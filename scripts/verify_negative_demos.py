#!/usr/bin/env python3
# ruff: noqa: E402, I001
"""Run and validate curated negative demo cases for Phase 1.5."""

from __future__ import annotations

import json
import logging
import sys
from decimal import Decimal
from pathlib import Path
from typing import Any

import httpx
from fastapi.testclient import TestClient

REPO_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = REPO_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from ads_growth_agent.api import (  # noqa: E402
    app as api_app,
    get_runtime_idempotency_store,
    get_runtime_performance_event_store,
    get_runtime_settings,
)
from ads_growth_agent.config import Settings  # noqa: E402
from ads_growth_agent.contracts import (  # noqa: E402
    AdvertiserBrief,
    CampaignPerformanceEventDetailResponse,
    CampaignPerformanceEventRequest,
)
from ads_growth_agent.feedback import analyze_campaign_performance_event  # noqa: E402
from ads_growth_agent.graph import StrategyGenerationError, run_growth_strategy_graph  # noqa: E402
from ads_growth_agent.llm import LiteLLMGatewayClient  # noqa: E402
from ads_growth_agent.persistence.idempotency_store import (  # noqa: E402
    IdempotencyConflictError,
)

logging.getLogger("ads_growth_agent").disabled = True


class NegativeDemoVerificationError(Exception):
    """Raised when a negative demo case fails its contract."""

    def __init__(self, issues: list[str]) -> None:
        super().__init__("\n".join(issues))
        self.issues = issues


class ConflictingIdempotencyStore:
    def begin(self, key: str, request_hash: str, *, ttl_seconds: int):
        raise IdempotencyConflictError(
            "IDEMPOTENCY_KEY_REUSED",
            "Idempotency key was already used with a different request body.",
        )

    def mark_completed(self, *args: Any, **kwargs: Any) -> None:
        raise AssertionError("conflicting idempotency request must not complete")

    def mark_failed(self, *args: Any, **kwargs: Any) -> None:
        raise AssertionError("conflicting idempotency request must not mark failed")


class ConflictingPerformanceEventStore:
    def __init__(self) -> None:
        event = CampaignPerformanceEventRequest.model_validate(_performance_event_payload())
        analysis = analyze_campaign_performance_event(event)
        self.detail = CampaignPerformanceEventDetailResponse(
            event_id=event.event_id,
            advertiser_id=event.advertiser_id,
            run_id=event.run_id,
            campaign_id=event.campaign_id,
            draft_id=event.draft_id,
            objective=event.objective,
            event_type=event.event_type,
            occurred_at=event.occurred_at,
            metrics=event.metrics,
            status="analyzed",
            metadata={"event_hash": "different_event_hash"},
            analysis=analysis,
            created_at=analysis.created_at,
            updated_at=analysis.created_at,
        )
        self.requested_event_ids: list[str] = []

    def get_event(self, event_id: str) -> CampaignPerformanceEventDetailResponse | None:
        self.requested_event_ids.append(event_id)
        if event_id == self.detail.event_id:
            return self.detail
        return None

    def record_analyzed(self, event, analysis) -> None:
        raise AssertionError("conflicting performance event must not be recorded")


def run_safe_failure_demo() -> dict[str, Any]:
    settings = Settings(
        litellm_base_url="http://llm.local",
        litellm_api_key="test-key",
        default_chat_model="test-model",
        use_llm_planner=True,
        llm_structured_output_max_repair_attempts=0,
        langsmith_tracing=False,
    )
    payload = _planner_payload(_brief())
    payload["tool_intents"][2]["tool_name"] = "launch_campaign"

    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=_completion(payload))

    client = LiteLLMGatewayClient(
        settings=settings,
        http_client=httpx.Client(transport=httpx.MockTransport(handler)),
    )
    try:
        run_growth_strategy_graph(_brief(), settings=settings, llm_client=client)
    except StrategyGenerationError as exc:
        error = exc.tool_result.error
        return {
            "case": "safe_failure_invalid_llm_planner_tool",
            "status": "blocked",
            "tool_name": exc.tool_result.tool_name,
            "error_code": error.code if error else None,
            "node_path": exc.run_metadata.node_path if exc.run_metadata else [],
            "tool_count": exc.run_metadata.tool_count if exc.run_metadata else None,
            "failed_tool_count": (
                exc.run_metadata.failed_tool_count if exc.run_metadata else None
            ),
        }

    raise NegativeDemoVerificationError(
        ["invalid LLM planner output unexpectedly completed successfully"]
    )


def run_idempotency_conflict_demo() -> dict[str, Any]:
    api_app.dependency_overrides[get_runtime_settings] = lambda: Settings(
        idempotency_backend="postgres"
    )
    api_app.dependency_overrides[get_runtime_idempotency_store] = (
        lambda: ConflictingIdempotencyStore()
    )
    try:
        response = TestClient(api_app).post(
            "/growth-strategies",
            json={"brief": _brief_payload()},
            headers={"Idempotency-Key": "demo-conflict-key"},
        )
    finally:
        api_app.dependency_overrides.clear()

    payload = response.json()
    detail = payload.get("detail", {})
    return {
        "case": "idempotency_key_reused",
        "status_code": response.status_code,
        "error_code": detail.get("error_code"),
        "message": detail.get("message"),
    }


def run_performance_event_conflict_demo() -> dict[str, Any]:
    store = ConflictingPerformanceEventStore()
    api_app.dependency_overrides[get_runtime_settings] = lambda: Settings(
        performance_event_persistence_backend="postgres"
    )
    api_app.dependency_overrides[get_runtime_performance_event_store] = lambda: store
    try:
        response = TestClient(api_app).post(
            "/campaign-events/performance",
            json=_performance_event_payload(),
        )
    finally:
        api_app.dependency_overrides.clear()

    payload = response.json()
    detail = payload.get("detail", {})
    return {
        "case": "performance_event_id_conflict",
        "status_code": response.status_code,
        "error_code": detail.get("error_code"),
        "event_id": detail.get("event_id"),
        "requested_event_ids": store.requested_event_ids,
    }


def validate_summary(summary: dict[str, Any]) -> None:
    issues: list[str] = []
    safe_failure = summary["safe_failure"]
    idempotency = summary["idempotency_conflict"]
    performance_event = summary["performance_event_conflict"]

    _expect(
        safe_failure["status"] == "blocked",
        issues,
        "safe failure demo should block invalid planner output",
    )
    _expect(
        safe_failure["tool_name"] == "llm_planner",
        issues,
        "safe failure should be attributed to llm_planner",
    )
    _expect(
        safe_failure["error_code"] == "LLM_PLANNER_INVALID_TOOL_PLAN",
        issues,
        "safe failure should return LLM_PLANNER_INVALID_TOOL_PLAN",
    )
    _expect(
        safe_failure["node_path"] == ["planner"],
        issues,
        "safe failure should stop before tool execution",
    )
    _expect(
        safe_failure["failed_tool_count"] == 1,
        issues,
        "safe failure should record one failed planner step",
    )
    _expect(
        idempotency["status_code"] == 409,
        issues,
        "idempotency conflict should return HTTP 409",
    )
    _expect(
        idempotency["error_code"] == "IDEMPOTENCY_KEY_REUSED",
        issues,
        "idempotency conflict should return IDEMPOTENCY_KEY_REUSED",
    )
    _expect(
        performance_event["status_code"] == 409,
        issues,
        "performance event conflict should return HTTP 409",
    )
    _expect(
        performance_event["error_code"] == "PERFORMANCE_EVENT_ID_CONFLICT",
        issues,
        "performance event conflict should return PERFORMANCE_EVENT_ID_CONFLICT",
    )

    if issues:
        raise NegativeDemoVerificationError(issues)


def render_summary(summary: dict[str, Any]) -> str:
    safe_failure = summary["safe_failure"]
    idempotency = summary["idempotency_conflict"]
    performance_event = summary["performance_event_conflict"]
    return "\n".join(
        [
            "Negative demo verification passed",
            (
                "Safe failure: "
                f"{safe_failure['tool_name']} rejected invalid tool plan "
                f"with {safe_failure['error_code']}; "
                f"node_path={' -> '.join(safe_failure['node_path'])}"
            ),
            (
                "Idempotency conflict: "
                f"HTTP {idempotency['status_code']} {idempotency['error_code']}"
            ),
            (
                "Performance event conflict: "
                f"HTTP {performance_event['status_code']} "
                f"{performance_event['error_code']} for "
                f"{performance_event['event_id']}"
            ),
            (
                "Result: unsafe or conflicting requests return structured errors "
                "and do not execute actions."
            ),
        ]
    )


def build_summary() -> dict[str, Any]:
    summary = {
        "safe_failure": run_safe_failure_demo(),
        "idempotency_conflict": run_idempotency_conflict_demo(),
        "performance_event_conflict": run_performance_event_conflict_demo(),
    }
    validate_summary(summary)
    return summary


def _brief() -> AdvertiserBrief:
    return AdvertiserBrief.model_validate(_brief_payload())


def _brief_payload() -> dict[str, object]:
    return {
        "advertiser_id": "adv_fitness_001",
        "product_name": "FitTrack Pro",
        "product_category": "fitness app",
        "objective": "registrations",
        "budget": "2000.00",
        "currency": "USD",
        "duration_days": 14,
        "target_market": "United States",
        "primary_kpi": "trial registrations",
        "target_cpa": "20.00",
        "brand_voice": "motivational and practical",
        "constraints": ["Avoid unrealistic body transformation claims"],
        "known_audiences": ["Home workout beginners"],
    }


def _performance_event_payload() -> dict[str, object]:
    return {
        "event_id": "evt_negative_demo_conflict",
        "advertiser_id": "adv_fitness_001",
        "run_id": "run_negative_demo",
        "objective": "registrations",
        "event_type": "performance_snapshot",
        "occurred_at": "2026-05-12T12:00:00Z",
        "metrics": {
            "impressions": 10000,
            "clicks": 500,
            "spend": "1000.00",
            "conversions": 20,
        },
        "target_cpa": "20.00",
        "attribution_window_days": 7,
    }


def _completion(content: dict[str, object]) -> dict[str, object]:
    return {
        "model": "test-model",
        "choices": [
            {
                "message": {"content": json.dumps(content)},
                "finish_reason": "stop",
            }
        ],
    }


def _planner_payload(brief: AdvertiserBrief) -> dict[str, object]:
    return {
        "rationale": "Plan audience, creative, and budget first before dependent analysis.",
        "tool_intents": [
            {
                "intent_id": "llm:audience",
                "tool_name": "recommend_audience",
                "requested_by": "planner",
                "risk_level": "low",
                "requires_human_approval": False,
                "params": {
                    "advertiser_id": brief.advertiser_id,
                    "product_category": brief.product_category,
                    "objective": brief.objective.value,
                    "target_market": brief.target_market,
                    "known_audiences": brief.known_audiences,
                },
            },
            {
                "intent_id": "llm:creative",
                "tool_name": "generate_creative_brief",
                "requested_by": "planner",
                "risk_level": "low",
                "requires_human_approval": False,
                "params": {
                    "product_name": brief.product_name,
                    "product_category": brief.product_category,
                    "objective": brief.objective.value,
                    "brand_voice": brief.brand_voice,
                    "constraints": brief.constraints,
                },
            },
            {
                "intent_id": "llm:budget",
                "tool_name": "optimize_budget",
                "requested_by": "planner",
                "risk_level": "low",
                "requires_human_approval": False,
                "params": {
                    "advertiser_id": brief.advertiser_id,
                    "objective": brief.objective.value,
                    "total_budget": str(Decimal(brief.budget)),
                    "currency": brief.currency,
                    "duration_days": brief.duration_days,
                },
            },
        ],
    }


def _expect(condition: bool, issues: list[str], message: str) -> None:
    if not condition:
        issues.append(message)


def main() -> int:
    try:
        summary = build_summary()
    except NegativeDemoVerificationError as exc:
        print("Negative demo verification failed", file=sys.stderr)
        for issue in exc.issues:
            print(f"- {issue}", file=sys.stderr)
        return 1

    print(render_summary(summary))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
