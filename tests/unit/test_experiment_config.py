from pathlib import Path

import pytest

from kokochi_ui_agent_readability_lab.records import (
    ExperimentConfigError,
    parse_experiment_config,
    validate_config_matches_record,
)
from record_factory import EXPERIMENT_CONFIG, build_record


REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
PREREGISTERED_EXPERIMENT_CONFIGS = tuple(
    sorted((REPOSITORY_ROOT / "experiments").glob("*/config.yaml"))
)


def test_parse_experiment_config_accepts_canonical_config() -> None:
    config = parse_experiment_config(EXPERIMENT_CONFIG)

    assert config.experiment_id == "label-clarity"
    assert config.experiment_version == "v1"
    assert config.fixtures[0].fixture_id == "semantic-form"
    assert config.execution.repetitions == 3


@pytest.mark.parametrize(
    "config_path",
    PREREGISTERED_EXPERIMENT_CONFIGS,
    ids=lambda path: path.parent.name,
)
def test_preregistered_experiment_configs_are_canonical(
    config_path: Path,
) -> None:
    config = parse_experiment_config(config_path.read_bytes())

    assert config.experiment_id == config_path.parent.name


@pytest.mark.parametrize(
    ("content", "message"),
    [
        (
            EXPERIMENT_CONFIG.replace(
                b"experiment_id: label-clarity\n",
                b"experiment_id: label-clarity\nexperiment_id: duplicate\n",
            ),
            "duplicate key",
        ),
        (
            EXPERIMENT_CONFIG.replace(
                b"title: Label clarity evaluation\n",
                b"title: &title Label clarity evaluation\n",
            ),
            "anchors, aliases, or tags",
        ),
        (
            EXPERIMENT_CONFIG.replace(
                b"title: Label clarity evaluation\n",
                b"title: !custom Label clarity evaluation\n",
            ),
            "anchors, aliases, or tags",
        ),
        (
            EXPERIMENT_CONFIG.replace(
                b"  hypothesis:",
                b" hypothesis:",
            ),
            "two-space indentation",
        ),
        (
            b"\xef\xbb\xbf" + EXPERIMENT_CONFIG,
            "UTF-8 BOM",
        ),
        (
            EXPERIMENT_CONFIG.replace(b"  hypothesis:", b"\thypothesis:"),
            "must not contain tabs",
        ),
        (
            EXPERIMENT_CONFIG.replace(
                b"title: Label clarity evaluation\n",
                b"title: Label clarity evaluation \n",
            ),
            "must not have trailing spaces",
        ),
        (
            EXPERIMENT_CONFIG.replace(b"\n", b"\r\n"),
            "LF line endings",
        ),
        (
            EXPERIMENT_CONFIG.removesuffix(b"\n"),
            "end with a newline",
        ),
    ],
)
def test_parse_experiment_config_rejects_non_canonical_yaml(
    content: bytes,
    message: str,
) -> None:
    with pytest.raises(ExperimentConfigError, match=message):
        parse_experiment_config(content)


def test_config_must_match_run_manifest_versions() -> None:
    config = parse_experiment_config(EXPERIMENT_CONFIG)
    record = build_record()
    changed_experiment = record.experiment.model_copy(update={"version": "v2"})
    changed_record = record.model_copy(update={"experiment": changed_experiment})

    with pytest.raises(
        ExperimentConfigError,
        match="experiment_version",
    ):
        validate_config_matches_record(config, changed_record)
