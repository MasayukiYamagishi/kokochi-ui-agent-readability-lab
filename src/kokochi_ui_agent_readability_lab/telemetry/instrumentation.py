"""Privacy-preserving OpenTelemetry instrumentation for experiment runs."""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from contextvars import Token
from dataclasses import dataclass
from enum import StrEnum
import logging
import math
from pathlib import Path
import re
import time
from typing import Literal, Self
from urllib.parse import urlsplit
from urllib.request import Request, urlopen
from uuid import UUID

from opentelemetry import context as otel_context
from opentelemetry import trace
from opentelemetry._logs import SeverityNumber
from opentelemetry.exporter.otlp.proto.http._log_exporter import OTLPLogExporter
from opentelemetry.exporter.otlp.proto.http.metric_exporter import (
    OTLPMetricExporter,
)
from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
from opentelemetry.metrics import Meter
from opentelemetry.sdk._logs import LoggerProvider
from opentelemetry.sdk._logs.export import BatchLogRecordProcessor
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.metrics.export import PeriodicExportingMetricReader
from opentelemetry.sdk.metrics.view import (
    ExplicitBucketHistogramAggregation,
    View,
)
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor
from opentelemetry.sdk.trace.sampling import ALWAYS_ON
from opentelemetry.trace import Span, SpanKind, Status, StatusCode
from pydantic import NonNegativeInt, PositiveInt, ValidationError, field_validator

from kokochi_ui_agent_readability_lab.evaluation import (
    ControlDiscoveryScoreDocument,
    GroupMembershipScoreDocument,
    ScoreDocument,
)
from kokochi_ui_agent_readability_lab.records.schema import (
    Identifier,
    NonEmptyString,
    RepositoryIdentifier,
    RunRecord,
    StrictModel,
)
from kokochi_ui_agent_readability_lab.records.storage import WriteOnceResultStore


INSTRUMENTATION_SCOPE = "kokochi_ui_agent_readability_lab.telemetry"
INSTRUMENTATION_VERSION = "0.1.0"

# GenAI semantic conventions remain Development. The emitted contract is pinned
# independently of the installed generated Python constants.
GEN_AI_SEMCONV_VERSION = "1.42.0"
GEN_AI_SCHEMA_URL = f"https://opentelemetry.io/schemas/gen-ai/{GEN_AI_SEMCONV_VERSION}"

EXPERIMENT_RUN_SPAN = "experiment.run"
STAGE_SPANS = (
    "fixture.render",
    "input.capture",
    "gen_ai.inference",
    "response.normalize",
    "evaluation.score",
    "result.persist",
)
StageName = Literal[
    "fixture.render",
    "input.capture",
    "gen_ai.inference",
    "response.normalize",
    "evaluation.score",
    "result.persist",
]
InputKind = Literal["html", "aria", "image"]

GEN_AI_OPERATION_DURATION = "gen_ai.client.operation.duration"
GEN_AI_TOKEN_USAGE = "gen_ai.client.token.usage"
RUN_COUNT = "kokochi.experiment.run.count"
RUN_DURATION = "kokochi.experiment.run.duration"
ITEM_ASSOCIATION_F1 = "kokochi.evaluation.item_association.f1"
TARGET_CONTROL_SET_F1 = "kokochi.evaluation.target_control_set.f1"
GROUP_MEMBERSHIP_F1 = "kokochi.evaluation.group_membership.f1"
COMPLETE_MATCH_COUNT = "kokochi.evaluation.complete_match.count"
SCHEMA_VALID_COUNT = "kokochi.evaluation.schema_valid.count"
HALLUCINATION_COUNT = "kokochi.evaluation.hallucination.count"
INPUT_SIZE = "kokochi.input.size"
ESTIMATED_INPUT_TOKEN_USAGE = "kokochi.input.estimated_token.usage"

GEN_AI_DURATION_BOUNDARIES = (
    0.01,
    0.02,
    0.04,
    0.08,
    0.16,
    0.32,
    0.64,
    1.28,
    2.56,
    5.12,
    10.24,
    20.48,
    40.96,
    81.92,
)
TOKEN_BOUNDARIES = (
    1,
    4,
    16,
    64,
    256,
    1024,
    4096,
    16384,
    65536,
    262144,
    1048576,
    4194304,
    16777216,
    67108864,
)
F1_BOUNDARIES = tuple(index / 10 for index in range(11))
HALLUCINATION_BOUNDARIES = (0, 1, 2, 3, 5, 8, 13, 21)
INPUT_SIZE_BOUNDARIES = (
    256,
    1024,
    4096,
    16384,
    65536,
    262144,
    1048576,
    4194304,
    16777216,
)

_LOGGER = logging.getLogger(__name__)
_ERROR_TYPE_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
_INPUT_KINDS = frozenset(("html", "aria", "image"))


class RunStatus(StrEnum):
    """Low-cardinality terminal run states used on metrics."""

    SUCCESS = "success"
    FAILURE = "failure"


class TelemetryFailureStage(StrEnum):
    """Stable failure stages matching the persisted run-record vocabulary."""

    RUN_SETUP = "run-setup"
    INPUT_COLLECTION = "input-collection"
    PROVIDER = "provider"
    NORMALIZATION = "normalization"
    SCORING = "scoring"
    PERSISTENCE = "persistence"
    UNKNOWN = "unknown"


_STAGE_FAILURES = {
    "fixture.render": TelemetryFailureStage.INPUT_COLLECTION,
    "input.capture": TelemetryFailureStage.INPUT_COLLECTION,
    "gen_ai.inference": TelemetryFailureStage.PROVIDER,
    "response.normalize": TelemetryFailureStage.NORMALIZATION,
    "evaluation.score": TelemetryFailureStage.SCORING,
    "result.persist": TelemetryFailureStage.PERSISTENCE,
}


class TelemetrySettings(StrictModel):
    """Local SDK and optional OTLP export settings."""

    export_otlp: bool = False
    service_name: NonEmptyString = "kokochi-ui-agent-readability-lab"
    service_version: NonEmptyString = INSTRUMENTATION_VERSION
    batch_schedule_delay_ms: PositiveInt = 5000
    metric_export_interval_ms: PositiveInt = 10000
    shutdown_timeout_ms: PositiveInt = 30000
    wait_for_collector_ready: bool = True
    collector_health_url: NonEmptyString = "http://127.0.0.1:13133/"
    collector_startup_timeout_ms: PositiveInt = 30000
    collector_check_interval_ms: PositiveInt = 250
    collector_request_timeout_ms: PositiveInt = 1000


class RunTelemetryContext(StrictModel):
    """Approved identifiers shared by spans, metrics, logs, and the manifest."""

    run_id: UUID
    experiment_id: RepositoryIdentifier
    experiment_version: RepositoryIdentifier
    fixture_id: RepositoryIdentifier
    fixture_version: RepositoryIdentifier
    task_id: RepositoryIdentifier | None = None
    input_representation: Identifier
    provider: Identifier
    model_id: NonEmptyString
    prompt_version: RepositoryIdentifier
    scorer_id: Identifier
    scorer_version: RepositoryIdentifier
    gen_ai_operation_name: Identifier = "generate_content"
    repetition_index: NonNegativeInt = 0

    @field_validator("run_id")
    @classmethod
    def run_id_must_not_be_nil(cls, value: UUID) -> UUID:
        if value.int == 0:
            raise ValueError("run_id must not be the nil UUID")
        return value


@dataclass(frozen=True, slots=True)
class _EvaluationMeasurements:
    """Internally validated measurements from the canonical score artifact."""

    experiment_id: str
    experiment_version: str
    fixture_id: str
    fixture_version: str
    scorer_id: str
    scorer_version: str
    task_id: str | None
    input_representation: str | None
    item_association_f1: float | None
    target_control_set_f1: float | None
    group_membership_f1: float | None
    complete_match: bool
    schema_valid: bool
    hallucinated_mapping_count: int | None

    @classmethod
    def from_score(
        cls,
        score: ScoreDocument
        | ControlDiscoveryScoreDocument
        | GroupMembershipScoreDocument,
    ) -> Self:
        if isinstance(score, GroupMembershipScoreDocument):
            try:
                score = GroupMembershipScoreDocument.model_validate(
                    score.model_dump(mode="python")
                )
            except (TypeError, ValueError, ValidationError) as error:
                raise ValueError("score document is not structurally valid") from error
            group_metrics = score.metrics
            if (
                score.schema_valid != group_metrics.schema_valid
                or score.task_success != group_metrics.complete_response
                or score.end_to_end_success != score.task_success
                or score.target.task_id != group_metrics.task_id
                or score.target.fixture_id != group_metrics.fixture_id
            ):
                raise ValueError(
                    "score document contains inconsistent evaluation metrics"
                )
            return cls(
                experiment_id=score.target.experiment_id,
                experiment_version=score.target.experiment_version,
                fixture_id=score.target.fixture_id,
                fixture_version=score.target.fixture_version,
                scorer_id=score.scorer.scorer_id,
                scorer_version=score.scorer.scorer_version,
                task_id=score.target.task_id,
                input_representation=score.target.input_representation,
                item_association_f1=None,
                target_control_set_f1=None,
                group_membership_f1=group_metrics.f1,
                complete_match=group_metrics.exact_match,
                schema_valid=score.schema_valid,
                hallucinated_mapping_count=None,
            )
        if isinstance(score, ControlDiscoveryScoreDocument):
            try:
                score = ControlDiscoveryScoreDocument.model_validate(
                    score.model_dump(mode="python")
                )
            except (TypeError, ValueError, ValidationError) as error:
                raise ValueError("score document is not structurally valid") from error

            metrics = score.metrics
            if metrics.valid_response:
                counts = (
                    metrics.true_positive_count,
                    metrics.false_positive_count,
                    metrics.false_negative_count,
                    metrics.predicted_target_count,
                )
                if any(value is None for value in counts):
                    raise ValueError(
                        "score document contains inconsistent evaluation metrics"
                    )
                true_positive_count = metrics.true_positive_count
                false_positive_count = metrics.false_positive_count
                false_negative_count = metrics.false_negative_count
                predicted_target_count = metrics.predicted_target_count
                assert true_positive_count is not None
                assert false_positive_count is not None
                assert false_negative_count is not None
                assert predicted_target_count is not None
                if metrics.ground_truth_target_count == 0:
                    expected_recall = 1.0
                    expected_precision = 1.0 if predicted_target_count == 0 else 0.0
                    expected_f1 = 1.0 if predicted_target_count == 0 else 0.0
                elif predicted_target_count == 0:
                    expected_precision = 0.0
                    expected_recall = 0.0
                    expected_f1 = 0.0
                else:
                    expected_precision = true_positive_count / predicted_target_count
                    expected_recall = (
                        true_positive_count / metrics.ground_truth_target_count
                    )
                    expected_f1 = (
                        2
                        * expected_precision
                        * expected_recall
                        / (expected_precision + expected_recall)
                        if expected_precision + expected_recall
                        else 0.0
                    )
                inconsistent = (
                    metrics.precision is None
                    or metrics.recall is None
                    or predicted_target_count
                    != true_positive_count + false_positive_count
                    or metrics.ground_truth_target_count
                    != true_positive_count + false_negative_count
                    or not math.isclose(
                        metrics.precision, expected_precision, abs_tol=1e-12
                    )
                    or not math.isclose(metrics.recall, expected_recall, abs_tol=1e-12)
                    or not math.isclose(metrics.f1, expected_f1, abs_tol=1e-12)
                )
            else:
                inconsistent = metrics.f1 != 0.0

            if (
                inconsistent
                or score.schema_valid != metrics.valid_response
                or score.task_success != metrics.complete_response
                or score.end_to_end_success != score.task_success
            ):
                raise ValueError(
                    "score document contains inconsistent evaluation metrics"
                )

            return cls(
                experiment_id=score.target.experiment_id,
                experiment_version=score.target.experiment_version,
                fixture_id=score.target.fixture_id,
                fixture_version=score.target.fixture_version,
                scorer_id=score.scorer.scorer_id,
                scorer_version=score.scorer.scorer_version,
                task_id=score.target.task_id,
                input_representation=score.target.input_representation,
                item_association_f1=None,
                target_control_set_f1=metrics.f1,
                group_membership_f1=None,
                complete_match=metrics.exact_match,
                schema_valid=score.schema_valid,
                hallucinated_mapping_count=None,
            )
        if not isinstance(score, ScoreDocument):
            raise TypeError("score must be a supported score document")
        try:
            score = ScoreDocument.model_validate(score.model_dump(mode="python"))
        except (TypeError, ValueError, ValidationError) as error:
            raise ValueError("score document is not structurally valid") from error

        item_metrics = score.metrics
        if item_metrics is None:
            if score.schema_valid or score.task_success or score.end_to_end_success:
                raise ValueError(
                    "score document contains inconsistent evaluation metrics"
                )
            complete_match = False
            item_association_f1 = None
        else:
            expected_precision = (
                item_metrics.true_positive_count / item_metrics.predicted_mapping_count
                if item_metrics.predicted_mapping_count
                else 0.0
            )
            expected_recall = (
                item_metrics.true_positive_count
                / item_metrics.ground_truth_mapping_count
            )
            expected_f1 = (
                2
                * expected_precision
                * expected_recall
                / (expected_precision + expected_recall)
                if expected_precision + expected_recall
                else 0.0
            )
            expected_complete_match = (
                item_metrics.true_positive_count
                == item_metrics.ground_truth_mapping_count
                and item_metrics.false_positive_count == 0
            )
            expected_purpose_accuracy = (
                item_metrics.purpose_correct_count
                / item_metrics.ground_truth_mapping_count
            )
            diagnostic_count = score.diagnostics.hallucinated_mapping_count
            if (
                not score.schema_valid
                or item_metrics.predicted_mapping_count
                != item_metrics.true_positive_count + item_metrics.false_positive_count
                or item_metrics.ground_truth_mapping_count
                != item_metrics.true_positive_count + item_metrics.false_negative_count
                or not math.isclose(
                    item_metrics.precision, expected_precision, abs_tol=1e-12
                )
                or not math.isclose(item_metrics.recall, expected_recall, abs_tol=1e-12)
                or not math.isclose(item_metrics.f1, expected_f1, abs_tol=1e-12)
                or item_metrics.complete_match != expected_complete_match
                or item_metrics.task_success != item_metrics.complete_match
                or score.task_success != item_metrics.task_success
                or score.end_to_end_success != score.task_success
                or item_metrics.hallucinated_mapping_count
                != item_metrics.false_positive_count
                or diagnostic_count != item_metrics.hallucinated_mapping_count
                or item_metrics.purpose_correct_count
                > item_metrics.ground_truth_mapping_count
                or not math.isclose(
                    item_metrics.purpose_accuracy,
                    expected_purpose_accuracy,
                    abs_tol=1e-12,
                )
            ):
                raise ValueError(
                    "score document contains inconsistent evaluation metrics"
                )
            complete_match = item_metrics.complete_match
            item_association_f1 = item_metrics.f1

        return cls(
            experiment_id=score.target.experiment_id,
            experiment_version=score.target.experiment_version,
            fixture_id=score.target.fixture_id,
            fixture_version=score.target.fixture_version,
            scorer_id=score.scorer.scorer_id,
            scorer_version=score.scorer.scorer_version,
            task_id=None,
            input_representation=None,
            item_association_f1=item_association_f1,
            target_control_set_f1=None,
            group_membership_f1=None,
            complete_match=complete_match,
            schema_valid=score.schema_valid,
            hallucinated_mapping_count=(score.diagnostics.hallucinated_mapping_count),
        )


class RunRecordCorrelationError(ValueError):
    """Raised when a run manifest does not belong to the active trace."""


def metric_views() -> tuple[View, ...]:
    """Return the frozen explicit histogram boundaries for this schema version."""

    boundaries_by_name: dict[str, Sequence[float]] = {
        GEN_AI_OPERATION_DURATION: GEN_AI_DURATION_BOUNDARIES,
        RUN_DURATION: GEN_AI_DURATION_BOUNDARIES,
        GEN_AI_TOKEN_USAGE: TOKEN_BOUNDARIES,
        ITEM_ASSOCIATION_F1: F1_BOUNDARIES,
        TARGET_CONTROL_SET_F1: F1_BOUNDARIES,
        GROUP_MEMBERSHIP_F1: F1_BOUNDARIES,
        HALLUCINATION_COUNT: HALLUCINATION_BOUNDARIES,
        INPUT_SIZE: INPUT_SIZE_BOUNDARIES,
        ESTIMATED_INPUT_TOKEN_USAGE: TOKEN_BOUNDARIES,
    }
    return tuple(
        View(
            instrument_name=name,
            aggregation=ExplicitBucketHistogramAggregation(boundaries=boundaries),
        )
        for name, boundaries in boundaries_by_name.items()
    )


class _Instruments:
    def __init__(self, meter: Meter) -> None:
        self.gen_ai_duration = meter.create_histogram(
            GEN_AI_OPERATION_DURATION,
            unit="s",
            description="GenAI operation duration.",
        )
        self.gen_ai_tokens = meter.create_histogram(
            GEN_AI_TOKEN_USAGE,
            unit="{token}",
            description="Number of input and output tokens used.",
        )
        self.run_count = meter.create_counter(
            RUN_COUNT,
            unit="{run}",
            description="Number of completed experiment run attempts.",
        )
        self.run_duration = meter.create_histogram(
            RUN_DURATION,
            unit="s",
            description="End-to-end duration of one experiment run attempt.",
        )
        self.item_association_f1 = meter.create_histogram(
            ITEM_ASSOCIATION_F1,
            unit="1",
            description="Per-run item-association F1 score.",
        )
        self.target_control_set_f1 = meter.create_histogram(
            TARGET_CONTROL_SET_F1,
            unit="1",
            description="Per-run task-scoped target-control set F1 score.",
        )
        self.group_membership_f1 = meter.create_histogram(
            GROUP_MEMBERSHIP_F1,
            unit="1",
            description="Per-run task-scoped control-group membership F1 score.",
        )
        self.complete_match_count = meter.create_counter(
            COMPLETE_MATCH_COUNT,
            unit="{run}",
            description="Number of evaluated runs by complete-match outcome.",
        )
        self.schema_valid_count = meter.create_counter(
            SCHEMA_VALID_COUNT,
            unit="{run}",
            description="Number of evaluated runs by schema-valid outcome.",
        )
        self.hallucination_count = meter.create_histogram(
            HALLUCINATION_COUNT,
            unit="{mapping}",
            description="Hallucinated item mappings observed in one run.",
        )
        self.input_size = meter.create_histogram(
            INPUT_SIZE,
            unit="By",
            description="Captured input size by representation kind.",
        )
        self.estimated_input_tokens = meter.create_histogram(
            ESTIMATED_INPUT_TOKEN_USAGE,
            unit="{token}",
            description="Estimated input token count by representation kind.",
        )


@dataclass
class TelemetryRuntime:
    """An isolated SDK runtime that never mutates global OpenTelemetry providers."""

    tracer_provider: TracerProvider
    meter_provider: MeterProvider
    logger_provider: LoggerProvider
    shutdown_timeout_ms: int = 30000

    def __post_init__(self) -> None:
        self._tracer = self.tracer_provider.get_tracer(
            INSTRUMENTATION_SCOPE,
            INSTRUMENTATION_VERSION,
            schema_url=GEN_AI_SCHEMA_URL,
        )
        meter = self.meter_provider.get_meter(
            INSTRUMENTATION_SCOPE,
            INSTRUMENTATION_VERSION,
            schema_url=GEN_AI_SCHEMA_URL,
        )
        self._instruments = _Instruments(meter)
        self._otel_logger = self.logger_provider.get_logger(
            INSTRUMENTATION_SCOPE,
            INSTRUMENTATION_VERSION,
            schema_url=GEN_AI_SCHEMA_URL,
        )
        self._shutdown = False
        self._shutdown_result: bool | None = None

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *_args: object) -> None:
        self.shutdown()

    def start_run(self, context: RunTelemetryContext) -> ExperimentRun:
        """Create a run context whose generated trace ID belongs in ``run.json``."""

        if self._shutdown:
            raise RuntimeError("telemetry runtime is shut down")
        return ExperimentRun(self, context)

    def shutdown(self) -> bool:
        """Flush every signal and suppress exporter failures from the application."""

        if self._shutdown_result is not None:
            return self._shutdown_result
        self._shutdown = True

        succeeded = True
        providers = (
            ("logs", self.logger_provider),
            ("metrics", self.meter_provider),
            ("traces", self.tracer_provider),
        )
        for signal, provider in providers:
            try:
                flushed = provider.force_flush(timeout_millis=self.shutdown_timeout_ms)
                if flushed is False:
                    succeeded = False
                    _LOGGER.warning(
                        "OpenTelemetry force_flush timed out",
                        extra={"signal": signal},
                    )
            except Exception as error:  # pragma: no cover - exporter dependent
                succeeded = False
                _log_sdk_failure("force_flush", signal, error)

        for signal, provider in providers:
            try:
                provider.shutdown()
            except Exception as error:  # pragma: no cover - exporter dependent
                succeeded = False
                _log_sdk_failure("shutdown", signal, error)
        self._shutdown_result = succeeded
        return succeeded


CollectorHealthProbe = Callable[[str, int], bool]


def collector_is_ready(endpoint: str, request_timeout_ms: int = 1000) -> bool:
    """Return whether the Collector health endpoint accepts an HTTP request."""

    if request_timeout_ms <= 0:
        raise ValueError("request_timeout_ms must be positive")
    parsed_endpoint = urlsplit(endpoint)
    if (
        parsed_endpoint.scheme not in {"http", "https"}
        or parsed_endpoint.hostname is None
        or parsed_endpoint.username is not None
        or parsed_endpoint.password is not None
    ):
        return False
    request = Request(endpoint, headers={"User-Agent": "kokochi-collector-readiness/1"})
    try:
        with urlopen(request, timeout=request_timeout_ms / 1000) as response:
            return 200 <= response.status < 300
    except (OSError, ValueError):
        return False


def wait_for_collector(
    endpoint: str,
    *,
    startup_timeout_ms: int = 30000,
    check_interval_ms: int = 250,
    request_timeout_ms: int = 1000,
    probe: CollectorHealthProbe = collector_is_ready,
) -> bool:
    """Wait for Collector readiness without turning telemetry into a run dependency."""

    if startup_timeout_ms <= 0:
        raise ValueError("startup_timeout_ms must be positive")
    if check_interval_ms <= 0:
        raise ValueError("check_interval_ms must be positive")
    if request_timeout_ms <= 0:
        raise ValueError("request_timeout_ms must be positive")

    deadline = time.monotonic() + startup_timeout_ms / 1000
    while True:
        if probe(endpoint, request_timeout_ms):
            return True
        remaining_seconds = deadline - time.monotonic()
        if remaining_seconds <= 0:
            return False
        time.sleep(min(check_interval_ms / 1000, remaining_seconds))


def create_otlp_telemetry(
    settings: TelemetrySettings | None = None,
) -> TelemetryRuntime:
    """Create an always-on local SDK with optional OTLP/HTTP exporters.

    OTLP exporters use the standard ``OTEL_EXPORTER_OTLP_*`` environment
    variables. Connection attempts happen in processors, where the SDK isolates
    export failures from experiment execution.
    """

    resolved = settings or TelemetrySettings()
    resource = Resource.create(
        {
            "service.name": resolved.service_name,
            "service.version": resolved.service_version,
        }
    )

    tracer_provider = TracerProvider(resource=resource, sampler=ALWAYS_ON)
    metric_readers = []
    logger_provider = LoggerProvider(resource=resource, shutdown_on_exit=False)

    export_otlp = resolved.export_otlp
    if export_otlp and resolved.wait_for_collector_ready:
        export_otlp = wait_for_collector(
            resolved.collector_health_url,
            startup_timeout_ms=resolved.collector_startup_timeout_ms,
            check_interval_ms=resolved.collector_check_interval_ms,
            request_timeout_ms=resolved.collector_request_timeout_ms,
        )
        if not export_otlp:
            _LOGGER.warning(
                "OpenTelemetry Collector did not become ready; OTLP export is disabled "
                "for this runtime",
                extra={"collector_health_url": resolved.collector_health_url},
            )

    if export_otlp:
        try:
            tracer_provider.add_span_processor(
                BatchSpanProcessor(
                    OTLPSpanExporter(),
                    schedule_delay_millis=resolved.batch_schedule_delay_ms,
                )
            )
            metric_readers.append(
                PeriodicExportingMetricReader(
                    OTLPMetricExporter(),
                    export_interval_millis=resolved.metric_export_interval_ms,
                )
            )
            logger_provider.add_log_record_processor(
                BatchLogRecordProcessor(
                    OTLPLogExporter(),
                    schedule_delay_millis=resolved.batch_schedule_delay_ms,
                )
            )
        except Exception as error:  # pragma: no cover - environment dependent
            _log_sdk_failure("configure", "otlp", error)

    meter_provider = MeterProvider(
        resource=resource,
        metric_readers=metric_readers,
        views=metric_views(),
    )
    return TelemetryRuntime(
        tracer_provider=tracer_provider,
        meter_provider=meter_provider,
        logger_provider=logger_provider,
        shutdown_timeout_ms=resolved.shutdown_timeout_ms,
    )


class ExperimentRun:
    """One active ``experiment.run`` span and its correlated signals."""

    def __init__(
        self,
        runtime: TelemetryRuntime,
        context: RunTelemetryContext,
    ) -> None:
        self._runtime = runtime
        self.context = context
        self._span: Span | None = None
        self._context_token: Token[otel_context.Context] | None = None
        self._failure_stage: TelemetryFailureStage | None = None
        self._error_type: str | None = None
        self._active_stage = False
        self._started_at = 0.0

    def __enter__(self) -> Self:
        if self._span is not None:
            raise RuntimeError("experiment run cannot be entered more than once")
        self._started_at = time.monotonic()
        self._span = self._runtime._tracer.start_span(
            EXPERIMENT_RUN_SPAN,
            kind=SpanKind.INTERNAL,
            attributes=self._span_attributes(),
        )
        self._context_token = otel_context.attach(trace.set_span_in_context(self._span))
        self._log("run.started", EXPERIMENT_RUN_SPAN)
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        _traceback: object,
    ) -> None:
        span = self._require_active_span()
        try:
            if exc_value is not None:
                error_type = _safe_error_type(exc_value)
                self._mark_failed(TelemetryFailureStage.UNKNOWN, error_type)
                _record_safe_exception(span, exc_value, error_type)

            if self._failure_stage is None:
                span.set_status(Status(StatusCode.OK))
                self._record_run_count(RunStatus.SUCCESS)
                self._record_run_duration(RunStatus.SUCCESS)
                self._log("run.completed", EXPERIMENT_RUN_SPAN)
            else:
                assert self._error_type is not None
                span.set_attribute("error.type", self._error_type)
                span.set_attribute("kokochi.failure.stage", self._failure_stage.value)
                span.set_status(Status(StatusCode.ERROR))
                self._record_run_count(RunStatus.FAILURE)
                self._record_run_duration(RunStatus.FAILURE)
                self._log(
                    "run.failed",
                    EXPERIMENT_RUN_SPAN,
                    failure_stage=self._failure_stage,
                    error_type=self._error_type,
                )
        finally:
            if self._context_token is not None:
                otel_context.detach(self._context_token)
            span.end()

    @property
    def trace_id(self) -> str:
        """Return the lowercase 32-hex trace identifier for the run manifest."""

        span = self._require_active_span()
        trace_id = span.get_span_context().trace_id
        if trace_id == 0:
            raise RuntimeError("the configured tracer did not create a valid trace ID")
        return f"{trace_id:032x}"

    def stage(self, name: StageName) -> StageTelemetry:
        """Create one of the fixed child stages for this run."""

        self._require_active_span()
        if name not in STAGE_SPANS:
            raise ValueError(f"unsupported experiment stage: {name!r}")
        if self._active_stage:
            raise RuntimeError("experiment stages must not overlap")
        return StageTelemetry(self, name)

    def validate_record(self, record: RunRecord) -> None:
        """Require a manifest to match every correlation field owned by this run."""

        if not isinstance(record, RunRecord):
            raise TypeError("record must be a RunRecord")
        expected = {
            "run_id": self.context.run_id,
            "trace_id": self.trace_id,
            "experiment.experiment_id": self.context.experiment_id,
            "experiment.version": self.context.experiment_version,
            "fixture.fixture_id": self.context.fixture_id,
            "fixture.version": self.context.fixture_version,
            "task_id": self.context.task_id,
            "input_collection.representation": self.context.input_representation,
            "model.provider": self.context.provider,
            "model.model_id": self.context.model_id,
            "prompt.version": self.context.prompt_version,
            "execution.repetition_index": self.context.repetition_index,
        }
        actual = {
            "run_id": record.run_id,
            "trace_id": record.trace_id,
            "experiment.experiment_id": record.experiment.experiment_id,
            "experiment.version": record.experiment.version,
            "fixture.fixture_id": record.fixture.fixture_id,
            "fixture.version": record.fixture.version,
            "task_id": record.task_id,
            "input_collection.representation": record.input_collection.representation,
            "model.provider": record.model.provider,
            "model.model_id": record.model.model_id,
            "prompt.version": record.prompt.version,
            "execution.repetition_index": record.execution.repetition_index,
        }
        if record.score is not None:
            expected["score.scorer_id"] = self.context.scorer_id
            expected["score.scorer_version"] = self.context.scorer_version
            actual["score.scorer_id"] = record.score.scorer_id
            actual["score.scorer_version"] = record.score.scorer_version

        mismatches = [name for name, value in actual.items() if value != expected[name]]
        if mismatches:
            raise RunRecordCorrelationError(
                "run record does not match active telemetry context: "
                + ", ".join(mismatches)
            )

    def persist_result(
        self,
        store: WriteOnceResultStore,
        record: RunRecord,
        artifacts: Mapping[str, bytes],
    ) -> Path:
        """Validate and atomically persist the authoritative result in its stage."""

        self.validate_record(record)
        with self.stage("result.persist"):
            return store.save(record, artifacts)

    def mark_failed(
        self,
        failure_stage: TelemetryFailureStage,
        error_type: str,
    ) -> None:
        """Mark a handled terminal failure without exposing an exception message."""

        self._require_active_span()
        if not isinstance(failure_stage, TelemetryFailureStage):
            raise TypeError("failure_stage must be a TelemetryFailureStage")
        self._mark_failed(failure_stage, _validated_error_type(error_type))

    def record_input_size(
        self,
        kind: InputKind,
        byte_count: int,
        *,
        estimated_tokens: int | None = None,
        estimation_method: str | None = None,
    ) -> None:
        """Record captured input sizes without recording input content."""

        span = self._require_active_span()
        if kind not in _INPUT_KINDS:
            raise ValueError(f"unsupported input kind: {kind!r}")
        _require_non_negative_int(byte_count, "byte_count")
        if estimated_tokens is None and estimation_method is not None:
            raise ValueError("estimation_method requires an estimated token count")
        if estimated_tokens is not None and estimation_method is None:
            raise ValueError("estimated token counts require an estimation_method")

        attributes = {**self._metric_attributes(), "kokochi.input.kind": kind}
        self._runtime._instruments.input_size.record(byte_count, attributes)
        span.set_attribute(f"kokochi.input.{kind}.size", byte_count)

        if estimated_tokens is not None:
            _require_non_negative_int(estimated_tokens, "estimated_tokens")
            method = _validated_identifier(estimation_method, "estimation_method")
            token_attributes = {
                **attributes,
                "kokochi.input.token_estimation.method": method,
            }
            self._runtime._instruments.estimated_input_tokens.record(
                estimated_tokens,
                token_attributes,
            )
            span.set_attribute(
                f"kokochi.input.{kind}.estimated_tokens", estimated_tokens
            )

    def record_evaluation(
        self,
        score: ScoreDocument
        | ControlDiscoveryScoreDocument
        | GroupMembershipScoreDocument,
    ) -> None:
        """Record metrics derived only from the canonical score artifact."""

        span = self._require_active_span()
        measurements = _EvaluationMeasurements.from_score(score)
        expected = (
            self.context.experiment_id,
            self.context.experiment_version,
            self.context.fixture_id,
            self.context.fixture_version,
            self.context.scorer_id,
            self.context.scorer_version,
        )
        actual = (
            measurements.experiment_id,
            measurements.experiment_version,
            measurements.fixture_id,
            measurements.fixture_version,
            measurements.scorer_id,
            measurements.scorer_version,
        )
        if actual != expected:
            raise ValueError(
                "evaluation identifiers do not match the telemetry run context"
            )
        if measurements.task_id != self.context.task_id:
            raise ValueError(
                "evaluation task_id does not match the telemetry run context"
            )
        if (
            measurements.input_representation is not None
            and measurements.input_representation != self.context.input_representation
        ):
            raise ValueError(
                "evaluation input representation does not match the telemetry run context"
            )

        attributes = self._metric_attributes()
        instruments = self._runtime._instruments
        if measurements.item_association_f1 is not None:
            instruments.item_association_f1.record(
                measurements.item_association_f1,
                attributes,
            )
            span.set_attribute(
                "kokochi.evaluation.item_association.f1",
                measurements.item_association_f1,
            )
        if measurements.target_control_set_f1 is not None:
            instruments.target_control_set_f1.record(
                measurements.target_control_set_f1,
                attributes,
            )
            span.set_attribute(
                "kokochi.evaluation.target_control_set.f1",
                measurements.target_control_set_f1,
            )
        if measurements.group_membership_f1 is not None:
            instruments.group_membership_f1.record(
                measurements.group_membership_f1,
                attributes,
            )
            span.set_attribute(
                "kokochi.evaluation.group_membership.f1",
                measurements.group_membership_f1,
            )

        instruments.complete_match_count.add(
            1,
            {
                **attributes,
                "kokochi.evaluation.outcome": str(measurements.complete_match).lower(),
            },
        )
        instruments.schema_valid_count.add(
            1,
            {
                **attributes,
                "kokochi.evaluation.outcome": str(measurements.schema_valid).lower(),
            },
        )
        span.set_attribute(
            "kokochi.evaluation.complete_match", measurements.complete_match
        )
        span.set_attribute("kokochi.evaluation.schema_valid", measurements.schema_valid)

        if measurements.hallucinated_mapping_count is not None:
            instruments.hallucination_count.record(
                measurements.hallucinated_mapping_count,
                attributes,
            )
            span.set_attribute(
                "kokochi.evaluation.hallucination.count",
                measurements.hallucinated_mapping_count,
            )

    def _span_attributes(self) -> dict[str, str | int]:
        attributes: dict[str, str | int] = {
            "kokochi.run.id": str(self.context.run_id),
            "kokochi.experiment.id": self.context.experiment_id,
            "kokochi.experiment.version": self.context.experiment_version,
            "kokochi.fixture.id": self.context.fixture_id,
            "kokochi.fixture.version": self.context.fixture_version,
            "kokochi.input.representation": self.context.input_representation,
            "gen_ai.provider.name": self.context.provider,
            "gen_ai.request.model": self.context.model_id,
            "gen_ai.operation.name": self.context.gen_ai_operation_name,
            "kokochi.prompt.version": self.context.prompt_version,
            "kokochi.scorer.id": self.context.scorer_id,
            "kokochi.scorer.version": self.context.scorer_version,
            "kokochi.repetition.index": self.context.repetition_index,
        }
        if self.context.task_id is not None:
            attributes["kokochi.task.id"] = self.context.task_id
        return attributes

    def _metric_attributes(self) -> dict[str, str | int]:
        # Every value comes from a finite, approved experiment configuration.
        # run_id, trace_id, URLs, and raw content are intentionally absent.
        attributes: dict[str, str | int] = {
            "kokochi.experiment.id": self.context.experiment_id,
            "kokochi.experiment.version": self.context.experiment_version,
            "kokochi.fixture.id": self.context.fixture_id,
            "kokochi.fixture.version": self.context.fixture_version,
            "kokochi.input.representation": self.context.input_representation,
            "gen_ai.provider.name": self.context.provider,
            "gen_ai.request.model": self.context.model_id,
            "kokochi.prompt.version": self.context.prompt_version,
            "kokochi.scorer.id": self.context.scorer_id,
            "kokochi.scorer.version": self.context.scorer_version,
            "kokochi.repetition.index": self.context.repetition_index,
        }
        if self.context.task_id is not None:
            attributes["kokochi.task.id"] = self.context.task_id
        return attributes

    def _gen_ai_metric_attributes(
        self,
        *,
        response_model: str | None = None,
        error_type: str | None = None,
    ) -> dict[str, str | int]:
        attributes = {
            **self._metric_attributes(),
            "gen_ai.operation.name": self.context.gen_ai_operation_name,
            "gen_ai.provider.name": self.context.provider,
            "gen_ai.request.model": self.context.model_id,
        }
        if response_model is not None:
            attributes["gen_ai.response.model"] = response_model
        if error_type is not None:
            attributes["error.type"] = error_type
        return attributes

    def _terminal_metric_attributes(
        self,
        status: RunStatus,
    ) -> dict[str, str | int]:
        attributes = {
            **self._metric_attributes(),
            "kokochi.run.status": status.value,
        }
        if self._failure_stage is not None:
            attributes["kokochi.failure.stage"] = self._failure_stage.value
        if self._error_type is not None:
            attributes["error.type"] = self._error_type
        return attributes

    def _record_run_count(self, status: RunStatus) -> None:
        attributes = self._terminal_metric_attributes(status)
        self._runtime._instruments.run_count.add(1, attributes)

    def _record_run_duration(self, status: RunStatus) -> None:
        duration = max(0.0, time.monotonic() - self._started_at)
        attributes = self._terminal_metric_attributes(status)
        self._runtime._instruments.run_duration.record(duration, attributes)

    def _mark_failed(
        self,
        failure_stage: TelemetryFailureStage,
        error_type: str,
    ) -> None:
        if self._failure_stage is not None:
            return
        self._failure_stage = failure_stage
        self._error_type = error_type
        self._require_active_span().add_event(
            "stage.failed",
            {
                "kokochi.failure.stage": failure_stage.value,
                "error.type": error_type,
            },
        )

    def _log(
        self,
        event_name: str,
        stage: str,
        *,
        failure_stage: TelemetryFailureStage | None = None,
        error_type: str | None = None,
        retry_attempt: int | None = None,
    ) -> None:
        attributes: dict[str, str | int] = {
            **self._metric_attributes(),
            "run_id": str(self.context.run_id),
            "trace_id": self.trace_id,
            "stage": stage,
            "event.name": event_name,
        }
        if failure_stage is not None:
            attributes["failure.stage"] = failure_stage.value
        if error_type is not None:
            attributes["error.classification"] = error_type
        if retry_attempt is not None:
            attributes["retry.attempt"] = retry_attempt
        self._runtime._otel_logger.emit(
            context=otel_context.get_current(),
            severity_number=SeverityNumber.INFO,
            severity_text="INFO",
            body=event_name,
            attributes=attributes,
            event_name=event_name,
        )

    def _require_active_span(self) -> Span:
        if self._span is None or not self._span.is_recording():
            raise RuntimeError("experiment run is not active")
        return self._span


class StageTelemetry:
    """One non-overlapping child stage inside an experiment run."""

    def __init__(self, run: ExperimentRun, name: StageName) -> None:
        self._run = run
        self.name = name
        self._span: Span | None = None
        self._context_token: Token[otel_context.Context] | None = None
        self._started_at = 0.0
        self._input_tokens: int | None = None
        self._output_tokens: int | None = None
        self._response_model: str | None = None

    def __enter__(self) -> Self:
        self._run._active_stage = True
        attributes = self._run._span_attributes()
        kind = SpanKind.CLIENT if self.name == "gen_ai.inference" else SpanKind.INTERNAL
        self._span = self._run._runtime._tracer.start_span(
            self.name,
            kind=kind,
            attributes=attributes,
        )
        self._context_token = otel_context.attach(trace.set_span_in_context(self._span))
        self._started_at = time.monotonic()
        self._run._log("stage.started", self.name)
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        _traceback: object,
    ) -> None:
        span = self._require_active_span()
        try:
            error_type = _safe_error_type(exc_value) if exc_value is not None else None
            if exc_value is None:
                span.set_status(Status(StatusCode.OK))
                self._run._log("stage.completed", self.name)
            else:
                assert error_type is not None
                failure_stage = _STAGE_FAILURES[self.name]
                span.set_attribute("error.type", error_type)
                span.set_status(Status(StatusCode.ERROR))
                _record_safe_exception(span, exc_value, error_type)
                self._run._mark_failed(failure_stage, error_type)
                self._run._log(
                    "stage.failed",
                    self.name,
                    failure_stage=failure_stage,
                    error_type=error_type,
                )

            if self.name == "gen_ai.inference":
                self._record_gen_ai_metrics(error_type)
        finally:
            if self._context_token is not None:
                otel_context.detach(self._context_token)
            span.end()
            self._run._active_stage = False

    def record_retry(self, attempt: int, error_type: str) -> None:
        """Record a bounded retry attempt as an event and structured log."""

        span = self._require_active_span()
        if isinstance(attempt, bool) or attempt < 1:
            raise ValueError("retry attempt must be a positive integer")
        classification = _validated_error_type(error_type)
        span.add_event(
            "retry",
            {"retry.attempt": attempt, "error.type": classification},
        )
        self._run._log(
            "stage.retry",
            self.name,
            error_type=classification,
            retry_attempt=attempt,
        )

    def set_token_usage(
        self,
        *,
        input_tokens: int | None = None,
        output_tokens: int | None = None,
        response_model: str | None = None,
    ) -> None:
        """Attach provider-reported token counts to the inference stage."""

        span = self._require_active_span()
        if self.name != "gen_ai.inference":
            raise RuntimeError("token usage belongs to gen_ai.inference")
        if input_tokens is None and output_tokens is None:
            raise ValueError("at least one token count is required")
        if input_tokens is not None:
            _require_non_negative_int(input_tokens, "input_tokens")
            span.set_attribute("gen_ai.usage.input_tokens", input_tokens)
        if output_tokens is not None:
            _require_non_negative_int(output_tokens, "output_tokens")
            span.set_attribute("gen_ai.usage.output_tokens", output_tokens)
        if response_model is not None:
            response_model = response_model.strip()
            if not response_model:
                raise ValueError("response_model must not be empty")
            span.set_attribute("gen_ai.response.model", response_model)
        self._input_tokens = input_tokens
        self._output_tokens = output_tokens
        self._response_model = response_model

    def _record_gen_ai_metrics(self, error_type: str | None) -> None:
        duration = max(0.0, time.monotonic() - self._started_at)
        attributes = self._run._gen_ai_metric_attributes(
            response_model=self._response_model,
            error_type=error_type,
        )
        instruments = self._run._runtime._instruments
        instruments.gen_ai_duration.record(duration, attributes)
        if self._input_tokens is not None:
            instruments.gen_ai_tokens.record(
                self._input_tokens,
                {**attributes, "gen_ai.token.type": "input"},
            )
        if self._output_tokens is not None:
            instruments.gen_ai_tokens.record(
                self._output_tokens,
                {**attributes, "gen_ai.token.type": "output"},
            )

    def _require_active_span(self) -> Span:
        if self._span is None or not self._span.is_recording():
            raise RuntimeError("experiment stage is not active")
        return self._span


def _record_safe_exception(
    span: Span,
    error: BaseException,
    error_type: str,
) -> None:
    # Exception messages and stack traces can contain prompts or model output.
    span.add_event(
        "exception",
        {"exception.type": error_type, "exception.escaped": True},
    )
    if isinstance(error, TimeoutError):
        span.add_event("timeout", {"error.type": error_type})


def _safe_error_type(error: BaseException) -> str:
    if isinstance(error, TimeoutError):
        return "timeout"
    try:
        code = getattr(error, "code", None)
    except Exception:
        code = None
    if isinstance(code, StrEnum):
        candidate = code.value
    elif isinstance(code, str):
        candidate = code
    else:
        candidate = f"{type(error).__module__}.{type(error).__qualname__}"
    if _ERROR_TYPE_PATTERN.fullmatch(candidate) is not None:
        return candidate

    fallback = f"{type(error).__module__}.{type(error).__name__}"
    if _ERROR_TYPE_PATTERN.fullmatch(fallback) is not None:
        return fallback
    return "unknown"


def _validated_error_type(value: str) -> str:
    if not isinstance(value, str) or _ERROR_TYPE_PATTERN.fullmatch(value) is None:
        raise ValueError("error_type must be a stable low-cardinality identifier")
    return value


def _validated_identifier(value: str | None, field_name: str) -> str:
    if value is None or _ERROR_TYPE_PATTERN.fullmatch(value) is None:
        raise ValueError(f"{field_name} must be a stable identifier")
    return value


def _require_non_negative_int(value: int, field_name: str) -> None:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError(f"{field_name} must be a non-negative integer")


def _log_sdk_failure(operation: str, signal: str, error: Exception) -> None:
    # Deliberately exclude the exception message because exporter configuration
    # can include credentials or endpoints.
    _LOGGER.warning(
        "OpenTelemetry SDK operation failed",
        extra={
            "otel.operation": operation,
            "signal": signal,
            "error.type": f"{type(error).__module__}.{type(error).__qualname__}",
        },
    )
