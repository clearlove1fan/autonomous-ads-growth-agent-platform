import json
from collections.abc import Sequence
from typing import Any, Literal, TypeVar

import httpx
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from ads_growth_agent.config import Settings, get_settings

StructuredModel = TypeVar("StructuredModel", bound=BaseModel)


class LLMMessage(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    role: Literal["system", "user", "assistant"]
    content: str = Field(min_length=1)


class LLMUsage(BaseModel):
    prompt_tokens: int | None = Field(default=None, ge=0)
    completion_tokens: int | None = Field(default=None, ge=0)
    total_tokens: int | None = Field(default=None, ge=0)


class LLMCompletion(BaseModel):
    content: str
    model: str
    finish_reason: str | None = None
    usage: LLMUsage | None = None


class StructuredOutputAttempt(BaseModel):
    mode: Literal["native_json_schema", "json_prompt", "repair"]
    success: bool
    error_code: str | None = None
    message: str


class StructuredOutputResult(BaseModel):
    success: bool
    value: dict[str, Any] | None = None
    error_code: str | None = None
    error_message: str | None = None
    attempts: list[StructuredOutputAttempt] = Field(default_factory=list)


class ModelGatewayError(Exception):
    def __init__(
        self,
        code: str,
        message: str,
        *,
        retryable: bool = False,
        status_code: int | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.retryable = retryable
        self.status_code = status_code


class LiteLLMGatewayClient:
    def __init__(
        self,
        *,
        settings: Settings | None = None,
        http_client: httpx.Client | None = None,
    ) -> None:
        self._settings = settings or get_settings()
        self._http_client = http_client or httpx.Client(timeout=60)

    def complete(
        self,
        messages: Sequence[LLMMessage],
        *,
        model: str | None = None,
        response_format: dict[str, Any] | None = None,
    ) -> LLMCompletion:
        payload: dict[str, Any] = {
            "model": model or self._settings.default_chat_model,
            "messages": [message.model_dump(mode="json") for message in messages],
        }
        if response_format is not None:
            payload["response_format"] = response_format

        try:
            response = self._http_client.post(
                f"{self._settings.litellm_base_url.rstrip('/')}/v1/chat/completions",
                headers={
                    "Authorization": f"Bearer {self._settings.litellm_api_key}",
                    "Content-Type": "application/json",
                },
                json=payload,
            )
        except httpx.TimeoutException as exc:
            raise ModelGatewayError(
                "MODEL_TIMEOUT",
                "Model gateway request timed out",
                retryable=True,
            ) from exc
        except httpx.HTTPError as exc:
            raise ModelGatewayError(
                "MODEL_GATEWAY_ERROR",
                "Model gateway request failed",
                retryable=True,
            ) from exc

        if response.status_code >= 400:
            raise ModelGatewayError(
                "MODEL_GATEWAY_HTTP_ERROR",
                response.text,
                retryable=response.status_code >= 500,
                status_code=response.status_code,
            )

        payload = response.json()
        choices = payload.get("choices") or []
        if not choices:
            raise ModelGatewayError("MODEL_EMPTY_RESPONSE", "Model gateway returned no choices")

        choice = choices[0]
        message = choice.get("message") or {}
        content = message.get("content")
        if content is None:
            raise ModelGatewayError("MODEL_EMPTY_CONTENT", "Model gateway returned empty content")

        usage_payload = payload.get("usage")
        return LLMCompletion(
            content=content,
            model=payload.get("model", model or self._settings.default_chat_model),
            finish_reason=choice.get("finish_reason"),
            usage=LLMUsage.model_validate(usage_payload) if usage_payload else None,
        )


def generate_structured_output(
    client: LiteLLMGatewayClient,
    messages: Sequence[LLMMessage],
    *,
    output_model: type[StructuredModel],
    model: str | None = None,
    max_repair_attempts: int = 1,
) -> tuple[StructuredModel | None, StructuredOutputResult]:
    attempts: list[StructuredOutputAttempt] = []
    schema = output_model.model_json_schema()

    try:
        completion = client.complete(
            messages,
            model=model,
            response_format={
                "type": "json_schema",
                "json_schema": {
                    "name": output_model.__name__,
                    "schema": schema,
                    "strict": True,
                },
            },
        )
        value = _parse_and_validate(completion.content, output_model)
        attempts.append(
            StructuredOutputAttempt(
                mode="native_json_schema",
                success=True,
                message="Provider-native structured output validated successfully.",
            )
        )
        return value, _success_result(value, attempts)
    except ModelGatewayError as exc:
        attempts.append(
            StructuredOutputAttempt(
                mode="native_json_schema",
                success=False,
                error_code=exc.code,
                message=exc.message,
            )
        )
        if exc.status_code not in {400, 404, 422}:
            return None, _failure_result(exc.code, exc.message, attempts)
    except (json.JSONDecodeError, ValidationError) as exc:
        attempts.append(
            StructuredOutputAttempt(
                mode="native_json_schema",
                success=False,
                error_code="STRUCTURED_OUTPUT_VALIDATION_ERROR",
                message=str(exc),
            )
        )

    prompt_messages = _with_json_schema_prompt(messages, output_model)
    try:
        completion = client.complete(prompt_messages, model=model)
        value = _parse_and_validate(completion.content, output_model)
        attempts.append(
            StructuredOutputAttempt(
                mode="json_prompt",
                success=True,
                message="JSON prompt fallback validated successfully.",
            )
        )
        return value, _success_result(value, attempts)
    except ModelGatewayError as exc:
        attempts.append(
            StructuredOutputAttempt(
                mode="json_prompt",
                success=False,
                error_code=exc.code,
                message=exc.message,
            )
        )
        return None, _failure_result(exc.code, exc.message, attempts)
    except (json.JSONDecodeError, ValidationError) as exc:
        attempts.append(
            StructuredOutputAttempt(
                mode="json_prompt",
                success=False,
                error_code="STRUCTURED_OUTPUT_VALIDATION_ERROR",
                message=str(exc),
            )
        )
        invalid_content = completion.content

    for _ in range(max_repair_attempts):
        try:
            repair_completion = client.complete(
                _repair_messages(messages, output_model, invalid_content),
                model=model,
            )
            value = _parse_and_validate(repair_completion.content, output_model)
            attempts.append(
                StructuredOutputAttempt(
                    mode="repair",
                    success=True,
                    message="Repair retry validated successfully.",
                )
            )
            return value, _success_result(value, attempts)
        except ModelGatewayError as exc:
            attempts.append(
                StructuredOutputAttempt(
                    mode="repair",
                    success=False,
                    error_code=exc.code,
                    message=exc.message,
                )
            )
            return None, _failure_result(exc.code, exc.message, attempts)
        except (json.JSONDecodeError, ValidationError) as exc:
            attempts.append(
                StructuredOutputAttempt(
                    mode="repair",
                    success=False,
                    error_code="STRUCTURED_OUTPUT_VALIDATION_ERROR",
                    message=str(exc),
                )
            )
            invalid_content = repair_completion.content

    return None, _failure_result(
        "STRUCTURED_OUTPUT_VALIDATION_ERROR",
        "Structured output validation failed after fallback and repair attempts.",
        attempts,
    )


def _with_json_schema_prompt(
    messages: Sequence[LLMMessage],
    output_model: type[BaseModel],
) -> list[LLMMessage]:
    schema = json.dumps(output_model.model_json_schema(), sort_keys=True)
    return [
        LLMMessage(
            role="system",
            content=(
                "Return only valid JSON matching this JSON Schema. "
                "Do not include markdown fences or commentary.\n"
                f"{schema}"
            ),
        ),
        *messages,
    ]


def _repair_messages(
    messages: Sequence[LLMMessage],
    output_model: type[BaseModel],
    invalid_content: str,
) -> list[LLMMessage]:
    schema = json.dumps(output_model.model_json_schema(), sort_keys=True)
    return [
        LLMMessage(
            role="system",
            content=(
                "Repair the previous response so it is valid JSON matching this JSON Schema. "
                "Return only JSON.\n"
                f"{schema}"
            ),
        ),
        *messages,
        LLMMessage(role="assistant", content=invalid_content),
        LLMMessage(role="user", content="Repair the JSON response."),
    ]


def _parse_and_validate(
    content: str,
    output_model: type[StructuredModel],
) -> StructuredModel:
    return output_model.model_validate(json.loads(content))


def _success_result(
    value: BaseModel,
    attempts: list[StructuredOutputAttempt],
) -> StructuredOutputResult:
    return StructuredOutputResult(
        success=True,
        value=value.model_dump(mode="json"),
        attempts=attempts,
    )


def _failure_result(
    error_code: str,
    error_message: str,
    attempts: list[StructuredOutputAttempt],
) -> StructuredOutputResult:
    return StructuredOutputResult(
        success=False,
        error_code=error_code,
        error_message=error_message,
        attempts=attempts,
    )
