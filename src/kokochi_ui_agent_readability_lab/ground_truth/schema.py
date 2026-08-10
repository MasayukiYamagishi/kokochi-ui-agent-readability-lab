"""Schema and independent validation for experiment ground truth."""

from __future__ import annotations

from enum import StrEnum
import hashlib
import json
import re
from typing import Literal, Self, TypeAlias
import unicodedata

from pydantic import AwareDatetime, Field, PositiveInt, ValidationError, model_validator

from kokochi_ui_agent_readability_lab.records import ExperimentConfig
from kokochi_ui_agent_readability_lab.records.schema import (
    NonEmptyString,
    RepositoryIdentifier,
    Sha256Hex,
    StrictModel,
)


CURRENT_GROUND_TRUTH_SCHEMA_VERSION: Literal["2.0.0"] = "2.0.0"
_WHITESPACE = re.compile(r"\s+")
_ASCII_CASEFOLD_TRANSLATION = str.maketrans(
    "ABCDEFGHIJKLMNOPQRSTUVWXYZ",
    "abcdefghijklmnopqrstuvwxyz",
)


class GroundTruthError(ValueError):
    """Raised when ground truth is malformed, mismatched, or unapproved."""


class ReviewStatus(StrEnum):
    """Human review state for a ground-truth version."""

    PENDING = "pending"
    APPROVED = "approved"


class LabelNormalization(StrictModel):
    """Explicit normalization rules shared with the future scorer."""

    unicode_form: Literal["NFKC"]
    strip_whitespace: Literal[True]
    collapse_whitespace: Literal[True]
    casefold: bool


def normalize_label(value: str, rules: LabelNormalization) -> str:
    """Apply the declared label normalization in its fixed order."""

    normalized = unicodedata.normalize(rules.unicode_form, value)
    if rules.strip_whitespace:
        normalized = normalized.strip()
    if rules.collapse_whitespace:
        normalized = _WHITESPACE.sub(" ", normalized)
    if rules.casefold:
        normalized = normalized.translate(_ASCII_CASEFOLD_TRANSLATION)
    return normalized


class GroundTruthItem(StrictModel):
    """One canonical control-to-label-and-purpose mapping."""

    control_index: PositiveInt
    canonical_label: NonEmptyString
    accepted_labels: tuple[NonEmptyString, ...] = Field(min_length=1)
    purpose: RepositoryIdentifier


class FixtureGroundTruth(StrictModel):
    """Expected mappings for one exact fixture version."""

    fixture_id: RepositoryIdentifier
    fixture_version: RepositoryIdentifier
    items: tuple[GroundTruthItem, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def control_indices_must_be_unique(self) -> Self:
        indices = [item.control_index for item in self.items]
        if len(indices) != len(set(indices)):
            raise ValueError("control_index values must be unique within a fixture")
        return self


class HumanReview(StrictModel):
    """Auditable human approval of the ground-truth definition."""

    status: ReviewStatus
    reviewer: NonEmptyString | None = None
    reviewed_at: AwareDatetime | None = None
    evidence_url: NonEmptyString | None = None

    @model_validator(mode="after")
    def metadata_must_match_status(self) -> Self:
        metadata = (self.reviewer, self.reviewed_at, self.evidence_url)
        if self.status is ReviewStatus.PENDING and any(
            value is not None for value in metadata
        ):
            raise ValueError("pending human review cannot include approval metadata")
        if self.status is ReviewStatus.APPROVED and any(
            value is None for value in metadata
        ):
            raise ValueError(
                "approved human review requires reviewer, reviewed_at, and evidence_url"
            )
        if self.evidence_url is not None and not self.evidence_url.startswith(
            "https://github.com/"
        ):
            raise ValueError("human review evidence_url must be a GitHub HTTPS URL")
        return self


class GroundTruthDocument(StrictModel):
    """Complete versioned ground truth for one experiment version."""

    schema_version: Literal["1.0.0"]
    ground_truth_version: RepositoryIdentifier
    experiment_id: RepositoryIdentifier
    experiment_version: RepositoryIdentifier
    control_count: PositiveInt
    normalization: LabelNormalization
    fixtures: tuple[FixtureGroundTruth, ...] = Field(min_length=1)
    human_review: HumanReview

    @model_validator(mode="after")
    def validate_fixture_mappings(self) -> Self:
        fixture_ids = [fixture.fixture_id for fixture in self.fixtures]
        if len(fixture_ids) != len(set(fixture_ids)):
            raise ValueError("fixture_id values must be unique")

        expected_indices = set(range(1, self.control_count + 1))
        for fixture in self.fixtures:
            actual_indices = {item.control_index for item in fixture.items}
            if actual_indices != expected_indices:
                raise ValueError(
                    f"fixture {fixture.fixture_id!r} must define control_index values "
                    f"1 through {self.control_count}"
                )
            for item in fixture.items:
                canonical = normalize_label(item.canonical_label, self.normalization)
                accepted = [
                    normalize_label(label, self.normalization)
                    for label in item.accepted_labels
                ]
                if canonical not in accepted:
                    raise ValueError(
                        "accepted_labels must include canonical_label after normalization"
                    )
                if len(accepted) != len(set(accepted)):
                    raise ValueError(
                        "accepted_labels must be unique after normalization"
                    )
        return self


class ControlDiscoveryTaskGroundTruth(StrictModel):
    """Task-specific target set for one control-discovery fixture."""

    task_id: RepositoryIdentifier
    instruction: NonEmptyString
    target_control_indices: tuple[PositiveInt, ...]

    @model_validator(mode="after")
    def target_indices_must_be_unique(self) -> Self:
        if len(self.target_control_indices) != len(set(self.target_control_indices)):
            raise ValueError("target_control_indices must be unique")
        return self


class ControlDiscoveryFixtureGroundTruth(StrictModel):
    """Eligible controls and task targets for one exact fixture version."""

    fixture_id: RepositoryIdentifier
    fixture_version: RepositoryIdentifier
    eligible_controls: tuple[GroundTruthItem, ...] = Field(min_length=1)
    tasks: tuple[ControlDiscoveryTaskGroundTruth, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def controls_and_tasks_must_be_complete(self) -> Self:
        indices = [item.control_index for item in self.eligible_controls]
        if indices != list(range(1, len(indices) + 1)):
            raise ValueError(
                "eligible control_index values must be contiguous and start at 1"
            )
        task_ids = [task.task_id for task in self.tasks]
        if len(task_ids) != len(set(task_ids)):
            raise ValueError("task_id values must be unique within a fixture")
        eligible_indices = set(indices)
        for task in self.tasks:
            unknown = sorted(set(task.target_control_indices) - eligible_indices)
            if unknown:
                raise ValueError(
                    f"task {task.task_id!r} references ineligible controls: {unknown}"
                )
        return self


class ControlDiscoveryGroundTruthDocument(StrictModel):
    """Task-scoped ground truth that permits different and empty target sets."""

    schema_version: Literal["2.0.0"]
    ground_truth_version: RepositoryIdentifier
    experiment_id: RepositoryIdentifier
    experiment_version: RepositoryIdentifier
    normalization: LabelNormalization
    fixtures: tuple[ControlDiscoveryFixtureGroundTruth, ...] = Field(min_length=1)
    human_review: HumanReview

    @model_validator(mode="after")
    def fixture_and_label_mappings_must_be_valid(self) -> Self:
        fixture_ids = [fixture.fixture_id for fixture in self.fixtures]
        if len(fixture_ids) != len(set(fixture_ids)):
            raise ValueError("fixture_id values must be unique")

        for fixture in self.fixtures:
            for item in fixture.eligible_controls:
                canonical = normalize_label(item.canonical_label, self.normalization)
                accepted = [
                    normalize_label(label, self.normalization)
                    for label in item.accepted_labels
                ]
                if canonical not in accepted:
                    raise ValueError(
                        "accepted_labels must include canonical_label after normalization"
                    )
                if len(accepted) != len(set(accepted)):
                    raise ValueError(
                        "accepted_labels must be unique after normalization"
                    )
        return self


GroundTruthDocumentType: TypeAlias = (
    GroundTruthDocument | ControlDiscoveryGroundTruthDocument
)


def _reject_duplicate_keys(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise GroundTruthError(f"ground truth contains duplicate key {key!r}")
        result[key] = value
    return result


def _validate_json_bytes(content: bytes) -> None:
    if content.startswith(b"\xef\xbb\xbf"):
        raise GroundTruthError("ground truth must not contain a UTF-8 BOM")
    if b"\r" in content:
        raise GroundTruthError("ground truth must use LF line endings")
    if not content.endswith(b"\n"):
        raise GroundTruthError("ground truth must end with a newline")
    try:
        text = content.decode("utf-8")
    except UnicodeDecodeError as error:
        raise GroundTruthError("ground truth must be valid UTF-8") from error
    try:
        json.loads(
            text,
            object_pairs_hook=_reject_duplicate_keys,
            parse_constant=lambda value: (_ for _ in ()).throw(
                GroundTruthError(f"ground truth contains non-finite value {value}")
            ),
        )
    except GroundTruthError:
        raise
    except json.JSONDecodeError as error:
        raise GroundTruthError(f"ground truth is not valid JSON: {error}") from error


def parse_ground_truth_json(content: bytes) -> GroundTruthDocumentType:
    """Parse exact ground-truth bytes with duplicate-key protection."""

    _validate_json_bytes(content)
    decoded = json.loads(content)
    if not isinstance(decoded, dict):
        raise GroundTruthError("ground truth must contain a top-level object")
    schema_version = decoded.get("schema_version")
    model: type[GroundTruthDocument] | type[ControlDiscoveryGroundTruthDocument]
    if schema_version == "1.0.0":
        model = GroundTruthDocument
    elif schema_version == "2.0.0":
        model = ControlDiscoveryGroundTruthDocument
    else:
        raise GroundTruthError(
            f"unsupported ground truth schema_version: {schema_version!r}"
        )
    try:
        return model.model_validate_json(content)
    except ValidationError as error:
        raise GroundTruthError(
            f"ground truth does not match schema: {error}"
        ) from error


def ground_truth_sha256(content: bytes) -> Sha256Hex:
    """Return the exact content hash recorded by an experiment run."""

    return hashlib.sha256(content).hexdigest()


def validate_ground_truth_matches_config(
    content: bytes,
    config: ExperimentConfig,
    *,
    require_approved: bool = True,
) -> GroundTruthDocumentType:
    """Parse exact bytes and reject config, completeness, or approval mismatches."""

    ground_truth = parse_ground_truth_json(content)
    mismatches: list[str] = []
    if ground_truth.experiment_id != config.experiment_id:
        mismatches.append("experiment_id")
    if ground_truth.experiment_version != config.experiment_version:
        mismatches.append("experiment_version")
    if ground_truth.ground_truth_version != config.ground_truth.ground_truth_version:
        mismatches.append("ground_truth_version")

    configured_fixtures = {
        fixture.fixture_id: fixture.fixture_version for fixture in config.fixtures
    }
    ground_truth_fixtures = {
        fixture.fixture_id: fixture.fixture_version for fixture in ground_truth.fixtures
    }
    missing = sorted(set(configured_fixtures) - set(ground_truth_fixtures))
    unknown = sorted(set(ground_truth_fixtures) - set(configured_fixtures))
    wrong_versions = sorted(
        fixture_id
        for fixture_id in set(configured_fixtures) & set(ground_truth_fixtures)
        if configured_fixtures[fixture_id] != ground_truth_fixtures[fixture_id]
    )
    if missing:
        mismatches.append("missing fixtures: " + ", ".join(missing))
    if unknown:
        mismatches.append("unknown fixtures: " + ", ".join(unknown))
    if wrong_versions:
        mismatches.append("fixture version: " + ", ".join(wrong_versions))
    if isinstance(ground_truth, ControlDiscoveryGroundTruthDocument):
        configured_tasks = {
            (fixture_id, task.task_id): task.instruction
            for task in (config.tasks or [])
            for fixture_id in task.fixture_ids
        }
        ground_truth_tasks = {
            (fixture.fixture_id, task.task_id): task.instruction
            for fixture in ground_truth.fixtures
            for task in fixture.tasks
        }
        missing_tasks = sorted(set(configured_tasks) - set(ground_truth_tasks))
        unknown_tasks = sorted(set(ground_truth_tasks) - set(configured_tasks))
        changed_instructions = sorted(
            key
            for key in set(configured_tasks) & set(ground_truth_tasks)
            if configured_tasks[key] != ground_truth_tasks[key]
        )
        if missing_tasks:
            mismatches.append(f"missing tasks: {missing_tasks}")
        if unknown_tasks:
            mismatches.append(f"unknown tasks: {unknown_tasks}")
        if changed_instructions:
            mismatches.append(f"task instruction: {changed_instructions}")
    if (
        require_approved
        and ground_truth.human_review.status is not ReviewStatus.APPROVED
    ):
        mismatches.append("human_review.status")
    if (
        require_approved
        and ground_truth_sha256(content) != config.ground_truth.approved_sha256
    ):
        mismatches.append("ground_truth.approved_sha256")

    if mismatches:
        raise GroundTruthError(
            "ground truth differs from the experiment config: " + "; ".join(mismatches)
        )
    return ground_truth
