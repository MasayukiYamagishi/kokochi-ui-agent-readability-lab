import json

import httpx
import pytest
from pydantic import ValidationError

from kokochi_ui_agent_readability_lab.providers import (
    OllamaAdapter,
    OllamaClientConfig,
    OllamaError,
    OllamaFailureCode,
    OllamaGenerateRequest,
    OllamaGenerationOptions,
    ollama_generation_parameters,
    ollama_model_configuration_bytes,
)


def generation_request() -> OllamaGenerateRequest:
    return OllamaGenerateRequest(
        model_id="qwen3:4b",
        prompt="Return JSON.",
        options=OllamaGenerationOptions(
            temperature=0.2,
            top_p=0.95,
            top_k=20,
            repeat_penalty=1.0,
            num_ctx=8192,
            num_predict=512,
            seed=1001,
        ),
        think=False,
        response_format={
            "type": "object",
            "properties": {"items": {"type": "array"}},
        },
    )


def test_ollama_adapter_inspects_model_and_returns_raw_exchange() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/version":
            return httpx.Response(200, json={"version": "0.11.4"})
        if request.url.path == "/api/tags":
            return httpx.Response(
                200,
                json={
                    "models": [
                        {
                            "name": "qwen3:4b",
                            "model": "qwen3:4b",
                            "digest": "sha256:model-digest",
                        }
                    ]
                },
            )
        if request.url.path == "/api/show":
            assert json.loads(request.content) == {"model": "qwen3:4b"}
            return httpx.Response(
                200,
                json={
                    "details": {
                        "family": "qwen3",
                        "parameter_size": "4.0B",
                        "quantization_level": "Q4_K_M",
                    }
                },
            )
        assert request.url.path == "/api/generate"
        payload = json.loads(request.content)
        assert payload["stream"] is False
        assert payload["think"] is False
        assert payload["options"]["seed"] == 1001
        assert "127.0.0.1" not in request.content.decode()
        return httpx.Response(
            200,
            json={
                "model": "qwen3:4b",
                "response": '{"items":[]}',
                "done": True,
                "done_reason": "stop",
                "prompt_eval_count": 42,
                "eval_count": 8,
                "total_duration": 1_500_000,
                "load_duration": 100_000,
                "prompt_eval_duration": 500_000,
                "eval_duration": 800_000,
            },
        )

    client = httpx.Client(transport=httpx.MockTransport(handler))
    ticks = iter([10.0, 10.01, 20.0, 20.01, 30.0, 30.01, 40.0, 40.25])
    adapter = OllamaAdapter(
        OllamaClientConfig(endpoint="http://127.0.0.1:11434", timeout_seconds=30.0),
        client=client,
        monotonic=lambda: next(ticks),
    )

    model = adapter.inspect_model("qwen3:4b")
    result = adapter.generate(generation_request())

    assert model.digest == "sha256:model-digest"
    assert model.quantization == "Q4_K_M"
    assert model.runtime_version == "0.11.4"
    assert result.generated_text == '{"items":[]}'
    assert result.status_code == 200
    assert result.latency_ms == 250.0
    assert result.token_usage is not None
    assert result.token_usage.total_tokens == 50
    assert json.loads(result.response_body)["done"] is True
    assert result.attempt_count == 1


def test_ollama_generation_parameters_cover_every_generation_control() -> None:
    parameters = ollama_generation_parameters(generation_request())

    assert {parameter.name: parameter.value for parameter in parameters} == {
        "num_ctx": 8192,
        "num_predict": 512,
        "repeat_penalty": 1.0,
        "seed": 1001,
        "think": False,
        "temperature": 0.2,
        "top_k": 20,
        "top_p": 0.95,
    }


def test_ollama_model_configuration_excludes_prompt_and_connection_data() -> None:
    content = ollama_model_configuration_bytes(generation_request())
    configuration = json.loads(content)

    assert configuration["model"] == "qwen3:4b"
    assert configuration["options"]["seed"] == 1001
    assert configuration["think"] is False
    assert "prompt" not in configuration
    assert b"127.0.0.1" not in content
    assert content.endswith(b"}")


@pytest.mark.parametrize(
    ("raised", "expected_code"),
    [
        (httpx.ReadTimeout("slow"), OllamaFailureCode.TIMEOUT),
        (httpx.ConnectError("offline"), OllamaFailureCode.CONNECTION_FAILED),
    ],
)
def test_ollama_adapter_classifies_transport_failures(
    raised: Exception,
    expected_code: OllamaFailureCode,
) -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        raise raised

    client = httpx.Client(transport=httpx.MockTransport(handler))
    adapter = OllamaAdapter(OllamaClientConfig(), client=client)

    with pytest.raises(OllamaError) as captured:
        adapter.generate(generation_request())

    assert captured.value.code is expected_code
    assert captured.value.retryable is True
    assert captured.value.response_body is None
    assert captured.value.attempt_count == 1


def test_ollama_adapter_preserves_http_failure_body() -> None:
    client = httpx.Client(
        transport=httpx.MockTransport(
            lambda _request: httpx.Response(503, content=b'{"error":"loading"}')
        )
    )
    adapter = OllamaAdapter(OllamaClientConfig(), client=client)

    with pytest.raises(OllamaError) as captured:
        adapter.generate(generation_request())

    assert captured.value.code is OllamaFailureCode.HTTP_ERROR
    assert captured.value.status_code == 503
    assert captured.value.response_body == b'{"error":"loading"}'
    assert captured.value.retryable is True


@pytest.mark.parametrize(
    ("response", "expected_code"),
    [
        (httpx.Response(200, content=b"not-json"), OllamaFailureCode.INVALID_JSON),
        (
            httpx.Response(
                200,
                json={"model": "qwen3:4b", "response": "", "done": True},
            ),
            OllamaFailureCode.EMPTY_RESPONSE,
        ),
    ],
)
def test_ollama_adapter_classifies_invalid_and_empty_responses(
    response: httpx.Response,
    expected_code: OllamaFailureCode,
) -> None:
    client = httpx.Client(transport=httpx.MockTransport(lambda _request: response))
    adapter = OllamaAdapter(OllamaClientConfig(), client=client)

    with pytest.raises(OllamaError) as captured:
        adapter.generate(generation_request())

    assert captured.value.code is expected_code
    assert captured.value.retryable is False
    assert captured.value.response_body == response.content


@pytest.mark.parametrize("done", [False, None, "true"])
def test_ollama_adapter_rejects_responses_without_strict_completion(
    done: object,
) -> None:
    payload: dict[str, object] = {
        "model": "qwen3:4b",
        "response": '{"items":[]}',
    }
    if done is not None:
        payload["done"] = done
    response = httpx.Response(200, json=payload)
    client = httpx.Client(transport=httpx.MockTransport(lambda _request: response))
    adapter = OllamaAdapter(OllamaClientConfig(), client=client)

    with pytest.raises(OllamaError) as captured:
        adapter.generate(generation_request())

    assert captured.value.code is OllamaFailureCode.INVALID_RESPONSE
    assert captured.value.retryable is False
    assert captured.value.response_body == response.content
    assert "did not report completion" in str(captured.value)


def test_ollama_adapter_rejects_a_different_reported_model() -> None:
    response = httpx.Response(
        200,
        json={"model": "another-model:latest", "response": "{}", "done": True},
    )
    client = httpx.Client(transport=httpx.MockTransport(lambda _request: response))
    adapter = OllamaAdapter(OllamaClientConfig(), client=client)

    with pytest.raises(OllamaError) as captured:
        adapter.generate(generation_request())

    assert captured.value.code is OllamaFailureCode.INVALID_RESPONSE
    assert "different model" in str(captured.value)


def test_ollama_adapter_reports_an_uninstalled_model() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/version":
            return httpx.Response(200, json={"version": "0.11.4"})
        assert request.url.path == "/api/tags"
        return httpx.Response(200, json={"models": []})

    client = httpx.Client(transport=httpx.MockTransport(handler))
    adapter = OllamaAdapter(OllamaClientConfig(), client=client)

    with pytest.raises(OllamaError) as captured:
        adapter.inspect_model("qwen3:4b")

    assert captured.value.code is OllamaFailureCode.MODEL_NOT_FOUND
    assert captured.value.retryable is False


def test_ollama_endpoint_rejects_embedded_credentials() -> None:
    with pytest.raises(ValidationError, match="must not contain credentials"):
        OllamaClientConfig(endpoint="http://secret@127.0.0.1:11434")
