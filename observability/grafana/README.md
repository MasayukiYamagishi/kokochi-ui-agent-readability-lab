# Grafana experiment comparison dashboard

## Scope and source of truth

This directory is the repository-managed source for the local Grafana
experiment comparison dashboard. Grafana is diagnostic infrastructure. The
authoritative record remains the immutable run artifacts below
`experiments/<experiment-id>/results/<tier>/<run-id>/`; dashboard values must
not be copied back into those artifacts or used to replace them.

The dashboard intentionally shows the registered metrics independently. It
does not invent a composite quality score. Metric names, attributes, and
histogram boundaries are defined in
[`docs/telemetry.md`](../../docs/telemetry.md).

## Repository layout and stable identities

| Repository path                                     | Container path or identity                                                            |
| --------------------------------------------------- | ------------------------------------------------------------------------------------- |
| `provisioning/datasources/grafana-datasources.yaml` | `/otel-lgtm/grafana/conf/provisioning/datasources/grafana-datasources.yaml`           |
| `provisioning/dashboards/kokochi-dashboards.yaml`   | `/otel-lgtm/grafana/conf/provisioning/dashboards/kokochi-dashboards.yaml`             |
| `dashboards/experiment-comparison.json`             | `/otel-lgtm/grafana/conf/provisioning/dashboards/kokochi/experiment-comparison.json`  |
| `dashboards/task-control-discovery.json`            | `/otel-lgtm/grafana/conf/provisioning/dashboards/kokochi/task-control-discovery.json` |
| Dashboard folder                                    | `KOKOCHI Experiments`, UID `kokochi-experiments`                                      |
| Dashboard                                           | `KOKOCHI experiment comparison`, UID `kokochi-experiment-comparison`                  |
| Task dashboard                                      | `KOKOCHI タスク別テレメトリー`, UID `kokochi-task-telemetry`                          |
| Datasources                                         | UIDs `prometheus`, `tempo`, `loki`, and `pyroscope`                                   |

The files are mounted read-only from `observability/otel/compose.yaml` into the
paths used by the pinned `grafana/otel-lgtm` image. Grafana polls dashboard
files every 30 seconds. Datasources, folders, and dashboards use stable UIDs so
links and trace/log correlations survive container replacement. UI updates are
disabled because Git is the source of configuration truth.

## Start and use

Run from the repository root:

```bash
docker compose -f observability/otel/compose.yaml up -d --build
pnpm observability:dashboard
```

Open
`http://localhost:3000/d/kokochi-experiment-comparison` (anonymous local
access is enabled by the pinned image). The smoke command emits a deterministic
multi-condition, multi-model dummy run matrix through the production telemetry
API, then verifies the exact provisioned dashboard contract, every visible
Prometheus query and its scatter invariants, the newly emitted Tempo trace and
filtered Loki log, and every rendered panel in headless Chromium. It never
invokes a model or paid API. Dummy
data is identifiable by the `kokochi-dashboard-smoke-<uuid>` service name and
must not be presented as an official experiment result.

For `form-group-membership-reconstruction`, open
`http://localhost:3000/d/kokochi-task-telemetry`. The Japanese guide is
authoritative. The task dashboard applies `kokochi.task.id` to cell coverage,
target-control-set F1, validity, latency, tokens, failures, traces, and logs.
Do not run the dummy-data smoke command before taking an article screenshot;
article evidence must come from pilot-runner telemetry.

After the real pilot completes, run `pnpm observability:task-results` while the
stack is still available. It does not execute a new experiment. It reconciles
the 36 immutable run records with Prometheus attempt, failure, and F1 counts,
Tempo traces, and Loki task metadata, then writes the dashboard and four
fixture screenshots to a local directory that is excluded from Git. Failed
runs count as observed attempts but never become F1 zeroes. A failure-only
pilot must show its attempts and failure classes while the F1 panel remains
empty.

The initial 24-hour time range and `All` filter values support side-by-side
comparison. Filters cover experiment/version, fixture/version, input
representation, provider, model/version, prompt version, and scorer version.
The comparison-condition selector changes the grouping of the generic F1
panel between fixture, input representation, and model. Select exactly one
experiment and fixture before following the source path or fixture URL shown
at the top; `All` is a regular-expression comparison value and is not a valid
filesystem or fixture URL segment.

## Panel definitions

Each value is calculated over the selected time range after all filters are
applied:

| Panel group                     | Definition                                                                                                                                                                                                                                                                                  |
| ------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Run attempts and outcomes       | Terminal `kokochi.experiment.run.count` samples, split by success or failure and stable failure classification.                                                                                                                                                                             |
| Complete match                  | `true` complete-match runs divided by all evaluated outcomes, including evaluated schema-invalid responses.                                                                                                                                                                                 |
| Schema validity                 | Schema-valid runs divided by all normalized evaluation attempts with a recorded schema outcome. Provider-stage failures remain visible in run outcomes and errors but are outside this denominator.                                                                                         |
| Hallucination-free rate         | Scored runs whose recorded hallucination count is zero, divided by all scored runs.                                                                                                                                                                                                         |
| Item-association F1             | Mean of exact per-repetition F1 samples within each complete configuration tuple. The displayed 95% interval is the normal-approximation interval `mean ± 1.96 × sample standard deviation / sqrt(n)`; it is omitted for `n < 2` and is not a bootstrap or population confidence guarantee. |
| Distribution and variability    | Per-repetition F1 samples; within each complete configuration tuple, coefficient of variation is sample standard deviation divided by mean and the minimum marks the low outlier. Treatment effects are never reported as repetition variance.                                              |
| Latency                         | Bucket-estimated p50/p95 for per-run end-to-end duration and provider inference duration. Prometheus linearly interpolates inside the explicit classic-histogram buckets; units are seconds.                                                                                                |
| Tokens                          | Mean provider-reported input and output tokens per inference. Estimated input-token metrics remain separate from provider billing tokens.                                                                                                                                                   |
| Quality versus cost             | One point per complete configuration tuple and repetition; x is mean provider-reported input tokens and y is mean F1 for that exact identity. Multiple models or versions are never summed into one point.                                                                                  |
| Error, trace, and log drilldown | Stable error stage/classification totals, the matching `experiment.run` traces in Tempo, and structured logs in Loki under the same dashboard filters. Trace and run IDs are retained only in traces/logs, never in metric labels.                                                          |

PromQL uses the bounded preregistered repetition index rather than run or trace
IDs. `max_over_time` recovers the exact histogram or counter sample for each
repetition series before aggregation. This assumes an experiment does not
reuse the same repetition index for the same full filter tuple inside the
selected time range. If a rerun is needed, use a new experiment version or
clear the local diagnostic volume; immutable result artifacts are unaffected.

All quantitative panels define units, legends, thresholds, and an explicit
no-data message. Empty panels mean that no telemetry matches the filters and
time range; they do not imply a zero score. The trace table opens Tempo's trace
view, where the configured datasource links to matching Loki logs and
Prometheus metrics. Loki trace-ID fields link back to Tempo.

## Screenshot and review procedure

1. Start the fixture and LGTM stack, then run `pnpm observability:dashboard`.
2. Open the stable dashboard URL and set the viewport to at least 1440 by 900.
3. Select one experiment/version and one fixture/version for source-link
   review, then select multiple conditions for the comparison screenshot.
4. Capture the filters and top quality panels, the latency/token/variability
   panels, and the trace/log drilldown. Include the visible time range.
5. Mark screenshots generated from the smoke command as dummy data. For an
   official result, record the experiment ID/version, Git commit, time range,
   and selected filters alongside the screenshot. Do not capture raw prompt,
   response, credential, or private artifact contents.
6. Confirm the experiment directory and fixture URL shown by the dashboard
   resolve to the registered `experiments/<experiment-id>/` source and the
   fixture catalog before review.

## Updating the dashboard

The committed JSON is authoritative and `allowUiUpdates` is false. For a
layout experiment, import a copy under a temporary UID or use a disposable
local Grafana instance. Export the dashboard JSON, then deliberately apply the
reviewed changes to `dashboards/experiment-comparison.json`. Preserve the
stable UID, remove runtime-only numeric `id` values if an export adds them,
keep two-space JSON formatting, and inspect the Git diff. Never edit a
provisioned dashboard and assume Grafana persisted the change.

Dashboard query changes must be coordinated with the telemetry contract. Do
not change prompts, scorers, fixtures, or ground truth merely to populate a
panel. Validate a change with:

```bash
pnpm observability:config
uv run pytest -v tests/unit/test_grafana_dashboard.py tests/unit/test_telemetry.py
pnpm observability:dashboard
```

The unit test pins the folder/dashboard/datasource UIDs, official mount paths,
filter defaults, required panels, metric names, required bounded labels, and
the absence of run/trace IDs in PromQL. The live smoke test fails when Grafana
loads a dashboard contract that differs from the committed JSON, a visible
Prometheus query returns no data, scatter values lose model identity or escape
the F1 range, the newly emitted Tempo/Loki correlation is unavailable, or a
panel renders a client-side error/no-data state in Chromium.

## Known limitations

- LGTM telemetry is best-effort and has the retention/loss properties
  documented in [`observability/otel/README.md`](../otel/README.md).
- The 95% interval is a descriptive normal approximation and can be unstable
  for small repetition counts or non-normal score distributions.
- Histograms exported before the dashboard contract was introduced may lack
  the repetition label or common GenAI dimensions and cannot populate every
  comparison reliably.
- Grafana links assume the fixture development server is available on
  `localhost:5173`.

Provisioning follows Grafana's
[dashboard provisioning](https://grafana.com/docs/grafana/latest/administration/provisioning/#dashboards)
and
[datasource provisioning](https://grafana.com/docs/grafana/latest/administration/provisioning/#data-sources)
contracts. The container paths follow the pinned
[`grafana/docker-otel-lgtm`](https://github.com/grafana/docker-otel-lgtm)
image.
