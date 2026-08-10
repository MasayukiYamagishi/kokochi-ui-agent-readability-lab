import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from kokochi_ui_agent_readability_lab.records import (
    CURRENT_SCHEMA_VERSION,
    FailureStage,
    MissingValueReason,
    RunRecord,
    RunRecordV1,
    RunRecordV1_1,
    RunRecordV2,
    SeedSupportStatus,
    UnsupportedSchemaVersionError,
    parse_run_record_json,
)
from record_factory import build_record


def test_run_record_accepts_complete_versioned_record() -> None:
    record = build_record()

    assert record.schema_version == CURRENT_SCHEMA_VERSION
    assert record.execution.repetition_count == 3
    assert record.ground_truth.version == "v1"
    assert len(record.artifacts) == 9


def test_current_run_record_can_identify_a_task_cell() -> None:
    record = build_record().model_copy(update={"task_id": "emergency-contact"})

    parsed = RunRecord.model_validate_json(record.model_dump_json())

    assert parsed.task_id == "emergency-contact"


def test_run_record_rejects_unknown_fields() -> None:
    record_data = build_record().model_dump(mode="python")
    record_data["undeclared"] = "value"

    with pytest.raises(ValidationError, match="Extra inputs are not permitted"):
        RunRecord.model_validate(record_data)


def test_run_record_requires_explicit_schema_version() -> None:
    record_data = build_record().model_dump(mode="python")
    del record_data["schema_version"]

    with pytest.raises(ValidationError, match="Field required"):
        RunRecord.model_validate(record_data)


def test_run_record_rejects_missing_referenced_artifact() -> None:
    record_data = build_record().model_dump(mode="python")
    record_data["artifacts"] = tuple(
        artifact
        for artifact in record_data["artifacts"]
        if artifact["artifact_id"] != "score-result"
    )

    with pytest.raises(ValidationError, match="referenced artifacts are missing"):
        RunRecord.model_validate(record_data)


def test_repository_identifiers_must_be_lowercase_kebab_case() -> None:
    record_data = build_record().model_dump(mode="python")
    record_data["experiment"]["experiment_id"] = "label_clarity"

    with pytest.raises(ValidationError, match="String should match pattern"):
        RunRecord.model_validate(record_data)


def test_asset_versions_must_be_lowercase_kebab_case() -> None:
    record_data = build_record().model_dump(mode="python")
    record_data["prompt"]["version"] = "1.0.0"

    with pytest.raises(ValidationError, match="String should match pattern"):
        RunRecord.model_validate(record_data)


def test_fixture_source_path_must_match_repository_layout() -> None:
    record_data = build_record().model_dump(mode="python")
    record_data["fixture"]["source_path"] = "fixtures/another-form.tsx"

    with pytest.raises(
        ValidationError,
        match="fixture source_path must be",
    ):
        RunRecord.model_validate(record_data)


def test_completed_run_requires_all_result_sections() -> None:
    record_data = build_record().model_dump(mode="python")
    record_data["score"] = None

    with pytest.raises(
        ValidationError,
        match="completed executions require provider, normalized, and score outputs",
    ):
        RunRecord.model_validate(record_data)


def test_artifact_paths_cannot_escape_run_directory() -> None:
    record_data = build_record().model_dump(mode="python")
    record_data["artifacts"][0]["relative_path"] = "../outside.json"

    with pytest.raises(
        ValidationError,
        match="relative_path must stay inside the run directory",
    ):
        RunRecord.model_validate(record_data)


@pytest.mark.parametrize("relative_path", ["C:/outside.json", "C:outside.json"])
def test_artifact_paths_cannot_include_windows_drives(
    relative_path: str,
) -> None:
    record_data = build_record().model_dump(mode="python")
    record_data["artifacts"][0]["relative_path"] = relative_path

    with pytest.raises(
        ValidationError,
        match="relative_path must not include a Windows drive",
    ):
        RunRecord.model_validate(record_data)


def test_artifact_path_cannot_replace_run_manifest() -> None:
    record_data = build_record().model_dump(mode="python")
    record_data["artifacts"][0]["relative_path"] = "run.json"

    with pytest.raises(ValidationError, match="reserved run.json"):
        RunRecord.model_validate(record_data)


def test_artifact_paths_cannot_have_file_directory_collision() -> None:
    record_data = build_record().model_dump(mode="python")
    record_data["artifacts"][0]["relative_path"] = "config"

    with pytest.raises(
        ValidationError,
        match="both a file and a parent directory",
    ):
        RunRecord.model_validate(record_data)


@pytest.mark.parametrize(
    ("section", "field"),
    [
        ("provider_response", "latency_ms"),
        ("input_collection", "browser_context.device_scale_factor"),
    ],
)
def test_run_record_rejects_non_finite_floats(
    section: str,
    field: str,
) -> None:
    record_data = build_record().model_dump(mode="python")
    target = record_data[section]
    field_parts = field.split(".")
    for field_part in field_parts[:-1]:
        target = target[field_part]
    target[field_parts[-1]] = float("inf")

    with pytest.raises(ValidationError, match="finite number"):
        RunRecord.model_validate(record_data)


def test_missing_model_metadata_requires_structured_reasons() -> None:
    record_data = build_record().model_dump(mode="python")
    record_data["model"]["model_digest"] = None

    with pytest.raises(
        ValidationError,
        match="model_digest_missing_reason is required",
    ):
        RunRecord.model_validate(record_data)

    record_data["model"]["model_digest_missing_reason"] = (
        MissingValueReason.PROVIDER_NOT_REPORTED
    )
    parsed = RunRecord.model_validate(record_data)

    assert parsed.model.model_digest is None
    assert (
        parsed.model.model_digest_missing_reason
        is MissingValueReason.PROVIDER_NOT_REPORTED
    )


def test_missing_token_usage_requires_a_reason_and_keeps_estimates_separate() -> None:
    record_data = build_record().model_dump(mode="python")
    record_data["provider_response"]["token_usage"] = None

    with pytest.raises(
        ValidationError,
        match="token_usage_missing_reason is required",
    ):
        RunRecord.model_validate(record_data)

    record_data["provider_response"]["token_usage_missing_reason"] = (
        MissingValueReason.PROVIDER_NOT_REPORTED
    )
    record_data["provider_response"]["estimated_token_usage"] = {
        "input_tokens": 40,
        "output_tokens": 10,
        "total_tokens": 50,
        "estimation_method": "provider-tokenizer-v1",
    }
    parsed = RunRecord.model_validate(record_data)

    assert parsed.provider_response is not None
    assert parsed.provider_response.token_usage is None
    assert parsed.provider_response.estimated_token_usage is not None


def test_seed_records_provider_request_report_and_support() -> None:
    seed = build_record().execution.seed

    assert seed.provider_parameter == "seed"
    assert seed.requested_value == 42
    assert seed.reported_value == 42
    assert seed.support_status is SeedSupportStatus.SUPPORTED


def test_generation_parameter_names_must_be_unique() -> None:
    record_data = build_record().model_dump(mode="python")
    record_data["model"]["generation_parameters"] = (
        *record_data["model"]["generation_parameters"],
        {"name": "temperature", "value": 0.2},
    )

    with pytest.raises(ValidationError, match="generation parameter names"):
        RunRecord.model_validate(record_data)


def test_ollama_generation_parameters_must_be_complete() -> None:
    record_data = build_record().model_dump(mode="python")
    record_data["model"]["generation_parameters"] = tuple(
        parameter
        for parameter in record_data["model"]["generation_parameters"]
        if parameter["name"] != "seed"
    )

    with pytest.raises(ValidationError, match="incomplete"):
        RunRecord.model_validate(record_data)


def test_ollama_seed_metadata_must_match_generation_parameters() -> None:
    record_data = build_record().model_dump(mode="python")
    record_data["execution"]["seed"]["requested_value"] = 7

    with pytest.raises(ValidationError, match="seed metadata"):
        RunRecord.model_validate(record_data)


def test_current_environment_requires_collector_and_lgtm_images() -> None:
    record_data = build_record().model_dump(mode="python")
    record_data["environment"]["container_images"] = ()

    with pytest.raises(ValidationError, match="at least 2 items"):
        RunRecord.model_validate(record_data)

    record_data = build_record().model_dump(mode="python")
    record_data["environment"]["container_images"][1]["service"] = "other-service"

    with pytest.raises(ValidationError, match="otel-collector"):
        RunRecord.model_validate(record_data)


def test_failed_run_requires_structured_failure_metadata() -> None:
    record_data = build_record().model_dump(mode="python")
    record_data["provider_response"] = None
    record_data["normalized_response"] = None
    record_data["score"] = None
    record_data["execution"]["status"] = record_data["execution"]["status"].FAILED
    record_data["execution"]["error_artifact_id"] = "score-result"

    with pytest.raises(
        ValidationError,
        match="failed executions require failure_stage",
    ):
        RunRecord.model_validate(record_data)

    record_data["execution"]["failure_stage"] = FailureStage.PROVIDER
    record_data["execution"]["error_code"] = "provider-timeout"
    record_data["execution"]["retryable"] = True
    parsed = RunRecord.model_validate(record_data)

    assert parsed.execution.failure_stage is FailureStage.PROVIDER
    assert parsed.execution.error_code == "provider-timeout"


def test_parser_rejects_unregistered_schema_version() -> None:
    record_data = json.loads(build_record().model_dump_json())
    record_data["schema_version"] = "3.0.0"

    with pytest.raises(
        UnsupportedSchemaVersionError,
        match="unsupported schema_version",
    ):
        parse_run_record_json(json.dumps(record_data))


def test_frozen_v1_record_remains_readable() -> None:
    fixture_path = (
        Path(__file__).resolve().parents[1] / "fixtures" / "run-record-v1.json"
    )

    parsed = parse_run_record_json(fixture_path.read_bytes())

    assert isinstance(parsed, RunRecordV1)
    assert parsed.schema_version == "1.0.0"
    assert not hasattr(parsed.environment, "container_images")


def test_frozen_v1_1_record_remains_readable() -> None:
    fixture_path = (
        Path(__file__).resolve().parents[1] / "fixtures" / "run-record-v1.1.json"
    )

    parsed = parse_run_record_json(fixture_path.read_bytes())

    assert isinstance(parsed, RunRecordV1_1)
    assert parsed.schema_version == "1.1.0"
    assert not hasattr(parsed.environment, "hardware")
    assert not hasattr(parsed.model, "generation_parameters")


def test_frozen_v2_record_remains_readable() -> None:
    fixture_path = (
        Path(__file__).resolve().parents[1] / "fixtures" / "run-record-v2.json"
    )

    parsed = parse_run_record_json(fixture_path.read_bytes())

    assert isinstance(parsed, RunRecordV2)
    assert parsed.schema_version == "2.0.0"
    assert not hasattr(parsed, "task_id")


def test_frozen_v2_1_record_matches_current_model() -> None:
    fixture_path = (
        Path(__file__).resolve().parents[1] / "fixtures" / "run-record-v2.1.json"
    )

    parsed = parse_run_record_json(fixture_path.read_bytes())

    assert isinstance(parsed, RunRecord)
    assert parsed == build_record()


def test_v1_reader_rejects_v1_1_environment_fields() -> None:
    fixture_path = (
        Path(__file__).resolve().parents[1] / "fixtures" / "run-record-v1.json"
    )
    record_data = json.loads(fixture_path.read_bytes())
    record_data["environment"]["container_images"] = []

    with pytest.raises(ValidationError, match="Extra inputs are not permitted"):
        parse_run_record_json(json.dumps(record_data))
