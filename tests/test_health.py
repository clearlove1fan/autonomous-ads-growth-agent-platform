from fastapi.testclient import TestClient

from ads_growth_agent import health as health_module
from ads_growth_agent.api import app, get_runtime_settings
from ads_growth_agent.config import Settings


def test_health_endpoint() -> None:
    client = TestClient(app)
    response = client.get("/health")

    assert response.status_code == 200
    assert response.json()["status"] == "ok"
    assert response.json()["service"] == "ads-growth-agent"


def test_liveness_endpoint_is_shallow() -> None:
    client = TestClient(app)
    response = client.get("/health/live")

    assert response.status_code == 200
    assert response.json()["status"] == "ok"


def test_readiness_skips_unconfigured_dependencies() -> None:
    app.dependency_overrides[get_runtime_settings] = lambda: Settings(
        knowledge_store_backend="memory",
        run_persistence_backend="none",
        campaign_draft_persistence_backend="none",
        performance_event_persistence_backend="none",
        advertiser_memory_persistence_backend="none",
        outbox_backend="none",
        memory_usage_tracking_backend="none",
        idempotency_backend="none",
        graph_checkpointer_backend="none",
        use_llm_planner=False,
        use_llm_critic=False,
    )
    try:
        response = TestClient(app).get("/health/ready")
    finally:
        app.dependency_overrides.clear()

    payload = response.json()
    assert response.status_code == 200
    assert payload["status"] == "ok"
    assert payload["dependencies"] == [
        {
            "name": "postgres",
            "status": "skipped",
            "required": False,
            "latency_ms": None,
            "detail": "not required by current configuration",
        },
        {
            "name": "litellm",
            "status": "skipped",
            "required": False,
            "latency_ms": None,
            "detail": "not required by current configuration",
        },
    ]


def test_readiness_fails_when_required_postgres_is_down(monkeypatch) -> None:
    def fake_check_postgres(settings: Settings) -> health_module.DependencyCheck:
        return health_module.DependencyCheck(
            name="postgres",
            status="failed",
            required=True,
            latency_ms=3,
            detail="OperationalError: connection refused",
        )

    monkeypatch.setattr(health_module, "_check_postgres", fake_check_postgres)
    app.dependency_overrides[get_runtime_settings] = lambda: Settings(
        run_persistence_backend="postgres",
        use_llm_planner=False,
        use_llm_critic=False,
    )
    try:
        response = TestClient(app).get("/health/ready")
    finally:
        app.dependency_overrides.clear()

    payload = response.json()
    assert response.status_code == 503
    assert payload["status"] == "not_ready"
    assert payload["dependencies"][0] == {
        "name": "postgres",
        "status": "failed",
        "required": True,
        "latency_ms": 3,
        "detail": "OperationalError: connection refused",
    }


def test_readiness_requires_postgres_for_advertiser_memory_backend(monkeypatch) -> None:
    def fake_check_postgres(settings: Settings) -> health_module.DependencyCheck:
        return health_module.DependencyCheck(
            name="postgres",
            status="ok",
            required=True,
            latency_ms=2,
        )

    monkeypatch.setattr(health_module, "_check_postgres", fake_check_postgres)
    app.dependency_overrides[get_runtime_settings] = lambda: Settings(
        advertiser_memory_persistence_backend="postgres",
        use_llm_planner=False,
        use_llm_critic=False,
    )
    try:
        response = TestClient(app).get("/health/ready")
    finally:
        app.dependency_overrides.clear()

    payload = response.json()
    assert response.status_code == 200
    assert payload["status"] == "ok"
    assert payload["dependencies"][0] == {
        "name": "postgres",
        "status": "ok",
        "required": True,
        "latency_ms": 2,
        "detail": None,
    }


def test_readiness_requires_postgres_for_outbox_backend(monkeypatch) -> None:
    def fake_check_postgres(settings: Settings) -> health_module.DependencyCheck:
        return health_module.DependencyCheck(
            name="postgres",
            status="ok",
            required=True,
            latency_ms=4,
        )

    monkeypatch.setattr(health_module, "_check_postgres", fake_check_postgres)
    app.dependency_overrides[get_runtime_settings] = lambda: Settings(
        outbox_backend="postgres",
        use_llm_planner=False,
        use_llm_critic=False,
    )
    try:
        response = TestClient(app).get("/health/ready")
    finally:
        app.dependency_overrides.clear()

    payload = response.json()
    assert response.status_code == 200
    assert payload["dependencies"][0]["status"] == "ok"
    assert payload["dependencies"][0]["required"] is True


def test_readiness_requires_postgres_for_memory_usage_tracking(monkeypatch) -> None:
    def fake_check_postgres(settings: Settings) -> health_module.DependencyCheck:
        return health_module.DependencyCheck(
            name="postgres",
            status="ok",
            required=True,
            latency_ms=6,
        )

    monkeypatch.setattr(health_module, "_check_postgres", fake_check_postgres)
    app.dependency_overrides[get_runtime_settings] = lambda: Settings(
        memory_usage_tracking_backend="outbox",
        use_llm_planner=False,
        use_llm_critic=False,
    )
    try:
        response = TestClient(app).get("/health/ready")
    finally:
        app.dependency_overrides.clear()

    payload = response.json()
    assert response.status_code == 200
    assert payload["dependencies"][0]["status"] == "ok"
    assert payload["dependencies"][0]["required"] is True


def test_readiness_checks_litellm_when_llm_features_are_enabled(monkeypatch) -> None:
    def fake_check_litellm(settings: Settings) -> health_module.DependencyCheck:
        return health_module.DependencyCheck(
            name="litellm",
            status="ok",
            required=True,
            latency_ms=5,
        )

    monkeypatch.setattr(health_module, "_check_litellm", fake_check_litellm)
    app.dependency_overrides[get_runtime_settings] = lambda: Settings(
        use_llm_planner=True,
        use_llm_critic=False,
    )
    try:
        response = TestClient(app).get("/health/ready")
    finally:
        app.dependency_overrides.clear()

    payload = response.json()
    assert response.status_code == 200
    assert payload["status"] == "ok"
    assert payload["dependencies"][0]["status"] == "skipped"
    assert payload["dependencies"][1] == {
        "name": "litellm",
        "status": "ok",
        "required": True,
        "latency_ms": 5,
        "detail": None,
    }
