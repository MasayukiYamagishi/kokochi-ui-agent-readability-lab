import json
from uuid import uuid4

import pytest

from kokochi_ui_agent_readability_lab.records import (
    ArtifactIntegrityError,
    ArtifactSetError,
    DirtyOfficialRunError,
    ExperimentLayoutError,
    ExperimentMismatchError,
    MissingValueReason,
    ModelConfigurationError,
    ResultTier,
    RunRecord,
    RunAlreadyExistsError,
    SensitiveConfigurationError,
    WriteOnceResultStore,
    artifact_reference,
)
from record_factory import (
    ARTIFACT_CONTENTS,
    EXPERIMENT_SOURCE_CONTENTS,
    build_record,
)


def prepare_store(
    repository_root,
    *,
    result_tier: ResultTier = ResultTier.PILOT,
) -> WriteOnceResultStore:
    experiment_root = repository_root / "experiments" / "label-clarity"
    for relative_path, content in EXPERIMENT_SOURCE_CONTENTS.items():
        source_path = experiment_root / relative_path
        source_path.parent.mkdir(parents=True, exist_ok=True)
        source_path.write_bytes(content)
    return WriteOnceResultStore(
        repository_root,
        "label-clarity",
        result_tier,
    )


def replace_model_configuration(record, content: bytes):
    changed_artifacts = dict(ARTIFACT_CONTENTS)
    changed_artifacts["config/model.json"] = content
    references = tuple(
        artifact_reference(
            artifact_id=reference.artifact_id,
            role=reference.role,
            kind=reference.kind,
            relative_path=reference.relative_path,
            media_type=reference.media_type,
            content=content,
        )
        if reference.artifact_id == "model-configuration"
        else reference
        for reference in record.artifacts
    )
    return record.model_copy(update={"artifacts": references}), changed_artifacts


def test_save_publishes_complete_run_and_loads_manifest(tmp_path) -> None:
    record = build_record()
    store = prepare_store(tmp_path)

    run_directory = store.save(record, ARTIFACT_CONTENTS)

    assert run_directory == (
        tmp_path
        / "experiments"
        / "label-clarity"
        / "results"
        / "pilot"
        / str(record.run_id)
    )
    assert (run_directory / "run.json").is_file()
    assert (
        run_directory / "outputs" / "provider-response.json"
    ).read_bytes() == ARTIFACT_CONTENTS["outputs/provider-response.json"]
    assert store.load(record.run_id) == record


def test_save_rejects_existing_run_without_changing_original(tmp_path) -> None:
    record = build_record()
    store = prepare_store(tmp_path)
    run_directory = store.save(record, ARTIFACT_CONTENTS)
    original_manifest = (run_directory / "run.json").read_bytes()

    with pytest.raises(RunAlreadyExistsError, match="run already exists"):
        store.save(record, ARTIFACT_CONTENTS)

    assert (run_directory / "run.json").read_bytes() == original_manifest


def test_save_rejects_hash_mismatch_before_publishing(tmp_path) -> None:
    record = build_record(run_id=uuid4())
    changed_artifacts = dict(ARTIFACT_CONTENTS)
    changed_artifacts["outputs/score.json"] = b'{"correct":false,"score":0.0}\n'
    store = prepare_store(tmp_path)

    with pytest.raises(ArtifactIntegrityError, match="unexpected byte length"):
        store.save(record, changed_artifacts)

    assert not (store.root / str(record.run_id)).exists()


def test_native_artifact_destination_cannot_escape_temporary_directory(
    tmp_path,
) -> None:
    temporary_path = tmp_path / "temporary-run"
    temporary_path.mkdir()
    outside_path = (tmp_path / "outside.json").resolve()

    with pytest.raises(ArtifactSetError, match="escapes the run directory"):
        WriteOnceResultStore._artifact_destination(
            temporary_path,
            str(outside_path),
        )


def test_interrupted_save_leaves_no_partial_run_or_temporary_files(
    tmp_path,
    monkeypatch,
) -> None:
    record = build_record(run_id=uuid4())
    store = prepare_store(tmp_path)

    def interrupt_promotion(_temporary_path, _final_path) -> None:
        raise OSError("simulated interruption")

    monkeypatch.setattr(store, "_promote", interrupt_promotion)

    with pytest.raises(OSError, match="simulated interruption"):
        store.save(record, ARTIFACT_CONTENTS)

    assert list(store.root.iterdir()) == []


def test_official_store_uses_official_result_tier(tmp_path) -> None:
    store = prepare_store(tmp_path, result_tier=ResultTier.OFFICIAL)

    assert store.root == (
        tmp_path / "experiments" / "label-clarity" / "results" / "official"
    )


def test_official_store_rejects_dirty_git_state(tmp_path) -> None:
    record = build_record()
    dirty_git = record.git.model_copy(update={"is_dirty": True})
    dirty_record = record.model_copy(update={"git": dirty_git})
    store = prepare_store(tmp_path, result_tier=ResultTier.OFFICIAL)

    with pytest.raises(
        DirtyOfficialRunError,
        match="clean Git worktree",
    ):
        store.save(dirty_record, ARTIFACT_CONTENTS)


def test_save_rejects_record_for_another_experiment(tmp_path) -> None:
    record = build_record()
    other_experiment = record.experiment.model_copy(
        update={"experiment_id": "other-experiment"}
    )
    mismatched_record = record.model_copy(update={"experiment": other_experiment})
    store = prepare_store(tmp_path)

    with pytest.raises(ExperimentMismatchError, match="does not match"):
        store.save(mismatched_record, ARTIFACT_CONTENTS)


def test_save_rejects_config_version_that_differs_from_manifest(tmp_path) -> None:
    record = build_record()
    changed_experiment = record.experiment.model_copy(update={"version": "v2"})
    changed_record = record.model_copy(update={"experiment": changed_experiment})
    store = prepare_store(tmp_path)

    with pytest.raises(ExperimentLayoutError, match="experiment_version"):
        store.save(changed_record, ARTIFACT_CONTENTS)


def test_save_serializes_explicit_null_with_missing_reason(tmp_path) -> None:
    record_data = build_record().model_dump(mode="python")
    record_data["model"]["model_digest"] = None
    record_data["model"]["model_digest_missing_reason"] = (
        MissingValueReason.PROVIDER_NOT_REPORTED
    )
    record = RunRecord.model_validate(record_data)
    store = prepare_store(tmp_path)

    run_directory = store.save(record, ARTIFACT_CONTENTS)
    manifest = json.loads((run_directory / "run.json").read_text(encoding="utf-8"))

    assert "model_digest" in manifest["model"]
    assert manifest["model"]["model_digest"] is None
    assert manifest["model"]["model_digest_missing_reason"] == "provider-not-reported"
    assert store.load(record.run_id) == record


def test_save_requires_repository_experiment_sources(tmp_path) -> None:
    record = build_record()
    store = WriteOnceResultStore(
        tmp_path,
        "label-clarity",
        ResultTier.PILOT,
    )

    with pytest.raises(ExperimentLayoutError, match="config.yaml"):
        store.save(record, ARTIFACT_CONTENTS)


def test_save_rejects_source_snapshot_that_differs_from_repository(tmp_path) -> None:
    record = build_record()
    store = prepare_store(tmp_path)
    (store.experiment_root / "config.yaml").write_text(
        "experiment_id: different\n",
        encoding="utf-8",
    )

    with pytest.raises(ExperimentLayoutError, match="differs from its run snapshot"):
        store.save(record, ARTIFACT_CONTENTS)


@pytest.mark.parametrize(
    "secret_field",
    [
        "api_key",
        "openai_api_key",
        "anthropicApiKey",
        "auth_token",
        "private_key",
        "credentials",
    ],
)
def test_save_rejects_secret_like_model_configuration_fields(
    tmp_path,
    secret_field: str,
) -> None:
    record = build_record(run_id=uuid4())
    secret_configuration = json.dumps(
        {secret_field: "must-not-be-persisted"},
        separators=(",", ":"),
    ).encode()
    changed_record, changed_artifacts = replace_model_configuration(
        record,
        secret_configuration,
    )
    store = prepare_store(tmp_path)

    with pytest.raises(SensitiveConfigurationError, match=secret_field):
        store.save(changed_record, changed_artifacts)


def test_save_rejects_manifest_parameters_that_differ_from_configuration(
    tmp_path,
) -> None:
    record = build_record(run_id=uuid4())
    changed_parameters = tuple(
        parameter.model_copy(update={"value": 0.9})
        if parameter.name == "temperature"
        else parameter
        for parameter in record.model.generation_parameters
    )
    changed_model = record.model.model_copy(
        update={"generation_parameters": changed_parameters}
    )
    changed_record = record.model_copy(update={"model": changed_model})
    store = prepare_store(tmp_path)

    with pytest.raises(ModelConfigurationError, match="differs"):
        store.save(changed_record, ARTIFACT_CONTENTS)


def test_save_rejects_incomplete_ollama_generation_parameters(tmp_path) -> None:
    record = build_record(run_id=uuid4())
    incomplete_parameters = tuple(
        parameter
        for parameter in record.model.generation_parameters
        if parameter.name != "seed"
    )
    changed_model = record.model.model_copy(
        update={"generation_parameters": incomplete_parameters}
    )
    changed_record = record.model_copy(update={"model": changed_model})
    store = prepare_store(tmp_path)

    with pytest.raises(ModelConfigurationError, match="incomplete"):
        store.save(changed_record, ARTIFACT_CONTENTS)


def test_save_rejects_seed_metadata_that_differs_from_generation_parameters(
    tmp_path,
) -> None:
    record = build_record(run_id=uuid4())
    changed_seed = record.execution.seed.model_copy(update={"requested_value": 7})
    changed_execution = record.execution.model_copy(update={"seed": changed_seed})
    changed_record = record.model_copy(update={"execution": changed_execution})
    store = prepare_store(tmp_path)

    with pytest.raises(ModelConfigurationError, match="seed metadata"):
        store.save(changed_record, ARTIFACT_CONTENTS)
