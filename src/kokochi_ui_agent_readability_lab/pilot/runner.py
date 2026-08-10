"""End-to-end runner for the preregistered local Ollama pilot."""

from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping, Sequence
from datetime import UTC, datetime
import hashlib
import json
from pathlib import Path
import re
from typing import Annotated, Literal, Self
from urllib.parse import urlsplit
from uuid import UUID

from playwright.sync_api import Browser, sync_playwright
from pydantic import AwareDatetime, Field, HttpUrl, model_validator

from kokochi_ui_agent_readability_lab.capture import (
    CaptureRequest,
    CaptureSettings,
    CapturedInputs,
    CapturedInputStore,
    capture_fixture,
    new_capture_context,
)
from kokochi_ui_agent_readability_lab.evaluation import (
    CONTROL_DISCOVERY_SCORER_ID,
    GROUP_MEMBERSHIP_SCORER_ID,
    EvaluationError,
    RawResponseStore,
    StoredEvaluationArtifacts,
    save_and_evaluate_control_discovery_response,
    save_and_evaluate_group_membership_response,
    save_and_evaluate_item_association_response,
)
from kokochi_ui_agent_readability_lab.prompts import (
    TASK_INSTRUCTION_PLACEHOLDER,
    PromptDocument,
    build_prompt,
    validate_prompt_matches_config,
)
from kokochi_ui_agent_readability_lab.providers import (
    OllamaAdapter,
    OllamaClientConfig,
    OllamaError,
    OllamaGenerateRequest,
    OllamaGenerateResult,
    OllamaGenerationOptions,
    OllamaModelInfo,
    ollama_generation_parameters,
    ollama_model_configuration_bytes,
)
from kokochi_ui_agent_readability_lab.records import (
    CURRENT_SCHEMA_VERSION,
    ArtifactReference,
    ArtifactRole,
    BrowserContext,
    BrowserRuntime,
    ConfigurationSanitization,
    ExecutionEnvironment,
    ExecutionResult,
    ExecutionStatus,
    ExperimentConfig,
    ExperimentDefinition,
    FailureStage,
    FixtureConfig,
    FixtureDefinition,
    GitRevision,
    GroundTruthReference,
    GpuRuntime,
    InputCollectionResult,
    MetadataCollectionError,
    MissingValueReason,
    ModelConfiguration,
    NormalizedResponse,
    PilotPlan,
    PilotPlanDocument,
    PromptReference,
    ProviderResponse,
    ResultTier,
    RunRecord,
    ScoreResult,
    SeedMetadata,
    SeedSupportStatus,
    TokenUsage,
    WriteOnceResultStore,
    artifact_reference,
    collect_compose_container_images,
    collect_execution_environment,
    collect_git_revision,
    new_run_id,
    parse_experiment_config,
    parse_pilot_plan,
    parse_run_record_json,
    validate_pilot_plan_matches_config,
)
from kokochi_ui_agent_readability_lab.records.schema import StrictModel
from kokochi_ui_agent_readability_lab.telemetry import (
    ExperimentRun,
    RunTelemetryContext,
    TelemetryRuntime,
    TelemetrySettings,
    create_otlp_telemetry,
    wait_for_collector,
)


_AUTHORIZATION_COMMENT_URL_PATTERN = re.compile(
    r"^https://github\.com/MasayukiYamagishi/"
    r"kokochi-ui-agent-readability-lab/issues/\d+#issuecomment-\d+$"
)
_BROWSER_REVISION_PATTERN = re.compile(
    r"(?:^|[\\/])chromium(?:_headless_shell)?-(\d+)(?:[\\/]|$)"
)
_REPRESENTATION_PATHS = {
    "dom-inner-html": ("inputs/dom-inner-html.html", "html"),
    "accessibility-tree": ("inputs/accessibility-tree.yml", "aria"),
}
_PILOT_CAPTURE_SETTINGS = CaptureSettings(locale="ja-JP")


class PilotModelMismatchError(RuntimeError):
    """Raised before inference when the local model differs from approval."""


class PilotRunAlreadyExistsError(RuntimeError):
    """Raised when a requested pilot cell/repetition was already attempted."""


class PilotAuthorization(StrictModel):
    """Durable evidence that a human authorized this exact pilot plan."""

    schema_version: Literal["1.1.0"] = "1.1.0"
    scope: Literal["local-ollama-pilot", "local-ollama-official"] = "local-ollama-pilot"
    experiment_id: str = Field(pattern=r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
    approved_by: str = Field(min_length=1)
    authorized_at: AwareDatetime
    record_url: HttpUrl
    pilot_plan_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")

    @model_validator(mode="after")
    def record_must_be_repository_issue_comment(self) -> Self:
        url = str(self.record_url)
        if _AUTHORIZATION_COMMENT_URL_PATTERN.fullmatch(url) is None:
            raise ValueError(
                "authorization must reference a comment in the experiment repository"
            )
        return self


class PilotRunRequest(StrictModel):
    """Runtime locations and the explicitly selected pilot repetitions."""

    repository_root: Path
    plan_path: Path
    fixture_base_url: str
    ollama_endpoint: str
    authorization: PilotAuthorization
    repetition_indices: tuple[int, ...] = Field(min_length=1)
    confirm_cpu_only_ollama: bool = False
    ollama_timeout_seconds: Annotated[float, Field(gt=0)] = 120.0

    @model_validator(mode="after")
    def locations_and_repetitions_must_be_safe(self) -> Self:
        parsed_base_url = urlsplit(self.fixture_base_url)
        if (
            parsed_base_url.scheme not in {"http", "https"}
            or not parsed_base_url.netloc
            or parsed_base_url.path not in {"", "/"}
            or parsed_base_url.query
            or parsed_base_url.fragment
            or parsed_base_url.username
            or parsed_base_url.password
        ):
            raise ValueError("fixture_base_url must be a credential-free HTTP origin")
        if len(self.repetition_indices) != len(set(self.repetition_indices)):
            raise ValueError("repetition_indices must be unique")
        if any(index < 0 for index in self.repetition_indices):
            raise ValueError("repetition_indices must be non-negative")
        return self


class PilotCell(StrictModel):
    """One task-fixture, representation, and matched-seed repetition."""

    fixture_id: str
    task_id: str | None = None
    input_representation: str
    repetition_index: int = Field(ge=0)
    seed: int


class PilotRunOutcome(StrictModel):
    """Terminal location and status of one attempted cell."""

    cell: PilotCell
    run_id: UUID
    status: ExecutionStatus
    run_directory: Path
    error_code: str | None = None


class PilotRunSummary(StrictModel):
    """Bounded, content-free summary printed by the pilot CLI."""

    experiment_id: str
    experiment_version: str
    plan_sha256: str
    outcomes: tuple[PilotRunOutcome, ...]


class PilotDescription(StrictModel):
    """Read-only description used at the human execution gate."""

    hypothesis: str
    independent_variable: str
    controlled_variables: tuple[str, ...]
    model_inputs: tuple[str, ...]
    expected_output_schema: dict[str, object] | str
    ground_truth_source: str
    scoring_method: str
    pilot_repetition_count: int
    expected_api_cost: str
    known_limitations: tuple[str, ...]
    cells: tuple[PilotCell, ...]
    asset_sha256: Mapping[str, str]


Clock = Callable[[], datetime]


def _canonical_json_bytes(value: object) -> bytes:
    return (
        json.dumps(
            value,
            ensure_ascii=False,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        + "\n"
    ).encode("utf-8")


def _sha256(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def _load_plan_and_config(
    request: PilotRunRequest,
) -> tuple[bytes, PilotPlanDocument, bytes, ExperimentConfig, Path]:
    repository_root = request.repository_root.resolve()
    plan_path = request.plan_path.resolve()
    if not plan_path.is_relative_to(repository_root):
        raise ValueError("pilot plan must stay inside the repository")
    plan_content = plan_path.read_bytes()
    plan = parse_pilot_plan(plan_content)
    plan_filename = f"{plan.result_tier}-plan.yaml"
    expected_plan_path = (
        repository_root / "experiments" / plan.experiment_id / plan_filename
    ).resolve()
    if plan_path != expected_plan_path:
        raise ValueError(f"pilot plan must be {expected_plan_path}")
    experiment_root = expected_plan_path.parent
    config_content = (experiment_root / "config.yaml").read_bytes()
    config = parse_experiment_config(config_content)
    validate_pilot_plan_matches_config(plan, config)
    if request.authorization.pilot_plan_sha256 != _sha256(plan_content):
        raise ValueError("authorization refers to a different pilot plan hash")
    if request.authorization.experiment_id != plan.experiment_id:
        raise ValueError("authorization refers to a different experiment")
    return plan_content, plan, config_content, config, experiment_root


def _selected_cells(
    plan: PilotPlanDocument,
    config: ExperimentConfig,
    repetition_indices: Sequence[int],
) -> tuple[PilotCell, ...]:
    selected = set(repetition_indices)
    unknown = sorted(selected - set(range(len(plan.repetitions))))
    if unknown:
        raise ValueError(f"unknown pilot repetition indices: {unknown}")
    cells: list[PilotCell] = []
    for repetition in plan.repetitions:
        if repetition.repetition_index not in selected:
            continue
        for representation in plan.input_representations:
            if representation not in _REPRESENTATION_PATHS:
                raise ValueError(
                    f"unsupported pilot input representation: {representation}"
                )
            if isinstance(plan, PilotPlan):
                tasks_by_id = {task.task_id: task for task in config.tasks or []}
                for task_id in plan.task_ids:
                    task = tasks_by_id[task_id]
                    for fixture_id in task.fixture_ids:
                        cells.append(
                            PilotCell(
                                fixture_id=fixture_id,
                                task_id=task_id,
                                input_representation=representation,
                                repetition_index=repetition.repetition_index,
                                seed=repetition.seed,
                            )
                        )
            else:
                for fixture_id in plan.fixture_ids:
                    cells.append(
                        PilotCell(
                            fixture_id=fixture_id,
                            input_representation=representation,
                            repetition_index=repetition.repetition_index,
                            seed=repetition.seed,
                        )
                    )
    return tuple(cells)


def describe_pilot(repository_root: Path, plan_path: Path) -> PilotDescription:
    """Return the ten preregistration items and exact planned pilot cells."""

    plan_content = plan_path.read_bytes()
    plan = parse_pilot_plan(plan_content)
    experiment_root = repository_root / "experiments" / plan.experiment_id
    config_content = (experiment_root / "config.yaml").read_bytes()
    config = parse_experiment_config(config_content)
    validate_pilot_plan_matches_config(plan, config)
    prompt_content = (experiment_root / config.prompt.source_path).read_bytes()
    ground_truth_content = (
        experiment_root / config.ground_truth.source_path
    ).read_bytes()
    fixture_hashes = {
        f"fixture:{fixture.fixture_id}": _sha256(
            (experiment_root / fixture.source_path).read_bytes()
        )
        for fixture in config.fixtures
    }
    backend_control = (
        "Ollama execution backend fixed to CPU with GPU acceleration disabled"
        if plan.execution_backend == "cpu"
        else (
            "Ollama execution backend fixed to the approved remote NVIDIA GPU "
            "server hardware"
        )
    )
    return PilotDescription(
        hypothesis=config.design.hypothesis,
        independent_variable=config.design.independent_variable,
        controlled_variables=(
            *config.design.controlled_variables,
            backend_control,
        ),
        model_inputs=tuple(config.design.model_inputs),
        expected_output_schema=config.design.expected_output_schema,
        ground_truth_source=config.ground_truth.source_path,
        scoring_method=config.design.scoring_method,
        pilot_repetition_count=len(plan.repetitions),
        expected_api_cost=plan.expected_api_cost,
        known_limitations=tuple(config.design.limitations),
        cells=_selected_cells(plan, config, range(len(plan.repetitions))),
        asset_sha256={
            "config": _sha256(config_content),
            "pilot-plan": _sha256(plan_content),
            "prompt": _sha256(prompt_content),
            "ground-truth": _sha256(ground_truth_content),
            **fixture_hashes,
        },
    )


def _fixture_by_id(config: ExperimentConfig, fixture_id: str) -> FixtureConfig:
    matching = [
        fixture for fixture in config.fixtures if fixture.fixture_id == fixture_id
    ]
    if len(matching) != 1:
        raise ValueError(f"fixture is not uniquely configured: {fixture_id}")
    return matching[0]


def _browser_revision(executable_path: str) -> str:
    match = _BROWSER_REVISION_PATTERN.search(executable_path)
    if match is None:
        raise MetadataCollectionError(
            "could not determine the Playwright Chromium revision"
        )
    return match.group(1)


def _assert_model_matches_plan(info: OllamaModelInfo, plan: PilotPlanDocument) -> None:
    mismatches: list[str] = []
    if info.model_id != plan.model_id:
        mismatches.append("model_id")
    if info.digest != plan.approved_model_digest:
        mismatches.append("digest")
    if info.quantization != plan.approved_quantization:
        mismatches.append("quantization")
    if info.parameter_size != plan.approved_parameter_size:
        mismatches.append("parameter_size")
    if isinstance(plan, PilotPlan) and (
        info.runtime_version != plan.approved_ollama_version
    ):
        mismatches.append("runtime_version")
    if mismatches:
        raise PilotModelMismatchError(
            "installed Ollama model differs from pilot approval: "
            + ", ".join(mismatches)
        )


def _assert_timeout_matches_plan(
    timeout_seconds: float,
    plan: PilotPlanDocument,
) -> None:
    if isinstance(plan, PilotPlan) and timeout_seconds != plan.request_timeout_seconds:
        raise MetadataCollectionError(
            "Ollama request timeout differs from the approved pilot plan"
        )


def _existing_cell_keys(
    result_root: Path,
) -> set[tuple[str, str | None, str, int]]:
    keys: set[tuple[str, str | None, str, int]] = set()
    if not result_root.is_dir():
        return keys
    for candidate in result_root.iterdir():
        if candidate.name.startswith(".") or not candidate.is_dir():
            continue
        record_path = candidate / "run.json"
        if not record_path.is_file():
            raise PilotRunAlreadyExistsError(
                f"pilot result directory has no run.json: {candidate}"
            )
        record = parse_run_record_json(record_path.read_bytes())
        keys.add(
            (
                record.fixture.fixture_id,
                getattr(record, "task_id", None),
                record.input_collection.representation,
                record.execution.repetition_index,
            )
        )
    return keys


def _reject_existing_cells(result_root: Path, cells: Sequence[PilotCell]) -> None:
    existing = _existing_cell_keys(result_root)
    duplicates = [
        cell
        for cell in cells
        if (
            cell.fixture_id,
            cell.task_id,
            cell.input_representation,
            cell.repetition_index,
        )
        in existing
    ]
    if duplicates:
        identifiers = [
            "/".join(
                part
                for part in (
                    cell.fixture_id,
                    cell.task_id,
                    cell.input_representation,
                    str(cell.repetition_index),
                )
                if part is not None
            )
            for cell in duplicates
        ]
        raise PilotRunAlreadyExistsError(
            "pilot cells already have immutable attempts: " + ", ".join(identifiers)
        )


def _capture_request(
    request: PilotRunRequest,
    plan: PilotPlanDocument,
    fixture: FixtureConfig,
) -> CaptureRequest:
    url = (
        f"{request.fixture_base_url.rstrip('/')}/experiments/"
        f"{plan.experiment_id}/{fixture.fixture_id}"
    )
    return CaptureRequest(
        url=url,
        experiment_id=plan.experiment_id,
        fixture_id=fixture.fixture_id,
        fixture_version=fixture.fixture_version,
        settings=_PILOT_CAPTURE_SETTINGS,
    )


def _preflight_fixtures(
    browser: Browser,
    request: PilotRunRequest,
    plan: PilotPlanDocument,
    config: ExperimentConfig,
) -> None:
    for fixture_id in plan.fixture_ids:
        fixture = _fixture_by_id(config, fixture_id)
        capture_request = _capture_request(request, plan, fixture)
        context = new_capture_context(browser, capture_request.settings)
        try:
            capture_fixture(context.new_page(), capture_request)
        finally:
            context.close()


def _model_request(
    plan: PilotPlanDocument,
    config: ExperimentConfig,
    prompt_document: PromptDocument,
    captured: CapturedInputs,
    cell: PilotCell,
) -> OllamaGenerateRequest:
    representation_path, _ = _REPRESENTATION_PATHS[cell.input_representation]
    fixture_input = captured.artifacts[representation_path].decode("utf-8")
    task_instruction = None
    if cell.task_id is not None:
        task = next(
            (task for task in config.tasks or [] if task.task_id == cell.task_id),
            None,
        )
        if task is None or cell.fixture_id not in task.fixture_ids:
            raise ValueError("pilot cell task does not match its fixture")
        if TASK_INSTRUCTION_PLACEHOLDER in prompt_document.body:
            task_instruction = task.instruction
    prompt = build_prompt(
        prompt_document,
        input_representation=cell.input_representation,
        fixture_input=fixture_input,
        task_instruction=task_instruction,
    )
    parameters = plan.generation_parameters
    if not isinstance(config.design.expected_output_schema, dict):
        raise ValueError("pilot output schema must be a JSON Schema mapping")
    return OllamaGenerateRequest(
        model_id=plan.model_id,
        prompt=prompt,
        options=OllamaGenerationOptions(
            temperature=parameters.temperature,
            top_p=parameters.top_p,
            top_k=parameters.top_k,
            repeat_penalty=parameters.repeat_penalty,
            num_ctx=parameters.num_ctx,
            num_predict=parameters.num_predict,
            seed=cell.seed,
        ),
        think=parameters.think,
        response_format=config.design.expected_output_schema,
    )


def _reference(
    *,
    artifact_id: str,
    role: ArtifactRole,
    kind: str,
    relative_path: str,
    media_type: str,
    content: bytes,
) -> ArtifactReference:
    return artifact_reference(
        artifact_id=artifact_id,
        role=role,
        kind=kind,
        relative_path=relative_path,
        media_type=media_type,
        content=content,
    )


def _source_artifacts(
    *,
    experiment_root: Path,
    config: ExperimentConfig,
    fixture: FixtureConfig,
    config_content: bytes,
    plan_content: bytes,
    result_tier: ResultTier,
) -> tuple[dict[str, bytes], list[ArtifactReference]]:
    plan_name = f"{result_tier.value}-plan"
    specifications = (
        (
            "experiment-definition",
            "experiment-definition",
            "sources/config.yaml",
            "application/yaml",
            config_content,
        ),
        (
            plan_name,
            plan_name,
            f"sources/{plan_name}.yaml",
            "application/yaml",
            plan_content,
        ),
        (
            "fixture-definition",
            "fixture-definition",
            f"sources/{fixture.source_path}",
            "text/typescript",
            (experiment_root / fixture.source_path).read_bytes(),
        ),
        (
            "prompt",
            "prompt",
            f"sources/{config.prompt.source_path}",
            "text/markdown",
            (experiment_root / config.prompt.source_path).read_bytes(),
        ),
        (
            "ground-truth",
            "ground-truth",
            f"sources/{config.ground_truth.source_path}",
            "application/json",
            (experiment_root / config.ground_truth.source_path).read_bytes(),
        ),
    )
    artifacts: dict[str, bytes] = {}
    references: list[ArtifactReference] = []
    for artifact_id, kind, relative_path, media_type, content in specifications:
        artifacts[relative_path] = content
        references.append(
            _reference(
                artifact_id=artifact_id,
                role=ArtifactRole.CONFIGURATION,
                kind=kind,
                relative_path=relative_path,
                media_type=media_type,
                content=content,
            )
        )
    return artifacts, references


def _runtime_artifacts(
    *,
    model_request: OllamaGenerateRequest,
    model_info: OllamaModelInfo,
    authorization: PilotAuthorization,
) -> tuple[dict[str, bytes], list[ArtifactReference]]:
    model_configuration = ollama_model_configuration_bytes(model_request)
    model_runtime = _canonical_json_bytes(model_info.model_dump(mode="json"))
    authorization_content = _canonical_json_bytes(authorization.model_dump(mode="json"))
    specifications = (
        (
            "model-configuration",
            "model-configuration",
            "config/model.json",
            model_configuration,
        ),
        (
            "model-runtime",
            "model-runtime",
            "config/model-runtime.json",
            model_runtime,
        ),
        (
            "pilot-authorization",
            "pilot-authorization",
            "config/pilot-authorization.json",
            authorization_content,
        ),
    )
    artifacts: dict[str, bytes] = {}
    references: list[ArtifactReference] = []
    for artifact_id, kind, relative_path, content in specifications:
        artifacts[relative_path] = content
        references.append(
            _reference(
                artifact_id=artifact_id,
                role=ArtifactRole.CONFIGURATION,
                kind=kind,
                relative_path=relative_path,
                media_type="application/json",
                content=content,
            )
        )
    return artifacts, references


def _capture_artifacts(
    captured: CapturedInputs,
) -> tuple[dict[str, bytes], list[ArtifactReference]]:
    return dict(captured.artifacts), list(captured.artifact_references())


def _provider_artifacts(
    generated: OllamaGenerateResult,
    evaluation: StoredEvaluationArtifacts,
) -> tuple[dict[str, bytes], list[ArtifactReference]]:
    specifications = (
        (
            "provider-request",
            "provider-request",
            "outputs/provider-request.json",
            "application/json",
            generated.request_body,
        ),
        (
            "provider-response-envelope",
            "provider-response-envelope",
            "outputs/provider-response.json",
            "application/json",
            generated.response_body,
        ),
        (
            "raw-model-response",
            "raw-model-response",
            RawResponseStore.response_relative_path,
            "text/plain; charset=utf-8",
            evaluation.raw_response,
        ),
        (
            "normalized-response",
            "normalized-response",
            RawResponseStore.normalized_response_relative_path,
            "application/json",
            evaluation.normalized_response_bytes,
        ),
        (
            "score-result",
            "score-result",
            RawResponseStore.score_relative_path,
            "application/json",
            evaluation.score_bytes,
        ),
    )
    artifacts: dict[str, bytes] = {}
    references: list[ArtifactReference] = []
    for artifact_id, kind, relative_path, media_type, content in specifications:
        artifacts[relative_path] = content
        references.append(
            _reference(
                artifact_id=artifact_id,
                role=ArtifactRole.OUTPUT,
                kind=kind,
                relative_path=relative_path,
                media_type=media_type,
                content=content,
            )
        )
    return artifacts, references


def _error_artifacts(
    *,
    error_code: str,
    failure_stage: FailureStage,
    retryable: bool,
    provider_error: OllamaError | None = None,
) -> tuple[dict[str, bytes], list[ArtifactReference]]:
    specifications: list[tuple[str, str, str, str, bytes]] = []
    if provider_error is not None:
        specifications.append(
            (
                "provider-request",
                "provider-request",
                "outputs/provider-request.json",
                "application/json",
                provider_error.request_body,
            )
        )
        if provider_error.response_body is not None:
            specifications.append(
                (
                    "provider-response-envelope",
                    "provider-response-envelope",
                    "outputs/provider-response.json",
                    "application/json",
                    provider_error.response_body,
                )
            )
    error_content = _canonical_json_bytes(
        {
            "schema_version": "1.0.0",
            "error_code": error_code,
            "failure_stage": failure_stage.value,
            "retryable": retryable,
        }
    )
    specifications.append(
        (
            "execution-error",
            "execution-error",
            "outputs/error.json",
            "application/json",
            error_content,
        )
    )
    artifacts: dict[str, bytes] = {}
    references: list[ArtifactReference] = []
    for artifact_id, kind, relative_path, media_type, content in specifications:
        artifacts[relative_path] = content
        references.append(
            _reference(
                artifact_id=artifact_id,
                role=ArtifactRole.OUTPUT,
                kind=kind,
                relative_path=relative_path,
                media_type=media_type,
                content=content,
            )
        )
    return artifacts, references


def _merge_artifact_groups(
    groups: Iterable[tuple[Mapping[str, bytes], Sequence[ArtifactReference]]],
) -> tuple[dict[str, bytes], tuple[ArtifactReference, ...]]:
    artifacts: dict[str, bytes] = {}
    references: list[ArtifactReference] = []
    for contents, group_references in groups:
        overlapping = sorted(set(artifacts) & set(contents))
        if overlapping:
            raise ValueError(f"duplicate artifact paths: {overlapping}")
        artifacts.update(contents)
        references.extend(group_references)
    return artifacts, tuple(references)


def _generated_failure_artifacts(
    generated: OllamaGenerateResult,
    *,
    error_code: str,
    failure_stage: FailureStage,
) -> tuple[dict[str, bytes], list[ArtifactReference]]:
    raw_response = generated.generated_text.encode("utf-8")
    output_specifications = (
        (
            "provider-request",
            "provider-request",
            "outputs/provider-request.json",
            "application/json",
            generated.request_body,
        ),
        (
            "provider-response-envelope",
            "provider-response-envelope",
            "outputs/provider-response.json",
            "application/json",
            generated.response_body,
        ),
        (
            "raw-model-response",
            "raw-model-response",
            RawResponseStore.response_relative_path,
            "text/plain; charset=utf-8",
            raw_response,
        ),
    )
    artifacts: dict[str, bytes] = {}
    references: list[ArtifactReference] = []
    for artifact_id, kind, relative_path, media_type, content in output_specifications:
        artifacts[relative_path] = content
        references.append(
            _reference(
                artifact_id=artifact_id,
                role=ArtifactRole.OUTPUT,
                kind=kind,
                relative_path=relative_path,
                media_type=media_type,
                content=content,
            )
        )
    error_group = _error_artifacts(
        error_code=error_code,
        failure_stage=failure_stage,
        retryable=False,
    )
    error_contents, error_references = error_group
    artifacts.update(error_contents)
    references.extend(error_references)
    return artifacts, references


def _provider_response(
    generated: OllamaGenerateResult,
    *,
    received_at: datetime,
) -> ProviderResponse:
    if generated.token_usage is None:
        token_usage = None
        missing_reason = MissingValueReason.PROVIDER_NOT_REPORTED
    else:
        token_usage = TokenUsage(
            input_tokens=generated.token_usage.input_tokens,
            output_tokens=generated.token_usage.output_tokens,
            total_tokens=generated.token_usage.total_tokens,
        )
        missing_reason = None
    return ProviderResponse(
        provider_request_id=None,
        received_at=received_at,
        latency_ms=generated.latency_ms,
        token_usage=token_usage,
        token_usage_missing_reason=missing_reason,
        estimated_token_usage=None,
        raw_response_artifact_id="raw-model-response",
    )


def _build_record(
    *,
    run_id: UUID,
    trace_id: str,
    git: GitRevision,
    environment: ExecutionEnvironment,
    config: ExperimentConfig,
    fixture: FixtureConfig,
    plan: PilotPlanDocument,
    cell: PilotCell,
    captured: CapturedInputs,
    model_request: OllamaGenerateRequest,
    model_info: OllamaModelInfo,
    artifacts: tuple[ArtifactReference, ...],
    started_at: datetime,
    completed_at: datetime,
    recorded_at: datetime,
    generated: OllamaGenerateResult | None = None,
    provider_received_at: datetime | None = None,
    evaluation: StoredEvaluationArtifacts | None = None,
    failure_stage: FailureStage | None = None,
    error_code: str | None = None,
    retryable: bool | None = None,
) -> RunRecord:
    input_artifact_ids = tuple(
        reference.artifact_id
        for reference in artifacts
        if reference.role is ArtifactRole.INPUT
    )
    if not input_artifact_ids:
        raise ValueError("pilot record requires captured input artifacts")
    capture_settings = _PILOT_CAPTURE_SETTINGS

    if failure_stage is None:
        status = ExecutionStatus.COMPLETED
        execution_failure_stage = None
        execution_error_code = None
        execution_retryable = None
        error_artifact_id = None
    else:
        status = ExecutionStatus.FAILED
        execution_failure_stage = failure_stage
        execution_error_code = error_code
        execution_retryable = retryable
        error_artifact_id = "execution-error"

    provider_response = None
    if generated is not None:
        if provider_received_at is None:
            raise ValueError("generated responses require received_at")
        provider_response = _provider_response(
            generated,
            received_at=provider_received_at,
        )

    return RunRecord(
        schema_version=CURRENT_SCHEMA_VERSION,
        run_id=run_id,
        trace_id=trace_id,
        recorded_at=recorded_at,
        git=git,
        environment=environment,
        experiment=ExperimentDefinition(
            experiment_id=config.experiment_id,
            version=config.experiment_version,
            source_path="config.yaml",
            definition_artifact_id="experiment-definition",
        ),
        task_id=cell.task_id,
        fixture=FixtureDefinition(
            fixture_id=fixture.fixture_id,
            version=fixture.fixture_version,
            source_path=fixture.source_path,
            definition_artifact_id="fixture-definition",
        ),
        model=ModelConfiguration(
            provider=plan.provider,
            model_id=plan.model_id,
            model_digest=model_info.digest,
            model_digest_missing_reason=(
                None
                if model_info.digest is not None
                else MissingValueReason.PROVIDER_NOT_REPORTED
            ),
            quantization=model_info.quantization,
            quantization_missing_reason=(
                None
                if model_info.quantization is not None
                else MissingValueReason.PROVIDER_NOT_REPORTED
            ),
            configuration_artifact_id="model-configuration",
            generation_parameters=ollama_generation_parameters(model_request),
            configuration_sanitization=ConfigurationSanitization(),
        ),
        prompt=PromptReference(
            prompt_id=config.prompt.prompt_id,
            version=config.prompt.prompt_version,
            source_path=config.prompt.source_path,
            prompt_artifact_id="prompt",
        ),
        ground_truth=GroundTruthReference(
            version=config.ground_truth.ground_truth_version,
            source_path=config.ground_truth.source_path,
            artifact_id="ground-truth",
        ),
        input_collection=InputCollectionResult(
            collected_at=captured.manifest.captured_at,
            representation=cell.input_representation,
            browser_context=BrowserContext(
                viewport_width=capture_settings.viewport_width,
                viewport_height=capture_settings.viewport_height,
                device_scale_factor=capture_settings.device_scale_factor,
                locale=capture_settings.locale,
                timezone=capture_settings.timezone,
                color_scheme=capture_settings.color_scheme,
                reduced_motion=capture_settings.reduced_motion,
            ),
            artifact_ids=input_artifact_ids,
        ),
        provider_response=provider_response,
        normalized_response=(
            NormalizedResponse(artifact_id="normalized-response")
            if evaluation is not None
            else None
        ),
        score=(
            ScoreResult(
                scorer_id=config.scorer.scorer_id,
                scorer_version=config.scorer.scorer_version,
                artifact_id="score-result",
            )
            if evaluation is not None
            else None
        ),
        execution=ExecutionResult(
            status=status,
            started_at=started_at,
            completed_at=completed_at,
            repetition_index=cell.repetition_index,
            repetition_count=len(plan.repetitions),
            seed=SeedMetadata(
                provider_parameter="seed",
                requested_value=cell.seed,
                reported_value=None,
                support_status=SeedSupportStatus.SUPPORTED,
            ),
            failure_stage=execution_failure_stage,
            error_code=execution_error_code,
            retryable=execution_retryable,
            error_artifact_id=error_artifact_id,
        ),
        artifacts=artifacts,
    )


def _record_input_sizes(run: ExperimentRun, captured: CapturedInputs) -> None:
    run.record_input_size(
        "html",
        len(captured.artifacts["inputs/dom-inner-html.html"]),
    )
    run.record_input_size(
        "aria",
        len(captured.artifacts["inputs/accessibility-tree.yml"]),
    )
    run.record_input_size(
        "image",
        len(captured.artifacts["inputs/screenshot.png"]),
    )


def _run_context(
    *,
    run_id: UUID,
    plan: PilotPlanDocument,
    config: ExperimentConfig,
    fixture: FixtureConfig,
    cell: PilotCell,
) -> RunTelemetryContext:
    return RunTelemetryContext(
        run_id=run_id,
        experiment_id=config.experiment_id,
        experiment_version=config.experiment_version,
        fixture_id=fixture.fixture_id,
        fixture_version=fixture.fixture_version,
        task_id=cell.task_id,
        input_representation=cell.input_representation,
        provider=plan.provider,
        model_id=plan.model_id,
        prompt_version=config.prompt.prompt_version,
        scorer_id=config.scorer.scorer_id,
        scorer_version=config.scorer.scorer_version,
        repetition_index=cell.repetition_index,
    )


def _execute_cell(
    *,
    browser: Browser,
    adapter: OllamaAdapter,
    telemetry: TelemetryRuntime,
    request: PilotRunRequest,
    plan_content: bytes,
    plan: PilotPlanDocument,
    config_content: bytes,
    config: ExperimentConfig,
    experiment_root: Path,
    prompt_document: PromptDocument,
    ground_truth_content: bytes,
    model_info: OllamaModelInfo,
    git: GitRevision,
    environment: ExecutionEnvironment,
    cell: PilotCell,
    clock: Clock,
) -> PilotRunOutcome:
    fixture = _fixture_by_id(config, cell.fixture_id)
    run_id = new_run_id()
    result_tier = ResultTier(plan.result_tier)
    result_store = WriteOnceResultStore(
        request.repository_root,
        config.experiment_id,
        result_tier,
    )
    capture_store = CapturedInputStore(
        request.repository_root,
        config.experiment_id,
        result_tier,
    )
    raw_response_store = RawResponseStore(
        request.repository_root,
        config.experiment_id,
        result_tier,
    )
    context = _run_context(
        run_id=run_id,
        plan=plan,
        config=config,
        fixture=fixture,
        cell=cell,
    )
    capture_request = _capture_request(request, plan, fixture)
    browser_context = new_capture_context(browser, capture_request.settings)
    started_at = clock()
    try:
        with telemetry.start_run(context) as run:
            with run.stage("fixture.render"):
                captured = capture_fixture(
                    browser_context.new_page(),
                    capture_request,
                    clock=clock,
                )
            with run.stage("input.capture"):
                capture_store.save(run_id, captured)
                _record_input_sizes(run, captured)

            model_request = _model_request(
                plan,
                config,
                prompt_document,
                captured,
                cell,
            )
            source_group = _source_artifacts(
                experiment_root=experiment_root,
                config=config,
                fixture=fixture,
                config_content=config_content,
                plan_content=plan_content,
                result_tier=result_tier,
            )
            runtime_group = _runtime_artifacts(
                model_request=model_request,
                model_info=model_info,
                authorization=request.authorization,
            )
            capture_group = _capture_artifacts(captured)

            try:
                with run.stage("gen_ai.inference") as inference:
                    generated = adapter.generate(model_request)
                    if generated.token_usage is not None:
                        inference.set_token_usage(
                            input_tokens=generated.token_usage.input_tokens,
                            output_tokens=generated.token_usage.output_tokens,
                            response_model=generated.model_id,
                        )
            except OllamaError as error:
                completed_at = clock()
                output_group = _error_artifacts(
                    error_code=error.code.value,
                    failure_stage=FailureStage.PROVIDER,
                    retryable=error.retryable,
                    provider_error=error,
                )
                artifacts, references = _merge_artifact_groups(
                    (source_group, runtime_group, capture_group, output_group)
                )
                record = _build_record(
                    run_id=run_id,
                    trace_id=run.trace_id,
                    git=git,
                    environment=environment,
                    config=config,
                    fixture=fixture,
                    plan=plan,
                    cell=cell,
                    captured=captured,
                    model_request=model_request,
                    model_info=model_info,
                    artifacts=references,
                    started_at=started_at,
                    completed_at=completed_at,
                    recorded_at=clock(),
                    failure_stage=FailureStage.PROVIDER,
                    error_code=error.code.value,
                    retryable=error.retryable,
                )
                run_directory = run.persist_result(result_store, record, artifacts)
                return PilotRunOutcome(
                    cell=cell,
                    run_id=run_id,
                    status=record.execution.status,
                    run_directory=run_directory,
                    error_code=error.code.value,
                )

            provider_received_at = clock()
            raw_response = generated.generated_text.encode("utf-8")
            try:
                with run.stage("response.normalize"):
                    evaluation: StoredEvaluationArtifacts
                    if config.scorer.scorer_id == CONTROL_DISCOVERY_SCORER_ID:
                        if cell.task_id is None:
                            raise EvaluationError(
                                "target-control scoring requires a task-aware cell"
                            )
                        evaluation = save_and_evaluate_control_discovery_response(
                            raw_response,
                            store=raw_response_store,
                            run_id=run_id,
                            config=config,
                            ground_truth_content=ground_truth_content,
                            fixture_id=fixture.fixture_id,
                            task_id=cell.task_id,
                            input_representation=cell.input_representation,
                        )
                    elif config.scorer.scorer_id == GROUP_MEMBERSHIP_SCORER_ID:
                        if cell.task_id is None:
                            raise EvaluationError(
                                "group-membership scoring requires a task-aware cell"
                            )
                        evaluation = save_and_evaluate_group_membership_response(
                            raw_response,
                            store=raw_response_store,
                            run_id=run_id,
                            config=config,
                            ground_truth_content=ground_truth_content,
                            fixture_id=fixture.fixture_id,
                            task_id=cell.task_id,
                            input_representation=cell.input_representation,
                        )
                    else:
                        evaluation = save_and_evaluate_item_association_response(
                            raw_response,
                            store=raw_response_store,
                            run_id=run_id,
                            config=config,
                            ground_truth_content=ground_truth_content,
                            fixture_id=fixture.fixture_id,
                        )
            except EvaluationError:
                completed_at = clock()
                output_group = _generated_failure_artifacts(
                    generated,
                    error_code="evaluation-error",
                    failure_stage=FailureStage.NORMALIZATION,
                )
                artifacts, references = _merge_artifact_groups(
                    (source_group, runtime_group, capture_group, output_group)
                )
                record = _build_record(
                    run_id=run_id,
                    trace_id=run.trace_id,
                    git=git,
                    environment=environment,
                    config=config,
                    fixture=fixture,
                    plan=plan,
                    cell=cell,
                    captured=captured,
                    model_request=model_request,
                    model_info=model_info,
                    artifacts=references,
                    started_at=started_at,
                    completed_at=completed_at,
                    recorded_at=clock(),
                    generated=generated,
                    provider_received_at=provider_received_at,
                    failure_stage=FailureStage.NORMALIZATION,
                    error_code="evaluation-error",
                    retryable=False,
                )
                run_directory = run.persist_result(result_store, record, artifacts)
                return PilotRunOutcome(
                    cell=cell,
                    run_id=run_id,
                    status=record.execution.status,
                    run_directory=run_directory,
                    error_code="evaluation-error",
                )

            with run.stage("evaluation.score"):
                run.record_evaluation(evaluation.score)
            completed_at = clock()
            output_group = _provider_artifacts(generated, evaluation)
            artifacts, references = _merge_artifact_groups(
                (source_group, runtime_group, capture_group, output_group)
            )
            record = _build_record(
                run_id=run_id,
                trace_id=run.trace_id,
                git=git,
                environment=environment,
                config=config,
                fixture=fixture,
                plan=plan,
                cell=cell,
                captured=captured,
                model_request=model_request,
                model_info=model_info,
                artifacts=references,
                started_at=started_at,
                completed_at=completed_at,
                recorded_at=clock(),
                generated=generated,
                provider_received_at=provider_received_at,
                evaluation=evaluation,
            )
            run_directory = run.persist_result(result_store, record, artifacts)
            return PilotRunOutcome(
                cell=cell,
                run_id=run_id,
                status=record.execution.status,
                run_directory=run_directory,
            )
    finally:
        browser_context.close()


def execute_pilot(
    request: PilotRunRequest,
    *,
    clock: Clock = lambda: datetime.now(UTC),
) -> PilotRunSummary:
    """Run selected pilot repetitions with no retries or paid providers."""

    plan_content, plan, config_content, config, experiment_root = _load_plan_and_config(
        request
    )
    _assert_timeout_matches_plan(request.ollama_timeout_seconds, plan)
    cells = _selected_cells(plan, config, request.repetition_indices)
    result_root = experiment_root / "results" / plan.result_tier
    _reject_existing_cells(result_root, cells)

    prompt_content = (experiment_root / config.prompt.source_path).read_bytes()
    prompt_document = validate_prompt_matches_config(prompt_content, config)
    ground_truth_content = (
        experiment_root / config.ground_truth.source_path
    ).read_bytes()
    git = collect_git_revision(request.repository_root)
    container_images = collect_compose_container_images(
        request.repository_root / "observability" / "otel" / "compose.yaml"
    )
    telemetry_settings = TelemetrySettings(export_otlp=True)
    collector_ready = wait_for_collector(
        telemetry_settings.collector_health_url,
        startup_timeout_ms=telemetry_settings.collector_startup_timeout_ms,
        check_interval_ms=telemetry_settings.collector_check_interval_ms,
        request_timeout_ms=telemetry_settings.collector_request_timeout_ms,
    )
    if not collector_ready:
        raise MetadataCollectionError(
            "OpenTelemetry Collector did not become ready before pilot execution"
        )
    if plan.execution_backend == "cpu":
        if not request.confirm_cpu_only_ollama:
            raise MetadataCollectionError(
                "pilot execution requires explicit confirmation that Ollama GPU "
                "acceleration is disabled"
            )
        gpus: tuple[GpuRuntime, ...] = ()
    else:
        if plan.approved_gpu is None:
            raise MetadataCollectionError(
                "remote NVIDIA GPU pilot requires approved GPU metadata"
            )
        gpus = (
            GpuRuntime(
                vendor=plan.approved_gpu.vendor,
                model=plan.approved_gpu.model,
                driver_version=plan.approved_gpu.driver_version,
                memory_mib=plan.approved_gpu.memory_mib,
            ),
        )

    adapter_config = OllamaClientConfig(
        endpoint=request.ollama_endpoint,
        timeout_seconds=request.ollama_timeout_seconds,
    )
    with OllamaAdapter(adapter_config) as adapter:
        model_info = adapter.inspect_model(plan.model_id)
        _assert_model_matches_plan(model_info, plan)

        telemetry_settings = telemetry_settings.model_copy(
            update={"wait_for_collector_ready": False}
        )
        with create_otlp_telemetry(telemetry_settings) as telemetry:
            with sync_playwright() as playwright:
                browser = playwright.chromium.launch()
                try:
                    _preflight_fixtures(browser, request, plan, config)
                    environment = collect_execution_environment(
                        BrowserRuntime(
                            name="chromium",
                            version=browser.version,
                            revision=_browser_revision(
                                playwright.chromium.executable_path
                            ),
                        ),
                        container_images=container_images,
                        gpus=gpus,
                    )
                    outcomes = tuple(
                        _execute_cell(
                            browser=browser,
                            adapter=adapter,
                            telemetry=telemetry,
                            request=request,
                            plan_content=plan_content,
                            plan=plan,
                            config_content=config_content,
                            config=config,
                            experiment_root=experiment_root,
                            prompt_document=prompt_document,
                            ground_truth_content=ground_truth_content,
                            model_info=model_info,
                            git=git,
                            environment=environment,
                            cell=cell,
                            clock=clock,
                        )
                        for cell in cells
                    )
                finally:
                    browser.close()
    return PilotRunSummary(
        experiment_id=plan.experiment_id,
        experiment_version=plan.experiment_version,
        plan_sha256=_sha256(plan_content),
        outcomes=outcomes,
    )

