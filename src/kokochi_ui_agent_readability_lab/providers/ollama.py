"""Small, injectable adapter for the local Ollama HTTP API."""

from __future__ import annotations

from collections.abc import Callable
from enum import StrEnum
import json
import math
import time
from typing import Annotated, Literal, NoReturn, Self
from urllib.parse import urlsplit

import httpx
from pydantic import Field, FiniteFloat, NonNegativeInt, PositiveInt, model_validator

from kokochi_ui_agent_readability_lab.records.schema import (
    GenerationParameter,
    NonEmptyString,
    StrictModel,
)


class OllamaFailureCode(StrEnum):
    """Stable failure classifications for provider result records."""

    TIMEOUT = "timeout"
    CONNECTION_FAILED = "connection-failed"
    HTTP_ERROR = "http-error"
    INVALID_JSON = "invalid-json"
    INVALID_RESPONSE = "invalid-response"
    EMPTY_RESPONSE = "empty-response"
    MODEL_NOT_FOUND = "model-not-found"


class OllamaError(RuntimeError):
    """Provider failure with saveable request, response, and retry metadata."""

    def __init__(
        self,
        code: OllamaFailureCode,
        message: str,
        *,
        operation: str,
        retryable: bool,
        request_body: bytes,
        response_body: bytes | None,
        status_code: int | None,
        latency_ms: float,
        attempt_count: int = 1,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.operation = operation
        self.retryable = retryable
        self.request_body = request_body
        self.response_body = response_body
        self.status_code = status_code
        self.latency_ms = latency_ms
        self.attempt_count = attempt_count


class OllamaClientConfig(StrictModel):
    """Connection controls supplied by the caller, never added to result bodies."""

    endpoint: NonEmptyString = "http://127.0.0.1:11434"
    timeout_seconds: Annotated[FiniteFloat, Field(gt=0)] = 120.0

    @model_validator(mode="after")
    def endpoint_must_be_safe_origin(self) -> Self:
        parsed = urlsplit(self.endpoint)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            raise ValueError("Ollama endpoint must be an absolute HTTP(S) URL")
        if parsed.username or parsed.password:
            raise ValueError("Ollama endpoint must not contain credentials")
        if parsed.query or parsed.fragment:
            raise ValueError("Ollama endpoint must not contain query or fragment")
        if parsed.path not in {"", "/"}:
            raise ValueError("Ollama endpoint must not contain an API path")
        return self


class OllamaGenerationOptions(StrictModel):
    """Experiment-specific generation controls passed through explicitly."""

    temperature: Annotated[FiniteFloat, Field(ge=0)]
    top_p: Annotated[FiniteFloat, Field(gt=0, le=1)]
    top_k: PositiveInt
    repeat_penalty: Annotated[FiniteFloat, Field(gt=0)]
    num_ctx: PositiveInt
    num_predict: PositiveInt
    seed: int


class OllamaGenerateRequest(StrictModel):
    """One non-streaming Ollama generation request."""

    model_id: NonEmptyString
    prompt: NonEmptyString
    options: OllamaGenerationOptions
    think: bool
    response_format: Literal["json"] | dict[str, object]


class OllamaTokenUsage(StrictModel):
    """Token counts reported by Ollama."""

    input_tokens: NonNegativeInt | None = None
    output_tokens: NonNegativeInt | None = None
    total_tokens: NonNegativeInt | None = None

    @model_validator(mode="after")
    def require_a_reported_count(self) -> Self:
        if self.input_tokens is None and self.output_tokens is None:
            raise ValueError("Ollama token usage requires a reported count")
        expected_total = (self.input_tokens or 0) + (self.output_tokens or 0)
        if self.total_tokens != expected_total:
            raise ValueError("Ollama total_tokens must equal input plus output")
        return self


class OllamaModelInfo(StrictModel):
    """Model and runtime identity available from the local Ollama instance."""

    model_id: NonEmptyString
    digest: NonEmptyString | None = None
    quantization: NonEmptyString | None = None
    parameter_size: NonEmptyString | None = None
    family: NonEmptyString | None = None
    runtime_version: NonEmptyString


class OllamaGenerateResult(StrictModel):
    """Raw and normalized metadata from one successful generation."""

    model_id: NonEmptyString
    status_code: PositiveInt
    request_body: bytes
    response_body: bytes
    generated_text: NonEmptyString
    latency_ms: Annotated[FiniteFloat, Field(ge=0)]
    attempt_count: Literal[1]
    token_usage: OllamaTokenUsage | None = None
    total_duration_ns: NonNegativeInt | None = None
    load_duration_ns: NonNegativeInt | None = None
    prompt_eval_duration_ns: NonNegativeInt | None = None
    eval_duration_ns: NonNegativeInt | None = None
    done_reason: NonEmptyString | None = None


def ollama_generation_parameters(
    request: OllamaGenerateRequest,
) -> tuple[GenerationParameter, ...]:
    """Return the complete primitive generation controls for a run manifest."""

    options = request.options
    return (
        GenerationParameter(name="num_ctx", value=options.num_ctx),
        GenerationParameter(name="num_predict", value=options.num_predict),
        GenerationParameter(name="repeat_penalty", value=options.repeat_penalty),
        GenerationParameter(name="seed", value=options.seed),
        GenerationParameter(name="think", value=request.think),
        GenerationParameter(name="temperature", value=options.temperature),
        GenerationParameter(name="top_k", value=options.top_k),
        GenerationParameter(name="top_p", value=options.top_p),
    )


def _canonical_json_bytes(value: object) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def ollama_model_configuration_bytes(request: OllamaGenerateRequest) -> bytes:
    """Serialize reproducible request controls without prompt text or credentials."""

    return _canonical_json_bytes(
        {
            "format": request.response_format,
            "model": request.model_id,
            "options": request.options.model_dump(mode="json"),
            "stream": False,
            "think": request.think,
        }
    )


def _optional_non_empty_string(value: object) -> str | None:
    return value if isinstance(value, str) and value.strip() else None


def _optional_non_negative_int(value: object) -> int | None:
    return (
        value
        if isinstance(value, int) and not isinstance(value, bool) and value >= 0
        else None
    )


class OllamaAdapter:
    """Direct Ollama client with no retries and an injectable HTTP transport."""

    def __init__(
        self,
        config: OllamaClientConfig,
        *,
        client: httpx.Client | None = None,
        monotonic: Callable[[], float] = time.monotonic,
    ) -> None:
        self.config = config
        self._client = client or httpx.Client()
        self._owns_client = client is None
        self._monotonic = monotonic

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *_args: object) -> None:
        self.close()

    def close(self) -> None:
        if self._owns_client:
            self._client.close()

    def inspect_model(self, model_id: str) -> OllamaModelInfo:
        """Resolve runtime version, digest, and quantization when Ollama reports them."""

        version_body = b""
        version_response, version_latency = self._request(
            operation="version",
            method="GET",
            path="/api/version",
            request_body=version_body,
        )
        version = self._decode_object(
            version_response,
            operation="version",
            request_body=version_body,
            latency_ms=version_latency,
        )
        runtime_version = _optional_non_empty_string(version.get("version"))
        if runtime_version is None:
            self._raise_invalid_response(
                "version",
                version_body,
                version_response,
                "Ollama version response omitted version",
                latency_ms=version_latency,
            )

        tags_body = b""
        tags_response, tags_latency = self._request(
            operation="list-models",
            method="GET",
            path="/api/tags",
            request_body=tags_body,
        )
        tags = self._decode_object(
            tags_response,
            operation="list-models",
            request_body=tags_body,
            latency_ms=tags_latency,
        )
        raw_models = tags.get("models")
        if not isinstance(raw_models, list):
            self._raise_invalid_response(
                "list-models",
                tags_body,
                tags_response,
                "Ollama model list omitted models",
                latency_ms=tags_latency,
            )
        model_entry = next(
            (
                candidate
                for candidate in raw_models
                if isinstance(candidate, dict)
                and model_id in {candidate.get("name"), candidate.get("model")}
            ),
            None,
        )
        if model_entry is None:
            raise OllamaError(
                OllamaFailureCode.MODEL_NOT_FOUND,
                f"Ollama model is not installed: {model_id}",
                operation="list-models",
                retryable=False,
                request_body=tags_body,
                response_body=tags_response.content,
                status_code=tags_response.status_code,
                latency_ms=tags_latency,
            )

        show_body = _canonical_json_bytes({"model": model_id})
        show_response, show_latency = self._request(
            operation="show-model",
            method="POST",
            path="/api/show",
            request_body=show_body,
        )
        show = self._decode_object(
            show_response,
            operation="show-model",
            request_body=show_body,
            latency_ms=show_latency,
        )
        details = show.get("details")
        details = details if isinstance(details, dict) else {}

        return OllamaModelInfo(
            model_id=model_id,
            digest=_optional_non_empty_string(model_entry.get("digest")),
            quantization=_optional_non_empty_string(details.get("quantization_level")),
            parameter_size=_optional_non_empty_string(details.get("parameter_size")),
            family=_optional_non_empty_string(details.get("family")),
            runtime_version=runtime_version,
        )

    def generate(self, request: OllamaGenerateRequest) -> OllamaGenerateResult:
        """Perform exactly one non-streaming generation request."""

        payload = json.loads(ollama_model_configuration_bytes(request))
        payload["prompt"] = request.prompt
        request_body = _canonical_json_bytes(payload)
        response, latency_ms = self._request(
            operation="generate",
            method="POST",
            path="/api/generate",
            request_body=request_body,
        )
        decoded = self._decode_object(
            response,
            operation="generate",
            request_body=request_body,
            latency_ms=latency_ms,
        )
        if decoded.get("done") is not True:
            self._raise_invalid_response(
                "generate",
                request_body,
                response,
                "Ollama generation response did not report completion",
                latency_ms=latency_ms,
            )
        generated_text = _optional_non_empty_string(decoded.get("response"))
        if generated_text is None:
            raise OllamaError(
                OllamaFailureCode.EMPTY_RESPONSE,
                "Ollama generation returned an empty response",
                operation="generate",
                retryable=False,
                request_body=request_body,
                response_body=response.content,
                status_code=response.status_code,
                latency_ms=latency_ms,
            )
        reported_model = _optional_non_empty_string(decoded.get("model"))
        if reported_model is None:
            self._raise_invalid_response(
                "generate",
                request_body,
                response,
                "Ollama generation response omitted model",
                latency_ms=latency_ms,
            )
        if reported_model != request.model_id:
            self._raise_invalid_response(
                "generate",
                request_body,
                response,
                "Ollama generation response reported a different model",
                latency_ms=latency_ms,
            )

        input_tokens = _optional_non_negative_int(decoded.get("prompt_eval_count"))
        output_tokens = _optional_non_negative_int(decoded.get("eval_count"))
        token_usage = None
        if input_tokens is not None or output_tokens is not None:
            token_usage = OllamaTokenUsage(
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                total_tokens=(input_tokens or 0) + (output_tokens or 0),
            )

        return OllamaGenerateResult(
            model_id=reported_model,
            status_code=response.status_code,
            request_body=request_body,
            response_body=response.content,
            generated_text=generated_text,
            latency_ms=latency_ms,
            attempt_count=1,
            token_usage=token_usage,
            total_duration_ns=_optional_non_negative_int(decoded.get("total_duration")),
            load_duration_ns=_optional_non_negative_int(decoded.get("load_duration")),
            prompt_eval_duration_ns=_optional_non_negative_int(
                decoded.get("prompt_eval_duration")
            ),
            eval_duration_ns=_optional_non_negative_int(decoded.get("eval_duration")),
            done_reason=_optional_non_empty_string(decoded.get("done_reason")),
        )

    def _request(
        self,
        *,
        operation: str,
        method: str,
        path: str,
        request_body: bytes,
    ) -> tuple[httpx.Response, float]:
        url = f"{self.config.endpoint.rstrip('/')}{path}"
        started = self._monotonic()
        try:
            response = self._client.request(
                method,
                url,
                content=request_body or None,
                headers={"content-type": "application/json"},
                timeout=self.config.timeout_seconds,
            )
        except httpx.TimeoutException as error:
            latency_ms = self._elapsed_ms(started)
            raise OllamaError(
                OllamaFailureCode.TIMEOUT,
                f"Ollama {operation} timed out",
                operation=operation,
                retryable=True,
                request_body=request_body,
                response_body=None,
                status_code=None,
                latency_ms=latency_ms,
            ) from error
        except (httpx.ConnectError, httpx.NetworkError) as error:
            latency_ms = self._elapsed_ms(started)
            raise OllamaError(
                OllamaFailureCode.CONNECTION_FAILED,
                f"Ollama {operation} connection failed",
                operation=operation,
                retryable=True,
                request_body=request_body,
                response_body=None,
                status_code=None,
                latency_ms=latency_ms,
            ) from error
        latency_ms = self._elapsed_ms(started)
        if response.status_code >= 400:
            raise OllamaError(
                OllamaFailureCode.HTTP_ERROR,
                f"Ollama {operation} returned HTTP {response.status_code}",
                operation=operation,
                retryable=response.status_code == 429 or response.status_code >= 500,
                request_body=request_body,
                response_body=response.content,
                status_code=response.status_code,
                latency_ms=latency_ms,
            )
        return response, latency_ms

    def _decode_object(
        self,
        response: httpx.Response,
        *,
        operation: str,
        request_body: bytes,
        latency_ms: float,
    ) -> dict[str, object]:
        try:
            decoded = json.loads(response.content)
        except (json.JSONDecodeError, UnicodeDecodeError) as error:
            raise OllamaError(
                OllamaFailureCode.INVALID_JSON,
                f"Ollama {operation} returned invalid JSON",
                operation=operation,
                retryable=False,
                request_body=request_body,
                response_body=response.content,
                status_code=response.status_code,
                latency_ms=latency_ms,
            ) from error
        if not isinstance(decoded, dict):
            self._raise_invalid_response(
                operation,
                request_body,
                response,
                f"Ollama {operation} response must be a JSON object",
                latency_ms=latency_ms,
            )
        return decoded

    def _raise_invalid_response(
        self,
        operation: str,
        request_body: bytes,
        response: httpx.Response,
        message: str,
        *,
        latency_ms: float = 0.0,
    ) -> NoReturn:
        raise OllamaError(
            OllamaFailureCode.INVALID_RESPONSE,
            message,
            operation=operation,
            retryable=False,
            request_body=request_body,
            response_body=response.content,
            status_code=response.status_code,
            latency_ms=latency_ms,
        )

    def _elapsed_ms(self, started: float) -> float:
        elapsed = (self._monotonic() - started) * 1000
        return elapsed if math.isfinite(elapsed) and elapsed >= 0 else 0.0
