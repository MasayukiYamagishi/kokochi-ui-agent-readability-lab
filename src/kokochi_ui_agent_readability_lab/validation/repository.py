"""Validate experiment assets, manifests, dashboards, and CI safety offline."""

from __future__ import annotations

import argparse
from collections.abc import Iterable
from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
import re
import subprocess

from jsonschema import Draft202012Validator  # type: ignore[import-untyped]
import yaml  # type: ignore[import-untyped]

from kokochi_ui_agent_readability_lab.evaluation import (
    CONTROL_DISCOVERY_SCORER_ID,
    CONTROL_DISCOVERY_SCORER_VERSION,
    GROUP_MEMBERSHIP_SCORER_ID,
    GROUP_MEMBERSHIP_SCORER_VERSION,
    SCORER_ID,
    SCORER_VERSION,
)
from kokochi_ui_agent_readability_lab.ground_truth import (
    validate_ground_truth_matches_config,
)
from kokochi_ui_agent_readability_lab.evaluation.group_membership import (
    load_group_membership_ground_truth,
)
from kokochi_ui_agent_readability_lab.prompts import validate_prompt_matches_config
from kokochi_ui_agent_readability_lab.records import (
    CURRENT_SCHEMA_VERSION,
    ExperimentConfig,
    RunRecord,
    RunRecordV2,
    WriteOnceResultStore,
    parse_experiment_config,
    parse_pilot_plan,
    parse_run_record_json,
    validate_pilot_plan_matches_config,
)


IDENTIFIER_PATTERN = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
PRIVATE_RESULT_TIERS = frozenset({"pilot", "official"})
PUBLIC_RESULT_FILENAME_PATTERN = re.compile(
    r"^[a-z0-9]+(?:-[a-z0-9]+)*\."
    r"(?:summary\.(?:json|md)|chart-data\.(?:json|csv))$"
)
FORBIDDEN_PUBLIC_FILENAME_TOKENS = frozenset(
    {
        "config",
        "configuration",
        "log",
        "prompt",
        "raw",
        "response",
        "run",
        "screenshot",
        "trace",
    }
)
FORBIDDEN_PUBLIC_JSON_KEYS = frozenset(
    {
        "apikey",
        "ariasnapshot",
        "authorization",
        "configuration",
        "cookie",
        "dom",
        "html",
        "password",
        "privatekey",
        "prompt",
        "prompttext",
        "providerrequestid",
        "providerresponse",
        "rawprompt",
        "rawresponse",
        "request",
        "requestbody",
        "response",
        "responsebody",
        "resourcespans",
        "runid",
        "screenshot",
        "spanid",
        "secret",
        "token",
        "trace",
        "traceid",
    }
)
FORBIDDEN_PUBLIC_TEXT_MARKERS = (
    "raw prompt",
    "raw response",
    "provider-response",
    "request_body",
    "response_body",
    "trace_id",
)
REQUIRED_EXPERIMENT_ENTRIES = (
    "README.md",
    "README.en.md",
    "config.yaml",
    "fixture-manifest.json",
    "fixtures",
    "ground-truth",
    "prompts",
    "tests",
)
REQUIRED_CI_COMMANDS = (
    "pnpm install --frozen-lockfile",
    "uv sync --dev --frozen",
    "pnpm --filter fixtures-web lint",
    "pnpm --recursive --filter=!kokochi-ui-agent-readability-lab test",
    "pnpm typecheck",
    "pnpm build",
    "pnpm observability:config",
    "uv run pytest -v",
    "uv run ruff check .",
    "uv run ruff format --check .",
    "uv run mypy src/kokochi_ui_agent_readability_lab",
    "uv run python scripts/validate_repository.py",
)
FORBIDDEN_CI_TEXT = (
    "${{ secrets.",
    "actions/upload-artifact",
    "test:external",
    "OPENAI_API_KEY",
    "ANTHROPIC_API_KEY",
    "OLLAMA_HOST",
    "docker compose up",
)


@dataclass(frozen=True)
class ValidationFailure:
    """One bounded validation failure safe to print in CI logs."""

    stage: str
    subject: str
    message: str

    def __str__(self) -> str:
        return f"[{self.stage}] {self.subject}: {self.message}"


class RepositoryValidationError(ValueError):
    """Raised after all offline validation stages have been evaluated."""

    def __init__(self, failures: Iterable[ValidationFailure]) -> None:
        self.failures = tuple(failures)
        super().__init__("\n".join(str(failure) for failure in self.failures))


def _failure(
    failures: list[ValidationFailure],
    stage: str,
    subject: str,
    error: object,
) -> None:
    failures.append(ValidationFailure(stage, subject, str(error)))


def _load_json(path: Path) -> object:
    def reject_duplicates(pairs: list[tuple[str, object]]) -> dict[str, object]:
        result: dict[str, object] = {}
        for key, value in pairs:
            if key in result:
                raise ValueError(f"duplicate JSON key {key!r}")
            result[key] = value
        return result

    return json.loads(path.read_bytes(), object_pairs_hook=reject_duplicates)


def _validate_experiments(
    repository_root: Path,
    failures: list[ValidationFailure],
) -> None:
    experiments_root = repository_root / "experiments"
    experiment_roots = sorted(
        path for path in experiments_root.iterdir() if path.is_dir()
    )
    if not experiment_roots:
        _failure(failures, "experiment-layout", "experiments", "no experiments found")
        return

    for experiment_root in experiment_roots:
        experiment_id = experiment_root.name
        subject = f"experiment={experiment_id}"
        if IDENTIFIER_PATTERN.fullmatch(experiment_id) is None:
            _failure(
                failures,
                "experiment-layout",
                subject,
                "directory name must be a lowercase kebab-case identifier",
            )
            continue

        for entry in REQUIRED_EXPERIMENT_ENTRIES:
            if not (experiment_root / entry).exists():
                _failure(
                    failures,
                    "experiment-layout",
                    subject,
                    f"required path is missing: {entry}",
                )

        config_path = experiment_root / "config.yaml"
        if not config_path.is_file():
            continue
        try:
            config = parse_experiment_config(config_path.read_bytes())
        except Exception as error:
            _failure(failures, "experiment-schema", subject, error)
            continue

        subject = (
            f"experiment={config.experiment_id} version={config.experiment_version}"
        )
        if config.experiment_id != experiment_id:
            _failure(
                failures,
                "experiment-layout",
                subject,
                "config experiment_id differs from its directory",
            )

        try:
            Draft202012Validator.check_schema(config.design.expected_output_schema)
        except Exception as error:
            _failure(failures, "output-schema", subject, error)

        prompt_path = experiment_root / config.prompt.source_path
        try:
            validate_prompt_matches_config(prompt_path.read_bytes(), config)
        except Exception as error:
            _failure(failures, "prompt", subject, error)

        ground_truth_path = experiment_root / config.ground_truth.source_path
        try:
            if config.scorer.scorer_id == GROUP_MEMBERSHIP_SCORER_ID:
                content = ground_truth_path.read_bytes()
                if hashlib.sha256(content).hexdigest() != str(
                    config.ground_truth.approved_sha256
                ):
                    raise ValueError("ground truth digest differs from config.yaml")
                load_group_membership_ground_truth(content)
            else:
                validate_ground_truth_matches_config(
                    ground_truth_path.read_bytes(),
                    config,
                    require_approved=True,
                )
        except Exception as error:
            _failure(failures, "ground-truth", subject, error)

        pilot_plan_path = experiment_root / "pilot-plan.yaml"
        if pilot_plan_path.exists():
            try:
                pilot_plan = parse_pilot_plan(pilot_plan_path.read_bytes())
                validate_pilot_plan_matches_config(pilot_plan, config)
            except Exception as error:
                _failure(failures, "pilot-plan", subject, error)

        implemented_scorers = {
            (SCORER_ID, SCORER_VERSION),
            (CONTROL_DISCOVERY_SCORER_ID, CONTROL_DISCOVERY_SCORER_VERSION),
            (GROUP_MEMBERSHIP_SCORER_ID, GROUP_MEMBERSHIP_SCORER_VERSION),
        }
        if (config.scorer.scorer_id, config.scorer.scorer_version) not in (
            implemented_scorers
        ):
            _failure(
                failures,
                "scorer",
                subject,
                "configured scorer is not implemented by the shared scorer module",
            )

        _validate_fixture_manifest(experiment_root, config, subject, failures)


def _validate_fixture_manifest(
    experiment_root: Path,
    config: ExperimentConfig,
    subject: str,
    failures: list[ValidationFailure],
) -> None:
    try:
        manifest = _load_json(experiment_root / "fixture-manifest.json")
        if not isinstance(manifest, dict):
            raise ValueError("fixture manifest must be an object")
        if (
            not isinstance(manifest.get("experimentTitle"), str)
            or not manifest["experimentTitle"].strip()
        ):
            raise ValueError("experimentTitle must be a non-empty string")
        fixtures = manifest.get("fixtures")
        if not isinstance(fixtures, list) or not fixtures:
            raise ValueError("fixtures must be a non-empty array")

        manifest_fixtures: dict[str, str] = {}
        for index, fixture in enumerate(fixtures):
            if not isinstance(fixture, dict):
                raise ValueError(f"fixtures[{index}] must be an object")
            fixture_id = fixture.get("fixtureId")
            fixture_version = fixture.get("fixtureVersion")
            description = fixture.get("description")
            if (
                not isinstance(fixture_id, str)
                or IDENTIFIER_PATTERN.fullmatch(fixture_id) is None
            ):
                raise ValueError(f"fixtures[{index}].fixtureId is invalid")
            if (
                not isinstance(fixture_version, str)
                or IDENTIFIER_PATTERN.fullmatch(fixture_version) is None
            ):
                raise ValueError(f"fixtures[{index}].fixtureVersion is invalid")
            if not isinstance(description, str) or not description.strip():
                raise ValueError(f"fixtures[{index}].description is required")
            if fixture_id in manifest_fixtures:
                raise ValueError(f"duplicate fixtureId {fixture_id!r}")
            manifest_fixtures[fixture_id] = fixture_version

        configured_fixtures = {
            fixture.fixture_id: fixture.fixture_version for fixture in config.fixtures
        }
        if manifest_fixtures != configured_fixtures:
            raise ValueError("fixture manifest IDs or versions differ from config.yaml")
        for fixture in config.fixtures:
            if not (experiment_root / fixture.source_path).is_file():
                raise ValueError(f"fixture source is missing: {fixture.source_path}")
    except Exception as error:
        _failure(failures, "fixture-manifest", subject, error)


def _validate_grafana(
    repository_root: Path,
    failures: list[ValidationFailure],
) -> None:
    dashboard_path = (
        repository_root
        / "observability"
        / "grafana"
        / "dashboards"
        / "group-membership-reconstruction.json"
    )
    subject = dashboard_path.relative_to(repository_root).as_posix()
    try:
        dashboard = _load_json(dashboard_path)
        if not isinstance(dashboard, dict):
            raise ValueError("dashboard must be a JSON object")
        if dashboard.get("uid") != "kokochi-task-telemetry":
            raise ValueError("dashboard uid is not stable")
        if not isinstance(dashboard.get("schemaVersion"), int):
            raise ValueError("dashboard schemaVersion must be an integer")
        panels = dashboard.get("panels")
        if not isinstance(panels, list) or not panels:
            raise ValueError("dashboard must define panels")

        datasource_path = (
            repository_root
            / "observability"
            / "grafana"
            / "provisioning"
            / "datasources"
            / "grafana-datasources.yaml"
        )
        datasource_config = yaml.safe_load(datasource_path.read_bytes())
        provisioned_uids = {
            datasource["uid"]
            for datasource in datasource_config["datasources"]
            if isinstance(datasource, dict) and isinstance(datasource.get("uid"), str)
        }
        referenced_uids = _collect_datasource_uids(dashboard)
        missing_uids = sorted(referenced_uids - provisioned_uids)
        if missing_uids:
            raise ValueError(
                "dashboard references unprovisioned datasource UIDs: "
                + ", ".join(missing_uids)
            )
    except Exception as error:
        _failure(failures, "grafana-json", subject, error)


def _collect_datasource_uids(value: object) -> set[str]:
    uids: set[str] = set()
    if isinstance(value, dict):
        datasource = value.get("datasource")
        if isinstance(datasource, dict):
            uid = datasource.get("uid")
            if isinstance(uid, str) and uid not in {"-- Mixed --", "grafana"}:
                uids.add(uid)
        for nested_value in value.values():
            uids.update(_collect_datasource_uids(nested_value))
    elif isinstance(value, list):
        for nested_value in value:
            uids.update(_collect_datasource_uids(nested_value))
    return uids


def _validate_compose(
    repository_root: Path,
    failures: list[ValidationFailure],
) -> None:
    compose_path = repository_root / "observability" / "otel" / "compose.yaml"
    subject = compose_path.relative_to(repository_root).as_posix()
    try:
        compose = yaml.safe_load(compose_path.read_bytes())
        services = compose["services"]
        for service in ("lgtm", "otel-collector"):
            image = services[service]["image"]
            final_segment = image.rsplit("/", maxsplit=1)[-1]
            if ":" not in final_segment or final_segment.endswith(":latest"):
                raise ValueError(f"service {service!r} must use a versioned image tag")
    except Exception as error:
        _failure(failures, "compose", subject, error)


def _validate_result_manifests(
    repository_root: Path,
    failures: list[ValidationFailure],
) -> None:
    for manifest_path in sorted(
        repository_root.glob("experiments/*/results/*/*/run.json")
    ):
        relative_parts = manifest_path.relative_to(repository_root).parts
        tier = relative_parts[3]
        if tier not in PRIVATE_RESULT_TIERS:
            continue
        subject = manifest_path.parent.relative_to(repository_root).as_posix()
        try:
            record = parse_run_record_json(manifest_path.read_bytes())
            if manifest_path.parent.name != str(record.run_id):
                raise ValueError("run directory name differs from run_id")
            if tier == "official" and record.git.is_dirty:
                raise ValueError("official run records must have a clean Git revision")
            expected_paths = {artifact.relative_path for artifact in record.artifacts}
            expected_by_path = {
                artifact.relative_path: artifact for artifact in record.artifacts
            }
            actual_paths = {
                path.relative_to(manifest_path.parent).as_posix()
                for path in manifest_path.parent.rglob("*")
                if path.is_file() and path != manifest_path
            }
            if actual_paths != expected_paths:
                raise ValueError("run artifacts differ from the manifest path set")
            artifact_contents: dict[str, bytes] = {}
            for artifact in record.artifacts:
                content = (manifest_path.parent / artifact.relative_path).read_bytes()
                artifact_contents[artifact.relative_path] = content
                digest = hashlib.sha256(content).hexdigest()
                if len(content) != artifact.byte_length:
                    raise ValueError(
                        f"artifact byte length differs: {artifact.relative_path}"
                    )
                if digest != artifact.content_hash.digest:
                    raise ValueError(f"artifact hash differs: {artifact.relative_path}")
            if isinstance(record, (RunRecordV2, RunRecord)):
                WriteOnceResultStore._validate_model_configuration(
                    record,
                    artifact_contents,
                    expected_by_path,
                )
            if (
                isinstance(record, RunRecord)
                and record.schema_version != CURRENT_SCHEMA_VERSION
            ):
                raise ValueError(
                    f"current run record does not use schema {CURRENT_SCHEMA_VERSION}"
                )
        except Exception as error:
            _failure(failures, "run-manifest", subject, error)


def _validate_result_boundary(
    repository_root: Path,
    failures: list[ValidationFailure],
) -> None:
    subject = "experiments/*/results/{pilot,official,public}"
    gitignore = (repository_root / ".gitignore").read_text(encoding="utf-8")
    gitignore_lines = set(gitignore.splitlines())

    private_results_ignored = (
        "**/results/" in gitignore_lines
        or (
            "/experiments/*/results/pilot/" in gitignore_lines
            and "/experiments/*/results/official/" in gitignore_lines
        )
    )

    if not private_results_ignored:
        _failure(
            failures,
            "result-boundary",
            subject,
            "missing .gitignore rule for private experiment results",
        )

    result = subprocess.run(
        ["git", "ls-files", "-z", "--", "experiments"],
        cwd=repository_root,
        capture_output=True,
        check=False,
    )
    if result.returncode != 0:
        _failure(failures, "result-boundary", subject, "git ls-files failed")
        return
    tracked_paths = result.stdout.decode("utf-8").split("\0")
    tracked_private = []
    tracked_public = []
    for tracked_path in tracked_paths:
        parts = Path(tracked_path).parts
        if (
            len(parts) >= 4
            and parts[2] == "results"
            and parts[3] in PRIVATE_RESULT_TIERS
        ):
            tracked_private.append(tracked_path)
        elif len(parts) >= 4 and parts[2] == "results" and parts[3] == "public":
            tracked_public.append(tracked_path)
    if tracked_private:
        _failure(
            failures,
            "result-boundary",
            subject,
            "private result files are tracked: " + ", ".join(sorted(tracked_private)),
        )
    for tracked_path in sorted(tracked_public):
        _validate_public_result_file(repository_root, tracked_path, failures)


def _validate_public_result_file(
    repository_root: Path,
    tracked_path: str,
    failures: list[ValidationFailure],
) -> None:
    subject = tracked_path.replace("\\", "/")
    parts = Path(tracked_path).parts
    if len(parts) != 5 or PUBLIC_RESULT_FILENAME_PATTERN.fullmatch(parts[-1]) is None:
        _failure(
            failures,
            "result-boundary",
            subject,
            "public results must be direct *.summary.{json,md} or "
            "*.chart-data.{json,csv} files",
        )
        return

    filename_tokens = {
        token for token in re.split(r"[^a-z0-9]+", parts[-1].lower()) if token
    }
    forbidden_tokens = sorted(filename_tokens & FORBIDDEN_PUBLIC_FILENAME_TOKENS)
    if forbidden_tokens:
        _failure(
            failures,
            "result-boundary",
            subject,
            "public result filename contains raw-result terms: "
            + ", ".join(forbidden_tokens),
        )
        return

    path = repository_root / Path(tracked_path)
    try:
        if path.suffix == ".json":
            content = _load_json(path)
            if not isinstance(content, dict):
                raise ValueError("public JSON result must be an object")
            _validate_public_json_value(content, "public-result")
        else:
            text = path.read_text(encoding="utf-8")
            lowered = text.lower()
            markers = sorted(
                marker for marker in FORBIDDEN_PUBLIC_TEXT_MARKERS if marker in lowered
            )
            if markers:
                raise ValueError(
                    "public result contains raw-result markers: " + ", ".join(markers)
                )
    except Exception as error:
        _failure(failures, "result-boundary", subject, error)


def _validate_public_json_value(value: object, path: str) -> None:
    if isinstance(value, dict):
        for key, nested_value in value.items():
            normalized_key = re.sub(r"[^a-z0-9]", "", str(key).lower())
            if normalized_key in FORBIDDEN_PUBLIC_JSON_KEYS or normalized_key.endswith(
                (
                    "apikey",
                    "accesstoken",
                    "authtoken",
                    "clientsecret",
                    "credential",
                    "credentials",
                    "password",
                    "privatekey",
                    "refreshtoken",
                    "secretkey",
                    "sessiontoken",
                )
            ):
                raise ValueError(
                    f"public result contains a private field: {path}.{key}"
                )
            _validate_public_json_value(nested_value, f"{path}.{key}")
    elif isinstance(value, list):
        for index, nested_value in enumerate(value):
            _validate_public_json_value(nested_value, f"{path}[{index}]")


def _validate_ci_contract(
    repository_root: Path,
    failures: list[ValidationFailure],
) -> None:
    workflow_path = repository_root / ".github" / "workflows" / "ci.yml"
    subject = workflow_path.relative_to(repository_root).as_posix()
    if not workflow_path.is_file():
        return
    text = workflow_path.read_text(encoding="utf-8")
    try:
        workflow = yaml.safe_load(text)
        if not isinstance(workflow, dict):
            raise ValueError("workflow must be a YAML mapping")
    except Exception as error:
        _failure(failures, "ci-contract", subject, error)
        return
    for command in REQUIRED_CI_COMMANDS:
        if command not in text:
            _failure(
                failures,
                "ci-contract",
                subject,
                f"required offline validation command is missing: {command}",
            )
    for forbidden in FORBIDDEN_CI_TEXT:
        if forbidden.lower() in text.lower():
            _failure(
                failures,
                "ci-contract",
                subject,
                f"forbidden external or artifact operation found: {forbidden}",
            )
    for action in re.findall(r"^\s*uses:\s+([^\s#]+)", text, flags=re.MULTILINE):
        _, separator, revision = action.rpartition("@")
        if not separator or re.fullmatch(r"[0-9a-f]{40}", revision) is None:
            _failure(
                failures,
                "ci-contract",
                subject,
                f"GitHub Action must be pinned to a full commit SHA: {action}",
            )
    if "permissions:\n  contents: read" not in text:
        _failure(
            failures,
            "ci-contract",
            subject,
            "workflow must declare read-only repository permissions",
        )


def validate_repository(repository_root: Path) -> tuple[str, ...]:
    """Run every offline validation stage and return their stable names."""

    root = repository_root.resolve()
    failures: list[ValidationFailure] = []
    _validate_experiments(root, failures)
    _validate_grafana(root, failures)
    _validate_compose(root, failures)
    _validate_result_manifests(root, failures)
    _validate_result_boundary(root, failures)
    _validate_ci_contract(root, failures)
    if failures:
        raise RepositoryValidationError(failures)
    return (
        "experiment-schema",
        "prompt",
        "ground-truth",
        "pilot-plan",
        "scorer",
        "fixture-manifest",
        "grafana-json",
        "compose",
        "run-manifest",
        "result-boundary",
        "ci-contract",
    )


def main(argv: list[str] | None = None) -> int:
    """Validate the checkout without calling an external model or service."""

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "repository_root",
        nargs="?",
        type=Path,
        default=Path.cwd(),
    )
    arguments = parser.parse_args(argv)
    try:
        stages = validate_repository(arguments.repository_root)
    except RepositoryValidationError as error:
        print("Repository validation failed:")
        for failure in error.failures:
            print(f"- {failure}")
        return 1
    print("Repository validation passed: " + ", ".join(stages))
    return 0

