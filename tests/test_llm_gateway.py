import json
from collections.abc import Callable

import httpx
from pydantic import BaseModel, Field

from ads_growth_agent.config import Settings
from ads_growth_agent.llm import (
    LiteLLMGatewayClient,
    LLMMessage,
    ModelGatewayError,
    generate_structured_output,
)


class PlannerDecision(BaseModel):
    objective: str = Field(min_length=1)
    confidence: float = Field(ge=0, le=1)


def test_litellm_client_sends_openai_compatible_request() -> None:
    captured_requests = []

    def handler(request: httpx.Request) -> httpx.Response:
        captured_requests.append(request)
        payload = json.loads(request.content)
        assert payload["model"] == "ads-growth-chat"
        assert payload["messages"][0]["role"] == "user"
        assert request.headers["Authorization"] == "Bearer test-key"
        return _chat_response({"objective": "registrations", "confidence": 0.9})

    client = _client(handler)
    completion = client.complete(
        [LLMMessage(role="user", content="Plan a registration campaign.")],
        model="ads-growth-chat",
    )

    assert captured_requests
    assert json.loads(completion.content)["objective"] == "registrations"


def test_structured_output_native_json_schema_success() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content)
        assert payload["response_format"]["type"] == "json_schema"
        return _chat_response({"objective": "registrations", "confidence": 0.92})

    value, result = generate_structured_output(
        _client(handler),
        [LLMMessage(role="user", content="Create planner decision.")],
        output_model=PlannerDecision,
    )

    assert value == PlannerDecision(objective="registrations", confidence=0.92)
    assert result.success is True
    assert [attempt.mode for attempt in result.attempts] == ["native_json_schema"]


def test_structured_output_falls_back_when_native_schema_is_unsupported() -> None:
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(json.loads(request.content))
        if len(calls) == 1:
            return httpx.Response(400, text="response_format json_schema unsupported")
        return _chat_response({"objective": "leads", "confidence": 0.81})

    value, result = generate_structured_output(
        _client(handler),
        [LLMMessage(role="user", content="Create planner decision.")],
        output_model=PlannerDecision,
    )

    assert value == PlannerDecision(objective="leads", confidence=0.81)
    assert result.success is True
    assert [attempt.mode for attempt in result.attempts] == [
        "native_json_schema",
        "json_prompt",
    ]
    assert "response_format" not in calls[1]
    assert "JSON Schema" in calls[1]["messages"][0]["content"]


def test_structured_output_repairs_invalid_json_prompt_response() -> None:
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(json.loads(request.content))
        if len(calls) == 1:
            return httpx.Response(400, text="response_format json_schema unsupported")
        if len(calls) == 2:
            return _chat_response_raw("not valid json")
        return _chat_response({"objective": "purchases", "confidence": 0.73})

    value, result = generate_structured_output(
        _client(handler),
        [LLMMessage(role="user", content="Create planner decision.")],
        output_model=PlannerDecision,
        max_repair_attempts=1,
    )

    assert value == PlannerDecision(objective="purchases", confidence=0.73)
    assert result.success is True
    assert [attempt.mode for attempt in result.attempts] == [
        "native_json_schema",
        "json_prompt",
        "repair",
    ]
    assert calls[-1]["messages"][-1]["content"] == "Repair the JSON response."


def test_structured_output_returns_safe_failure_after_repair_exhausted() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content)
        if "response_format" in payload:
            return httpx.Response(400, text="response_format json_schema unsupported")
        return _chat_response({"objective": "", "confidence": 2})

    value, result = generate_structured_output(
        _client(handler),
        [LLMMessage(role="user", content="Create planner decision.")],
        output_model=PlannerDecision,
        max_repair_attempts=1,
    )

    assert value is None
    assert result.success is False
    assert result.error_code == "STRUCTURED_OUTPUT_VALIDATION_ERROR"
    assert [attempt.mode for attempt in result.attempts] == [
        "native_json_schema",
        "json_prompt",
        "repair",
    ]


def test_structured_output_does_not_fallback_on_retryable_gateway_error() -> None:
    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(500, text="upstream unavailable")

    value, result = generate_structured_output(
        _client(handler),
        [LLMMessage(role="user", content="Create planner decision.")],
        output_model=PlannerDecision,
    )

    assert value is None
    assert result.success is False
    assert result.error_code == "MODEL_GATEWAY_HTTP_ERROR"
    assert [attempt.mode for attempt in result.attempts] == ["native_json_schema"]


def test_litellm_client_raises_gateway_error_for_empty_choices() -> None:
    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"choices": []})

    client = _client(handler)

    try:
        client.complete([LLMMessage(role="user", content="hello")])
    except ModelGatewayError as exc:
        assert exc.code == "MODEL_EMPTY_RESPONSE"
    else:
        raise AssertionError("Expected ModelGatewayError")


def _client(handler: Callable[[httpx.Request], httpx.Response]) -> LiteLLMGatewayClient:
    return LiteLLMGatewayClient(
        settings=Settings(
            litellm_base_url="http://litellm.test",
            litellm_api_key="test-key",
            default_chat_model="ads-growth-chat",
        ),
        http_client=httpx.Client(transport=httpx.MockTransport(handler)),
    )


def _chat_response(payload: dict) -> httpx.Response:
    return _chat_response_raw(json.dumps(payload))


def _chat_response_raw(content: str) -> httpx.Response:
    return httpx.Response(
        200,
        json={
            "id": "chatcmpl-test",
            "object": "chat.completion",
            "model": "ads-growth-chat",
            "choices": [
                {
                    "index": 0,
                    "message": {"role": "assistant", "content": content},
                    "finish_reason": "stop",
                }
            ],
            "usage": {
                "prompt_tokens": 10,
                "completion_tokens": 5,
                "total_tokens": 15,
            },
        },
    )
