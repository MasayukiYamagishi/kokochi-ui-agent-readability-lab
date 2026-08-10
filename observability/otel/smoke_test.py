"""Emit one run and verify its traces, metrics, and logs in the local LGTM stack."""

from __future__ import annotations

import os
from pathlib import Path
import sys
import time
from uuid import uuid4

import httpx

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPOSITORY_ROOT / "src"))

from kokochi_ui_agent_readability_lab.telemetry import (  # noqa: E402
    RunTelemetryContext,
    TelemetrySettings,
    create_otlp_telemetry,
)


SERVICE_NAME_PREFIX = "kokochi-observability-smoke"
OTLP_HTTP_URL = "http://127.0.0.1:4318"
TEMPO_URL = "http://127.0.0.1:3200"
PROMETHEUS_URL = "http://127.0.0.1:9090"
LOKI_URL = "http://127.0.0.1:3100"
DELIVERY_TIMEOUT_SECONDS = 60.0


def configure_local_otlp_environment() -> None:
    """Make this standalone check independent from inherited OTLP settings."""

    for variable in tuple(os.environ):
        if variable.startswith("OTEL_EXPORTER_OTLP"):
            del os.environ[variable]
    os.environ.update(
        {
            "OTEL_EXPORTER_OTLP_ENDPOINT": OTLP_HTTP_URL,
            "OTEL_EXPORTER_OTLP_PROTOCOL": "http/protobuf",
            "OTEL_EXPORTER_OTLP_TRACES_ENDPOINT": f"{OTLP_HTTP_URL}/v1/traces",
            "OTEL_EXPORTER_OTLP_TRACES_PROTOCOL": "http/protobuf",
            "OTEL_EXPORTER_OTLP_METRICS_ENDPOINT": f"{OTLP_HTTP_URL}/v1/metrics",
            "OTEL_EXPORTER_OTLP_METRICS_PROTOCOL": "http/protobuf",
            "OTEL_EXPORTER_OTLP_LOGS_ENDPOINT": f"{OTLP_HTTP_URL}/v1/logs",
            "OTEL_EXPORTER_OTLP_LOGS_PROTOCOL": "http/protobuf",
        }
    )


def smoke_service_name(run_id: str) -> str:
    """Return a unique resource identity for one smoke-test invocation."""

    return f"{SERVICE_NAME_PREFIX}-{run_id}"


def emit_smoke_run() -> tuple[str, str, str]:
    """Send correlated signals through the production telemetry factory."""

    configure_local_otlp_environment()
    context = RunTelemetryContext(
        run_id=uuid4(),
        experiment_id="observability-smoke",
        experiment_version="v1",
        fixture_id="dummy-signal",
        fixture_version="v1",
        input_representation="metadata-only",
        provider="local-smoke",
        model_id="not-applicable",
        prompt_version="v1",
        scorer_id="delivery-check",
        scorer_version="v1",
    )
    service_name = smoke_service_name(str(context.run_id))
    with create_otlp_telemetry(
        TelemetrySettings(
            export_otlp=True,
            service_name=service_name,
            batch_schedule_delay_ms=100,
            metric_export_interval_ms=100,
        )
    ) as telemetry:
        with telemetry.start_run(context) as run:
            with run.stage("fixture.render"):
                pass
            trace_id = run.trace_id
    return str(context.run_id), trace_id, service_name


def trace_arrived(client: httpx.Client, trace_id: str) -> bool:
    response = client.get(f"{TEMPO_URL}/api/traces/{trace_id}")
    return response.status_code == 200


def metric_arrived(client: httpx.Client, service_name: str) -> bool:
    names_response = client.get(f"{PROMETHEUS_URL}/api/v1/label/__name__/values")
    if names_response.status_code != 200:
        return False
    names = names_response.json().get("data", ())
    candidates = [
        name
        for name in names
        if isinstance(name, str) and name.startswith("kokochi_experiment_run_count")
    ]
    for name in candidates:
        response = client.get(
            f"{PROMETHEUS_URL}/api/v1/query",
            params={"query": f'{name}{{service_name="{service_name}"}}'},
        )
        if response.status_code == 200 and response.json().get("data", {}).get(
            "result"
        ):
            return True
    return False


def log_arrived(
    client: httpx.Client,
    run_id: str,
    service_name: str,
    started_at_ns: int,
) -> bool:
    response = client.get(
        f"{LOKI_URL}/loki/api/v1/query_range",
        params={
            "query": f'{{service_name="{service_name}"}}',
            "start": str(started_at_ns),
            "limit": "1000",
        },
    )
    return response.status_code == 200 and run_id in response.text


def main() -> int:
    started_at_ns = time.time_ns()
    run_id, trace_id, service_name = emit_smoke_run()
    deadline = time.monotonic() + DELIVERY_TIMEOUT_SECONDS
    arrived = {"trace": False, "metric": False, "log": False}

    with httpx.Client(timeout=2.0) as client:
        while time.monotonic() < deadline and not all(arrived.values()):
            try:
                arrived["trace"] = arrived["trace"] or trace_arrived(
                    client,
                    trace_id,
                )
                arrived["metric"] = arrived["metric"] or metric_arrived(
                    client,
                    service_name,
                )
                arrived["log"] = arrived["log"] or log_arrived(
                    client,
                    run_id,
                    service_name,
                    started_at_ns,
                )
            except (httpx.HTTPError, ValueError):
                pass
            if not all(arrived.values()):
                time.sleep(1.0)

    missing = [signal for signal, present in arrived.items() if not present]
    if missing:
        print(
            "LGTM smoke test failed; missing signals: " + ", ".join(missing),
        )
        print(f"run_id={run_id} trace_id={trace_id}")
        print(
            "Inspect with: docker compose -f observability/otel/compose.yaml logs",
        )
        return 1

    print(f"LGTM smoke test passed: run_id={run_id} trace_id={trace_id}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
