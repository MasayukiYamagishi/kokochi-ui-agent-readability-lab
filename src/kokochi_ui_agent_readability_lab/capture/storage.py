"""Write-once staging for private browser-capture artifacts."""

from __future__ import annotations

import os
from pathlib import Path
import re
import shutil
import tempfile
from types import MappingProxyType
from uuid import UUID

from pydantic import ValidationError

from kokochi_ui_agent_readability_lab.capture.browser import (
    CaptureManifest,
    CapturedInputs,
)
from kokochi_ui_agent_readability_lab.records import ResultTier, sha256_digest


_REPOSITORY_IDENTIFIER_PATTERN = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
_CAPTURE_MANIFEST_PATH = "inputs/capture-manifest.json"


class CapturedInputStagingError(ValueError):
    """Base error for invalid staged capture input."""


class CapturedInputsAlreadyExistError(FileExistsError):
    """Raised when a run already has staged or published data."""


class CapturedInputSetError(CapturedInputStagingError):
    """Raised when bundle files differ from its capture manifest."""


class CapturedInputIntegrityError(CapturedInputStagingError):
    """Raised when staged bytes differ from declared hashes or sizes."""


class CaptureExperimentMismatchError(CapturedInputStagingError):
    """Raised when capture metadata belongs to another experiment."""


class CapturedInputStore:
    """Persist complete input bundles without publishing an incomplete run."""

    staging_directory_name = ".capture-staging"

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
        self.result_root = self.experiment_root / "results" / self.result_tier.value
        self.root = self.result_root / self.staging_directory_name

    def save(self, run_id: UUID, captured: CapturedInputs) -> Path:
        """Atomically stage one complete capture bundle for a future run."""

        self._validate_run_id(run_id)
        self._validate_bundle(captured)

        self.root.mkdir(parents=True, exist_ok=True)
        staged_path = self.root / str(run_id)
        published_path = self.result_root / str(run_id)
        if staged_path.exists() or published_path.exists():
            raise CapturedInputsAlreadyExistError(
                f"capture inputs or published run already exist: {run_id}"
            )

        temporary_path = Path(tempfile.mkdtemp(prefix=f".{run_id}.", dir=self.root))
        try:
            for relative_path, content in captured.artifacts.items():
                destination = self._artifact_destination(
                    temporary_path,
                    relative_path,
                )
                destination.parent.mkdir(parents=True, exist_ok=True)
                self._write_new_file(destination, content)

            self._sync_directory_tree(temporary_path)
            try:
                self._promote(temporary_path, staged_path)
            except OSError as error:
                if staged_path.exists() or published_path.exists():
                    raise CapturedInputsAlreadyExistError(
                        f"capture inputs or published run already exist: {run_id}"
                    ) from error
                raise
            self._sync_directory(self.root)
            return staged_path
        finally:
            if temporary_path.exists():
                shutil.rmtree(temporary_path)

    def load(self, run_id: UUID) -> CapturedInputs:
        """Load staged bytes and revalidate their manifest, sizes, and hashes."""

        self._validate_run_id(run_id)
        staged_path = self.root / str(run_id)
        if not staged_path.is_dir():
            raise FileNotFoundError(f"staged capture inputs do not exist: {run_id}")

        artifacts: dict[str, bytes] = {}
        for candidate in staged_path.rglob("*"):
            if candidate.is_symlink():
                raise CapturedInputSetError("staged capture must not contain symlinks")
            if candidate.is_file():
                relative_path = candidate.relative_to(staged_path).as_posix()
                artifacts[relative_path] = candidate.read_bytes()

        manifest_content = artifacts.get(_CAPTURE_MANIFEST_PATH)
        if manifest_content is None:
            raise CapturedInputSetError("staged capture manifest is missing")
        try:
            manifest = CaptureManifest.model_validate_json(manifest_content)
        except ValidationError as error:
            raise CapturedInputIntegrityError(
                "staged capture manifest is invalid"
            ) from error

        captured = CapturedInputs(
            manifest=manifest,
            artifacts=MappingProxyType(artifacts),
        )
        self._validate_bundle(captured)
        return captured

    @staticmethod
    def _validate_run_id(run_id: UUID) -> None:
        if not isinstance(run_id, UUID):
            raise TypeError("run_id must be a UUID")
        if run_id.int == 0:
            raise ValueError("run_id must not be the nil UUID")

    def _validate_bundle(self, captured: CapturedInputs) -> None:
        if captured.manifest.experiment_id != self.experiment_id:
            raise CaptureExperimentMismatchError(
                "capture experiment_id does not match the input store"
            )

        expected_paths = {
            artifact.relative_path for artifact in captured.manifest.artifacts
        }
        expected_paths.add(_CAPTURE_MANIFEST_PATH)
        actual_paths = set(captured.artifacts)
        if expected_paths != actual_paths:
            missing = sorted(expected_paths - actual_paths)
            unexpected = sorted(actual_paths - expected_paths)
            raise CapturedInputSetError(
                f"capture artifact paths differ; missing={missing}, "
                f"unexpected={unexpected}"
            )

        for metadata in captured.manifest.artifacts:
            content = captured.artifacts[metadata.relative_path]
            if not isinstance(content, bytes):
                raise TypeError(
                    f"capture artifact {metadata.relative_path!r} must be bytes"
                )
            if metadata.byte_length != len(content):
                raise CapturedInputIntegrityError(
                    f"capture artifact {metadata.relative_path!r} has an "
                    "unexpected byte length"
                )
            if metadata.content_hash != sha256_digest(content):
                raise CapturedInputIntegrityError(
                    f"capture artifact {metadata.relative_path!r} has an "
                    "unexpected content hash"
                )

        manifest_content = captured.artifacts[_CAPTURE_MANIFEST_PATH]
        try:
            parsed_manifest = CaptureManifest.model_validate_json(manifest_content)
        except ValidationError as error:
            raise CapturedInputIntegrityError(
                "capture manifest bytes are invalid"
            ) from error
        if parsed_manifest != captured.manifest:
            raise CapturedInputIntegrityError(
                "capture manifest bytes differ from the in-memory manifest"
            )

        try:
            captured.artifact_references()
        except (KeyError, ValidationError, ValueError) as error:
            raise CapturedInputSetError(
                "capture artifact metadata is not portable"
            ) from error

    @staticmethod
    def _artifact_destination(root: Path, relative_path: str) -> Path:
        destination = root / Path(relative_path)
        resolved_root = root.resolve()
        resolved_destination = destination.resolve(strict=False)
        if not resolved_destination.is_relative_to(resolved_root):
            raise CapturedInputSetError(
                f"capture artifact path escapes staging: {relative_path!r}"
            )
        return destination

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
