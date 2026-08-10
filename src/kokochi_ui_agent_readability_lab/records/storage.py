"""Write-once, atomic persistence for experiment run records."""

from __future__ import annotations

from collections.abc import Mapping
from enum import StrEnum
import json
import os
from pathlib import Path
import re
import shutil
import tempfile
from uuid import UUID

from kokochi_ui_agent_readability_lab.records.config import (
    ExperimentConfig,
    ExperimentConfigError,
    parse_experiment_config,
    validate_config_matches_record,
)
from kokochi_ui_agent_readability_lab.records.schema import (
    OLLAMA_GENERATION_PARAMETER_NAMES,
    ArtifactReference,
    RunRecord,
    RunRecordV1,
    RunRecordV1_1,
    RunRecordV2,
    parse_run_record_json,
    sha256_digest,
)
from kokochi_ui_agent_readability_lab.records.pilot import (
    PILOT_PLAN_SOURCE_PATH,
    PilotPlanError,
    parse_pilot_plan,
    validate_pilot_plan_matches_record,
)

REPOSITORY_IDENTIFIER_PATTERN = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")


class ResultTier(StrEnum):
    """Non-public raw-result tiers defined by the repository layout policy."""

    PILOT = "pilot"
    OFFICIAL = "official"


class RunAlreadyExistsError(FileExistsError):
    """Raised when a run_id already has a persisted result."""


class ArtifactSetError(ValueError):
    """Raised when supplied artifact paths differ from the manifest."""


class ArtifactIntegrityError(ValueError):
    """Raised when supplied artifact bytes do not match the manifest."""


class SensitiveConfigurationError(ValueError):
    """Raised when a persisted provider configuration may contain a secret."""


class ModelConfigurationError(ValueError):
    """Raised when provider configuration and manifest controls disagree."""


class ExperimentMismatchError(ValueError):
    """Raised when a record is written under another experiment."""


class ExperimentLayoutError(ValueError):
    """Raised when required experiment source files are missing or differ."""


class DirtyOfficialRunError(ValueError):
    """Raised when an official result is based on an uncommitted worktree."""


class WriteOnceResultStore:
    """Persist complete runs without exposing partial or overwritten results."""

    record_filename = "run.json"

    def __init__(
        self,
        repository_root: Path,
        experiment_id: str,
        result_tier: ResultTier,
    ) -> None:
        if REPOSITORY_IDENTIFIER_PATTERN.fullmatch(experiment_id) is None:
            raise ValueError("experiment_id must be a lowercase kebab-case identifier")
        if not isinstance(result_tier, ResultTier):
            raise TypeError("result_tier must be ResultTier.PILOT or OFFICIAL")

        self.repository_root = repository_root
        self.experiment_id = experiment_id
        self.result_tier = result_tier
        self.experiment_root = repository_root / "experiments" / experiment_id
        self.root = self.experiment_root / "results" / result_tier.value

    def save(
        self,
        record: RunRecord,
        artifacts: Mapping[str, bytes],
    ) -> Path:
        """Atomically publish one complete run directory."""

        if record.experiment.experiment_id != self.experiment_id:
            raise ExperimentMismatchError(
                "record experiment_id does not match the result store"
            )
        if self.result_tier is ResultTier.OFFICIAL and record.git.is_dirty:
            raise DirtyOfficialRunError("official runs require a clean Git worktree")

        expected_by_path = {
            artifact.relative_path: artifact for artifact in record.artifacts
        }
        expected_paths = set(expected_by_path)
        actual_paths = set(artifacts)
        if expected_paths != actual_paths:
            missing = sorted(expected_paths - actual_paths)
            unexpected = sorted(actual_paths - expected_paths)
            raise ArtifactSetError(
                f"artifact paths differ; missing={missing}, unexpected={unexpected}"
            )

        for relative_path, content in artifacts.items():
            if not isinstance(content, bytes):
                raise TypeError(f"artifact {relative_path!r} must be bytes")
            reference = expected_by_path[relative_path]
            if len(content) != reference.byte_length:
                raise ArtifactIntegrityError(
                    f"artifact {relative_path!r} has an unexpected byte length"
                )
            if sha256_digest(content) != reference.content_hash:
                raise ArtifactIntegrityError(
                    f"artifact {relative_path!r} has an unexpected content hash"
                )

        self._validate_model_configuration(record, artifacts, expected_by_path)
        self._validate_source_snapshots(record, artifacts, expected_by_path)

        self.root.mkdir(parents=True, exist_ok=True)
        final_path = self.root / str(record.run_id)
        if final_path.exists():
            raise RunAlreadyExistsError(f"run already exists: {record.run_id}")

        temporary_path = Path(
            tempfile.mkdtemp(prefix=f".{record.run_id}.", dir=self.root)
        )
        try:
            for relative_path, content in artifacts.items():
                destination = self._artifact_destination(
                    temporary_path,
                    relative_path,
                )
                destination.parent.mkdir(parents=True, exist_ok=True)
                self._write_new_file(destination, content)

            serialized_record = (record.model_dump_json(indent=2) + "\n").encode()
            self._write_new_file(
                temporary_path / self.record_filename,
                serialized_record,
            )
            self._sync_directory_tree(temporary_path)

            try:
                self._promote(temporary_path, final_path)
            except OSError as error:
                if final_path.exists():
                    raise RunAlreadyExistsError(
                        f"run already exists: {record.run_id}"
                    ) from error
                raise

            self._sync_directory(self.root)
            return final_path
        finally:
            if temporary_path.exists():
                shutil.rmtree(temporary_path)

    def load(
        self,
        run_id: UUID,
    ) -> RunRecordV1 | RunRecordV1_1 | RunRecordV2 | RunRecord:
        """Load and validate a stored run manifest."""

        record_path = self.root / str(run_id) / self.record_filename
        return parse_run_record_json(record_path.read_bytes())

    def _validate_source_snapshots(
        self,
        record: RunRecord,
        artifacts: Mapping[str, bytes],
        expected_by_path: Mapping[str, ArtifactReference],
    ) -> None:
        source_artifact_ids = (
            (
                record.experiment.source_path,
                record.experiment.definition_artifact_id,
            ),
            (record.fixture.source_path, record.fixture.definition_artifact_id),
            (record.prompt.source_path, record.prompt.prompt_artifact_id),
            (
                record.ground_truth.source_path,
                record.ground_truth.artifact_id,
            ),
        )
        artifacts_by_id = {
            artifact.artifact_id: artifact for artifact in expected_by_path.values()
        }

        for source_path, artifact_id in source_artifact_ids:
            repository_source = self.experiment_root / Path(source_path)
            if not repository_source.is_file():
                raise ExperimentLayoutError(
                    f"required experiment source is missing: {source_path}"
                )

            artifact = artifacts_by_id[artifact_id]
            if repository_source.read_bytes() != artifacts[artifact.relative_path]:
                raise ExperimentLayoutError(
                    f"experiment source differs from its run snapshot: {source_path}"
                )

        config_artifact = artifacts_by_id[record.experiment.definition_artifact_id]
        try:
            config = parse_experiment_config(artifacts[config_artifact.relative_path])
            repetition_count_matches = (
                config.execution.repetitions == record.execution.repetition_count
            )
            validate_config_matches_record(
                config,
                record,
                validate_repetition_count=repetition_count_matches,
            )
            if not repetition_count_matches:
                if self.result_tier is not ResultTier.PILOT:
                    raise ExperimentConfigError(
                        "official run repetition count differs from config.yaml"
                    )
                self._validate_pilot_plan(
                    record,
                    artifacts,
                    expected_by_path,
                    config,
                )
        except (ExperimentConfigError, PilotPlanError) as error:
            raise ExperimentLayoutError(str(error)) from error

    def _validate_pilot_plan(
        self,
        record: RunRecord,
        artifacts: Mapping[str, bytes],
        expected_by_path: Mapping[str, ArtifactReference],
        config: ExperimentConfig,
    ) -> None:
        matching = [
            artifact
            for artifact in expected_by_path.values()
            if artifact.artifact_id == "pilot-plan"
        ]
        if len(matching) != 1:
            raise PilotPlanError(
                "pilot runs whose repetition count differs from config.yaml "
                "require exactly one pilot-plan artifact"
            )
        reference = matching[0]
        repository_plan = self.experiment_root / PILOT_PLAN_SOURCE_PATH
        if not repository_plan.is_file():
            raise PilotPlanError(
                "required experiment source is missing: pilot-plan.yaml"
            )
        content = artifacts[reference.relative_path]
        if repository_plan.read_bytes() != content:
            raise PilotPlanError(
                "experiment source differs from its run snapshot: pilot-plan.yaml"
            )
        plan = parse_pilot_plan(content)
        validate_pilot_plan_matches_record(plan, config, record)

    @staticmethod
    def _validate_model_configuration(
        record: RunRecordV2 | RunRecord,
        artifacts: Mapping[str, bytes],
        expected_by_path: Mapping[str, ArtifactReference],
    ) -> None:
        artifacts_by_id = {
            artifact.artifact_id: artifact for artifact in expected_by_path.values()
        }
        reference = artifacts_by_id[record.model.configuration_artifact_id]
        content = artifacts[reference.relative_path]

        def reject_duplicates(pairs: list[tuple[str, object]]) -> dict[str, object]:
            result: dict[str, object] = {}
            for key, value in pairs:
                if key in result:
                    raise SensitiveConfigurationError(
                        f"model configuration contains duplicate field {key!r}"
                    )
                result[key] = value
            return result

        try:
            decoded = json.loads(content, object_pairs_hook=reject_duplicates)
        except SensitiveConfigurationError:
            raise
        except (UnicodeDecodeError, json.JSONDecodeError) as error:
            raise SensitiveConfigurationError(
                "model configuration artifact must be valid JSON"
            ) from error
        if not isinstance(decoded, dict):
            raise SensitiveConfigurationError(
                "model configuration artifact must contain a JSON object"
            )

        blocked_keys = {
            "apikey",
            "accesstoken",
            "authtoken",
            "authorization",
            "bearertoken",
            "clientsecret",
            "cookie",
            "credential",
            "credentials",
            "idtoken",
            "password",
            "privatekey",
            "refreshtoken",
            "secret",
            "secretkey",
            "sessiontoken",
            "token",
        }
        blocked_suffixes = blocked_keys - {"secret", "token"}

        def inspect(value: object, path: str) -> None:
            if isinstance(value, dict):
                for key, nested_value in value.items():
                    normalized_key = re.sub(r"[^a-z0-9]", "", str(key).lower())
                    key_tokens = {
                        token
                        for token in re.split(r"[^a-z0-9]+", str(key).lower())
                        if token
                    }
                    if (
                        normalized_key in blocked_keys
                        or any(
                            normalized_key.endswith(suffix)
                            for suffix in blocked_suffixes
                        )
                        or bool(key_tokens & {"secret", "password", "credential"})
                    ):
                        raise SensitiveConfigurationError(
                            "model configuration contains a secret-like field: "
                            f"{path}.{key}"
                        )
                    inspect(nested_value, f"{path}.{key}")
            elif isinstance(value, list):
                for index, nested_value in enumerate(value):
                    inspect(nested_value, f"{path}[{index}]")

        inspect(decoded, "configuration")
        WriteOnceResultStore._validate_provider_configuration(record, decoded)

    @staticmethod
    def _validate_provider_configuration(
        record: RunRecordV2 | RunRecord,
        configuration: dict[str, object],
    ) -> None:
        if record.model.provider != "ollama":
            raise ModelConfigurationError(
                "no model configuration validator is registered for provider "
                f"{record.model.provider!r}"
            )

        required_top_level = {"format", "model", "options", "stream", "think"}
        if set(configuration) != required_top_level:
            raise ModelConfigurationError(
                "Ollama model configuration fields differ from the reproducibility "
                "contract"
            )
        if configuration["model"] != record.model.model_id:
            raise ModelConfigurationError(
                "Ollama model configuration model differs from the manifest"
            )
        if configuration["stream"] is not False:
            raise ModelConfigurationError(
                "Ollama model configuration must disable streaming"
            )
        response_format = configuration["format"]
        if response_format != "json" and not isinstance(response_format, dict):
            raise ModelConfigurationError(
                "Ollama model configuration format must be json or a JSON schema"
            )

        options = configuration["options"]
        if not isinstance(options, dict):
            raise ModelConfigurationError(
                "Ollama model configuration options must be an object"
            )

        parameters = {
            parameter.name: parameter.value
            for parameter in record.model.generation_parameters
        }
        if set(parameters) != OLLAMA_GENERATION_PARAMETER_NAMES:
            raise ModelConfigurationError(
                "Ollama generation parameters are incomplete or contain unknown fields"
            )

        expected_options = {
            name: value for name, value in parameters.items() if name != "think"
        }
        if options != expected_options or configuration["think"] != parameters["think"]:
            raise ModelConfigurationError(
                "Ollama model configuration differs from manifest generation parameters"
            )

        seed = record.execution.seed
        if (
            seed.provider_parameter != "seed"
            or seed.requested_value != parameters["seed"]
        ):
            raise ModelConfigurationError(
                "Ollama seed metadata differs from manifest generation parameters"
            )

    @staticmethod
    def _artifact_destination(
        temporary_path: Path,
        relative_path: str,
    ) -> Path:
        destination = temporary_path / Path(relative_path)
        resolved_root = temporary_path.resolve()
        resolved_destination = destination.resolve(strict=False)
        if not resolved_destination.is_relative_to(resolved_root):
            raise ArtifactSetError(
                f"artifact path escapes the run directory: {relative_path!r}"
            )
        return destination

    @staticmethod
    def _write_new_file(path: Path, content: bytes) -> None:
        with path.open("xb") as file:
            file.write(content)
            file.flush()
            os.fsync(file.fileno())

    @staticmethod
    def _promote(temporary_path: Path, final_path: Path) -> None:
        temporary_path.rename(final_path)

    @classmethod
    def _sync_directory_tree(cls, root: Path) -> None:
        if os.name == "nt":
            return
        for directory, _, _ in os.walk(root, topdown=False):
            cls._sync_directory(Path(directory))

    @staticmethod
    def _sync_directory(path: Path) -> None:
        if os.name == "nt":
            return
        descriptor = os.open(path, os.O_RDONLY)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
