"""Versioned deterministic scorer for form item associations."""

from __future__ import annotations

from collections import Counter
from collections.abc import Iterable
from dataclasses import dataclass
from enum import StrEnum
import hashlib
import json
from pathlib import Path
from typing import Annotated, Literal, cast

from jsonschema import exceptions as jsonschema_exceptions  # type: ignore[import-untyped]
from jsonschema import validators  # type: ignore[import-untyped]
from pydantic import Field, FiniteFloat, NonNegativeInt, PositiveInt

from kokochi_ui_agent_readability_lab.ground_truth import (
    FixtureGroundTruth,
    GroundTruthDocument,
    GroundTruthError,
    ground_truth_sha256,
    normalize_label,
    validate_ground_truth_matches_config,
)
from kokochi_ui_agent_readability_lab.records import ExperimentConfig
from kokochi_ui_agent_readability_lab.records.schema import (
    NonEmptyString,
    RepositoryIdentifier,
    Sha256Hex,
    StrictModel,
)


CURRENT_EVALUATION_SCHEMA_VERSION: Literal["1.0.0"] = "1.0.0"
SCORER_ID: Literal["item-association-f1"] = "item-association-f1"
SCORER_VERSION: Literal["v1"] = "v1"
SCORER_SOURCE_PATH: Literal[
    "src/kokochi_ui_agent_readability_lab/evaluation/item_association.py"
] = "src/kokochi_ui_agent_readability_lab/evaluation/item_association.py"
NORMALIZATION_SOURCE_PATH: Literal[
    "src/kokochi_ui_agent_readability_lab/ground_truth/schema.py"
] = "src/kokochi_ui_agent_readability_lab/ground_truth/schema.py"

_REQUIRED_ITEM_FIELDS = frozenset({"control_index", "label", "purpose", "confidence"})


class EvaluationError(ValueError):
    """Raised for scorer setup errors rather than malformed model output."""


class EvaluationStatus(StrEnum):
    """Whether a model response reached deterministic scoring."""

    SCORED = "scored"
    NORMALIZATION_FAILED = "normalization-failed"


class ResponseClassification(StrEnum):
    """Stable, non-exclusive classifications for one model response."""

    SYNTAX_ERROR = "syntax-error"
    SCHEMA_VIOLATION = "schema-violation"
    PARTIAL_RESPONSE = "partial-response"
    EXTRA_EXPLANATION = "extra-explanation"
    DUPLICATE_ITEM = "duplicate-item"


_CLASSIFICATION_ORDER = {
    classification: index for index, classification in enumerate(ResponseClassification)
}


class AssociationOutcome(StrEnum):
    """One-to-one association judgment for a normalized prediction."""

    TRUE_POSITIVE = "true-positive"
    FALSE_POSITIVE = "false-positive"
    UNANSWERED = "unanswered"


class SchemaIssue(StrictModel):
    """Stable JSON Schema evidence without library-specific prose."""

    instance_path: str
    schema_path: str
    rule: NonEmptyString


class NormalizedItem(StrictModel):
    """One schema-valid response item after declared label normalization."""

    response_position: PositiveInt
    control_index: PositiveInt
    label: str | None
    purpose: RepositoryIdentifier | None
    confidence: Annotated[FiniteFloat, Field(ge=0, le=1)]


class NormalizedResponseDocument(StrictModel):
    """Canonical normalized-response artifact linked to the raw bytes."""

    schema_version: Literal["1.0.0"]
    status: EvaluationStatus
    raw_response_sha256: Sha256Hex
    output_schema_sha256: Sha256Hex
    schema_valid: bool
    classifications: tuple[ResponseClassification, ...]
    schema_issues: tuple[SchemaIssue, ...]
    items: tuple[NormalizedItem, ...] | None


class ResponseDiagnostics(StrictModel):
    """Counts that remain useful even when strict normalization fails."""

    observed_item_count: NonNegativeInt | None
    duplicate_control_indices: tuple[int, ...]
    unknown_control_indices: tuple[int, ...]
    unknown_purposes: tuple[str, ...]
    hallucinated_mapping_count: NonNegativeInt | None


class ItemJudgment(StrictModel):
    """Auditable judgment for one normalized prediction."""

    response_position: PositiveInt
    control_index: PositiveInt
    normalized_label: str | None
    purpose: RepositoryIdentifier | None
    association_outcome: AssociationOutcome
    label_matches_ground_truth: bool | None
    purpose_matches_ground_truth: bool
    hallucinated_mapping: bool


class EvaluationMetrics(StrictModel):
    """Preregistered primary and secondary per-run metrics."""

    true_positive_count: NonNegativeInt
    false_positive_count: NonNegativeInt
    false_negative_count: NonNegativeInt
    predicted_mapping_count: NonNegativeInt
    ground_truth_mapping_count: PositiveInt
    precision: Annotated[FiniteFloat, Field(ge=0, le=1)]
    recall: Annotated[FiniteFloat, Field(ge=0, le=1)]
    f1: Annotated[FiniteFloat, Field(ge=0, le=1)]
    complete_match: bool
    task_success: bool
    purpose_correct_count: NonNegativeInt
    purpose_accuracy: Annotated[FiniteFloat, Field(ge=0, le=1)]
    hallucinated_mapping_count: NonNegativeInt


class ScorerReference(StrictModel):
    """Exact scorer identity and implementation digest."""

    scorer_id: Literal["item-association-f1"]
    scorer_version: Literal["v1"]
    source_path: Literal[
        "src/kokochi_ui_agent_readability_lab/evaluation/item_association.py"
    ]
    source_sha256: Sha256Hex
    normalization_source_path: Literal[
        "src/kokochi_ui_agent_readability_lab/ground_truth/schema.py"
    ]
    normalization_source_sha256: Sha256Hex


class GroundTruthScoreReference(StrictModel):
    """Exact ground-truth version and byte digest used for scoring."""

    ground_truth_version: RepositoryIdentifier
    source_sha256: Sha256Hex


class EvaluationTargetReference(StrictModel):
    """Experiment and fixture identity for one score artifact."""

    experiment_id: RepositoryIdentifier
    experiment_version: RepositoryIdentifier
    fixture_id: RepositoryIdentifier
    fixture_version: RepositoryIdentifier


class ScoreDocument(StrictModel):
    """Canonical score artifact for a valid or rejected model response."""

    schema_version: Literal["1.0.0"]
    status: EvaluationStatus
    target: EvaluationTargetReference
    scorer: ScorerReference
    ground_truth: GroundTruthScoreReference
    raw_response_sha256: Sha256Hex
    normalized_response_sha256: Sha256Hex
    output_schema_sha256: Sha256Hex
    schema_valid: bool
    task_success: bool
    end_to_end_success: bool
    classifications: tuple[ResponseClassification, ...]
    diagnostics: ResponseDiagnostics
    item_judgments: tuple[ItemJudgment, ...]
    metrics: EvaluationMetrics | None


@dataclass(frozen=True, slots=True)
class EvaluationArtifacts:
    """Raw bytes plus their linked normalized-response and score artifacts."""

    raw_response: bytes
    normalized_response: NormalizedResponseDocument
    score: ScoreDocument

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


def canonical_model_json(model: StrictModel) -> bytes:
    """Serialize a result model with stable keys, separators, and LF ending."""

    return (
        json.dumps(
            model.model_dump(mode="json"),
            ensure_ascii=False,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        + "\n"
    ).encode("utf-8")


def scorer_source_sha256() -> Sha256Hex:
    """Hash the exact shared scorer source bytes used by this process."""

    return hashlib.sha256(Path(__file__).read_bytes()).hexdigest()


def normalization_source_sha256() -> Sha256Hex:
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
    encoded: list[str] = []
    for part in parts:
        token = str(part).replace("~", "~0").replace("/", "~1")
        encoded.append(token)
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


def _schema_contract_mapping(value: object, path: str) -> dict[str, object]:
    if not isinstance(value, dict):
        raise EvaluationError(
            "configured expected_output_schema is incompatible with "
            f"{SCORER_ID} {SCORER_VERSION}: {path} must be a mapping"
        )
    return cast("dict[str, object]", value)


def _schema_contract_string_set(value: object, path: str) -> set[str]:
    if not isinstance(value, list) or any(not isinstance(item, str) for item in value):
        raise EvaluationError(
            "configured expected_output_schema is incompatible with "
            f"{SCORER_ID} {SCORER_VERSION}: {path} must be a string array"
        )
    return set(cast("list[str]", value))


def _validate_scorer_schema_contract(
    schema: dict[str, object],
    ground_truth: GroundTruthDocument,
) -> None:
    """Reject valid JSON Schemas that cannot safely drive scorer v1."""

    validator_class = validators.validator_for(schema)
    try:
        validator_class.check_schema(schema)
    except jsonschema_exceptions.SchemaError as error:
        raise EvaluationError("configured expected_output_schema is invalid") from error

    def require(condition: bool, message: str) -> None:
        if not condition:
            raise EvaluationError(
                "configured expected_output_schema is incompatible with "
                f"{SCORER_ID} {SCORER_VERSION}: {message}"
            )

    require(schema.get("type") == "object", "root type must be object")
    require(schema.get("additionalProperties") is False, "root must reject extras")
    require(
        _schema_contract_string_set(schema.get("required"), "required") == {"items"},
        "root must require only items",
    )
    root_properties = _schema_contract_mapping(
        schema.get("properties"),
        "properties",
    )
    require(set(root_properties) == {"items"}, "root must define only items")

    array_schema = _schema_contract_mapping(root_properties.get("items"), "items")
    require(array_schema.get("type") == "array", "items type must be array")
    require(
        array_schema.get("minItems") == ground_truth.control_count,
        "items minItems must equal ground-truth control_count",
    )
    require(
        array_schema.get("maxItems") == ground_truth.control_count,
        "items maxItems must equal ground-truth control_count",
    )
    require(array_schema.get("uniqueItems") is True, "items must use uniqueItems")

    item_schema = _schema_contract_mapping(array_schema.get("items"), "items.items")
    required_item_fields = set(_REQUIRED_ITEM_FIELDS)
    require(item_schema.get("type") == "object", "item type must be object")
    require(
        item_schema.get("additionalProperties") is False,
        "items must reject extra properties",
    )
    require(
        _schema_contract_string_set(item_schema.get("required"), "items.required")
        == required_item_fields,
        "items must require the scorer fields",
    )
    item_properties = _schema_contract_mapping(
        item_schema.get("properties"),
        "items.properties",
    )
    require(
        set(item_properties) == required_item_fields,
        "items must define only the scorer fields",
    )

    control_schema = _schema_contract_mapping(
        item_properties.get("control_index"),
        "control_index",
    )
    require(control_schema.get("type") == "integer", "control_index must be integer")
    require(control_schema.get("minimum") == 1, "control_index minimum must be 1")
    require(
        control_schema.get("maximum") == ground_truth.control_count,
        "control_index maximum must equal ground-truth control_count",
    )

    label_schema = _schema_contract_mapping(item_properties.get("label"), "label")
    require(
        _schema_contract_string_set(label_schema.get("type"), "label.type")
        == {"string", "null"},
        "label must allow only string or null",
    )
    require(label_schema.get("minLength") == 1, "label minLength must be 1")

    purpose_schema = _schema_contract_mapping(
        item_properties.get("purpose"),
        "purpose",
    )
    purpose_options = purpose_schema.get("anyOf")
    require(
        isinstance(purpose_options, list) and len(purpose_options) == 2,
        "purpose must have enum and null alternatives",
    )
    options = [
        _schema_contract_mapping(option, "purpose.anyOf")
        for option in cast("list[object]", purpose_options)
    ]
    enum_options = [option for option in options if "enum" in option]
    null_options = [option for option in options if option.get("type") == "null"]
    require(
        len(enum_options) == 1 and len(null_options) == 1,
        "purpose must have exactly one enum and one null alternative",
    )
    configured_purposes = _schema_contract_string_set(
        enum_options[0].get("enum"),
        "purpose enum",
    )
    ground_truth_purposes = {
        item.purpose for fixture in ground_truth.fixtures for item in fixture.items
    }
    require(
        configured_purposes == ground_truth_purposes,
        "purpose enum must match ground-truth purposes",
    )

    confidence_schema = _schema_contract_mapping(
        item_properties.get("confidence"),
        "confidence",
    )
    require(confidence_schema.get("type") == "number", "confidence must be number")
    require(confidence_schema.get("minimum") == 0, "confidence minimum must be 0")
    require(confidence_schema.get("maximum") == 1, "confidence maximum must be 1")


def _response_items(value: object) -> list[object] | None:
    if not isinstance(value, dict):
        return None
    items = value.get("items")
    return items if isinstance(items, list) else None


def _is_partial_response(value: object, schema: dict[str, object]) -> bool:
    if not isinstance(value, dict):
        return False
    if "items" not in value:
        return True
    items = _response_items(value)
    if items is None:
        return False

    minimum = 0
    properties = schema.get("properties")
    if isinstance(properties, dict):
        item_schema = properties.get("items")
        if isinstance(item_schema, dict):
            configured_minimum = item_schema.get("minItems")
            if isinstance(configured_minimum, int) and not isinstance(
                configured_minimum, bool
            ):
                minimum = configured_minimum
    if len(items) < minimum:
        return True
    return any(
        isinstance(item, dict) and not _REQUIRED_ITEM_FIELDS.issubset(item)
        for item in items
    )


def _item_diagnostics(
    value: object,
    fixture: FixtureGroundTruth,
    ground_truth: GroundTruthDocument,
) -> ResponseDiagnostics:
    items = _response_items(value)
    if items is None:
        return ResponseDiagnostics(
            observed_item_count=None,
            duplicate_control_indices=(),
            unknown_control_indices=(),
            unknown_purposes=(),
            hallucinated_mapping_count=None,
        )

    indices = [
        item.get("control_index")
        for item in items
        if isinstance(item, dict)
        and isinstance(item.get("control_index"), int)
        and not isinstance(item.get("control_index"), bool)
    ]
    index_counts = Counter(cast("list[int]", indices))
    duplicate_indices = tuple(
        sorted(index for index, count in index_counts.items() if count > 1)
    )
    expected_by_index = {item.control_index: item for item in fixture.items}
    unknown_indices = tuple(sorted(set(index_counts).difference(expected_by_index)))
    known_purposes = {item.purpose for item in fixture.items}
    observed_purposes = {
        purpose
        for item in items
        if isinstance(item, dict) and isinstance((purpose := item.get("purpose")), str)
    }
    unknown_purposes = tuple(sorted(observed_purposes.difference(known_purposes)))

    matched_indices: set[int] = set()
    hallucinations = 0
    for item in items:
        if not isinstance(item, dict):
            continue
        control_index = item.get("control_index")
        label = item.get("label")
        if (
            not isinstance(control_index, int)
            or isinstance(control_index, bool)
            or not isinstance(label, str)
        ):
            continue
        expected = expected_by_index.get(control_index)
        normalized_label = normalize_label(label, ground_truth.normalization)
        accepted = (
            {
                normalize_label(candidate, ground_truth.normalization)
                for candidate in expected.accepted_labels
            }
            if expected is not None
            else set()
        )
        if (
            expected is not None
            and normalized_label in accepted
            and control_index not in matched_indices
        ):
            matched_indices.add(control_index)
        else:
            hallucinations += 1

    return ResponseDiagnostics(
        observed_item_count=len(items),
        duplicate_control_indices=duplicate_indices,
        unknown_control_indices=unknown_indices,
        unknown_purposes=unknown_purposes,
        hallucinated_mapping_count=hallucinations,
    )


def _normalize_items(
    value: object,
    ground_truth: GroundTruthDocument,
) -> tuple[NormalizedItem, ...]:
    try:
        response = cast("dict[str, object]", value)
        items = cast("list[dict[str, object]]", response["items"])
        return tuple(
            NormalizedItem(
                response_position=position,
                control_index=int(cast("int | float", item["control_index"])),
                label=(
                    normalize_label(label, ground_truth.normalization)
                    if isinstance((label := item["label"]), str)
                    else None
                ),
                purpose=cast("str | None", item["purpose"]),
                confidence=float(cast("int | float", item["confidence"])),
            )
            for position, item in enumerate(items, start=1)
        )
    except (KeyError, TypeError, ValueError) as error:
        raise EvaluationError(
            "schema-valid response could not be normalized by the configured scorer"
        ) from error


def _score_items(
    items: tuple[NormalizedItem, ...],
    fixture: FixtureGroundTruth,
    ground_truth: GroundTruthDocument,
) -> tuple[tuple[ItemJudgment, ...], EvaluationMetrics]:
    expected_by_index = {item.control_index: item for item in fixture.items}
    accepted_by_index = {
        item.control_index: {
            normalize_label(label, ground_truth.normalization)
            for label in item.accepted_labels
        }
        for item in fixture.items
    }
    matched_associations: set[int] = set()
    matched_purposes: set[int] = set()
    judgments: list[ItemJudgment] = []
    predicted_count = 0
    false_positive_count = 0

    for item in items:
        expected = expected_by_index.get(item.control_index)
        if expected is None:
            raise EvaluationError(
                "normalized response contains a control_index absent from ground truth"
            )
        purpose_matches = item.purpose == expected.purpose
        if purpose_matches:
            matched_purposes.add(item.control_index)

        if item.label is None:
            outcome = AssociationOutcome.UNANSWERED
            label_matches: bool | None = None
            hallucinated = False
        else:
            predicted_count += 1
            label_matches = item.label in accepted_by_index[item.control_index]
            if label_matches and item.control_index not in matched_associations:
                outcome = AssociationOutcome.TRUE_POSITIVE
                matched_associations.add(item.control_index)
                hallucinated = False
            else:
                outcome = AssociationOutcome.FALSE_POSITIVE
                false_positive_count += 1
                hallucinated = True

        judgments.append(
            ItemJudgment(
                response_position=item.response_position,
                control_index=item.control_index,
                normalized_label=item.label,
                purpose=item.purpose,
                association_outcome=outcome,
                label_matches_ground_truth=label_matches,
                purpose_matches_ground_truth=purpose_matches,
                hallucinated_mapping=hallucinated,
            )
        )

    true_positive_count = len(matched_associations)
    ground_truth_count = len(fixture.items)
    false_negative_count = ground_truth_count - true_positive_count
    precision = true_positive_count / predicted_count if predicted_count else 0.0
    recall = true_positive_count / ground_truth_count
    f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.0
    complete_match = (
        true_positive_count == ground_truth_count and false_positive_count == 0
    )
    purpose_correct_count = len(matched_purposes)
    metrics = EvaluationMetrics(
        true_positive_count=true_positive_count,
        false_positive_count=false_positive_count,
        false_negative_count=false_negative_count,
        predicted_mapping_count=predicted_count,
        ground_truth_mapping_count=ground_truth_count,
        precision=precision,
        recall=recall,
        f1=f1,
        complete_match=complete_match,
        task_success=complete_match,
        purpose_correct_count=purpose_correct_count,
        purpose_accuracy=purpose_correct_count / ground_truth_count,
        hallucinated_mapping_count=false_positive_count,
    )
    return tuple(judgments), metrics


def _select_fixture(
    ground_truth: GroundTruthDocument,
    fixture_id: str,
) -> FixtureGroundTruth:
    for fixture in ground_truth.fixtures:
        if fixture.fixture_id == fixture_id:
            return fixture
    raise EvaluationError(f"fixture {fixture_id!r} is absent from ground truth")


def evaluate_item_association_response(
    raw_response: bytes,
    *,
    config: ExperimentConfig,
    ground_truth_content: bytes,
    fixture_id: str,
) -> EvaluationArtifacts:
    """Normalize and score exact raw model bytes without mutating or repairing them."""

    if not isinstance(raw_response, bytes):
        raise TypeError("raw_response must be bytes")
    if config.scorer.scorer_id != SCORER_ID:
        raise EvaluationError(
            f"unsupported scorer_id {config.scorer.scorer_id!r}; expected {SCORER_ID!r}"
        )
    if config.scorer.scorer_version != SCORER_VERSION:
        raise EvaluationError(
            "unsupported scorer_version "
            f"{config.scorer.scorer_version!r}; expected {SCORER_VERSION!r}"
        )
    if not isinstance(config.design.expected_output_schema, dict):
        raise EvaluationError("expected_output_schema must be a JSON Schema mapping")

    try:
        ground_truth = validate_ground_truth_matches_config(
            ground_truth_content,
            config,
        )
    except GroundTruthError as error:
        raise EvaluationError("ground truth validation failed") from error
    if not isinstance(ground_truth, GroundTruthDocument):
        raise EvaluationError(
            f"{SCORER_ID} {SCORER_VERSION} requires ground truth schema 1.0.0"
        )
    fixture = _select_fixture(ground_truth, fixture_id)
    _validate_scorer_schema_contract(
        config.design.expected_output_schema,
        ground_truth,
    )

    raw_sha256 = hashlib.sha256(raw_response).hexdigest()
    output_schema_sha256 = _canonical_json_sha256(config.design.expected_output_schema)
    parsed = _parse_json_candidate(raw_response)
    classifications: set[ResponseClassification] = set()
    schema_issues: tuple[SchemaIssue, ...] = ()
    schema_valid = False
    normalized_items: tuple[NormalizedItem, ...] | None = None

    if parsed is None:
        classifications.add(ResponseClassification.SYNTAX_ERROR)
        diagnostics = _item_diagnostics(None, fixture, ground_truth)
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
                SchemaIssue(
                    instance_path="",
                    schema_path="",
                    rule="duplicate-key",
                ),
            )
        if schema_issues:
            classifications.add(ResponseClassification.SCHEMA_VIOLATION)
        if _is_partial_response(
            parsed.value,
            config.design.expected_output_schema,
        ):
            classifications.add(ResponseClassification.PARTIAL_RESPONSE)

        diagnostics = _item_diagnostics(parsed.value, fixture, ground_truth)
        if diagnostics.duplicate_control_indices:
            classifications.add(ResponseClassification.DUPLICATE_ITEM)
        schema_valid = parsed.exact_json and not duplicate_keys and not schema_issues
        if schema_valid:
            normalized_items = _normalize_items(parsed.value, ground_truth)

    ordered_classifications = tuple(
        sorted(classifications, key=_CLASSIFICATION_ORDER.__getitem__)
    )
    status = (
        EvaluationStatus.SCORED
        if schema_valid
        else EvaluationStatus.NORMALIZATION_FAILED
    )
    normalized_response = NormalizedResponseDocument(
        schema_version=CURRENT_EVALUATION_SCHEMA_VERSION,
        status=status,
        raw_response_sha256=raw_sha256,
        output_schema_sha256=output_schema_sha256,
        schema_valid=schema_valid,
        classifications=ordered_classifications,
        schema_issues=schema_issues,
        items=normalized_items,
    )
    normalized_response_bytes = canonical_model_json(normalized_response)

    if normalized_items is None:
        judgments: tuple[ItemJudgment, ...] = ()
        metrics = None
    else:
        judgments, metrics = _score_items(
            normalized_items,
            fixture,
            ground_truth,
        )

    score = ScoreDocument(
        schema_version=CURRENT_EVALUATION_SCHEMA_VERSION,
        status=status,
        target=EvaluationTargetReference(
            experiment_id=config.experiment_id,
            experiment_version=config.experiment_version,
            fixture_id=fixture.fixture_id,
            fixture_version=fixture.fixture_version,
        ),
        scorer=ScorerReference(
            scorer_id=SCORER_ID,
            scorer_version=SCORER_VERSION,
            source_path=SCORER_SOURCE_PATH,
            source_sha256=scorer_source_sha256(),
            normalization_source_path=NORMALIZATION_SOURCE_PATH,
            normalization_source_sha256=normalization_source_sha256(),
        ),
        ground_truth=GroundTruthScoreReference(
            ground_truth_version=ground_truth.ground_truth_version,
            source_sha256=ground_truth_sha256(ground_truth_content),
        ),
        raw_response_sha256=raw_sha256,
        normalized_response_sha256=hashlib.sha256(
            normalized_response_bytes
        ).hexdigest(),
        output_schema_sha256=output_schema_sha256,
        schema_valid=schema_valid,
        task_success=metrics.task_success if metrics is not None else False,
        end_to_end_success=(metrics.task_success if metrics is not None else False),
        classifications=ordered_classifications,
        diagnostics=diagnostics,
        item_judgments=judgments,
        metrics=metrics,
    )
    return EvaluationArtifacts(
        raw_response=raw_response,
        normalized_response=normalized_response,
        score=score,
    )
