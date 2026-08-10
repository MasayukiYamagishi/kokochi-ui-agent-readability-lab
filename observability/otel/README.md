# Local OpenTelemetry and LGTM operations

## Scope and source of truth

This directory is the shared, experiment-independent source for the local
Collector and Grafana LGTM stack. The stack is diagnostic infrastructure only.
Immutable experiment artifacts under
`experiments/<experiment-id>/results/<tier>/<run-id>/` remain authoritative,
even when telemetry is unavailable or incomplete.

The stack uses these pinned distributions:

- `otel/opentelemetry-collector:0.156.0`, wrapped only to add an HTTP health
  probe to the upstream minimal runtime image;
- `grafana/otel-lgtm:0.27.1` for local development, demonstrations, and tests.

The Collector core distribution at `0.156.0` includes the configured OTLP
receiver, OTLP and debug exporters, `memory_limiter` and `batch` processors,
and the `health_check` extension. The Go build stage in `Dockerfile` uses only
the standard library. It is necessary because the upstream minimal Collector
runtime has no shell, curl, or wget for a Docker container health check. It
does not replace or rebuild the Collector binary.

## Start and readiness

Run all commands from the repository root:

```bash
docker compose -f observability/otel/compose.yaml config
docker compose -f observability/otel/compose.yaml up -d --build
docker compose -f observability/otel/compose.yaml ps
```

Grafana provisions the repository-managed comparison dashboard and correlated
Prometheus, Tempo, Loki, and Pyroscope datasources during startup. See
[`observability/grafana/README.md`](../grafana/README.md) for the stable URL,
panel definitions, filters, screenshot procedure, and dashboard smoke test.

`otel-collector` becomes healthy only after the `health_check` extension
responds on `http://127.0.0.1:13133/`. The experiment telemetry factory waits
up to 30 seconds for this endpoint before constructing OTLP exporters. If it
does not become ready, OTLP export is disabled for that runtime and a warning
is recorded; experiment execution and write-once result persistence can still
continue.

Enable the verbose debug exporter only for interactive diagnosis:

```bash
docker compose \
  -f observability/otel/compose.yaml \
  -f observability/otel/compose.debug.yaml \
  up -d --build
docker compose -f observability/otel/compose.yaml logs -f otel-collector
```

The normal configuration never enables the debug exporter. Debug output can
be high volume and must not include raw prompts, HTML, images, credentials, or
provider responses.

## Pipeline and resilience decisions

All traces, metrics, and logs use the same explicit pipeline:

1. receive OTLP/gRPC or OTLP/HTTP;
2. apply `memory_limiter` first;
3. batch small local workloads;
4. export to the LGTM OTLP endpoint.

The container limit is 256 MiB. `memory_limiter` uses a 192 MiB hard limit and
64 MiB spike allowance, preserving headroom for the process and health probe.
The batch processor flushes after one second or 512 items.

The LGTM exporter uses a five-second attempt timeout, a 256-request in-memory
queue with two consumers, and bounded exponential retry (1 second initially,
5 seconds maximum, 30 seconds total). These bounds make short LGTM restarts
observable without letting an auxiliary backend stall an experiment. The
queue is intentionally not persistent: Collector restart can discard unsent
telemetry, while the authoritative raw run directory remains write-once and
independent. Do not use telemetry as a result backup.

## Ports

Every published port is bound to `127.0.0.1`; none is intentionally reachable
from the LAN or internet.

| Port | Purpose |
| ---: | --- |
| 13133 | Collector readiness |
| 4317 | OTLP/gRPC input |
| 4318 | OTLP/HTTP input |
| 3000 | Grafana |
| 3100 | Loki API used by the smoke test |
| 3200 | Tempo API used by the smoke test |
| 9090 | Prometheus API used by the smoke test |

Do not change a mapping to `0.0.0.0` without an explicit security review.

## Image identity in run records

Tags select distributions; local content digests identify what actually ran.
After `up -d --build`, the stack must have exactly one running container for
each service. Collection resolves the configured tag, finds that service's
running container, and records the container's immutable image ID rather than
whatever the mutable tag points to later. Pass the result into the normal
execution-environment collector:

```python
from pathlib import Path

from kokochi_ui_agent_readability_lab.records import (
    collect_compose_container_images,
    collect_execution_environment,
)

images = collect_compose_container_images(
    Path("observability/otel/compose.yaml"),
)
environment = collect_execution_environment(browser, container_images=images)
```

Each `container_images` entry records the Compose service, tagged reference,
local `sha256:` image digest, and registry repository digests when Docker
reports them. Collection fails instead of silently recording an unresolved
tag, a stopped or scaled service, or a container that changes during
collection.

## Volume lifecycle and retention

`lgtm-data` contains Grafana state and the local Prometheus, Loki, Tempo, and
Pyroscope data below `/data`. It survives normal `down`, container replacement,
and restart. It is diagnostic data, not an experiment artifact.

The lab retention policy is at most seven days after the associated experiment
finishes. The all-in-one image does not enforce one uniform seven-day policy
across every backend, so the operator must reset the volume at the end of that
window. Before resetting, confirm no active run is using the stack:

```bash
docker compose -f observability/otel/compose.yaml down
docker compose -f observability/otel/compose.yaml down --volumes
docker compose -f observability/otel/compose.yaml up -d --build
```

`down --volumes` permanently deletes local dashboards and telemetry. It never
targets experiment result directories. Do not copy raw experiment artifacts
into `lgtm-data`.

Known limitation: the upstream all-in-one LGTM image can keep a child process
alive past the 30-second Compose grace period and then exit with code 137. The
named volume remains separate from the container, but telemetry is diagnostic
and is not guaranteed to survive a forced stop. After such a stop, restart the
stack and run `pnpm observability:smoke` before relying on new telemetry. This
does not affect the authoritative experiment result directory.

## Diagnosis by state

| State | Expected behavior | Diagnosis |
| --- | --- | --- |
| First start | LGTM becomes healthy, then Collector starts and becomes healthy | `docker compose -f observability/otel/compose.yaml ps`; inspect both service logs if either stays unhealthy |
| Collector restart | SDK batches may retry; unsent in-memory Collector queue can be lost | inspect `otel-collector` logs and rerun the smoke test; authoritative run files remain unchanged |
| Collector stopped | readiness fails and a new telemetry runtime disables OTLP export after its bounded wait | inspect the runner warning and `docker compose ... logs otel-collector`; continue preserving raw results |
| LGTM stopped | Collector remains reachable, then queues and retries for at most 30 seconds | inspect exporter errors, queue/drop metrics in Collector logs, and LGTM logs after restart |

For a concise snapshot:

```bash
docker compose -f observability/otel/compose.yaml ps
docker compose -f observability/otel/compose.yaml logs --tail=200 otel-collector
docker compose -f observability/otel/compose.yaml logs --tail=200 lgtm
```

## Verification and smoke test

Static Compose validation is part of `pnpm test` and can be run separately:

```bash
pnpm observability:config
```

With the stack healthy, emit a uniquely identified dummy trace, metric, and
log through the production telemetry factory and verify them through Tempo,
Prometheus, and Loki:

```bash
pnpm observability:smoke
```

The smoke test does not call a model or a paid API. A failure prints the run
and trace IDs used for log correlation.

To emit deterministic dummy experiment runs and verify every visible dashboard
query plus trace/log drilldown, run:

```bash
pnpm observability:dashboard
```
