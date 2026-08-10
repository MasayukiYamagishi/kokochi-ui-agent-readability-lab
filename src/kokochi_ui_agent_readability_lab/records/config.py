"""Validated experiment-level configuration stored in ``config.yaml``."""

from __future__ import annotations

from pathlib import PurePosixPath, PureWindowsPath
from typing import Literal, Self

from pydantic import (
    Field,
    PositiveInt,
    ValidationError,
    field_validator,
    model_validator,
)
import yaml  # type: ignore[import-untyped]

from kokochi_ui_agent_readability_lab.records.schema import (
    Identifier,
    NonEmptyString,
    RepositoryIdentifier,
    RunRecord,
    Sha256Hex,
    StrictModel,
)

CURRENT_CONFIG_SCHEMA_VERSION: Literal["1.1.0"] = "1.1.0"


class ExperimentConfigError(ValueError):
    """Raised when an experiment configuration is invalid or non-canonical."""


class ExperimentDesign(StrictModel):
    """Preregistered research design."""

    research_question: NonEmptyString
    hypothesis: NonEmptyString
    independent_variable: NonEmptyString
    controlled_variables: list[NonEmptyString]
    model_inputs: list[NonEmptyString]
    expected_output_schema: dict[str, object] | NonEmptyString
    scoring_method: NonEmptyString
    limitations: list[NonEmptyString]


class PlannedModel(StrictModel):
    """Provider and model selected before execution."""

    provider: Identifier
    model_id: NonEmptyString


class ExperimentExecutionPlan(StrictModel):
    """Preregistered execution controls and cost expectation."""

    models: list[PlannedModel] = Field(min_length=1)
    repetitions: PositiveInt
    randomness_controls: NonEmptyString
    expected_api_cost: NonEmptyString

    @model_validator(mode="after")
    def model_pairs_must_be_unique(self) -> Self:
        pairs = [(model.provider, model.model_id) for model in self.models]
        if len(pairs) != len(set(pairs)):
            raise ValueError("execution models must be unique")
        return self


class FixtureConfig(StrictModel):
    """One fixture condition referenced by an experiment."""

    fixture_id: RepositoryIdentifier
    fixture_version: RepositoryIdentifier
    source_path: NonEmptyString
    condition: NonEmptyString
    independent_variable_value: NonEmptyString

    @model_validator(mode="after")
    def source_path_must_match_fixture_id(self) -> Self:
        expected = f"fixtures/{self.fixture_id}.tsx"
        if self.source_path != expected:
            raise ValueError(f"fixture source_path must be {expected!r}")
        return self


class ExperimentTask(StrictModel):
    """One task instruction applied to one or more fixture conditions."""

    task_id: RepositoryIdentifier
    fixture_ids: list[RepositoryIdentifier] = Field(min_length=1)
    instruction: NonEmptyString

    @field_validator("fixture_ids")
    @classmethod
    def fixture_ids_must_be_unique(
        cls,
        value: list[RepositoryIdentifier],
    ) -> list[RepositoryIdentifier]:
        if len(value) != len(set(value)):
            raise ValueError("task fixture_ids must be unique")
        return value


class PromptConfig(StrictModel):
    """The immutable prompt selected by an experiment."""

    prompt_id: Identifier
    prompt_version: RepositoryIdentifier
    source_path: NonEmptyString
    approved_body_sha256: Sha256Hex | None = None

    @model_validator(mode="after")
    def source_path_must_match_version(self) -> Self:
        expected = f"prompts/{self.prompt_version}.md"
        if self.source_path != expected:
            raise ValueError(f"prompt source_path must be {expected!r}")
        return self


class GroundTruthConfig(StrictModel):
    """The immutable ground truth selected by an experiment."""

    ground_truth_version: RepositoryIdentifier
    source_path: NonEmptyString
    approved_sha256: Sha256Hex | None = None

    @model_validator(mode="after")
    def source_path_must_match_version(self) -> Self:
        expected = f"ground-truth/{self.ground_truth_version}.json"
        if self.source_path != expected:
            raise ValueError(f"ground truth source_path must be {expected!r}")
        return self


class ScorerConfig(StrictModel):
    """The immutable scorer selected by an experiment."""

    scorer_id: Identifier
    scorer_version: RepositoryIdentifier


class ExperimentConfig(StrictModel):
    """Canonical contents of one experiment's ``config.yaml``."""

    config_schema_version: Literal["1.0.0", "1.1.0"]
    experiment_id: RepositoryIdentifier
    experiment_version: RepositoryIdentifier
    title: NonEmptyString
    design: ExperimentDesign
    execution: ExperimentExecutionPlan
    fixtures: list[FixtureConfig] = Field(min_length=1)
    tasks: list[ExperimentTask] | None = None
    prompt: PromptConfig
    ground_truth: GroundTruthConfig
    scorer: ScorerConfig

    @field_validator("fixtures")
    @classmethod
    def fixture_ids_must_be_unique(
        cls,
        value: list[FixtureConfig],
    ) -> list[FixtureConfig]:
        fixture_ids = [fixture.fixture_id for fixture in value]
        if len(fixture_ids) != len(set(fixture_ids)):
            raise ValueError("fixture_id values must be unique")
        return value

    @model_validator(mode="after")
    def tasks_must_match_schema_and_fixtures(self) -> Self:
        if self.config_schema_version == "1.0.0":
            if self.tasks is not None:
                raise ValueError("config schema 1.0.0 must not define tasks")
            return self

        if not self.tasks:
            raise ValueError("config schema 1.1.0 requires tasks")

        task_ids = [task.task_id for task in self.tasks]
        if len(task_ids) != len(set(task_ids)):
            raise ValueError("task_id values must be unique")

        configured_fixture_ids = {fixture.fixture_id for fixture in self.fixtures}
        task_fixture_ids = {
            fixture_id for task in self.tasks for fixture_id in task.fixture_ids
        }
        unknown_fixture_ids = sorted(task_fixture_ids - configured_fixture_ids)
        if unknown_fixture_ids:
            raise ValueError(
                "tasks reference unknown fixture_ids: " + ", ".join(unknown_fixture_ids)
            )
        missing_fixture_ids = sorted(configured_fixture_ids - task_fixture_ids)
        if missing_fixture_ids:
            raise ValueError(
                "every fixture must be referenced by a task: "
                + ", ".join(missing_fixture_ids)
            )
        return self


class _UniqueKeySafeLoader(yaml.SafeLoader):
    """SafeLoader variant that rejects duplicate mapping keys."""


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


def _validate_normalized_yaml(content: bytes) -> str:
    if content.startswith(b"\xef\xbb\xbf"):
        raise ExperimentConfigError("config.yaml must not contain a UTF-8 BOM")
    if b"\r" in content:
        raise ExperimentConfigError("config.yaml must use LF line endings")
    if b"\t" in content:
        raise ExperimentConfigError("config.yaml must not contain tabs")
    if not content.endswith(b"\n"):
        raise ExperimentConfigError("config.yaml must end with a newline")

    try:
        text = content.decode("utf-8")
    except UnicodeDecodeError as error:
        raise ExperimentConfigError("config.yaml must be valid UTF-8") from error

    for line_number, line in enumerate(text.splitlines(), start=1):
        indentation = len(line) - len(line.lstrip(" "))
        if indentation % 2:
            raise ExperimentConfigError(
                f"config.yaml line {line_number} must use two-space indentation"
            )
        if line.endswith(" "):
            raise ExperimentConfigError(
                f"config.yaml line {line_number} must not have trailing spaces"
            )

    try:
        for token in yaml.scan(text):
            if isinstance(
                token,
                (
                    yaml.tokens.AnchorToken,
                    yaml.tokens.AliasToken,
                    yaml.tokens.TagToken,
                ),
            ):
                raise ExperimentConfigError(
                    "config.yaml must not use anchors, aliases, or tags"
                )
    except yaml.YAMLError as error:
        raise ExperimentConfigError(
            f"config.yaml is not valid YAML: {error}"
        ) from error
    return text


def parse_experiment_config(content: bytes) -> ExperimentConfig:
    """Parse and validate canonical experiment configuration bytes."""

    text = _validate_normalized_yaml(content)
    try:
        decoded = yaml.load(text, Loader=_UniqueKeySafeLoader)
    except yaml.YAMLError as error:
        raise ExperimentConfigError(
            f"config.yaml is not valid YAML: {error}"
        ) from error
    if not isinstance(decoded, dict):
        raise ExperimentConfigError("config.yaml must contain a top-level mapping")

    try:
        return ExperimentConfig.model_validate(decoded)
    except ValidationError as error:
        raise ExperimentConfigError(
            f"config.yaml does not match the schema: {error}"
        ) from error


def _validate_portable_source_path(source_path: str) -> None:
    path = PurePosixPath(source_path)
    if (
        path.is_absolute()
        or ".." in path.parts
        or str(path) != source_path
        or PureWindowsPath(source_path).drive
    ):
        raise ExperimentConfigError(
            f"config.yaml contains a non-portable source path: {source_path!r}"
        )


def validate_config_matches_record(
    config: ExperimentConfig,
    record: RunRecord,
    *,
    validate_repetition_count: bool = True,
) -> None:
    """Ensure the manifest and its experiment-level configuration agree."""

    for source_path in (
        *(fixture.source_path for fixture in config.fixtures),
        config.prompt.source_path,
        config.ground_truth.source_path,
    ):
        _validate_portable_source_path(source_path)

    mismatches: list[str] = []
    if config.experiment_id != record.experiment.experiment_id:
        mismatches.append("experiment_id")
    if config.experiment_version != record.experiment.version:
        mismatches.append("experiment_version")
    if (
        validate_repetition_count
        and config.execution.repetitions != record.execution.repetition_count
    ):
        mismatches.append("execution.repetitions")

    matching_fixture = next(
        (
            fixture
            for fixture in config.fixtures
            if fixture.fixture_id == record.fixture.fixture_id
        ),
        None,
    )
    if matching_fixture is None:
        mismatches.append("fixtures.fixture_id")
    else:
        if matching_fixture.fixture_version != record.fixture.version:
            mismatches.append("fixtures.fixture_version")
        if matching_fixture.source_path != record.fixture.source_path:
            mismatches.append("fixtures.source_path")

    if config.prompt.prompt_id != record.prompt.prompt_id:
        mismatches.append("prompt.prompt_id")
    if config.prompt.prompt_version != record.prompt.version:
        mismatches.append("prompt.prompt_version")
    if config.prompt.source_path != record.prompt.source_path:
        mismatches.append("prompt.source_path")

    if config.ground_truth.ground_truth_version != record.ground_truth.version:
        mismatches.append("ground_truth.ground_truth_version")
    if config.ground_truth.source_path != record.ground_truth.source_path:
        mismatches.append("ground_truth.source_path")

    configured_tasks = config.tasks or []
    if not configured_tasks:
        if record.task_id is not None:
            mismatches.append("task_id")
    else:
        matching_task = next(
            (task for task in configured_tasks if task.task_id == record.task_id),
            None,
        )
        if matching_task is None:
            mismatches.append("task_id")
        elif record.fixture.fixture_id not in matching_task.fixture_ids:
            mismatches.append("tasks.fixture_ids")

    planned_models = {
        (model.provider, model.model_id) for model in config.execution.models
    }
    if (record.model.provider, record.model.model_id) not in planned_models:
        mismatches.append("execution.models")

    if record.score is not None:
        if config.scorer.scorer_id != record.score.scorer_id:
            mismatches.append("scorer.scorer_id")
        if config.scorer.scorer_version != record.score.scorer_version:
            mismatches.append("scorer.scorer_version")

    if mismatches:
        raise ExperimentConfigError(
            "config.yaml differs from the run manifest: " + ", ".join(mismatches)
        )
