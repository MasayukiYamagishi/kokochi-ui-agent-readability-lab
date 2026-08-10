"""Write-once staging for raw model responses before evaluation."""

from __future__ import annotations

import os
from pathlib import Path
import re
import shutil
import tempfile
from uuid import UUID

from kokochi_ui_agent_readability_lab.evaluation.control_discovery import (
    ControlDiscoveryEvaluationArtifacts,
    evaluate_control_discovery_response,
)
from kokochi_ui_agent_readability_lab.evaluation.item_association import (
    EvaluationArtifacts,
    evaluate_item_association_response,
)
from kokochi_ui_agent_readability_lab.evaluation.group_membership import (
    GroupMembershipEvaluationArtifacts,
    evaluate_group_membership_experiment_response,
)
from kokochi_ui_agent_readability_lab.records import ExperimentConfig, ResultTier


_REPOSITORY_IDENTIFIER_PATTERN = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
StoredEvaluationArtifacts = (
    EvaluationArtifacts
    | ControlDiscoveryEvaluationArtifacts
    | GroupMembershipEvaluationArtifacts
)


class RawResponseAlreadyExistsError(FileExistsError):
    """Raised when a run already has staged or published response data."""


class RawResponseExperimentMismatchError(ValueError):
    """Raised when evaluation configuration belongs to another experiment."""


class EvaluationArtifactsAlreadyExistError(FileExistsError):
    """Raised when normalized or score artifacts already exist for a run."""


class EvaluationArtifactMismatchError(ValueError):
    """Raised when evaluation artifacts do not belong to the staged response."""


class RawResponseStore:
    """Persist exact raw model bytes before normalization can begin."""

    staging_directory_name = ".response-staging"
    response_relative_path = "outputs/raw-model-response.txt"
    normalized_response_relative_path = "outputs/normalized-response.json"
    score_relative_path = "outputs/score.json"
    evaluation_directory_name = ".evaluation"

    def __init__(
        self,
        repository_root: Path,
        experiment_id: str,
        result_tier: ResultTier,
    ) -> None:
        if _REPOSITORY_IDENTIFIER_PATTERN.fullmatch(experiment_id) is None:
            raise ValueError("experiment_id must be a lowercase kebab-case identifier")
        if not isinstance(result_tier, ResultTier):
            raise TypeError("result_tier must be ResultTier.PILOT or OFFICIAL")

        self.repository_root = repository_root
        self.experiment_id = experiment_id
        self.result_tier = result_tier
        self.experiment_root = repository_root / "experiments" / experiment_id
        self.result_root = self.experiment_root / "results" / result_tier.value
        self.root = self.result_root / self.staging_directory_name

    def save(self, run_id: UUID, raw_response: bytes) -> Path:
        """Atomically stage exact raw bytes without overwriting a prior run."""

        self._validate_run_id(run_id)
        if not isinstance(raw_response, bytes):
            raise TypeError("raw_response must be bytes")

        self.root.mkdir(parents=True, exist_ok=True)
        staged_path = self.root / str(run_id)
        published_path = self.result_root / str(run_id)
        if staged_path.exists() or published_path.exists():
            raise RawResponseAlreadyExistsError(
                f"raw response or published run already exists: {run_id}"
            )

        temporary_path = Path(tempfile.mkdtemp(prefix=f".{run_id}.", dir=self.root))
        try:
            destination = temporary_path / Path(self.response_relative_path)
            destination.parent.mkdir(parents=True, exist_ok=True)
            self._write_new_file(destination, raw_response)
            self._sync_directory_tree(temporary_path)
            try:
                self._promote(temporary_path, staged_path)
            except OSError as error:
                if staged_path.exists() or published_path.exists():
                    raise RawResponseAlreadyExistsError(
                        f"raw response or published run already exists: {run_id}"
                    ) from error
                raise
            self._sync_directory(self.root)
            return staged_path / Path(self.response_relative_path)
        finally:
            if temporary_path.exists():
                shutil.rmtree(temporary_path)

    def load(self, run_id: UUID) -> bytes:
        """Load the exact staged bytes for a run."""

        self._validate_run_id(run_id)
        response_path = self.root / str(run_id) / Path(self.response_relative_path)
        if response_path.is_symlink() or not response_path.is_file():
            raise FileNotFoundError(f"staged raw response does not exist: {run_id}")
        return response_path.read_bytes()

    def stage_or_verify(self, run_id: UUID, raw_response: bytes) -> Path:
        """Create raw staging once, or verify an identical staged response."""

        try:
            return self.save(run_id, raw_response)
        except RawResponseAlreadyExistsError:
            published_path = self.result_root / str(run_id)
            if published_path.exists() or published_path.is_symlink():
                raise
            try:
                existing_response = self.load(run_id)
            except FileNotFoundError:
                raise
            if existing_response != raw_response:
                raise
            return self.root / str(run_id) / Path(self.response_relative_path)

    def save_evaluation(
        self,
        run_id: UUID,
        artifacts: StoredEvaluationArtifacts,
    ) -> None:
        """Write normalized and score artifacts beside their staged raw response."""

        self._validate_run_id(run_id)
        if not isinstance(
            artifacts,
            (
                EvaluationArtifacts,
                ControlDiscoveryEvaluationArtifacts,
                GroupMembershipEvaluationArtifacts,
            ),
        ):
            raise TypeError("artifacts must be supported evaluation artifacts")
        if self.load(run_id) != artifacts.raw_response:
            raise EvaluationArtifactMismatchError(
                "evaluation raw response differs from the staged response"
            )

        run_root = self.root / str(run_id)
        outputs_root = run_root / "outputs"
        evaluation_root = outputs_root / self.evaluation_directory_name
        outputs = {
            self.normalized_response_relative_path: artifacts.normalized_response_bytes,
            self.score_relative_path: artifacts.score_bytes,
        }
        if evaluation_root.exists() or evaluation_root.is_symlink():
            self._verify_existing_evaluation(evaluation_root, outputs, run_id)
            return

        outputs_root.mkdir(parents=True, exist_ok=True)
        temporary_path = Path(tempfile.mkdtemp(prefix=".evaluation.", dir=outputs_root))
        try:
            for relative_path, content in outputs.items():
                destination = temporary_path / Path(relative_path).name
                self._write_new_file(destination, content)
            self._sync_directory_tree(temporary_path)
            try:
                self._promote(temporary_path, evaluation_root)
            except OSError:
                if evaluation_root.exists() or evaluation_root.is_symlink():
                    self._verify_existing_evaluation(
                        evaluation_root,
                        outputs,
                        run_id,
                    )
                    return
                raise
            self._sync_directory(outputs_root)
        finally:
            if temporary_path.exists():
                shutil.rmtree(temporary_path)

    def load_evaluation_artifacts(self, run_id: UUID) -> dict[str, bytes]:
        """Load the three evaluation outputs by their run-manifest paths."""

        self._validate_run_id(run_id)
        run_root = self.root / str(run_id)
        evaluation_root = run_root / "outputs" / self.evaluation_directory_name
        paths = {
            self.response_relative_path: run_root / Path(self.response_relative_path),
            self.normalized_response_relative_path: (
                evaluation_root / Path(self.normalized_response_relative_path).name
            ),
            self.score_relative_path: (
                evaluation_root / Path(self.score_relative_path).name
            ),
        }
        artifacts: dict[str, bytes] = {}
        for relative_path, path in paths.items():
            if path.is_symlink() or not path.is_file():
                raise FileNotFoundError(
                    f"staged evaluation artifact does not exist: {relative_path}"
                )
            artifacts[relative_path] = path.read_bytes()
        return artifacts

    @staticmethod
    def _verify_existing_evaluation(
        evaluation_root: Path,
        outputs: dict[str, bytes],
        run_id: UUID,
    ) -> None:
        if evaluation_root.is_symlink() or not evaluation_root.is_dir():
            raise EvaluationArtifactsAlreadyExistError(
                f"evaluation artifact location is not a stored directory: {run_id}"
            )
        for relative_path, expected_content in outputs.items():
            path = evaluation_root / Path(relative_path).name
            if (
                path.is_symlink()
                or not path.is_file()
                or path.read_bytes() != expected_content
            ):
                raise EvaluationArtifactsAlreadyExistError(
                    "evaluation artifacts already exist with different or "
                    f"incomplete content: {run_id}"
                )

    @staticmethod
    def _validate_run_id(run_id: UUID) -> None:
        if not isinstance(run_id, UUID):
            raise TypeError("run_id must be a UUID")
        if run_id.int == 0:
            raise ValueError("run_id must not be the nil UUID")

    @staticmethod
    def _write_new_file(path: Path, content: bytes) -> None:
        with path.open("xb") as file:
            file.write(content)
            file.flush()
            os.fsync(file.fileno())

    @staticmethod
    def _promote(temporary_path: Path, staged_path: Path) -> None:
        temporary_path.rename(staged_path)

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


def save_and_evaluate_item_association_response(
    raw_response: bytes,
    *,
    store: RawResponseStore,
    run_id: UUID,
    config: ExperimentConfig,
    ground_truth_content: bytes,
    fixture_id: str,
) -> EvaluationArtifacts:
    """Persist raw bytes first, then normalize and score the persisted content."""

    if store.experiment_id != config.experiment_id:
        raise RawResponseExperimentMismatchError(
            "config experiment_id does not match the raw response store"
        )
    store.stage_or_verify(run_id, raw_response)
    persisted_response = store.load(run_id)
    artifacts = evaluate_item_association_response(
        persisted_response,
        config=config,
        ground_truth_content=ground_truth_content,
        fixture_id=fixture_id,
    )
    store.save_evaluation(run_id, artifacts)
    return artifacts


def save_and_evaluate_control_discovery_response(
    raw_response: bytes,
    *,
    store: RawResponseStore,
    run_id: UUID,
    config: ExperimentConfig,
    ground_truth_content: bytes,
    fixture_id: str,
    task_id: str,
    input_representation: str,
) -> ControlDiscoveryEvaluationArtifacts:
    """Persist raw bytes first, then score one task-aware response."""

    if store.experiment_id != config.experiment_id:
        raise RawResponseExperimentMismatchError(
            "config experiment_id does not match the raw response store"
        )
    store.stage_or_verify(run_id, raw_response)
    persisted_response = store.load(run_id)
    artifacts = evaluate_control_discovery_response(
        persisted_response,
        config=config,
        ground_truth_content=ground_truth_content,
        fixture_id=fixture_id,
        task_id=task_id,
        input_representation=input_representation,
    )
    store.save_evaluation(run_id, artifacts)
    return artifacts


def save_and_evaluate_group_membership_response(
    raw_response: bytes,
    *,
    store: RawResponseStore,
    run_id: UUID,
    config: ExperimentConfig,
    ground_truth_content: bytes,
    fixture_id: str,
    task_id: str,
    input_representation: str,
) -> GroupMembershipEvaluationArtifacts:
    """Persist raw bytes first, then score one v2 membership response."""

    if store.experiment_id != config.experiment_id:
        raise RawResponseExperimentMismatchError(
            "config experiment_id does not match the raw response store"
        )
    store.stage_or_verify(run_id, raw_response)
    persisted_response = store.load(run_id)
    artifacts = evaluate_group_membership_experiment_response(
        persisted_response,
        config=config,
        ground_truth_content=ground_truth_content,
        fixture_id=fixture_id,
        task_id=task_id,
        input_representation=input_representation,
    )
    store.save_evaluation(run_id, artifacts)
    return artifacts
