import json
from datetime import UTC, datetime
from decimal import Decimal

from fastapi.testclient import TestClient
from typer.testing import CliRunner

from ads_growth_agent import api as api_module
from ads_growth_agent.api import app as api_app
from ads_growth_agent.api import (
    get_runtime_idempotency_store,
    get_runtime_run_read_store,
    get_runtime_settings,
)
from ads_growth_agent.cli import app as cli_app
from ads_growth_agent.config import Settings
from ads_growth_agent.contracts import (
    AdvertiserBrief,
    AgentRunDetailResponse,
    AgentRunStepRecord,
    GrowthStrategyRequest,
)
from ads_growth_agent.persistence.idempotency_store import (
    IdempotencyConflictError,
    IdempotencyStart,
    hash_growth_strategy_request,
)
from ads_growth_agent.strategy import generate_mock_growth_strategy


def test_generate_mock_growth_strategy_returns_valid_budget_plan() -> None:
    response = generate_mock_growth_strategy(AdvertiserBrief.model_validate(_brief_payload()))

    assert response.strategy.advertiser_id == "adv_fitness_001"
    assert response.strategy.budget_plan.allocated_budget <= Decimal("2000.00")
    assert response.strategy.actions
    assert len(response.tool_results) == 5
    assert response.node_path == ["planner", "retriever", "tool_executor", "critic", "finalizer"]
    assert response.run_metadata.tool_count == 5
    assert response.run_metadata.node_path == response.node_path
    assert all(result.success for result in response.tool_results)


def test_growth_strategy_api_returns_structured_strategy() -> None:
    client = TestClient(api_app)
    response = client.post("/growth-strategies", json={"brief": _brief_payload()})

    assert response.status_code == 200
    payload = response.json()
    strategy = payload["strategy"]
    allocations = strategy["budget_plan"]["allocations"]
    allocated_budget = sum(Decimal(str(item["amount"])) for item in allocations)

    assert strategy["advertiser_id"] == "adv_fitness_001"
    assert allocated_budget <= Decimal(str(strategy["budget_plan"]["total_budget"]))
    assert strategy["critique"]["passed"] is True
    assert payload["node_path"] == ["planner", "retriever", "tool_executor", "critic", "finalizer"]
    assert payload["run_metadata"]["tool_count"] == 5
    assert payload["run_metadata"]["node_path"] == payload["node_path"]
    assert payload["run_metadata"]["trace_id"].startswith("trace_")
    assert payload["tool_results"][0]["success"] is True


def test_growth_strategy_api_idempotency_completes_new_request() -> None:
    store = FakeIdempotencyStore(IdempotencyStart(status="started"))
    _override_api_dependencies(
        settings=Settings(idempotency_backend="postgres", idempotency_ttl_seconds=60),
        store=store,
    )
    try:
        response = TestClient(api_app).post(
            "/growth-strategies",
            json={"brief": _brief_payload()},
            headers={"Idempotency-Key": "idem_001"},
        )
    finally:
        api_app.dependency_overrides.clear()

    assert response.status_code == 200
    assert response.headers["idempotency-status"] == "created"
    assert store.begins == [
        (
            "idem_001",
            hash_growth_strategy_request(
                GrowthStrategyRequest.model_validate({"brief": _brief_payload()})
            ),
            60,
        )
    ]
    assert store.completed[0]["key"] == "idem_001"
    assert store.completed[0]["run_id"] is None
    assert store.completed[0]["response_json"]["strategy"]["advertiser_id"] == "adv_fitness_001"
    assert store.failed == []


def test_growth_strategy_api_uses_x_tenant_id_for_request_scoped_settings(
    monkeypatch,
) -> None:
    captured: dict[str, str] = {}
    store = FakeIdempotencyStore(IdempotencyStart(status="started"))

    def fake_build_configured_idempotency_store(settings: Settings) -> FakeIdempotencyStore:
        captured["idempotency_tenant_id"] = settings.tenant_id
        return store

    def fake_generate_growth_strategy(
        brief: AdvertiserBrief,
        *,
        settings: Settings,
    ):
        captured["strategy_tenant_id"] = settings.tenant_id
        return generate_mock_growth_strategy(brief)

    monkeypatch.setattr(
        api_module,
        "build_configured_idempotency_store",
        fake_build_configured_idempotency_store,
    )
    monkeypatch.setattr(
        api_module,
        "generate_growth_strategy",
        fake_generate_growth_strategy,
    )
    api_app.dependency_overrides[get_runtime_settings] = lambda: Settings(
        idempotency_backend="postgres",
        tenant_id="process_default",
    )
    try:
        response = TestClient(api_app).post(
            "/growth-strategies",
            json={"brief": _brief_payload()},
            headers={
                "Idempotency-Key": "idem_tenant",
                "X-Tenant-ID": "tenant_api",
            },
        )
    finally:
        api_app.dependency_overrides.clear()

    assert response.status_code == 200
    assert response.headers["x-tenant-id"] == "tenant_api"
    assert response.headers["idempotency-status"] == "created"
    assert captured == {
        "idempotency_tenant_id": "tenant_api",
        "strategy_tenant_id": "tenant_api",
    }


def test_growth_strategy_api_rejects_invalid_x_tenant_id() -> None:
    response = TestClient(api_app).post(
        "/growth-strategies",
        json={"brief": _brief_payload()},
        headers={"X-Tenant-ID": "tenant with spaces"},
    )

    assert response.status_code == 400
    assert response.json()["detail"]["error_code"] == "INVALID_TENANT_ID"


def test_growth_strategy_api_idempotency_replays_completed_response() -> None:
    replay_payload = generate_mock_growth_strategy(
        AdvertiserBrief.model_validate(_brief_payload())
    ).model_dump(mode="json")
    store = FakeIdempotencyStore(
        IdempotencyStart(status="replayed", response_json=replay_payload)
    )
    _override_api_dependencies(
        settings=Settings(idempotency_backend="postgres"),
        store=store,
    )
    try:
        response = TestClient(api_app).post(
            "/growth-strategies",
            json={"brief": _brief_payload()},
            headers={"Idempotency-Key": "idem_replay"},
        )
    finally:
        api_app.dependency_overrides.clear()

    assert response.status_code == 200
    assert response.headers["idempotency-status"] == "replayed"
    assert response.json() == replay_payload
    assert store.completed == []


def test_growth_strategy_api_idempotency_rejects_conflicting_key() -> None:
    store = FakeIdempotencyStore(
        begin_error=IdempotencyConflictError(
            "IDEMPOTENCY_KEY_REUSED",
            "Idempotency key was already used with a different request body.",
        )
    )
    _override_api_dependencies(
        settings=Settings(idempotency_backend="postgres"),
        store=store,
    )
    try:
        response = TestClient(api_app).post(
            "/growth-strategies",
            json={"brief": _brief_payload()},
            headers={"Idempotency-Key": "idem_conflict"},
        )
    finally:
        api_app.dependency_overrides.clear()

    assert response.status_code == 409
    assert response.json()["detail"]["error_code"] == "IDEMPOTENCY_KEY_REUSED"
    assert store.completed == []


def test_get_agent_run_api_returns_tenant_scoped_detail(monkeypatch) -> None:
    growth_response = generate_mock_growth_strategy(
        AdvertiserBrief.model_validate(_brief_payload())
    )
    run_detail = _run_detail_from_growth_response(growth_response)
    store = FakeRunReadStore(run_detail)
    captured: dict[str, str] = {}

    def fake_build_configured_run_read_store(settings: Settings) -> FakeRunReadStore:
        captured["tenant_id"] = settings.tenant_id
        return store

    monkeypatch.setattr(
        api_module,
        "build_configured_run_read_store",
        fake_build_configured_run_read_store,
    )
    api_app.dependency_overrides[get_runtime_settings] = lambda: Settings(
        run_persistence_backend="postgres",
        tenant_id="process_default",
    )
    try:
        response = TestClient(api_app).get(
            f"/runs/{run_detail.run_id}",
            headers={"X-Tenant-ID": "tenant_api"},
        )
    finally:
        api_app.dependency_overrides.clear()

    payload = response.json()
    assert response.status_code == 200
    assert response.headers["x-tenant-id"] == "tenant_api"
    assert captured == {"tenant_id": "tenant_api"}
    assert store.requested_run_ids == [run_detail.run_id]
    assert payload["run_id"] == run_detail.run_id
    assert payload["execution_id"] == run_detail.run_id
    assert payload["strategy_id"] == growth_response.strategy.strategy_id
    assert payload["status"] == "completed"
    assert payload["final_strategy"]["strategy_id"] == growth_response.strategy.strategy_id
    assert payload["steps"][0]["node_name"] == "planner"


def test_get_agent_run_api_returns_404_when_run_is_missing() -> None:
    _override_api_dependencies(
        settings=Settings(run_persistence_backend="none"),
        store=FakeIdempotencyStore(),
        run_read_store=FakeRunReadStore(None),
    )
    try:
        response = TestClient(api_app).get("/runs/missing_run")
    finally:
        api_app.dependency_overrides.clear()

    assert response.status_code == 404
    assert response.json()["detail"]["error_code"] == "RUN_NOT_FOUND"


def test_retry_agent_run_api_retries_failed_run_as_new_execution(monkeypatch) -> None:
    original_response = generate_mock_growth_strategy(
        AdvertiserBrief.model_validate(_brief_payload())
    )
    failed_run = _run_detail_from_growth_response(original_response, status="failed")
    store = FakeRunReadStore(failed_run)
    retried_response = generate_mock_growth_strategy(
        AdvertiserBrief.model_validate(_brief_payload())
    )
    captured: dict[str, str] = {}

    def fake_generate_growth_strategy(
        brief: AdvertiserBrief,
        *,
        settings: Settings,
    ):
        captured["tenant_id"] = settings.tenant_id
        captured["advertiser_id"] = brief.advertiser_id
        return retried_response

    monkeypatch.setattr(api_module, "generate_growth_strategy", fake_generate_growth_strategy)
    _override_api_dependencies(
        settings=Settings(run_persistence_backend="postgres", tenant_id="process_default"),
        store=FakeIdempotencyStore(),
        run_read_store=store,
    )
    try:
        response = TestClient(api_app).post(
            f"/runs/{failed_run.run_id}/retry",
            json={"brief": _brief_payload()},
            headers={"X-Tenant-ID": "tenant_api"},
        )
    finally:
        api_app.dependency_overrides.clear()

    payload = response.json()
    assert response.status_code == 200
    assert response.headers["x-tenant-id"] == "tenant_api"
    assert response.headers["retried-run-id"] == failed_run.run_id
    assert store.requested_run_ids == [failed_run.run_id]
    assert captured == {
        "tenant_id": "tenant_api",
        "advertiser_id": "adv_fitness_001",
    }
    assert payload["run_metadata"]["run_id"] == retried_response.run_metadata.run_id
    assert payload["run_metadata"]["run_id"] != failed_run.run_id
    assert payload["strategy"]["strategy_id"] == retried_response.strategy.strategy_id


def test_retry_agent_run_api_rejects_non_failed_run() -> None:
    completed_response = generate_mock_growth_strategy(
        AdvertiserBrief.model_validate(_brief_payload())
    )
    completed_run = _run_detail_from_growth_response(completed_response)
    _override_api_dependencies(
        settings=Settings(run_persistence_backend="postgres"),
        store=FakeIdempotencyStore(),
        run_read_store=FakeRunReadStore(completed_run),
    )
    try:
        response = TestClient(api_app).post(
            f"/runs/{completed_run.run_id}/retry",
            json={"brief": _brief_payload()},
        )
    finally:
        api_app.dependency_overrides.clear()

    assert response.status_code == 409
    assert response.json()["detail"]["error_code"] == "RUN_NOT_RETRYABLE"


def test_retry_agent_run_api_rejects_brief_identity_mismatch() -> None:
    original_response = generate_mock_growth_strategy(
        AdvertiserBrief.model_validate(_brief_payload())
    )
    failed_run = _run_detail_from_growth_response(original_response, status="failed")
    mismatched_brief = {**_brief_payload(), "advertiser_id": "adv_other"}
    _override_api_dependencies(
        settings=Settings(run_persistence_backend="postgres"),
        store=FakeIdempotencyStore(),
        run_read_store=FakeRunReadStore(failed_run),
    )
    try:
        response = TestClient(api_app).post(
            f"/runs/{failed_run.run_id}/retry",
            json={"brief": mismatched_brief},
        )
    finally:
        api_app.dependency_overrides.clear()

    assert response.status_code == 409
    assert response.json()["detail"]["error_code"] == "RETRY_BRIEF_MISMATCH"


def test_plan_cli_accepts_brief_file(tmp_path) -> None:
    brief_file = tmp_path / "brief.json"
    brief_file.write_text(json.dumps(_brief_payload()))

    result = CliRunner().invoke(cli_app, ["plan", str(brief_file)])

    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["strategy"]["advertiser_id"] == "adv_fitness_001"
    assert payload["strategy"]["actions"]
    assert payload["run_metadata"]["tool_count"] == 5


def test_seed_knowledge_cli_uses_configured_database_and_tenant(monkeypatch) -> None:
    calls: dict[str, object] = {}

    class FakeEngine:
        def dispose(self) -> None:
            calls["disposed"] = True

    def fake_create_engine(database_url: str, **kwargs: object) -> FakeEngine:
        calls["database_url"] = database_url
        calls["engine_kwargs"] = kwargs
        return FakeEngine()

    def fake_seed_default_knowledge(engine: FakeEngine, *, tenant_id: str) -> None:
        calls["seed_engine"] = engine
        calls["tenant_id"] = tenant_id

    monkeypatch.setattr(
        "ads_growth_agent.cli.get_settings",
        lambda: Settings(
            database_url="postgresql+psycopg://ads_growth:secret@localhost:5432/ads_growth",
            tenant_id="tenant_cli",
        ),
    )
    monkeypatch.setattr("ads_growth_agent.cli.sa.create_engine", fake_create_engine)
    monkeypatch.setattr("ads_growth_agent.cli.seed_default_knowledge", fake_seed_default_knowledge)

    result = CliRunner().invoke(cli_app, ["seed-knowledge"])

    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["status"] == "ok"
    assert payload["tenant_id"] == "tenant_cli"
    assert payload["database_url"] == "postgresql+psycopg://ads_growth:***@localhost:5432/ads_growth"
    assert calls["database_url"] == "postgresql+psycopg://ads_growth:secret@localhost:5432/ads_growth"
    assert calls["engine_kwargs"] == {"pool_pre_ping": True}
    assert calls["tenant_id"] == "tenant_cli"
    assert calls["disposed"] is True


def _override_api_dependencies(
    *,
    settings: Settings,
    store: object,
    run_read_store: object | None = None,
) -> None:
    api_app.dependency_overrides[get_runtime_settings] = lambda: settings
    api_app.dependency_overrides[get_runtime_idempotency_store] = lambda: store
    if run_read_store is not None:
        api_app.dependency_overrides[get_runtime_run_read_store] = lambda: run_read_store


class FakeIdempotencyStore:
    def __init__(
        self,
        start: IdempotencyStart | None = None,
        *,
        begin_error: IdempotencyConflictError | None = None,
    ) -> None:
        self._start = start or IdempotencyStart(status="started")
        self._begin_error = begin_error
        self.begins: list[tuple[str, str, int]] = []
        self.completed: list[dict[str, object]] = []
        self.failed: list[dict[str, object]] = []

    def begin(self, key: str, request_hash: str, *, ttl_seconds: int) -> IdempotencyStart:
        self.begins.append((key, request_hash, ttl_seconds))
        if self._begin_error is not None:
            raise self._begin_error
        return self._start

    def mark_completed(
        self,
        key: str,
        request_hash: str,
        *,
        run_id: str | None,
        response_json: dict[str, object],
        ttl_seconds: int,
    ) -> None:
        self.completed.append(
            {
                "key": key,
                "request_hash": request_hash,
                "run_id": run_id,
                "response_json": response_json,
                "ttl_seconds": ttl_seconds,
            }
        )

    def mark_failed(
        self,
        key: str,
        request_hash: str,
        *,
        run_id: str | None,
        error_json: dict[str, object],
        ttl_seconds: int,
    ) -> None:
        self.failed.append(
            {
                "key": key,
                "request_hash": request_hash,
                "run_id": run_id,
                "error_json": error_json,
                "ttl_seconds": ttl_seconds,
            }
        )


class FakeRunReadStore:
    def __init__(self, run_detail: AgentRunDetailResponse | None) -> None:
        self._run_detail = run_detail
        self.requested_run_ids: list[str] = []

    def get_run(self, run_id: str) -> AgentRunDetailResponse | None:
        self.requested_run_ids.append(run_id)
        if self._run_detail is None or self._run_detail.run_id != run_id:
            return None
        return self._run_detail


def _run_detail_from_growth_response(
    growth_response,
    *,
    status: str = "completed",
) -> AgentRunDetailResponse:
    created_at = datetime.now(UTC)
    final_strategy = growth_response.strategy if status == "completed" else None
    completed_at = created_at if status in {"completed", "failed"} else None
    return AgentRunDetailResponse(
        run_id=growth_response.run_metadata.run_id,
        execution_id=growth_response.run_metadata.run_id,
        strategy_id=growth_response.strategy.strategy_id,
        advertiser_id=growth_response.strategy.advertiser_id,
        objective=growth_response.strategy.objective,
        status=status,
        trace_id=growth_response.run_metadata.trace_id,
        node_path=growth_response.node_path,
        final_strategy=final_strategy,
        error_summary=[] if status == "completed" else ["original run failed"],
        metadata={"execution_id": growth_response.run_metadata.run_id},
        steps=[
            AgentRunStepRecord(
                step_index=0,
                node_name="planner",
                status="completed",
                input_json={"run_id": growth_response.run_metadata.run_id},
                output_json={"node_name": "planner"},
                latency_ms=0,
                created_at=created_at,
            )
        ],
        created_at=created_at,
        completed_at=completed_at,
    )


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
        "constraints": [
            "Avoid unrealistic body transformation claims",
            "Do not imply medical outcomes",
        ],
        "known_audiences": [
            "Home workout beginners",
            "Wearable fitness tracker users",
        ],
    }
