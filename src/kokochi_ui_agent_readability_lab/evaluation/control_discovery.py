"""Versioned deterministic scorer for task-scoped target-control discovery."""

from __future__ import annotations

from collections import Counter
from collections.abc import Iterable
from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
from typing import Annotated, Literal, Self, cast

from jsonschema import exceptions as jsonschema_exceptions  # type: ignore[import-untyped]
from jsonschema import validators  # type: ignore[import-untyped]
from pydantic import Field, FiniteFloat, NonNegativeInt, PositiveInt, model_validator

from kokochi_ui_agent_readability_lab.evaluation.item_association import (
    EvaluationError,
    EvaluationStatus,
    ResponseClassification,
    SchemaIssue,
    canonical_model_json,
)
from kokochi_ui_agent_readability_lab.ground_truth import (
    ControlDiscoveryFixtureGroundTruth,
    ControlDiscoveryGroundTruthDocument,
    ControlDiscoveryTaskGroundTruth,
    GroundTruthError,
    ground_truth_sha256,
    normalize_label,
    validate_ground_truth_matches_config,
)
from kokochi_ui_agent_readability_lab.records import ExperimentConfig
from kokochi_ui_agent_readability_lab.records.schema import (
    Identifier,
    NonEmptyString,
    RepositoryIdentifier,
    Sha256Hex,
    StrictModel,
)


CURRENT_CONTROL_DISCOVERY_EVALUATION_SCHEMA_VERSION: Literal["1.1.0"] = "1.1.0"
CONTROL_DISCOVERY_SCORER_ID: Literal["target-control-set-f1"] = "target-control-set-f1"
CONTROL_DISCOVERY_SCORER_VERSION: Literal["v1"] = "v1"
CONTROL_DISCOVERY_SCORER_SOURCE_PATH: Literal[
    "src/kokochi_ui_agent_readability_lab/evaluation/control_discovery.py"
] = "src/kokochi_ui_agent_readability_lab/evaluation/control_discovery.py"
CONTROL_DISCOVERY_NORMALIZATION_SOURCE_PATH: Literal[
    "src/kokochi_ui_agent_readability_lab/ground_truth/schema.py"
] = "src/kokochi_ui_agent_readability_lab/ground_truth/schema.py"

_REQUIRED_ITEM_FIELDS = frozenset({"control_index", "label", "purpose", "confidence"})
_CLASSIFICATION_ORDER = {
    classification: index for index, classification in enumerate(ResponseClassification)
}


class ControlDiscoveryNormalizedItem(StrictModel):
    """One schema-valid target prediction after declared label normalization."""

    response_position: PositiveInt
    control_index: PositiveInt
    label: str | None
    purpose: RepositoryIdentifier | None
    confidence: Annotated[FiniteFloat, Field(ge=0, le=1)]


class ControlDiscoveryNormalizedResponse(StrictModel):
    """Canonical normalized-response artifact linked to exact raw bytes."""

    schema_version: Literal["1.1.0"]
    status: EvaluationStatus
    raw_response_sha256: Sha256Hex
    output_schema_sha256: Sha256Hex
    schema_valid: bool
    classifications: tuple[ResponseClassification, ...]
    schema_issues: tuple[SchemaIssue, ...]
    target_controls: tuple[ControlDiscoveryNormalizedItem, ...] | None


class ControlDiscoveryDiagnostics(StrictModel):
    """Task-specific error components retained for every attempted response."""

    observed_target_count: NonNegativeInt | None
    duplicate_control_indices: tuple[int, ...]
    out_of_range_control_indices: tuple[int, ...]
    unknown_purposes: tuple[str, ...]


class ControlDiscoveryItemJudgment(StrictModel):
    """Auditable target, label, and purpose judgment for one prediction."""

    response_position: PositiveInt
    control_index: PositiveInt
    normalized_label: str | None
    purpose: RepositoryIdentifier | None
    is_target: bool
    label_matches_ground_truth: bool
    purpose_matches_ground_truth: bool


class ControlDiscoveryMetrics(StrictModel):
    """Preregistered per-task primary and official secondary metrics."""

    true_positive_count: NonNegativeInt | None
    false_positive_count: NonNegativeInt | None
    false_negative_count: NonNegativeInt | None
    predicted_target_count: NonNegativeInt | None
    ground_truth_target_count: NonNegativeInt
    precision: Annotated[FiniteFloat, Field(ge=0, le=1)] | None
    recall: Annotated[FiniteFloat, Field(ge=0, le=1)] | None
    f1: Annotated[FiniteFloat, Field(ge=0, le=1)]
    exact_match: bool
    zero_target_correct_empty: bool | None
    valid_response: bool
    complete_response: bool
    label_correct_count: NonNegativeInt | None
    label_accuracy_denominator: NonNegativeInt | None
    label_accuracy: Annotated[FiniteFloat, Field(ge=0, le=1)] | None
    purpose_correct_count: NonNegativeInt | None
    purpose_accuracy_denominator: NonNegativeInt | None
    purpose_accuracy: Annotated[FiniteFloat, Field(ge=0, le=1)] | None
    component_metrics_missing_reason: NonEmptyString | None = None

    @model_validator(mode="after")
    def component_metrics_must_match_response_validity(self) -> Self:
        components = (
            self.true_positive_count,
            self.false_positive_count,
            self.false_negative_count,
            self.predicted_target_count,
            self.precision,
            self.recall,
            self.label_correct_count,
            self.label_accuracy_denominator,
            self.purpose_correct_count,
            self.purpose_accuracy_denominator,
        )
        if self.valid_response:
            if any(value is None for value in components):
                raise ValueError("valid responses require component metrics")
            if self.component_metrics_missing_reason is not None:
                raise ValueError(
                    "valid responses cannot include a component-metrics missing reason"
                )
        else:
            invalid_only_components = (
                *components,
                self.label_accuracy,
                self.purpose_accuracy,
                self.zero_target_correct_empty,
            )
            if any(value is not None for value in invalid_only_components):
                raise ValueError(
                    "invalid responses must omit non-applicable component metrics"
                )
            if self.component_metrics_missing_reason is None:
                raise ValueError(
                    "invalid responses require a component-metrics missing reason"
                )
        return self


class ControlDiscoveryScorerReference(StrictModel):
    """Exact scorer identity and implementation digest."""

    scorer_id: Literal["target-control-set-f1"]
    scorer_version: Literal["v1"]
    source_path: Literal[
        "src/kokochi_ui_agent_readability_lab/evaluation/control_discovery.py"
    ]
    source_sha256: Sha256Hex
    normalization_source_path: Literal[
        "src/kokochi_ui_agent_readability_lab/ground_truth/schema.py"
    ]
    normalization_source_sha256: Sha256Hex


class ControlDiscoveryGroundTruthReference(StrictModel):
    """Exact ground-truth version and byte digest used for scoring."""

    ground_truth_version: RepositoryIdentifier
    source_sha256: Sha256Hex


class ControlDiscoveryTargetReference(StrictModel):
    """Experiment, fixture, and task identity for one score artifact."""

    experiment_id: RepositoryIdentifier
    experiment_version: RepositoryIdentifier
    fixture_id: RepositoryIdentifier
    fixture_version: RepositoryIdentifier
    task_id: RepositoryIdentifier
    input_representation: Identifier


class ControlDiscoveryScoreDocument(StrictModel):
    """Canonical score artifact for a valid or rejected response."""

    schema_version: Literal["1.1.0"]
    status: EvaluationStatus
    target: ControlDiscoveryTargetReference
    scorer: ControlDiscoveryScorerReference
    ground_truth: ControlDiscoveryGroundTruthReference
    raw_response_sha256: Sha256Hex
    normalized_response_sha256: Sha256Hex
    output_schema_sha256: Sha256Hex
    schema_valid: bool
    task_success: bool
    end_to_end_success: bool
    classifications: tuple[ResponseClassification, ...]
    diagnostics: ControlDiscoveryDiagnostics
    item_judgments: tuple[ControlDiscoveryItemJudgment, ...]
    metrics: ControlDiscoveryMetrics


@dataclass(frozen=True, slots=True)
class ControlDiscoveryEvaluationArtifacts:
    """Raw bytes plus their linked normalized-response and score artifacts."""

    raw_response: bytes
    normalized_response: ControlDiscoveryNormalizedResponse
    score: ControlDiscoveryScoreDocument

    @property
    def normalized_response_bytes(self) -> bytes:
        """Return canonical bytes suitable for write-once artifact storage."""

        return canonical_model_json(self.normalized_response)

    @property
    def score_bytes(self) -> bytes:
        """Return canonical bytes suitable for write-once artifact storage."""

        return canonical_model_json(self.score)


@dataclass(frozen=True, slots=True)
class _ParsedCandidate:
    value: object
    candidate_text: str
    exact_json: bool
    syntax_error: bool
    extra_explanation: bool


class _DuplicateKeyError(ValueError):
    pass


def control_discovery_scorer_source_sha256() -> Sha256Hex:
    """Hash the exact target-control scorer source bytes used by this process."""

    return hashlib.sha256(Path(__file__).read_bytes()).hexdigest()


def control_discovery_normalization_source_sha256() -> Sha256Hex:
    """Hash the exact shared normalization source bytes used by this process."""

    source_path = Path(__file__).parents[1] / "ground_truth" / "schema.py"
    return hashlib.sha256(source_path.read_bytes()).hexdigest()


def _canonical_json_sha256(value: object) -> Sha256Hex:
    content = json.dumps(
        value,
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(content).hexdigest()


def _reject_non_finite(value: str) -> object:
    raise ValueError(f"non-finite JSON number {value!r}")


def _reject_duplicate_keys(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise _DuplicateKeyError(key)
        result[key] = value
    return result


def _parse_json_candidate(raw_response: bytes) -> _ParsedCandidate | None:
    try:
        text = raw_response.decode("utf-8")
    except UnicodeDecodeError:
        return None
    if text.startswith("\ufeff"):
        return None

    decoder = json.JSONDecoder(parse_constant=_reject_non_finite)
    first_non_whitespace = len(text) - len(text.lstrip())
    try:
        value, end = decoder.raw_decode(text, first_non_whitespace)
    except (json.JSONDecodeError, ValueError):
        value = None
    else:
        exact_json = not text[end:].strip()
        return _ParsedCandidate(
            value=value,
            candidate_text=text[first_non_whitespace:end],
            exact_json=exact_json,
            syntax_error=False,
            extra_explanation=not exact_json,
        )

    starts_with_json_container = (
        first_non_whitespace < len(text) and text[first_non_whitespace] in "{["
    )
    for start, character in enumerate(text):
        if character not in "{[":
            continue
        try:
            value, end = decoder.raw_decode(text, start)
        except (json.JSONDecodeError, ValueError):
            continue
        return _ParsedCandidate(
            value=value,
            candidate_text=text[start:end],
            exact_json=False,
            syntax_error=starts_with_json_container,
            extra_explanation=not starts_with_json_container,
        )
    return None


def _has_duplicate_keys(candidate_text: str) -> bool:
    try:
        json.loads(
            candidate_text,
            object_pairs_hook=_reject_duplicate_keys,
            parse_constant=_reject_non_finite,
        )
    except _DuplicateKeyError:
        return True
    return False


def _json_pointer(parts: Iterable[object]) -> str:
    encoded = [str(part).replace("~", "~0").replace("/", "~1") for part in parts]
    return "" if not encoded else "/" + "/".join(encoded)


def _validate_schema(
    value: object,
    schema: dict[str, object],
) -> tuple[SchemaIssue, ...]:
    validator_class = validators.validator_for(schema)
    try:
        validator_class.check_schema(schema)
    except jsonschema_exceptions.SchemaError as error:
        raise EvaluationError("configured expected_output_schema is invalid") from error
    errors = sorted(
        validator_class(schema).iter_errors(value),
        key=lambda error: (
            tuple(str(part) for part in error.absolute_path),
            tuple(str(part) for part in error.absolute_schema_path),
            str(error.validator),
        ),
    )
    return tuple(
        SchemaIssue(
            instance_path=_json_pointer(error.absolute_path),
            schema_path=_json_pointer(error.absolute_schema_path),
            rule=str(error.validator),
        )
        for error in errors
    )


def _mapping(value: object, path: str) -> dict[str, object]:
    if not isinstance(value, dict):
        raise EvaluationError(f"output schema {path} must be a mapping")
    return cast("dict[str, object]", value)


def _string_set(value: object, path: str) -> set[str]:
    if not isinstance(value, list) or any(not isinstance(item, str) for item in value):
        raise EvaluationError(f"output schema {path} must be a string array")
    return set(cast("list[str]", value))


def _validate_scorer_schema_contract(
    schema: dict[str, object],
    ground_truth: ControlDiscoveryGroundTruthDocument,
) -> None:
    def require(condition: bool, message: str) -> None:
        if not condition:
            raise EvaluationError(
                "configured expected_output_schema is incompatible with "
                f"{CONTROL_DISCOVERY_SCORER_ID} "
                f"{CONTROL_DISCOVERY_SCORER_VERSION}: {message}"
            )

    root_properties = _mapping(schema.get("properties"), "properties")
    require(schema.get("type") == "object", "root type must be object")
    require(schema.get("additionalProperties") is False, "root must reject extras")
    require(
        _string_set(schema.get("required"), "required") == {"target_controls"},
        "root must require only target_controls",
    )
    require(
        set(root_properties) == {"target_controls"},
        "root must define only target_controls",
    )
    array_schema = _mapping(root_properties.get("target_controls"), "target_controls")
    require(array_schema.get("type") == "array", "target_controls must be an array")
    require(
        "minItems" not in array_schema and "maxItems" not in array_schema,
        "target count must not be disclosed by array bounds",
    )
    require(array_schema.get("uniqueItems") is True, "array must use uniqueItems")

    item_schema = _mapping(array_schema.get("items"), "target_controls.items")
    item_properties = _mapping(item_schema.get("properties"), "item properties")
    require(item_schema.get("type") == "object", "items must be objects")
    require(item_schema.get("additionalProperties") is False, "items reject extras")
    require(
        _string_set(item_schema.get("required"), "item required")
        == _REQUIRED_ITEM_FIELDS,
        "items must require the scorer fields",
    )
    require(
        set(item_properties) == _REQUIRED_ITEM_FIELDS,
        "items must define only the scorer fields",
    )

    control_schema = _mapping(item_properties.get("control_index"), "control_index")
    require(control_schema.get("type") == "integer", "control_index must be integer")
    require(control_schema.get("minimum") == 1, "control_index minimum must be 1")
    require(
        "maximum" not in control_schema,
        "control_index maximum must not disclose fixture size",
    )

    label_schema = _mapping(item_properties.get("label"), "label")
    require(
        _string_set(label_schema.get("type"), "label.type") == {"string", "null"},
        "label must allow only string or null",
    )
    require(label_schema.get("minLength") == 1, "label minLength must be 1")

    purpose_schema = _mapping(item_properties.get("purpose"), "purpose")
    purpose_options = purpose_schema.get("anyOf")
    require(
        isinstance(purpose_options, list) and len(purpose_options) == 2,
        "purpose must have enum and null alternatives",
    )
    options = [
        _mapping(option, "purpose.anyOf")
        for option in cast("list[object]", purpose_options)
    ]
    enum_options = [option for option in options if "enum" in option]
    null_options = [option for option in options if option.get("type") == "null"]
    require(
        len(enum_options) == 1 and len(null_options) == 1,
        "purpose must have exactly one enum and one null alternative",
    )
    configured_purposes = _string_set(enum_options[0].get("enum"), "purpose enum")
    ground_truth_purposes = {
        item.purpose
        for fixture in ground_truth.fixtures
        for item in fixture.eligible_controls
    }
    require(
        configured_purposes == ground_truth_purposes,
        "purpose enum must match ground-truth purposes",
    )

    confidence_schema = _mapping(item_properties.get("confidence"), "confidence")
    require(confidence_schema.get("type") == "number", "confidence must be number")
    require(confidence_schema.get("minimum") == 0, "confidence minimum must be 0")
    require(confidence_schema.get("maximum") == 1, "confidence maximum must be 1")


def _select_fixture_and_task(
    ground_truth: ControlDiscoveryGroundTruthDocument,
    fixture_id: str,
    task_id: str,
) -> tuple[ControlDiscoveryFixtureGroundTruth, ControlDiscoveryTaskGroundTruth]:
    fixture = next(
        (
            candidate
            for candidate in ground_truth.fixtures
            if candidate.fixture_id == fixture_id
        ),
        None,
    )
    if fixture is None:
        raise EvaluationError(f"unknown ground-truth fixture_id {fixture_id!r}")
    task = next(
        (candidate for candidate in fixture.tasks if candidate.task_id == task_id), None
    )
    if task is None:
        raise EvaluationError(
            f"task_id {task_id!r} is not defined for fixture {fixture_id!r}"
        )
    return fixture, task


def _response_items(value: object) -> list[object] | None:
    if not isinstance(value, dict):
        return None
    items = value.get("target_controls")
    return items if isinstance(items, list) else None


def _is_partial_response(value: object) -> bool:
    if not isinstance(value, dict) or "target_controls" not in value:
        return True
    items = _response_items(value)
    if items is None:
        return False
    return any(
        isinstance(item, dict) and not _REQUIRED_ITEM_FIELDS.issubset(item)
        for item in items
    )


def _raw_indices(value: object) -> tuple[int, ...]:
    items = _response_items(value) or []
    return tuple(
        item["control_index"]
        for item in items
        if isinstance(item, dict)
        and isinstance(item.get("control_index"), int)
        and not isinstance(item.get("control_index"), bool)
    )


def _diagnostics(
    value: object | None,
    fixture: ControlDiscoveryFixtureGroundTruth,
    ground_truth: ControlDiscoveryGroundTruthDocument,
) -> ControlDiscoveryDiagnostics:
    items = _response_items(value) if value is not None else None
    indices = _raw_indices(value) if value is not None else ()
    counts = Counter(indices)
    eligible_indices = {item.control_index for item in fixture.eligible_controls}
    known_purposes = {
        item.purpose
        for candidate in ground_truth.fixtures
        for item in candidate.eligible_controls
    }
    purposes = tuple(
        item["purpose"]
        for item in (items or [])
        if isinstance(item, dict) and isinstance(item.get("purpose"), str)
    )
    return ControlDiscoveryDiagnostics(
        observed_target_count=len(items) if items is not None else None,
        duplicate_control_indices=tuple(
            sorted(index for index, count in counts.items() if count > 1)
        ),
        out_of_range_control_indices=tuple(
            sorted(index for index in set(indices) if index not in eligible_indices)
        ),
        unknown_purposes=tuple(sorted(set(purposes) - known_purposes)),
    )


def _normalize_items(
    value: object,
    ground_truth: ControlDiscoveryGroundTruthDocument,
) -> tuple[ControlDiscoveryNormalizedItem, ...]:
    items = _response_items(value)
    if items is None:
        raise EvaluationError("schema-valid response is missing target_controls")
    normalized: list[ControlDiscoveryNormalizedItem] = []
    for position, value_item in enumerate(items, start=1):
        item = cast("dict[str, object]", value_item)
        label = cast("str | None", item["label"])
        normalized.append(
            ControlDiscoveryNormalizedItem(
                response_position=position,
                control_index=cast("int", item["control_index"]),
                label=(
                    normalize_label(label, ground_truth.normalization)
                    if label is not None
                    else None
                ),
                purpose=cast("str | None", item["purpose"]),
                confidence=cast("float", item["confidence"]),
            )
        )
    return tuple(normalized)


def _invalid_metrics(
    task: ControlDiscoveryTaskGroundTruth,
) -> ControlDiscoveryMetrics:
    target_count = len(task.target_control_indices)
    return ControlDiscoveryMetrics(
        true_positive_count=None,
        false_positive_count=None,
        false_negative_count=None,
        predicted_target_count=None,
        ground_truth_target_count=target_count,
        precision=None,
        recall=None,
        f1=0.0,
        exact_match=False,
        zero_target_correct_empty=None,
        valid_response=False,
        complete_response=False,
        label_correct_count=None,
        label_accuracy_denominator=None,
        label_accuracy=None,
        purpose_correct_count=None,
        purpose_accuracy_denominator=None,
        purpose_accuracy=None,
        component_metrics_missing_reason="response was not schema-valid",
    )


def _score_items(
    items: tuple[ControlDiscoveryNormalizedItem, ...],
    fixture: ControlDiscoveryFixtureGroundTruth,
    task: ControlDiscoveryTaskGroundTruth,
    ground_truth: ControlDiscoveryGroundTruthDocument,
) -> tuple[tuple[ControlDiscoveryItemJudgment, ...], ControlDiscoveryMetrics]:
    predicted_indices = {item.control_index for item in items}
    target_indices = set(task.target_control_indices)
    true_positive_indices = predicted_indices & target_indices
    true_positive_count = len(true_positive_indices)
    false_positive_count = len(predicted_indices - target_indices)
    false_negative_count = len(target_indices - predicted_indices)

    if not target_indices:
        recall = 1.0
        precision = 1.0 if not predicted_indices else 0.0
        f1 = 1.0 if not predicted_indices else 0.0
    elif not predicted_indices:
        precision = 0.0
        recall = 0.0
        f1 = 0.0
    else:
        precision = true_positive_count / len(predicted_indices)
        recall = true_positive_count / len(target_indices)
        f1 = (
            0.0
            if precision + recall == 0
            else 2 * precision * recall / (precision + recall)
        )

    expected_by_index = {item.control_index: item for item in fixture.eligible_controls}
    judgments: list[ControlDiscoveryItemJudgment] = []
    label_correct_count = 0
    purpose_correct_count = 0
    for item in items:
        expected = expected_by_index.get(item.control_index)
        is_target = item.control_index in target_indices
        label_matches = False
        purpose_matches = False
        if is_target and expected is not None:
            accepted = {
                normalize_label(label, ground_truth.normalization)
                for label in expected.accepted_labels
            }
            label_matches = item.label in accepted
            purpose_matches = item.purpose == expected.purpose
            label_correct_count += int(label_matches)
            purpose_correct_count += int(purpose_matches)
        judgments.append(
            ControlDiscoveryItemJudgment(
                response_position=item.response_position,
                control_index=item.control_index,
                normalized_label=item.label,
                purpose=item.purpose,
                is_target=is_target,
                label_matches_ground_truth=label_matches,
                purpose_matches_ground_truth=purpose_matches,
            )
        )

    conditional_denominator = true_positive_count
    label_accuracy = (
        label_correct_count / conditional_denominator
        if conditional_denominator
        else None
    )
    purpose_accuracy = (
        purpose_correct_count / conditional_denominator
        if conditional_denominator
        else None
    )
    exact_match = predicted_indices == target_indices
    complete_response = (
        exact_match
        and label_correct_count == len(target_indices)
        and purpose_correct_count == len(target_indices)
    )
    return tuple(judgments), ControlDiscoveryMetrics(
        true_positive_count=true_positive_count,
        false_positive_count=false_positive_count,
        false_negative_count=false_negative_count,
        predicted_target_count=len(predicted_indices),
        ground_truth_target_count=len(target_indices),
        precision=precision,
        recall=recall,
        f1=f1,
        exact_match=exact_match,
        zero_target_correct_empty=(not predicted_indices)
        if not target_indices
        else None,
        valid_response=True,
        complete_response=complete_response,
        label_correct_count=label_correct_count,
        label_accuracy_denominator=conditional_denominator,
        label_accuracy=label_accuracy,
        purpose_correct_count=purpose_correct_count,
        purpose_accuracy_denominator=conditional_denominator,
        purpose_accuracy=purpose_accuracy,
    )


def evaluate_control_discovery_response(
    raw_response: bytes,
    *,
    config: ExperimentConfig,
    ground_truth_content: bytes,
    fixture_id: str,
    task_id: str,
    input_representation: str,
) -> ControlDiscoveryEvaluationArtifacts:
    """Normalize and score one exact response for a fixture-task cell."""

    if not isinstance(raw_response, bytes):
        raise TypeError("raw_response must be bytes")
    if (
        config.scorer.scorer_id != CONTROL_DISCOVERY_SCORER_ID
        or config.scorer.scorer_version != CONTROL_DISCOVERY_SCORER_VERSION
    ):
        raise EvaluationError("configured scorer is not target-control-set-f1 v1")
    if not isinstance(config.design.expected_output_schema, dict):
        raise EvaluationError("expected_output_schema must be a JSON Schema mapping")

    try:
        parsed_ground_truth = validate_ground_truth_matches_config(
            ground_truth_content,
            config,
        )
    except GroundTruthError as error:
        raise EvaluationError("ground truth validation failed") from error
    if not isinstance(parsed_ground_truth, ControlDiscoveryGroundTruthDocument):
        raise EvaluationError(
            "target-control-set-f1 v1 requires ground truth schema 2.0.0"
        )
    ground_truth = parsed_ground_truth
    fixture, task = _select_fixture_and_task(ground_truth, fixture_id, task_id)
    _validate_scorer_schema_contract(config.design.expected_output_schema, ground_truth)

    raw_sha256 = hashlib.sha256(raw_response).hexdigest()
    output_schema_sha256 = _canonical_json_sha256(config.design.expected_output_schema)
    parsed = _parse_json_candidate(raw_response)
    classifications: set[ResponseClassification] = set()
    schema_issues: tuple[SchemaIssue, ...] = ()
    schema_valid = False
    normalized_items: tuple[ControlDiscoveryNormalizedItem, ...] | None = None

    if parsed is None:
        classifications.add(ResponseClassification.SYNTAX_ERROR)
        diagnostics = _diagnostics(None, fixture, ground_truth)
    else:
        if parsed.syntax_error:
            classifications.add(ResponseClassification.SYNTAX_ERROR)
        if parsed.extra_explanation:
            classifications.add(ResponseClassification.EXTRA_EXPLANATION)
        duplicate_keys = _has_duplicate_keys(parsed.candidate_text)
        schema_issues = _validate_schema(
            parsed.value,
            config.design.expected_output_schema,
        )
        if duplicate_keys:
            schema_issues = (
                *schema_issues,
                SchemaIssue(instance_path="", schema_path="", rule="duplicate-key"),
            )
        if _is_partial_response(parsed.value):
            classifications.add(ResponseClassification.PARTIAL_RESPONSE)
        diagnostics = _diagnostics(parsed.value, fixture, ground_truth)
        if diagnostics.duplicate_control_indices:
            classifications.add(ResponseClassification.DUPLICATE_ITEM)
            schema_issues = (
                *schema_issues,
                SchemaIssue(
                    instance_path="/target_controls",
                    schema_path="/properties/target_controls",
                    rule="unique-control-index",
                ),
            )
        if schema_issues:
            classifications.add(ResponseClassification.SCHEMA_VIOLATION)
        schema_valid = parsed.exact_json and not duplicate_keys and not schema_issues
        if schema_valid:
            normalized_items = _normalize_items(parsed.value, ground_truth)

    ordered_classifications = tuple(
        sorted(classifications, key=_CLASSIFICATION_ORDER.__getitem__)
    )
    status = (
        EvaluationStatus.SCORED
        if normalized_items is not None
        else EvaluationStatus.NORMALIZATION_FAILED
    )
    normalized_response = ControlDiscoveryNormalizedResponse(
        schema_version=CURRENT_CONTROL_DISCOVERY_EVALUATION_SCHEMA_VERSION,
        status=status,
        raw_response_sha256=raw_sha256,
        output_schema_sha256=output_schema_sha256,
        schema_valid=schema_valid,
        classifications=ordered_classifications,
        schema_issues=schema_issues,
        target_controls=normalized_items,
    )
    normalized_response_bytes = canonical_model_json(normalized_response)

    if normalized_items is None:
        judgments: tuple[ControlDiscoveryItemJudgment, ...] = ()
        metrics = _invalid_metrics(task)
    else:
        judgments, metrics = _score_items(
            normalized_items,
            fixture,
            task,
            ground_truth,
        )

    score = ControlDiscoveryScoreDocument(
        schema_version=CURRENT_CONTROL_DISCOVERY_EVALUATION_SCHEMA_VERSION,
        status=status,
        target=ControlDiscoveryTargetReference(
            experiment_id=config.experiment_id,
            experiment_version=config.experiment_version,
            fixture_id=fixture.fixture_id,
            fixture_version=fixture.fixture_version,
            task_id=task.task_id,
            input_representation=input_representation,
        ),
        scorer=ControlDiscoveryScorerReference(
            scorer_id=CONTROL_DISCOVERY_SCORER_ID,
            scorer_version=CONTROL_DISCOVERY_SCORER_VERSION,
            source_path=CONTROL_DISCOVERY_SCORER_SOURCE_PATH,
            source_sha256=control_discovery_scorer_source_sha256(),
            normalization_source_path=CONTROL_DISCOVERY_NORMALIZATION_SOURCE_PATH,
            normalization_source_sha256=(
                control_discovery_normalization_source_sha256()
            ),
        ),
        ground_truth=ControlDiscoveryGroundTruthReference(
            ground_truth_version=ground_truth.ground_truth_version,
            source_sha256=ground_truth_sha256(ground_truth_content),
        ),
        raw_response_sha256=raw_sha256,
        normalized_response_sha256=hashlib.sha256(
            normalized_response_bytes
        ).hexdigest(),
        output_schema_sha256=output_schema_sha256,
        schema_valid=schema_valid,
        task_success=metrics.complete_response,
        end_to_end_success=metrics.complete_response,
        classifications=ordered_classifications,
        diagnostics=diagnostics,
        item_judgments=judgments,
        metrics=metrics,
    )
    return ControlDiscoveryEvaluationArtifacts(
        raw_response=raw_response,
        normalized_response=normalized_response,
        score=score,
    )
