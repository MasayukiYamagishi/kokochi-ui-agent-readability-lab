"""Machine-readable controls for an explicitly authorized pilot execution."""

from __future__ import annotations

from typing import Annotated, Literal, Self, TypeAlias

from pydantic import Field, FiniteFloat, PositiveInt, ValidationError, model_validator
import yaml  # type: ignore[import-untyped]

from kokochi_ui_agent_readability_lab.records.config import ExperimentConfig
from kokochi_ui_agent_readability_lab.records.schema import (
    Identifier,
    NonEmptyString,
    RepositoryIdentifier,
    RunRecord,
    StrictModel,
)


CURRENT_PILOT_PLAN_SCHEMA_VERSION: Literal["1.2.0"] = "1.2.0"
PILOT_PLAN_SOURCE_PATH: Literal["pilot-plan.yaml"] = "pilot-plan.yaml"


class PilotPlanError(ValueError):
    """Raised when a pilot plan is malformed or differs from a run."""


class PilotGenerationParameters(StrictModel):
    """Complete Ollama controls approved for the local pilot."""

    temperature: Annotated[FiniteFloat, Field(ge=0)]
    top_p: Annotated[FiniteFloat, Field(gt=0, le=1)]
    top_k: PositiveInt
    repeat_penalty: Annotated[FiniteFloat, Field(gt=0)]
    num_ctx: PositiveInt
    num_predict: PositiveInt
    think: Literal[False]


class PilotRepetition(StrictModel):
    """One zero-based repetition and its provider seed."""

    repetition_index: int = Field(ge=0)
    seed: int


class PilotGpuRuntime(StrictModel):
    """Approved NVIDIA inference-server hardware for a remote GPU pilot."""

    vendor: Literal["NVIDIA"]
    model: NonEmptyString
    driver_version: NonEmptyString
    memory_mib: PositiveInt


class _PilotPlanBase(StrictModel):
    """Fields and validation shared by immutable pilot-plan versions."""

    experiment_id: RepositoryIdentifier
    experiment_version: RepositoryIdentifier
    result_tier: Literal["pilot", "official"]
    execution_backend: Literal["cpu", "remote-nvidia-gpu"]
    approved_gpu: PilotGpuRuntime | None = None
    fixture_ids: list[RepositoryIdentifier] = Field(min_length=1)
    input_representations: list[Identifier] = Field(min_length=1)
    provider: Literal["ollama"]
    model_id: NonEmptyString
    approved_model_digest: NonEmptyString
    approved_quantization: NonEmptyString
    approved_parameter_size: NonEmptyString
    generation_parameters: PilotGenerationParameters
    repetitions: list[PilotRepetition] = Field(min_length=1)
    expected_api_cost: NonEmptyString

    @model_validator(mode="after")
    def cells_and_repetitions_must_be_unique(self) -> Self:
        schema_version = getattr(self, "pilot_plan_schema_version", None)
        if schema_version != "1.2.0" and self.execution_backend != "cpu":
            raise ValueError(
                "remote NVIDIA GPU execution requires pilot plan schema 1.2.0"
            )
        if self.execution_backend == "cpu" and self.approved_gpu is not None:
            raise ValueError("CPU pilot must not declare approved_gpu")
        if self.execution_backend == "remote-nvidia-gpu" and self.approved_gpu is None:
            raise ValueError("remote NVIDIA GPU pilot requires approved_gpu")
        if len(self.fixture_ids) != len(set(self.fixture_ids)):
            raise ValueError("pilot fixture_ids must be unique")
        if len(self.input_representations) != len(set(self.input_representations)):
            raise ValueError("pilot input_representations must be unique")

        indices = [repetition.repetition_index for repetition in self.repetitions]
        if indices != list(range(len(self.repetitions))):
            raise ValueError(
                "pilot repetition_index values must be contiguous and zero-based"
            )
        seeds = [repetition.seed for repetition in self.repetitions]
        if len(seeds) != len(set(seeds)):
            raise ValueError("pilot seeds must be unique")
        return self


class PilotPlanV1(_PilotPlanBase):
    """Frozen reader model for taskless pilot-plan schema 1.0.0."""

    pilot_plan_schema_version: Literal["1.0.0"]


class PilotPlan(_PilotPlanBase):
    """Frozen cells and execution controls for a task-aware pilot."""

    pilot_plan_schema_version: Literal["1.1.0", "1.2.0"]
    task_ids: list[RepositoryIdentifier] = Field(min_length=1)
    approved_ollama_version: NonEmptyString
    request_timeout_seconds: Annotated[FiniteFloat, Field(gt=0)]
    retry_policy: Literal["no-automatic-retry"]
    exclusion_policy: Literal["keep-all-planned-attempts"]

    @model_validator(mode="after")
    def task_ids_must_be_unique(self) -> Self:
        if len(self.task_ids) != len(set(self.task_ids)):
            raise ValueError("pilot task_ids must be unique")
        return self


PilotPlanDocument: TypeAlias = PilotPlanV1 | PilotPlan


class _UniqueKeySafeLoader(yaml.SafeLoader):
    """YAML loader that rejects duplicate mapping keys."""


def _construct_unique_mapping(
    loader: _UniqueKeySafeLoader,
    node: yaml.nodes.MappingNode,
    deep: bool = False,
) -> dict[object, object]:
    mapping: dict[object, object] = {}
    for key_node, value_node in node.value:
        key = loader.construct_object(key_node, deep=deep)
        try:
            duplicate = key in mapping
        except TypeError as error:
            raise yaml.constructor.ConstructorError(
                "while constructing a mapping",
                node.start_mark,
                "found an unhashable mapping key",
                key_node.start_mark,
            ) from error
        if duplicate:
            raise yaml.constructor.ConstructorError(
                "while constructing a mapping",
                node.start_mark,
                f"found duplicate key {key!r}",
                key_node.start_mark,
            )
        mapping[key] = loader.construct_object(value_node, deep=deep)
    return mapping


_UniqueKeySafeLoader.add_constructor(
    yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG,
    _construct_unique_mapping,
)


def _canonical_yaml_text(content: bytes) -> str:
    if content.startswith(b"\xef\xbb\xbf"):
        raise PilotPlanError("pilot-plan.yaml must not contain a UTF-8 BOM")
    if b"\r" in content:
        raise PilotPlanError("pilot-plan.yaml must use LF line endings")
    if b"\t" in content:
        raise PilotPlanError("pilot-plan.yaml must not contain tabs")
    if not content.endswith(b"\n"):
        raise PilotPlanError("pilot-plan.yaml must end with a newline")
    try:
        text = content.decode("utf-8")
    except UnicodeDecodeError as error:
        raise PilotPlanError("pilot-plan.yaml must be valid UTF-8") from error
    for line_number, line in enumerate(text.splitlines(), start=1):
        indentation = len(line) - len(line.lstrip(" "))
        if indentation % 2:
            raise PilotPlanError(
                f"pilot-plan.yaml line {line_number} must use two-space indentation"
            )
        if line.endswith(" "):
            raise PilotPlanError(
                f"pilot-plan.yaml line {line_number} has trailing spaces"
            )
    return text


def parse_pilot_plan(content: bytes) -> PilotPlanDocument:
    """Parse a canonical pilot plan with duplicate-key protection."""

    text = _canonical_yaml_text(content)
    try:
        decoded = yaml.load(text, Loader=_UniqueKeySafeLoader)
    except yaml.YAMLError as error:
        raise PilotPlanError(f"pilot-plan.yaml is not valid YAML: {error}") from error
    if not isinstance(decoded, dict):
        raise PilotPlanError("pilot-plan.yaml must contain a top-level mapping")
    schema_version = decoded.get("pilot_plan_schema_version")
    try:
        if schema_version == "1.0.0":
            return PilotPlanV1.model_validate(decoded)
        if schema_version in {"1.1.0", CURRENT_PILOT_PLAN_SCHEMA_VERSION}:
            return PilotPlan.model_validate(decoded)
        raise PilotPlanError(
            f"unsupported pilot_plan_schema_version: {schema_version!r}"
        )
    except ValidationError as error:
        raise PilotPlanError(
            f"pilot-plan.yaml does not match the schema: {error}"
        ) from error


def validate_pilot_plan_matches_config(
    plan: PilotPlanDocument,
    config: ExperimentConfig,
) -> None:
    """Require the operational pilot plan to remain inside preregistration."""

    mismatches: list[str] = []
    if plan.experiment_id != config.experiment_id:
        mismatches.append("experiment_id")
    if plan.experiment_version != config.experiment_version:
        mismatches.append("experiment_version")
    if set(plan.fixture_ids) != {fixture.fixture_id for fixture in config.fixtures}:
        mismatches.append("fixture_ids")
    if config.tasks is None:
        if isinstance(plan, PilotPlan):
            mismatches.append("task_ids")
    elif not isinstance(plan, PilotPlan):
        mismatches.append("task_ids")
    elif not set(plan.task_ids).issubset({task.task_id for task in config.tasks}):
        mismatches.append("task_ids")
    if (plan.provider, plan.model_id) not in {
        (model.provider, model.model_id) for model in config.execution.models
    }:
        mismatches.append("model")
    declared_inputs = " ".join(config.design.model_inputs).lower()
    if any(
        representation not in declared_inputs
        and representation.replace("-", " ") not in declared_inputs
        for representation in plan.input_representations
    ):
        mismatches.append("input_representations")
    if plan.expected_api_cost != config.execution.expected_api_cost:
        mismatches.append("expected_api_cost")
    if mismatches:
        raise PilotPlanError(
            "pilot plan differs from config.yaml: " + ", ".join(mismatches)
        )


def validate_pilot_plan_matches_record(
    plan: PilotPlanDocument,
    config: ExperimentConfig,
    record: RunRecord,
) -> None:
    """Validate one persisted pilot run against its approved plan."""

    validate_pilot_plan_matches_config(plan, config)
    mismatches: list[str] = []
    if record.experiment.experiment_id != plan.experiment_id:
        mismatches.append("experiment.experiment_id")
    if record.experiment.version != plan.experiment_version:
        mismatches.append("experiment.version")
    if record.fixture.fixture_id not in plan.fixture_ids:
        mismatches.append("fixture.fixture_id")
    if isinstance(plan, PilotPlan):
        if record.task_id not in plan.task_ids:
            mismatches.append("task_id")
        else:
            task = next(
                task for task in config.tasks or [] if task.task_id == record.task_id
            )
            if record.fixture.fixture_id not in task.fixture_ids:
                mismatches.append("tasks.fixture_ids")
    elif record.task_id is not None:
        mismatches.append("task_id")
    if record.input_collection.representation not in plan.input_representations:
        mismatches.append("input_collection.representation")
    if record.model.provider != plan.provider:
        mismatches.append("model.provider")
    if record.model.model_id != plan.model_id:
        mismatches.append("model.model_id")
    if record.model.model_digest != plan.approved_model_digest:
        mismatches.append("model.model_digest")
    if record.model.quantization != plan.approved_quantization:
        mismatches.append("model.quantization")
    recorded_gpus = record.environment.hardware.gpus
    if plan.execution_backend == "cpu" and recorded_gpus:
        mismatches.append("environment.hardware.gpus")
    elif plan.execution_backend == "remote-nvidia-gpu":
        approved_gpu = plan.approved_gpu
        if approved_gpu is None or len(recorded_gpus) != 1:
            mismatches.append("environment.hardware.gpus")
        else:
            recorded_gpu = recorded_gpus[0]
            if (
                recorded_gpu.vendor != approved_gpu.vendor
                or recorded_gpu.model != approved_gpu.model
                or recorded_gpu.driver_version != approved_gpu.driver_version
                or recorded_gpu.memory_mib != approved_gpu.memory_mib
            ):
                mismatches.append("environment.hardware.gpus")
    if record.execution.repetition_count != len(plan.repetitions):
        mismatches.append("execution.repetition_count")
    elif record.execution.repetition_index >= len(plan.repetitions):
        mismatches.append("execution.repetition_index")
    else:
        planned_repetition = plan.repetitions[record.execution.repetition_index]
        if record.execution.seed.requested_value != planned_repetition.seed:
            mismatches.append("execution.seed.requested_value")

    parameters = {
        parameter.name: parameter.value
        for parameter in record.model.generation_parameters
    }
    planned_parameters = {
        **plan.generation_parameters.model_dump(mode="python"),
        "seed": record.execution.seed.requested_value,
    }
    if parameters != planned_parameters:
        mismatches.append("model.generation_parameters")
    if mismatches:
        raise PilotPlanError(
            "pilot plan differs from run manifest: " + ", ".join(mismatches)
        )
