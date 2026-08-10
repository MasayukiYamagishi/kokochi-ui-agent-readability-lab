"""Pydantic models for immutable, reproducible experiment run records."""

from __future__ import annotations

from enum import StrEnum
import hashlib
import json
import math
from pathlib import PurePosixPath, PureWindowsPath
from typing import Annotated, Literal, Self
from uuid import UUID

from pydantic import (
    AwareDatetime,
    BaseModel,
    ConfigDict,
    Field,
    FiniteFloat,
    NonNegativeInt,
    PositiveInt,
    StringConstraints,
    field_validator,
    model_validator,
)


CURRENT_SCHEMA_VERSION: Literal["2.1.0"] = "2.1.0"
OLLAMA_GENERATION_PARAMETER_NAMES = frozenset(
    {
        "num_ctx",
        "num_predict",
        "repeat_penalty",
        "seed",
        "temperature",
        "think",
        "top_k",
        "top_p",
    }
)

NonEmptyString = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]
Identifier = Annotated[
    str,
    StringConstraints(
        pattern=r"^[a-z0-9][a-z0-9._-]{0,127}$",
    ),
]
RepositoryIdentifier = Annotated[
    str,
    StringConstraints(pattern=r"^[a-z0-9]+(?:-[a-z0-9]+)*$"),
]
Sha256Hex = Annotated[str, StringConstraints(pattern=r"^[0-9a-f]{64}$")]
GitCommitSha = Annotated[
    str,
    StringConstraints(pattern=r"^(?:[0-9a-f]{40}|[0-9a-f]{64})$"),
]
TraceId = Annotated[str, StringConstraints(pattern=r"^[0-9a-f]{32}$")]
DockerImageDigest = Annotated[
    str,
    StringConstraints(pattern=r"^sha256:[0-9a-f]{64}$"),
]
RepositoryDigest = Annotated[
    str,
    StringConstraints(pattern=r"^[^\s@]+@sha256:[0-9a-f]{64}$"),
]


class StrictModel(BaseModel):
    """Base model that prevents implicit coercion and undeclared fields."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)


class ArtifactRole(StrEnum):
    """The role an artifact plays in reconstructing a run."""

    INPUT = "input"
    CONFIGURATION = "configuration"
    OUTPUT = "output"


class ExecutionStatus(StrEnum):
    """Terminal state of an experiment execution."""

    COMPLETED = "completed"
    FAILED = "failed"


class ColorScheme(StrEnum):
    """Browser color-scheme emulation."""

    LIGHT = "light"
    DARK = "dark"
    NO_PREFERENCE = "no-preference"


class ReducedMotion(StrEnum):
    """Browser reduced-motion emulation."""

    REDUCE = "reduce"
    NO_PREFERENCE = "no-preference"


class MissingValueReason(StrEnum):
    """Why provider metadata could not be recorded."""

    PROVIDER_NOT_REPORTED = "provider-not-reported"
    NOT_APPLICABLE = "not-applicable"
    COLLECTION_FAILED = "collection-failed"


class SeedSupportStatus(StrEnum):
    """Whether a provider supports and reports the requested seed control."""

    SUPPORTED = "supported"
    UNSUPPORTED = "unsupported"
    UNKNOWN = "unknown"


class FailureStage(StrEnum):
    """Stable stage classification for a failed run."""

    RUN_SETUP = "run-setup"
    INPUT_COLLECTION = "input-collection"
    PROVIDER = "provider"
    NORMALIZATION = "normalization"
    SCORING = "scoring"
    PERSISTENCE = "persistence"
    UNKNOWN = "unknown"


class ContentHash(StrictModel):
    """A content address for an artifact."""

    algorithm: Literal["sha256"] = "sha256"
    digest: Sha256Hex


class ArtifactReference(StrictModel):
    """Metadata that binds a logical artifact to immutable bytes."""

    artifact_id: Identifier
    role: ArtifactRole
    kind: Identifier
    relative_path: NonEmptyString
    media_type: NonEmptyString
    byte_length: NonNegativeInt
    content_hash: ContentHash

    @field_validator("relative_path")
    @classmethod
    def relative_path_must_be_portable(cls, value: str) -> str:
        if "\\" in value:
            raise ValueError("relative_path must use POSIX separators")

        path = PurePosixPath(value)
        if path.is_absolute() or ".." in path.parts:
            raise ValueError("relative_path must stay inside the run directory")
        if PureWindowsPath(value).drive:
            raise ValueError("relative_path must not include a Windows drive")
        if str(path) != value or value in {"", "."}:
            raise ValueError("relative_path must be normalized")
        return value


class GitRevision(StrictModel):
    """Git state used for an execution."""

    commit_sha: GitCommitSha
    is_dirty: bool


class OperatingSystem(StrictModel):
    """Host operating-system and kernel details."""

    name: NonEmptyString
    version: NonEmptyString
    kernel_name: NonEmptyString
    kernel_release: NonEmptyString
    kernel_version: NonEmptyString


class BrowserRuntime(StrictModel):
    """Browser build used by Playwright."""

    name: NonEmptyString
    version: NonEmptyString
    revision: NonEmptyString


class ContainerImage(StrictModel):
    """Resolved container image identity used by an execution."""

    service: Identifier
    component_version: NonEmptyString
    image_reference: NonEmptyString
    image_digest: DockerImageDigest
    repository_digests: tuple[RepositoryDigest, ...] = ()


class ContainerImageV1_1(StrictModel):
    """Container identity serialized by run-record schema 1.1.0."""

    service: Identifier
    image_reference: NonEmptyString
    image_digest: DockerImageDigest
    repository_digests: tuple[RepositoryDigest, ...] = ()


class CpuRuntime(StrictModel):
    """CPU identity recorded for a run."""

    architecture: NonEmptyString
    model: NonEmptyString
    logical_core_count: PositiveInt


class GpuRuntime(StrictModel):
    """GPU identity recorded when a run uses accelerator hardware."""

    vendor: NonEmptyString
    model: NonEmptyString
    driver_version: NonEmptyString
    memory_mib: PositiveInt | None = None


class HardwareRuntime(StrictModel):
    """Host hardware that can materially affect local inference."""

    cpu: CpuRuntime
    gpus: tuple[GpuRuntime, ...] = ()


class _ExecutionEnvironmentBase(StrictModel):
    operating_system: OperatingSystem
    python_version: NonEmptyString
    node_version: NonEmptyString
    pnpm_version: NonEmptyString
    playwright_version: NonEmptyString
    browser: BrowserRuntime


class ExecutionEnvironmentV1(_ExecutionEnvironmentBase):
    """Runtime versions serialized by run-record schema 1.0.0."""


class ExecutionEnvironmentV1_1(_ExecutionEnvironmentBase):
    """Runtime versions serialized by run-record schema 1.1.0."""

    container_images: tuple[ContainerImageV1_1, ...] = ()


class ExecutionEnvironment(_ExecutionEnvironmentBase):
    """Complete runtime and hardware identity for new run records."""

    uv_version: NonEmptyString
    docker_version: NonEmptyString
    docker_compose_version: NonEmptyString
    hardware: HardwareRuntime
    container_images: tuple[ContainerImage, ...] = Field(min_length=2)

    @field_validator("container_images")
    @classmethod
    def container_services_must_be_unique(
        cls,
        value: tuple[ContainerImage, ...],
    ) -> tuple[ContainerImage, ...]:
        services = [image.service for image in value]
        if len(services) != len(set(services)):
            raise ValueError("container image services must be unique")
        required_services = {"lgtm", "otel-collector"}
        missing_services = sorted(required_services - set(services))
        if missing_services:
            raise ValueError(
                "container image metadata is missing required services: "
                + ", ".join(missing_services)
            )
        return value


class BrowserContext(StrictModel):
    """Controlled browser settings used while collecting model input."""

    viewport_width: PositiveInt
    viewport_height: PositiveInt
    device_scale_factor: Annotated[FiniteFloat, Field(gt=0)]
    locale: NonEmptyString
    timezone: NonEmptyString
    color_scheme: ColorScheme
    reduced_motion: ReducedMotion


class ExperimentDefinition(StrictModel):
    """Stable identity and hashed definition of an approved experiment."""

    experiment_id: RepositoryIdentifier
    version: RepositoryIdentifier
    source_path: Literal["config.yaml"]
    definition_artifact_id: Identifier


class FixtureDefinition(StrictModel):
    """Stable identity and hashed definition of the tested fixture."""

    fixture_id: RepositoryIdentifier
    version: RepositoryIdentifier
    source_path: NonEmptyString
    definition_artifact_id: Identifier

    @model_validator(mode="after")
    def source_path_must_match_fixture_id(self) -> Self:
        expected = f"fixtures/{self.fixture_id}.tsx"
        if self.source_path != expected:
            raise ValueError(f"fixture source_path must be {expected!r}")
        return self


GenerationParameterValue = bool | int | FiniteFloat | NonEmptyString | None


class GenerationParameter(StrictModel):
    """One non-secret provider generation control."""

    name: Identifier
    value: GenerationParameterValue

    @field_validator("value")
    @classmethod
    def value_must_be_finite(
        cls, value: GenerationParameterValue
    ) -> GenerationParameterValue:
        if isinstance(value, float) and not math.isfinite(value):
            raise ValueError("generation parameter values must be finite")
        return value


class ConfigurationSanitization(StrictModel):
    """Declaration that a stored provider configuration excludes secrets."""

    contains_secret_values: Literal[False] = False
    redacted_fields: tuple[NonEmptyString, ...] = ()


class _ModelConfigurationBase(StrictModel):
    """Fields shared by historical and current model configuration records."""

    provider: Identifier
    model_id: NonEmptyString
    model_digest: NonEmptyString | None = None
    model_digest_missing_reason: MissingValueReason | None = None
    quantization: NonEmptyString | None = None
    quantization_missing_reason: MissingValueReason | None = None
    configuration_artifact_id: Identifier

    @model_validator(mode="after")
    def validate_missing_metadata(self) -> Self:
        pairs = (
            (
                "model_digest",
                self.model_digest,
                self.model_digest_missing_reason,
            ),
            (
                "quantization",
                self.quantization,
                self.quantization_missing_reason,
            ),
        )
        for field_name, value, missing_reason in pairs:
            if value is None and missing_reason is None:
                raise ValueError(
                    f"{field_name}_missing_reason is required when {field_name} is null"
                )
            if value is not None and missing_reason is not None:
                raise ValueError(
                    f"{field_name}_missing_reason must be null when {field_name} is set"
                )
        return self


class ModelConfigurationV1_1(_ModelConfigurationBase):
    """Model configuration serialized by run-record schemas 1.0.0 and 1.1.0."""


class ModelConfiguration(_ModelConfigurationBase):
    """Provider identity and complete, sanitized generation configuration."""

    generation_parameters: tuple[GenerationParameter, ...] = Field(min_length=1)
    configuration_sanitization: ConfigurationSanitization

    @field_validator("generation_parameters")
    @classmethod
    def parameter_names_must_be_unique(
        cls,
        value: tuple[GenerationParameter, ...],
    ) -> tuple[GenerationParameter, ...]:
        names = [parameter.name for parameter in value]
        if len(names) != len(set(names)):
            raise ValueError("generation parameter names must be unique")
        return value


class PromptReference(StrictModel):
    """Stable prompt identity without embedding prompt text in the record."""

    prompt_id: Identifier
    version: RepositoryIdentifier
    source_path: NonEmptyString
    prompt_artifact_id: Identifier

    @model_validator(mode="after")
    def source_path_must_match_version(self) -> Self:
        expected = f"prompts/{self.version}.md"
        if self.source_path != expected:
            raise ValueError(f"prompt source_path must be {expected!r}")
        return self


class GroundTruthReference(StrictModel):
    """Versioned ground truth used by the scorer."""

    version: RepositoryIdentifier
    source_path: NonEmptyString
    artifact_id: Identifier

    @model_validator(mode="after")
    def source_path_must_match_version(self) -> Self:
        expected = f"ground-truth/{self.version}.json"
        if self.source_path != expected:
            raise ValueError(f"ground truth source_path must be {expected!r}")
        return self


class InputCollectionResult(StrictModel):
    """Collected model inputs and the browser controls used to capture them."""

    collected_at: AwareDatetime
    representation: Identifier
    browser_context: BrowserContext
    artifact_ids: tuple[Identifier, ...] = Field(min_length=1)


class TokenUsage(StrictModel):
    """Provider token usage when the provider reports it."""

    input_tokens: NonNegativeInt | None = None
    output_tokens: NonNegativeInt | None = None
    total_tokens: NonNegativeInt | None = None

    @model_validator(mode="after")
    def require_at_least_one_count(self) -> Self:
        if all(
            value is None
            for value in (self.input_tokens, self.output_tokens, self.total_tokens)
        ):
            raise ValueError("token usage must include at least one count")
        return self


class EstimatedTokenUsage(TokenUsage):
    """Token usage estimated independently from provider-reported values."""

    estimation_method: NonEmptyString


class ProviderResponse(StrictModel):
    """Provider metadata and the immutable raw response artifact."""

    provider_request_id: NonEmptyString | None = None
    received_at: AwareDatetime
    latency_ms: Annotated[FiniteFloat, Field(ge=0)]
    token_usage: TokenUsage | None = None
    token_usage_missing_reason: MissingValueReason | None = None
    estimated_token_usage: EstimatedTokenUsage | None = None
    raw_response_artifact_id: Identifier

    @model_validator(mode="after")
    def validate_token_usage(self) -> Self:
        if self.token_usage is None and self.token_usage_missing_reason is None:
            raise ValueError(
                "token_usage_missing_reason is required when token_usage is null"
            )
        if self.token_usage is not None and self.token_usage_missing_reason is not None:
            raise ValueError(
                "token_usage_missing_reason must be null when token_usage is set"
            )
        return self


class NormalizedResponse(StrictModel):
    """Reference to the normalized response used by scoring."""

    artifact_id: Identifier


class ScoreResult(StrictModel):
    """Scorer identity and immutable score/evidence output."""

    scorer_id: Identifier
    scorer_version: RepositoryIdentifier
    artifact_id: Identifier


class SeedMetadata(StrictModel):
    """Provider-specific seed request, response, and support metadata."""

    provider_parameter: NonEmptyString | None = None
    requested_value: int | NonEmptyString | None = None
    reported_value: int | NonEmptyString | None = None
    support_status: SeedSupportStatus

    @model_validator(mode="after")
    def validate_seed_metadata(self) -> Self:
        if (
            self.requested_value is not None or self.reported_value is not None
        ) and self.provider_parameter is None:
            raise ValueError(
                "provider_parameter is required when a seed value is recorded"
            )
        if (
            self.support_status is SeedSupportStatus.UNSUPPORTED
            and self.reported_value is not None
        ):
            raise ValueError("unsupported seed controls cannot have a reported value")
        return self


class ExecutionResult(StrictModel):
    """Terminal execution state and repetition controls."""

    status: ExecutionStatus
    started_at: AwareDatetime
    completed_at: AwareDatetime
    repetition_index: NonNegativeInt
    repetition_count: PositiveInt
    seed: SeedMetadata
    failure_stage: FailureStage | None = None
    error_code: Identifier | None = None
    retryable: bool | None = None
    error_artifact_id: Identifier | None = None

    @model_validator(mode="after")
    def validate_execution(self) -> Self:
        if self.completed_at < self.started_at:
            raise ValueError("completed_at must not be earlier than started_at")
        if self.repetition_index >= self.repetition_count:
            raise ValueError("repetition_index must be less than repetition_count")
        failure_metadata = (
            self.failure_stage,
            self.error_code,
            self.retryable,
            self.error_artifact_id,
        )
        if self.status is ExecutionStatus.COMPLETED and any(
            value is not None for value in failure_metadata
        ):
            raise ValueError("completed executions cannot include failure metadata")
        if self.status is ExecutionStatus.FAILED and any(
            value is None for value in failure_metadata
        ):
            raise ValueError(
                "failed executions require failure_stage, error_code, retryable, "
                "and error_artifact_id"
            )
        return self


class _RunRecordBase(StrictModel):
    """Fields and validation shared by immutable run-record versions."""

    schema_version: str
    run_id: UUID
    trace_id: TraceId
    recorded_at: AwareDatetime
    git: GitRevision
    environment: (
        ExecutionEnvironmentV1 | ExecutionEnvironmentV1_1 | ExecutionEnvironment
    )
    experiment: ExperimentDefinition
    fixture: FixtureDefinition
    model: ModelConfigurationV1_1 | ModelConfiguration
    prompt: PromptReference
    ground_truth: GroundTruthReference
    input_collection: InputCollectionResult
    provider_response: ProviderResponse | None = None
    normalized_response: NormalizedResponse | None = None
    score: ScoreResult | None = None
    execution: ExecutionResult
    artifacts: tuple[ArtifactReference, ...] = Field(min_length=1)

    @field_validator("run_id")
    @classmethod
    def run_id_must_not_be_nil(cls, value: UUID) -> UUID:
        if value.int == 0:
            raise ValueError("run_id must not be the nil UUID")
        return value

    @field_validator("trace_id")
    @classmethod
    def trace_id_must_not_be_zero(cls, value: str) -> str:
        if int(value, 16) == 0:
            raise ValueError("trace_id must not be all zeroes")
        return value

    @model_validator(mode="after")
    def validate_artifact_graph(self) -> Self:
        artifacts_by_id: dict[str, ArtifactReference] = {}
        artifact_paths: set[str] = set()
        for artifact in self.artifacts:
            if artifact.artifact_id in artifacts_by_id:
                raise ValueError(f"duplicate artifact_id: {artifact.artifact_id}")
            if artifact.relative_path == "run.json":
                raise ValueError(
                    "artifact relative_path cannot be the reserved run.json"
                )
            if artifact.relative_path in artifact_paths:
                raise ValueError(
                    f"duplicate artifact relative_path: {artifact.relative_path}"
                )
            artifacts_by_id[artifact.artifact_id] = artifact
            artifact_paths.add(artifact.relative_path)

        sorted_paths = sorted(PurePosixPath(path).parts for path in artifact_paths)
        for parent_parts, child_parts in zip(sorted_paths, sorted_paths[1:]):
            if child_parts[: len(parent_parts)] == parent_parts:
                raise ValueError(
                    "artifact paths cannot be both a file and a parent directory"
                )

        expected_roles: dict[str, ArtifactRole] = {}

        def expect_role(artifact_id: str, role: ArtifactRole) -> None:
            existing_role = expected_roles.get(artifact_id)
            if existing_role is not None and existing_role is not role:
                raise ValueError(
                    f"artifact {artifact_id} is referenced with conflicting roles"
                )
            expected_roles[artifact_id] = role

        expect_role(
            self.experiment.definition_artifact_id,
            ArtifactRole.CONFIGURATION,
        )
        expect_role(
            self.fixture.definition_artifact_id,
            ArtifactRole.CONFIGURATION,
        )
        expect_role(
            self.model.configuration_artifact_id,
            ArtifactRole.CONFIGURATION,
        )
        expect_role(self.prompt.prompt_artifact_id, ArtifactRole.CONFIGURATION)
        expect_role(self.ground_truth.artifact_id, ArtifactRole.CONFIGURATION)
        for artifact_id in self.input_collection.artifact_ids:
            expect_role(artifact_id, ArtifactRole.INPUT)

        if self.provider_response is not None:
            expect_role(
                self.provider_response.raw_response_artifact_id,
                ArtifactRole.OUTPUT,
            )
        if self.normalized_response is not None:
            expect_role(self.normalized_response.artifact_id, ArtifactRole.OUTPUT)
        if self.score is not None:
            expect_role(self.score.artifact_id, ArtifactRole.OUTPUT)
        if self.execution.error_artifact_id is not None:
            expect_role(self.execution.error_artifact_id, ArtifactRole.OUTPUT)

        missing = sorted(set(expected_roles) - set(artifacts_by_id))
        if missing:
            raise ValueError(f"referenced artifacts are missing: {', '.join(missing)}")

        wrong_roles = sorted(
            artifact_id
            for artifact_id, expected_role in expected_roles.items()
            if artifacts_by_id[artifact_id].role is not expected_role
        )
        if wrong_roles:
            raise ValueError(
                "referenced artifacts have an unexpected role: "
                + ", ".join(wrong_roles)
            )

        if self.execution.status is ExecutionStatus.COMPLETED:
            completed_outputs = (
                self.provider_response,
                self.normalized_response,
                self.score,
            )
            if any(output is None for output in completed_outputs):
                raise ValueError(
                    "completed executions require provider, normalized, and score outputs"
                )

        if self.recorded_at < self.execution.completed_at:
            raise ValueError("recorded_at must not be earlier than completed_at")
        return self


class RunRecordV1(_RunRecordBase):
    """Frozen reader model for run-record schema 1.0.0."""

    schema_version: Literal["1.0.0"]
    environment: ExecutionEnvironmentV1
    model: ModelConfigurationV1_1


class RunRecordV1_1(_RunRecordBase):
    """Frozen reader model for run-record schema 1.1.0."""

    schema_version: Literal["1.1.0"]
    environment: ExecutionEnvironmentV1_1
    model: ModelConfigurationV1_1


class RunRecordV2(_RunRecordBase):
    """Frozen reader model for run-record schema 2.0.0."""

    schema_version: Literal["2.0.0"]
    environment: ExecutionEnvironment
    model: ModelConfiguration

    @model_validator(mode="after")
    def validate_current_provider_controls(self) -> Self:
        if self.model.provider != "ollama":
            return self

        parameters = {
            parameter.name: parameter.value
            for parameter in self.model.generation_parameters
        }
        if set(parameters) != OLLAMA_GENERATION_PARAMETER_NAMES:
            raise ValueError(
                "Ollama generation parameters are incomplete or contain unknown fields"
            )
        seed = self.execution.seed
        if (
            seed.provider_parameter != "seed"
            or seed.requested_value != parameters["seed"]
        ):
            raise ValueError(
                "Ollama seed metadata differs from manifest generation parameters"
            )
        return self


class RunRecord(_RunRecordBase):
    """The current manifest model for new experiment executions."""

    schema_version: Literal["2.1.0"]
    environment: ExecutionEnvironment
    model: ModelConfiguration
    task_id: RepositoryIdentifier | None = None

    @model_validator(mode="after")
    def validate_current_provider_controls(self) -> Self:
        if self.model.provider != "ollama":
            return self

        parameters = {
            parameter.name: parameter.value
            for parameter in self.model.generation_parameters
        }
        if set(parameters) != OLLAMA_GENERATION_PARAMETER_NAMES:
            raise ValueError(
                "Ollama generation parameters are incomplete or contain unknown fields"
            )
        seed = self.execution.seed
        if (
            seed.provider_parameter != "seed"
            or seed.requested_value != parameters["seed"]
        ):
            raise ValueError(
                "Ollama seed metadata differs from manifest generation parameters"
            )
        return self


class UnsupportedSchemaVersionError(ValueError):
    """Raised when no registered reader can parse a schema version."""


def sha256_digest(content: bytes) -> ContentHash:
    """Return the canonical SHA-256 content hash for bytes."""

    return ContentHash(digest=hashlib.sha256(content).hexdigest())


def artifact_reference(
    *,
    artifact_id: str,
    role: ArtifactRole,
    kind: str,
    relative_path: str,
    media_type: str,
    content: bytes,
) -> ArtifactReference:
    """Build an artifact reference from the exact bytes that will be stored."""

    return ArtifactReference(
        artifact_id=artifact_id,
        role=role,
        kind=kind,
        relative_path=relative_path,
        media_type=media_type,
        byte_length=len(content),
        content_hash=sha256_digest(content),
    )


def parse_run_record_json(
    data: str | bytes,
) -> RunRecordV1 | RunRecordV1_1 | RunRecordV2 | RunRecord:
    """Parse a record with an explicit version dispatch point."""

    decoded = json.loads(data)
    if isinstance(decoded, dict):
        schema_version = decoded.get("schema_version")
        if schema_version == "1.0.0":
            return RunRecordV1.model_validate_json(data)
        if schema_version == "1.1.0":
            return RunRecordV1_1.model_validate_json(data)
        if schema_version == "2.0.0":
            return RunRecordV2.model_validate_json(data)
        if schema_version == CURRENT_SCHEMA_VERSION:
            return RunRecord.model_validate_json(data)
        if schema_version is not None:
            raise UnsupportedSchemaVersionError(
                f"unsupported schema_version: {schema_version!r}"
            )
    return RunRecord.model_validate_json(data)
