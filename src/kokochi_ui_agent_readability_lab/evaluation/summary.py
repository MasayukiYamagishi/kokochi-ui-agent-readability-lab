"""Deterministic aggregation for item-association score documents."""

from __future__ import annotations

from collections.abc import Iterable
import hashlib
from pathlib import Path
from typing import Annotated, Literal

from pydantic import Field, FiniteFloat, NonNegativeInt, PositiveInt

from kokochi_ui_agent_readability_lab.evaluation.item_association import (
    EvaluationError,
    EvaluationStatus,
    EvaluationTargetReference,
    GroundTruthScoreReference,
    ScoreDocument,
    ScorerReference,
)
from kokochi_ui_agent_readability_lab.evaluation.control_discovery import (
    ControlDiscoveryGroundTruthReference,
    ControlDiscoveryScoreDocument,
    ControlDiscoveryScorerReference,
)
from kokochi_ui_agent_readability_lab.records.schema import (
    Identifier,
    RepositoryIdentifier,
    Sha256Hex,
    StrictModel,
)
from kokochi_ui_agent_readability_lab.records import ExperimentConfig


CURRENT_EVALUATION_SUMMARY_SCHEMA_VERSION: Literal["1.0.0"] = "1.0.0"
AGGREGATOR_ID: Literal["item-association-summary"] = "item-association-summary"
AGGREGATOR_VERSION: Literal["v1"] = "v1"
AGGREGATOR_SOURCE_PATH: Literal[
    "src/kokochi_ui_agent_readability_lab/evaluation/summary.py"
] = "src/kokochi_ui_agent_readability_lab/evaluation/summary.py"
CONTROL_DISCOVERY_SUMMARY_SCHEMA_VERSION: Literal["1.0.0"] = "1.0.0"
CONTROL_DISCOVERY_AGGREGATOR_ID: Literal["target-control-task-summary"] = (
    "target-control-task-summary"
)
CONTROL_DISCOVERY_AGGREGATOR_VERSION: Literal["v1"] = "v1"


class AggregatorReference(StrictModel):
    """Exact aggregation algorithm identity and implementation digest."""

    aggregator_id: Literal["item-association-summary"]
    aggregator_version: Literal["v1"]
    source_path: Literal["src/kokochi_ui_agent_readability_lab/evaluation/summary.py"]
    source_sha256: Sha256Hex


class EvaluationSummary(StrictModel):
    """Aggregate rates over one comparable set of evaluation attempts."""

    schema_version: Literal["1.0.0"]
    target: EvaluationTargetReference
    scorer: ScorerReference
    aggregator: AggregatorReference
    ground_truth: GroundTruthScoreReference
    output_schema_sha256: Sha256Hex
    attempt_count: PositiveInt
    scored_count: NonNegativeInt
    normalization_failure_count: NonNegativeInt
    schema_valid_count: NonNegativeInt
    schema_validity_rate: Annotated[FiniteFloat, Field(ge=0, le=1)]
    task_success_count: NonNegativeInt
    task_success_rate: Annotated[FiniteFloat, Field(ge=0, le=1)] | None
    end_to_end_success_count: NonNegativeInt
    end_to_end_success_rate: Annotated[FiniteFloat, Field(ge=0, le=1)]
    mean_f1_on_scored: Annotated[FiniteFloat, Field(ge=0, le=1)] | None
    hallucinated_mapping_count: NonNegativeInt


def aggregator_source_sha256() -> Sha256Hex:
    """Hash the exact aggregation source bytes used by this process."""

    return hashlib.sha256(Path(__file__).read_bytes()).hexdigest()


def aggregate_score_documents(
    scores: Iterable[ScoreDocument],
) -> EvaluationSummary:
    """Aggregate comparable attempts without hiding normalization failures."""

    documents = tuple(scores)
    if not documents:
        raise EvaluationError("at least one score document is required")

    first = documents[0]
    for score in documents[1:]:
        if score.target != first.target:
            raise EvaluationError("score documents target different fixtures")
        if score.scorer != first.scorer:
            raise EvaluationError("score documents use different scorers")
        if score.ground_truth != first.ground_truth:
            raise EvaluationError("score documents use different ground truth")
        if score.output_schema_sha256 != first.output_schema_sha256:
            raise EvaluationError("score documents use different output schemas")

    scored_metrics = []
    for score in documents:
        if score.status is EvaluationStatus.SCORED:
            if not score.schema_valid or score.metrics is None:
                raise EvaluationError("scored document is missing valid task metrics")
            if score.task_success != score.metrics.task_success:
                raise EvaluationError(
                    "score document task_success differs from task metrics"
                )
            if score.end_to_end_success != score.task_success:
                raise EvaluationError(
                    "scored document end_to_end_success differs from task success"
                )
            scored_metrics.append(score.metrics)
        else:
            if score.schema_valid or score.metrics is not None:
                raise EvaluationError(
                    "normalization failure must not contain valid task metrics"
                )
            if score.task_success or score.end_to_end_success:
                raise EvaluationError(
                    "normalization failure cannot be a successful attempt"
                )

    attempt_count = len(documents)
    scored_count = len(scored_metrics)
    schema_valid_count = sum(score.schema_valid for score in documents)
    task_success_count = sum(score.task_success for score in documents)
    end_to_end_success_count = sum(score.end_to_end_success for score in documents)
    hallucination_count = sum(
        score.diagnostics.hallucinated_mapping_count or 0 for score in documents
    )
    mean_f1 = (
        sum(metrics.f1 for metrics in scored_metrics) / scored_count
        if scored_count
        else None
    )

    return EvaluationSummary(
        schema_version=CURRENT_EVALUATION_SUMMARY_SCHEMA_VERSION,
        target=first.target,
        scorer=first.scorer,
        aggregator=AggregatorReference(
            aggregator_id=AGGREGATOR_ID,
            aggregator_version=AGGREGATOR_VERSION,
            source_path=AGGREGATOR_SOURCE_PATH,
            source_sha256=aggregator_source_sha256(),
        ),
        ground_truth=first.ground_truth,
        output_schema_sha256=first.output_schema_sha256,
        attempt_count=attempt_count,
        scored_count=scored_count,
        normalization_failure_count=attempt_count - scored_count,
        schema_valid_count=schema_valid_count,
        schema_validity_rate=schema_valid_count / attempt_count,
        task_success_count=task_success_count,
        task_success_rate=(task_success_count / scored_count if scored_count else None),
        end_to_end_success_count=end_to_end_success_count,
        end_to_end_success_rate=end_to_end_success_count / attempt_count,
        mean_f1_on_scored=mean_f1,
        hallucinated_mapping_count=hallucination_count,
    )


class ControlDiscoveryAggregatorReference(StrictModel):
    """Exact task-aware aggregation algorithm identity."""

    aggregator_id: Literal["target-control-task-summary"]
    aggregator_version: Literal["v1"]
    source_path: Literal["src/kokochi_ui_agent_readability_lab/evaluation/summary.py"]
    source_sha256: Sha256Hex


class ControlDiscoveryCellSummary(StrictModel):
    """Aggregate for one task, fixture, and input-representation cell."""

    task_id: RepositoryIdentifier
    fixture_id: RepositoryIdentifier
    fixture_version: RepositoryIdentifier
    input_representation: Identifier
    attempt_count: PositiveInt
    valid_response_count: NonNegativeInt
    valid_response_rate: Annotated[FiniteFloat, Field(ge=0, le=1)]
    exact_match_count: NonNegativeInt
    exact_match_rate: Annotated[FiniteFloat, Field(ge=0, le=1)]
    complete_response_count: NonNegativeInt
    complete_response_rate: Annotated[FiniteFloat, Field(ge=0, le=1)]
    mean_f1: Annotated[FiniteFloat, Field(ge=0, le=1)]
    ground_truth_target_count: NonNegativeInt


class ControlDiscoveryTaskSummary(StrictModel):
    """Equal-condition aggregate for one task and input representation."""

    task_id: RepositoryIdentifier
    input_representation: Identifier
    fixture_summaries: tuple[ControlDiscoveryCellSummary, ...] = Field(min_length=1)
    mean_f1: Annotated[FiniteFloat, Field(ge=0, le=1)]


class ControlDiscoveryRepresentationSummary(StrictModel):
    """Equal-task macro-F1 for one input representation."""

    input_representation: Identifier
    task_count: PositiveInt
    macro_f1: Annotated[FiniteFloat, Field(ge=0, le=1)]


class ControlDiscoveryEvaluationSummary(StrictModel):
    """Task-aware aggregate that retains every invalid attempt at F1 zero."""

    schema_version: Literal["1.0.0"]
    experiment_id: RepositoryIdentifier
    experiment_version: RepositoryIdentifier
    scorer: ControlDiscoveryScorerReference
    aggregator: ControlDiscoveryAggregatorReference
    ground_truth: ControlDiscoveryGroundTruthReference
    output_schema_sha256: Sha256Hex
    attempt_count: PositiveInt
    cell_summaries: tuple[ControlDiscoveryCellSummary, ...] = Field(min_length=1)
    task_summaries: tuple[ControlDiscoveryTaskSummary, ...] = Field(min_length=1)
    representation_summaries: tuple[ControlDiscoveryRepresentationSummary, ...] = Field(
        min_length=1
    )


def aggregate_control_discovery_scores(
    scores: Iterable[ControlDiscoveryScoreDocument],
    *,
    config: ExperimentConfig,
) -> ControlDiscoveryEvaluationSummary:
    """Aggregate attempts by task, fixture, and input representation."""

    documents = tuple(scores)
    if not documents:
        raise EvaluationError("at least one control-discovery score is required")

    first = documents[0]
    if (
        config.experiment_id != first.target.experiment_id
        or config.experiment_version != first.target.experiment_version
        or config.tasks is None
    ):
        raise EvaluationError("config does not match the task-aware score documents")
    configured_fixtures = {fixture.fixture_id: fixture for fixture in config.fixtures}
    for score in documents:
        if (
            score.target.experiment_id != first.target.experiment_id
            or score.target.experiment_version != first.target.experiment_version
        ):
            raise EvaluationError("score documents target different experiments")
        if score.scorer != first.scorer:
            raise EvaluationError("score documents use different scorers")
        if score.ground_truth != first.ground_truth:
            raise EvaluationError("score documents use different ground truth")
        if score.output_schema_sha256 != first.output_schema_sha256:
            raise EvaluationError("score documents use different output schemas")
        if score.schema_valid != score.metrics.valid_response:
            raise EvaluationError("schema validity differs from response metrics")
        if score.task_success != score.metrics.complete_response:
            raise EvaluationError("task success differs from response metrics")
        if score.end_to_end_success != score.task_success:
            raise EvaluationError("end-to-end success differs from task success")
        expected_status = (
            EvaluationStatus.SCORED
            if score.metrics.valid_response
            else EvaluationStatus.NORMALIZATION_FAILED
        )
        if score.status is not expected_status:
            raise EvaluationError("score status differs from response validity")
        fixture = configured_fixtures.get(score.target.fixture_id)
        if fixture is None or fixture.fixture_version != score.target.fixture_version:
            raise EvaluationError("score fixture does not match config")

    representations = {score.target.input_representation for score in documents}
    expected_cell_keys = {
        (representation, task.task_id, fixture_id)
        for representation in representations
        for task in config.tasks
        for fixture_id in task.fixture_ids
    }
    actual_cell_keys = {
        (
            score.target.input_representation,
            score.target.task_id,
            score.target.fixture_id,
        )
        for score in documents
    }
    if actual_cell_keys != expected_cell_keys:
        raise EvaluationError(
            "score documents do not cover every configured task-fixture cell"
        )

    grouped: dict[
        tuple[str, str, str, str],
        list[ControlDiscoveryScoreDocument],
    ] = {}
    for score in documents:
        key = (
            score.target.input_representation,
            score.target.task_id,
            score.target.fixture_id,
            score.target.fixture_version,
        )
        grouped.setdefault(key, []).append(score)

    cell_summaries: list[ControlDiscoveryCellSummary] = []
    for key in sorted(grouped):
        input_representation, task_id, fixture_id, fixture_version = key
        attempts = grouped[key]
        target_counts = {score.metrics.ground_truth_target_count for score in attempts}
        if len(target_counts) != 1:
            raise EvaluationError("cell ground-truth target counts differ")
        attempt_count = len(attempts)
        valid_count = sum(score.metrics.valid_response for score in attempts)
        exact_count = sum(score.metrics.exact_match for score in attempts)
        complete_count = sum(score.metrics.complete_response for score in attempts)
        cell_summaries.append(
            ControlDiscoveryCellSummary(
                task_id=task_id,
                fixture_id=fixture_id,
                fixture_version=fixture_version,
                input_representation=input_representation,
                attempt_count=attempt_count,
                valid_response_count=valid_count,
                valid_response_rate=valid_count / attempt_count,
                exact_match_count=exact_count,
                exact_match_rate=exact_count / attempt_count,
                complete_response_count=complete_count,
                complete_response_rate=complete_count / attempt_count,
                mean_f1=(sum(score.metrics.f1 for score in attempts) / attempt_count),
                ground_truth_target_count=next(iter(target_counts)),
            )
        )

    task_groups: dict[tuple[str, str], list[ControlDiscoveryCellSummary]] = {}
    for cell in cell_summaries:
        task_key = (cell.input_representation, cell.task_id)
        task_groups.setdefault(task_key, []).append(cell)

    task_summaries = tuple(
        ControlDiscoveryTaskSummary(
            task_id=task_id,
            input_representation=input_representation,
            fixture_summaries=tuple(sorted(cells, key=lambda cell: cell.fixture_id)),
            mean_f1=sum(cell.mean_f1 for cell in cells) / len(cells),
        )
        for (input_representation, task_id), cells in sorted(task_groups.items())
    )

    representation_groups: dict[str, list[ControlDiscoveryTaskSummary]] = {}
    for task in task_summaries:
        representation_groups.setdefault(task.input_representation, []).append(task)
    representation_summaries = tuple(
        ControlDiscoveryRepresentationSummary(
            input_representation=input_representation,
            task_count=len(tasks),
            macro_f1=sum(task.mean_f1 for task in tasks) / len(tasks),
        )
        for input_representation, tasks in sorted(representation_groups.items())
    )

    return ControlDiscoveryEvaluationSummary(
        schema_version=CONTROL_DISCOVERY_SUMMARY_SCHEMA_VERSION,
        experiment_id=first.target.experiment_id,
        experiment_version=first.target.experiment_version,
        scorer=first.scorer,
        aggregator=ControlDiscoveryAggregatorReference(
            aggregator_id=CONTROL_DISCOVERY_AGGREGATOR_ID,
            aggregator_version=CONTROL_DISCOVERY_AGGREGATOR_VERSION,
            source_path=AGGREGATOR_SOURCE_PATH,
            source_sha256=aggregator_source_sha256(),
        ),
        ground_truth=first.ground_truth,
        output_schema_sha256=first.output_schema_sha256,
        attempt_count=len(documents),
        cell_summaries=tuple(cell_summaries),
        task_summaries=task_summaries,
        representation_summaries=representation_summaries,
    )
