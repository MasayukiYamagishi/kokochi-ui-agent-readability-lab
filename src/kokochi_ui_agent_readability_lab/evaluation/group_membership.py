"""Normalize and score control-to-form-group membership responses."""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
import hashlib
from pathlib import Path
import re
import unicodedata
from typing import Annotated, Literal, Self, TypeVar

from pydantic import (
    Field,
    NonNegativeInt,
    PositiveInt,
    ValidationError,
    model_validator,
)

from kokochi_ui_agent_readability_lab.evaluation.item_association import (
    EvaluationError,
    EvaluationStatus,
    canonical_model_json,
)
from kokochi_ui_agent_readability_lab.records import ExperimentConfig
from kokochi_ui_agent_readability_lab.records.schema import (
    Identifier,
    NonEmptyString,
    RepositoryIdentifier,
    Sha256Hex,
    StrictModel,
)


GROUP_MEMBERSHIP_SCORER_ID: Literal["control-group-membership-f1"] = (
    "control-group-membership-f1"
)
GROUP_MEMBERSHIP_SCORER_VERSION: Literal["v1"] = "v1"
CURRENT_GROUP_MEMBERSHIP_EVALUATION_SCHEMA_VERSION: Literal["1.0.0"] = "1.0.0"
GROUP_MEMBERSHIP_SCORER_SOURCE_PATH: Literal[
    "src/kokochi_ui_agent_readability_lab/evaluation/group_membership.py"
] = "src/kokochi_ui_agent_readability_lab/evaluation/group_membership.py"
MetricItem = TypeVar("MetricItem")


class GroupMembershipError(ValueError):
    """Raised when ground truth or the scoring target is invalid."""


class GroupMembershipNormalization(StrictModel):
    unicode_form: Literal["NFKC"]
    strip_whitespace: Literal[True]
    collapse_whitespace: Literal[True]
    casefold: Literal[False]


class GroundTruthGroup(StrictModel):
    group_label: NonEmptyString
    control_indices: tuple[PositiveInt, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def indices_must_be_unique(self) -> Self:
        if len(self.control_indices) != len(set(self.control_indices)):
            raise ValueError("group control indices must be unique")
        return self


class GroupMembershipTask(StrictModel):
    task_id: NonEmptyString
    fixture_ids: tuple[NonEmptyString, NonEmptyString]
    eligible_control_indices: tuple[PositiveInt, ...] = Field(min_length=1)
    groups: tuple[GroundTruthGroup, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def groups_must_partition_eligible_controls(self) -> Self:
        eligible = set(self.eligible_control_indices)
        grouped = [index for group in self.groups for index in group.control_indices]
        if len(grouped) != len(set(grouped)):
            raise ValueError("a control cannot belong to multiple ground-truth groups")
        if set(grouped) != eligible:
            raise ValueError("ground-truth groups must partition eligible controls")
        labels = [group.group_label for group in self.groups]
        if len(labels) != len(set(labels)):
            raise ValueError("ground-truth group labels must be unique")
        return self


class GroupMembershipGroundTruth(StrictModel):
    schema_version: Literal["1.0.0"]
    diagnostic_id: Literal["group-membership"]
    ground_truth_version: Literal["v1"]
    normalization: GroupMembershipNormalization
    tasks: tuple[GroupMembershipTask, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def task_and_fixture_ids_must_be_unique(self) -> Self:
        task_ids = [task.task_id for task in self.tasks]
        fixture_ids = [fixture for task in self.tasks for fixture in task.fixture_ids]
        if len(task_ids) != len(set(task_ids)):
            raise ValueError("task IDs must be unique")
        if len(fixture_ids) != len(set(fixture_ids)):
            raise ValueError("fixture IDs must be unique")
        return self


class MembershipPrediction(StrictModel):
    control_index: PositiveInt
    group_label: NonEmptyString


class GroupMembershipResponse(StrictModel):
    memberships: tuple[MembershipPrediction, ...]
    ungrouped_control_indices: tuple[PositiveInt, ...]


class GroupMetrics(StrictModel):
    group_label: NonEmptyString
    true_positive_count: NonNegativeInt
    false_positive_count: NonNegativeInt
    false_negative_count: NonNegativeInt
    precision: Annotated[float, Field(ge=0, le=1)]
    recall: Annotated[float, Field(ge=0, le=1)]
    f1: Annotated[float, Field(ge=0, le=1)]


class GroupMembershipScore(StrictModel):
    scorer_id: Literal["control-group-membership-f1"]
    scorer_version: Literal["v1"]
    task_id: NonEmptyString
    fixture_id: NonEmptyString
    schema_valid: bool
    complete_response: bool
    exact_match: bool
    true_positive_count: NonNegativeInt
    false_positive_count: NonNegativeInt
    false_negative_count: NonNegativeInt
    precision: Annotated[float, Field(ge=0, le=1)]
    recall: Annotated[float, Field(ge=0, le=1)]
    f1: Annotated[float, Field(ge=0, le=1)]
    coverage: Annotated[float, Field(ge=0, le=1)]
    duplicate_control_indices: tuple[PositiveInt, ...]
    out_of_range_control_indices: tuple[PositiveInt, ...]
    unknown_group_labels: tuple[NonEmptyString, ...]
    missing_control_indices: tuple[PositiveInt, ...]
    group_metrics: tuple[GroupMetrics, ...]
    validation_error: str | None = None


class GroupMembershipTargetReference(StrictModel):
    """Experiment cell identity retained by RunRecord and telemetry."""

    experiment_id: RepositoryIdentifier
    experiment_version: RepositoryIdentifier
    fixture_id: RepositoryIdentifier
    fixture_version: RepositoryIdentifier
    task_id: RepositoryIdentifier
    input_representation: Identifier


class GroupMembershipScorerReference(StrictModel):
    """Exact scorer implementation used for a membership score."""

    scorer_id: Literal["control-group-membership-f1"]
    scorer_version: Literal["v1"]
    source_path: Literal[
        "src/kokochi_ui_agent_readability_lab/evaluation/group_membership.py"
    ]
    source_sha256: Sha256Hex


class GroupMembershipGroundTruthReference(StrictModel):
    ground_truth_version: RepositoryIdentifier
    source_sha256: Sha256Hex


class GroupMembershipNormalizedResponseDocument(StrictModel):
    schema_version: Literal["1.0.0"]
    status: EvaluationStatus
    raw_response_sha256: Sha256Hex
    schema_valid: bool
    response: GroupMembershipResponse | None


class GroupMembershipScoreDocument(StrictModel):
    """Canonical score artifact for task-aware group reconstruction."""

    schema_version: Literal["1.0.0"]
    status: EvaluationStatus
    target: GroupMembershipTargetReference
    scorer: GroupMembershipScorerReference
    ground_truth: GroupMembershipGroundTruthReference
    raw_response_sha256: Sha256Hex
    normalized_response_sha256: Sha256Hex
    schema_valid: bool
    task_success: bool
    end_to_end_success: bool
    metrics: GroupMembershipScore


@dataclass(frozen=True, slots=True)
class GroupMembershipEvaluationArtifacts:
    raw_response: bytes
    normalized_response: GroupMembershipNormalizedResponseDocument
    score: GroupMembershipScoreDocument

    @property
    def normalized_response_bytes(self) -> bytes:
        return canonical_model_json(self.normalized_response)

    @property
    def score_bytes(self) -> bytes:
        return canonical_model_json(self.score)


def response_json_schema() -> dict[str, object]:
    """Return the schema sent to Ollama without revealing fixture-specific bounds."""

    return {
        "type": "object",
        "additionalProperties": False,
        "required": ["memberships", "ungrouped_control_indices"],
        "properties": {
            "memberships": {
                "type": "array",
                "items": {
                    "type": "object",
                    "additionalProperties": False,
                    "required": ["control_index", "group_label"],
                    "properties": {
                        "control_index": {"type": "integer", "minimum": 1},
                        "group_label": {"type": "string", "minLength": 1},
                    },
                },
            },
            "ungrouped_control_indices": {
                "type": "array",
                "items": {"type": "integer", "minimum": 1},
            },
        },
    }


def load_group_membership_ground_truth(content: bytes) -> GroupMembershipGroundTruth:
    """Parse the versioned ground-truth document from exact bytes."""

    try:
        return GroupMembershipGroundTruth.model_validate_json(content)
    except ValidationError as error:
        raise GroupMembershipError("invalid group-membership ground truth") from error


def select_task(
    ground_truth: GroupMembershipGroundTruth,
    fixture_id: str,
) -> GroupMembershipTask:
    """Resolve the single task assigned to a fixture."""

    matches = [task for task in ground_truth.tasks if fixture_id in task.fixture_ids]
    if len(matches) != 1:
        raise GroupMembershipError(f"fixture has no unique task: {fixture_id}")
    return matches[0]


def normalize_group_label(value: str) -> str:
    """Apply the preregistered NFKC and whitespace normalization."""

    return re.sub(r"\s+", " ", unicodedata.normalize("NFKC", value).strip())


def _prf(
    predicted: set[MetricItem],
    expected: set[MetricItem],
) -> tuple[int, int, int, float, float, float]:
    true_positive = len(predicted & expected)
    false_positive = len(predicted - expected)
    false_negative = len(expected - predicted)
    precision = true_positive / len(predicted) if predicted else 0.0
    recall = true_positive / len(expected) if expected else 1.0
    f1 = (
        0.0
        if precision + recall == 0
        else 2 * precision * recall / (precision + recall)
    )
    return true_positive, false_positive, false_negative, precision, recall, f1


def evaluate_group_membership_response(
    raw_response: bytes,
    *,
    ground_truth: GroupMembershipGroundTruth,
    fixture_id: str,
) -> tuple[GroupMembershipResponse | None, GroupMembershipScore]:
    """Normalize and score one response by (group label, control index) relation F1."""

    task = select_task(ground_truth, fixture_id)
    expected = {
        (normalize_group_label(group.group_label), index)
        for group in task.groups
        for index in group.control_indices
    }
    expected_labels = {label for label, _ in expected}
    eligible = set(task.eligible_control_indices)
    try:
        response = GroupMembershipResponse.model_validate_json(raw_response)
    except ValidationError as error:
        score = GroupMembershipScore(
            scorer_id=GROUP_MEMBERSHIP_SCORER_ID,
            scorer_version=GROUP_MEMBERSHIP_SCORER_VERSION,
            task_id=task.task_id,
            fixture_id=fixture_id,
            schema_valid=False,
            complete_response=False,
            exact_match=False,
            true_positive_count=0,
            false_positive_count=0,
            false_negative_count=len(expected),
            precision=0.0,
            recall=0.0,
            f1=0.0,
            coverage=0.0,
            duplicate_control_indices=(),
            out_of_range_control_indices=(),
            unknown_group_labels=(),
            missing_control_indices=tuple(sorted(eligible)),
            group_metrics=(),
            validation_error=str(error),
        )
        return None, score

    normalized_memberships = tuple(
        MembershipPrediction(
            control_index=item.control_index,
            group_label=normalize_group_label(item.group_label),
        )
        for item in response.memberships
    )
    normalized = GroupMembershipResponse(
        memberships=normalized_memberships,
        ungrouped_control_indices=response.ungrouped_control_indices,
    )
    assigned_indices = [item.control_index for item in normalized.memberships]
    all_reported_indices = [*assigned_indices, *normalized.ungrouped_control_indices]
    counts = Counter(all_reported_indices)
    duplicate_indices = tuple(
        sorted(index for index, count in counts.items() if count > 1)
    )
    out_of_range = tuple(sorted(set(all_reported_indices) - eligible))
    covered = set(all_reported_indices) & eligible
    missing = tuple(sorted(eligible - covered))
    unknown_labels = tuple(
        sorted({item.group_label for item in normalized.memberships} - expected_labels)
    )
    predicted = {
        (item.group_label, item.control_index) for item in normalized.memberships
    }
    tp, fp, fn, precision, recall, f1 = _prf(predicted, expected)
    per_group: list[GroupMetrics] = []
    for group in task.groups:
        label = normalize_group_label(group.group_label)
        group_predicted = {
            index for predicted_label, index in predicted if predicted_label == label
        }
        group_expected = set(group.control_indices)
        group_values = _prf(group_predicted, group_expected)
        per_group.append(
            GroupMetrics(
                group_label=label,
                true_positive_count=group_values[0],
                false_positive_count=group_values[1],
                false_negative_count=group_values[2],
                precision=group_values[3],
                recall=group_values[4],
                f1=group_values[5],
            )
        )
    complete = (
        not duplicate_indices
        and not out_of_range
        and not missing
        and not unknown_labels
    )
    exact = (
        complete and predicted == expected and not normalized.ungrouped_control_indices
    )
    score = GroupMembershipScore(
        scorer_id=GROUP_MEMBERSHIP_SCORER_ID,
        scorer_version=GROUP_MEMBERSHIP_SCORER_VERSION,
        task_id=task.task_id,
        fixture_id=fixture_id,
        schema_valid=True,
        complete_response=complete,
        exact_match=exact,
        true_positive_count=tp,
        false_positive_count=fp,
        false_negative_count=fn,
        precision=precision,
        recall=recall,
        f1=f1,
        coverage=len(covered) / len(eligible),
        duplicate_control_indices=duplicate_indices,
        out_of_range_control_indices=out_of_range,
        unknown_group_labels=unknown_labels,
        missing_control_indices=missing,
        group_metrics=tuple(per_group),
    )
    return normalized, score


def group_membership_scorer_source_sha256() -> str:
    """Return the digest of the scorer source recorded with every result."""

    return hashlib.sha256(Path(__file__).read_bytes()).hexdigest()


def evaluate_group_membership_experiment_response(
    raw_response: bytes,
    *,
    config: ExperimentConfig,
    ground_truth_content: bytes,
    fixture_id: str,
    task_id: str,
    input_representation: str,
) -> GroupMembershipEvaluationArtifacts:
    """Validate experiment identity, normalize, and score one v2 response."""

    if not isinstance(raw_response, bytes):
        raise TypeError("raw_response must be bytes")
    if (
        config.scorer.scorer_id != GROUP_MEMBERSHIP_SCORER_ID
        or config.scorer.scorer_version != GROUP_MEMBERSHIP_SCORER_VERSION
    ):
        raise EvaluationError("configured scorer is not control-group-membership-f1 v1")
    if config.design.expected_output_schema != response_json_schema():
        raise EvaluationError("configured output schema differs from the v2 scorer")
    if hashlib.sha256(ground_truth_content).hexdigest() != str(
        config.ground_truth.approved_sha256
    ):
        raise EvaluationError("ground truth digest differs from config.yaml")
    try:
        ground_truth = load_group_membership_ground_truth(ground_truth_content)
        selected_task = select_task(ground_truth, fixture_id)
    except GroupMembershipError as error:
        raise EvaluationError("group-membership ground truth is invalid") from error
    if selected_task.task_id != task_id:
        raise EvaluationError("task does not match the fixture ground truth")
    configured_task = next(
        (task for task in config.tasks or () if task.task_id == task_id),
        None,
    )
    configured_fixture = next(
        (fixture for fixture in config.fixtures if fixture.fixture_id == fixture_id),
        None,
    )
    if (
        configured_task is None
        or fixture_id not in configured_task.fixture_ids
        or configured_fixture is None
    ):
        raise EvaluationError("task or fixture is not declared by config.yaml")

    normalized, metrics = evaluate_group_membership_response(
        raw_response,
        ground_truth=ground_truth,
        fixture_id=fixture_id,
    )
    status = (
        EvaluationStatus.SCORED
        if metrics.schema_valid
        else EvaluationStatus.NORMALIZATION_FAILED
    )
    raw_sha256 = hashlib.sha256(raw_response).hexdigest()
    normalized_document = GroupMembershipNormalizedResponseDocument(
        schema_version=CURRENT_GROUP_MEMBERSHIP_EVALUATION_SCHEMA_VERSION,
        status=status,
        raw_response_sha256=raw_sha256,
        schema_valid=metrics.schema_valid,
        response=normalized,
    )
    normalized_bytes = canonical_model_json(normalized_document)
    score = GroupMembershipScoreDocument(
        schema_version=CURRENT_GROUP_MEMBERSHIP_EVALUATION_SCHEMA_VERSION,
        status=status,
        target=GroupMembershipTargetReference(
            experiment_id=config.experiment_id,
            experiment_version=config.experiment_version,
            fixture_id=fixture_id,
            fixture_version=configured_fixture.fixture_version,
            task_id=task_id,
            input_representation=input_representation,
        ),
        scorer=GroupMembershipScorerReference(
            scorer_id=GROUP_MEMBERSHIP_SCORER_ID,
            scorer_version=GROUP_MEMBERSHIP_SCORER_VERSION,
            source_path=GROUP_MEMBERSHIP_SCORER_SOURCE_PATH,
            source_sha256=group_membership_scorer_source_sha256(),
        ),
        ground_truth=GroupMembershipGroundTruthReference(
            ground_truth_version=ground_truth.ground_truth_version,
            source_sha256=hashlib.sha256(ground_truth_content).hexdigest(),
        ),
        raw_response_sha256=raw_sha256,
        normalized_response_sha256=hashlib.sha256(normalized_bytes).hexdigest(),
        schema_valid=metrics.schema_valid,
        task_success=metrics.complete_response,
        end_to_end_success=metrics.complete_response,
        metrics=metrics,
    )
    return GroupMembershipEvaluationArtifacts(
        raw_response=raw_response,
        normalized_response=normalized_document,
        score=score,
    )
