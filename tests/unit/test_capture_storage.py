from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID, uuid4

import pytest

from kokochi_ui_agent_readability_lab.capture import (
    CAPTURE_SCHEMA_VERSION,
    CaptureArtifactMetadata,
    CaptureExperimentMismatchError,
    CaptureManifest,
    CapturedInputIntegrityError,
    CapturedInputStore,
    CapturedInputs,
    CapturedInputsAlreadyExistError,
)
from kokochi_ui_agent_readability_lab.records import ResultTier, sha256_digest


RUN_ID = UUID("018f05d2-1ca3-7a4b-9c2d-123456789abc")


def build_captured_inputs(
    *,
    experiment_id: str = "example-experiment",
) -> CapturedInputs:
    contents = {
        "inputs/dom-inner-html.html": b"<label>Full name</label>\n",
        "inputs/accessibility-tree.yml": b'- textbox "Full name"\n',
        "inputs/screenshot.png": b"example-png-bytes",
        "inputs/capture-settings.json": b'{"schema_version":"1.0.0"}\n',
    }
    specifications = (
        (
            "input-dom-inner-html",
            "dom-inner-html",
            "inputs/dom-inner-html.html",
            "text/html; charset=utf-8",
        ),
        (
            "input-accessibility-tree",
            "accessibility-tree",
            "inputs/accessibility-tree.yml",
            "application/yaml",
        ),
        (
            "input-screenshot",
            "fixture-screenshot",
            "inputs/screenshot.png",
            "image/png",
        ),
        (
            "input-capture-settings",
            "capture-settings",
            "inputs/capture-settings.json",
            "application/json",
        ),
    )
    metadata = tuple(
        CaptureArtifactMetadata(
            artifact_id=artifact_id,
            kind=kind,
            relative_path=relative_path,
            media_type=media_type,
            byte_length=len(contents[relative_path]),
            content_hash=sha256_digest(contents[relative_path]),
        )
        for artifact_id, kind, relative_path, media_type in specifications
    )
    manifest = CaptureManifest(
        schema_version=CAPTURE_SCHEMA_VERSION,
        captured_at=datetime(2026, 8, 5, 0, 0, tzinfo=UTC),
        experiment_id=experiment_id,
        fixture_id="explicit-label",
        fixture_version="v1",
        fixture_path=f"/experiments/{experiment_id}/explicit-label",
        artifacts=metadata,
    )
    manifest_content = (manifest.model_dump_json(indent=2) + "\n").encode()
    return CapturedInputs(
        manifest=manifest,
        artifacts={
            **contents,
            "inputs/capture-manifest.json": manifest_content,
        },
    )


def test_save_stages_complete_inputs_in_private_result_tier(tmp_path: Path) -> None:
    captured = build_captured_inputs()
    store = CapturedInputStore(
        tmp_path,
        "example-experiment",
        ResultTier.PILOT,
    )

    staged_path = store.save(RUN_ID, captured)

    assert staged_path == (
        tmp_path
        / "experiments"
        / "example-experiment"
        / "results"
        / "pilot"
        / ".capture-staging"
        / str(RUN_ID)
    )
    assert (staged_path / "inputs" / "dom-inner-html.html").read_bytes() == (
        captured.artifacts["inputs/dom-inner-html.html"]
    )
    assert not (store.result_root / str(RUN_ID)).exists()
    assert store.load(RUN_ID) == captured


def test_save_rejects_existing_inputs_without_overwriting(tmp_path: Path) -> None:
    captured = build_captured_inputs()
    store = CapturedInputStore(
        tmp_path,
        "example-experiment",
        ResultTier.PILOT,
    )
    staged_path = store.save(RUN_ID, captured)
    original_manifest = (staged_path / "inputs" / "capture-manifest.json").read_bytes()

    with pytest.raises(CapturedInputsAlreadyExistError, match="already exist"):
        store.save(RUN_ID, captured)

    assert (
        staged_path / "inputs" / "capture-manifest.json"
    ).read_bytes() == original_manifest


def test_save_rejects_hash_mismatch_before_staging(tmp_path: Path) -> None:
    captured = build_captured_inputs()
    changed_artifacts = dict(captured.artifacts)
    changed_artifacts["inputs/dom-inner-html.html"] = b"changed\n"
    changed = CapturedInputs(
        manifest=captured.manifest,
        artifacts=changed_artifacts,
    )
    store = CapturedInputStore(
        tmp_path,
        "example-experiment",
        ResultTier.PILOT,
    )

    with pytest.raises(CapturedInputIntegrityError, match="byte length"):
        store.save(RUN_ID, changed)

    assert not (store.root / str(RUN_ID)).exists()


def test_load_rejects_tampered_staged_bytes(tmp_path: Path) -> None:
    captured = build_captured_inputs()
    store = CapturedInputStore(
        tmp_path,
        "example-experiment",
        ResultTier.PILOT,
    )
    staged_path = store.save(RUN_ID, captured)
    (staged_path / "inputs" / "dom-inner-html.html").write_bytes(b"tampered\n")

    with pytest.raises(CapturedInputIntegrityError, match="byte length"):
        store.load(RUN_ID)


def test_save_rejects_capture_for_another_experiment(tmp_path: Path) -> None:
    store = CapturedInputStore(
        tmp_path,
        "example-experiment",
        ResultTier.PILOT,
    )

    with pytest.raises(CaptureExperimentMismatchError, match="experiment_id"):
        store.save(RUN_ID, build_captured_inputs(experiment_id="other-experiment"))


def test_save_rejects_a_published_run_id(tmp_path: Path) -> None:
    store = CapturedInputStore(
        tmp_path,
        "example-experiment",
        ResultTier.PILOT,
    )
    published_path = store.result_root / str(RUN_ID)
    published_path.mkdir(parents=True)

    with pytest.raises(CapturedInputsAlreadyExistError, match="already exist"):
        store.save(RUN_ID, build_captured_inputs())


def test_interrupted_save_leaves_no_staged_or_temporary_files(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = CapturedInputStore(
        tmp_path,
        "example-experiment",
        ResultTier.PILOT,
    )

    def interrupt_promotion(_temporary_path: Path, _staged_path: Path) -> None:
        raise OSError("simulated interruption")

    monkeypatch.setattr(store, "_promote", interrupt_promotion)

    with pytest.raises(OSError, match="simulated interruption"):
        store.save(uuid4(), build_captured_inputs())

    assert list(store.root.iterdir()) == []

