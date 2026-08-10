from datetime import UTC, datetime, timedelta
from uuid import UUID

from kokochi_ui_agent_readability_lab.providers import (
    OllamaGenerateRequest,
    OllamaGenerationOptions,
    ollama_generation_parameters,
    ollama_model_configuration_bytes,
)
from kokochi_ui_agent_readability_lab.records import (
    CURRENT_SCHEMA_VERSION,
    ArtifactRole,
    BrowserContext,
    BrowserRuntime,
    ColorScheme,
    ConfigurationSanitization,
    ContainerImage,
    CpuRuntime,
    ExecutionEnvironment,
    ExecutionResult,
    ExecutionStatus,
    ExperimentDefinition,
    FixtureDefinition,
    GitRevision,
    GroundTruthReference,
    HardwareRuntime,
    InputCollectionResult,
    ModelConfiguration,
    NormalizedResponse,
    OperatingSystem,
    PromptReference,
    ProviderResponse,
    ReducedMotion,
    RunRecord,
    ScoreResult,
    SeedMetadata,
    SeedSupportStatus,
    TokenUsage,
    artifact_reference,
)


RUN_ID = UUID("018f05d2-1ca3-7a4b-9c2d-123456789abc")
STARTED_AT = datetime(2026, 7, 30, 1, 2, 3, tzinfo=UTC)

OLLAMA_REQUEST = OllamaGenerateRequest(
    model_id="example-model:latest",
    prompt="Return the label for the email field.",
    options=OllamaGenerationOptions(
        temperature=0.0,
        top_p=0.9,
        top_k=40,
        repeat_penalty=1.1,
        num_ctx=4096,
        num_predict=256,
        seed=42,
    ),
    think=False,
    response_format="json",
)

EXPERIMENT_CONFIG = (
    b"config_schema_version: 1.0.0\n"
    b"experiment_id: label-clarity\n"
    b"experiment_version: v1\n"
    b"title: Label clarity evaluation\n"
    b"design:\n"
    b"  research_question: Can the agent identify the email field label?\n"
    b"  hypothesis: Semantic labels improve identification accuracy.\n"
    b"  independent_variable: Label implementation\n"
    b"  controlled_variables:\n"
    b"    - viewport and browser context\n"
    b"  model_inputs:\n"
    b"    - aria snapshot\n"
    b"  expected_output_schema:\n"
    b"    type: object\n"
    b"  scoring_method: Exact match against the expected label\n"
    b"  limitations:\n"
    b"    - Single synthetic form\n"
    b"execution:\n"
    b"  models:\n"
    b"    - provider: ollama\n"
    b'      model_id: "example-model:latest"\n'
    b"  repetitions: 3\n"
    b"  randomness_controls: Seed 42 requested through the seed parameter\n"
    b"  expected_api_cost: JPY 0 using a local model\n"
    b"fixtures:\n"
    b"  - fixture_id: semantic-form\n"
    b"    fixture_version: v1\n"
    b"    source_path: fixtures/semantic-form.tsx\n"
    b"    condition: semantic\n"
    b"    independent_variable_value: semantic-label\n"
    b"prompt:\n"
    b"  prompt_id: field-label\n"
    b"  prompt_version: v1\n"
    b"  source_path: prompts/v1.md\n"
    b"ground_truth:\n"
    b"  ground_truth_version: v1\n"
    b"  source_path: ground-truth/v1.json\n"
    b"scorer:\n"
    b"  scorer_id: exact-match\n"
    b"  scorer_version: v1\n"
)

ARTIFACT_CONTENTS = {
    "sources/config.yaml": EXPERIMENT_CONFIG,
    "sources/fixtures/semantic-form.tsx": (
        b"export function SemanticForm() { return null; }\n"
    ),
    "sources/prompts/v1.md": b"Return the label for the email field.\n",
    "sources/ground-truth/v1.json": (b'{"expected_label":"Email address"}\n'),
    "config/model.json": ollama_model_configuration_bytes(OLLAMA_REQUEST),
    "inputs/aria-snapshot.yml": b'- textbox "Email address"\n',
    "outputs/provider-response.json": b'{"response":"Email address"}\n',
    "outputs/normalized-response.json": b'{"label":"Email address"}\n',
    "outputs/score.json": b'{"correct":true,"score":1.0}\n',
}

ARTIFACT_SPECS = (
    (
        "experiment-definition",
        ArtifactRole.CONFIGURATION,
        "experiment-definition",
        "sources/config.yaml",
        "application/yaml",
    ),
    (
        "fixture-definition",
        ArtifactRole.CONFIGURATION,
        "fixture-definition",
        "sources/fixtures/semantic-form.tsx",
        "text/typescript",
    ),
    (
        "model-configuration",
        ArtifactRole.CONFIGURATION,
        "model-configuration",
        "config/model.json",
        "application/json",
    ),
    (
        "prompt",
        ArtifactRole.CONFIGURATION,
        "prompt",
        "sources/prompts/v1.md",
        "text/markdown",
    ),
    (
        "ground-truth",
        ArtifactRole.CONFIGURATION,
        "ground-truth",
        "sources/ground-truth/v1.json",
        "application/json",
    ),
    (
        "collected-input",
        ArtifactRole.INPUT,
        "aria-snapshot",
        "inputs/aria-snapshot.yml",
        "application/yaml",
    ),
    (
        "raw-provider-response",
        ArtifactRole.OUTPUT,
        "provider-response",
        "outputs/provider-response.json",
        "application/json",
    ),
    (
        "normalized-response",
        ArtifactRole.OUTPUT,
        "normalized-response",
        "outputs/normalized-response.json",
        "application/json",
    ),
    (
        "score-result",
        ArtifactRole.OUTPUT,
        "score-result",
        "outputs/score.json",
        "application/json",
    ),
)

EXPERIMENT_SOURCE_CONTENTS = {
    "config.yaml": ARTIFACT_CONTENTS["sources/config.yaml"],
    "fixtures/semantic-form.tsx": ARTIFACT_CONTENTS[
        "sources/fixtures/semantic-form.tsx"
    ],
    "prompts/v1.md": ARTIFACT_CONTENTS["sources/prompts/v1.md"],
    "ground-truth/v1.json": ARTIFACT_CONTENTS["sources/ground-truth/v1.json"],
}


def build_record(
    *,
    run_id: UUID = RUN_ID,
    trace_id: str = "0123456789abcdef0123456789abcdef",
) -> RunRecord:
    artifacts = tuple(
        artifact_reference(
            artifact_id=artifact_id,
            role=role,
            kind=kind,
            relative_path=relative_path,
            media_type=media_type,
            content=ARTIFACT_CONTENTS[relative_path],
        )
        for artifact_id, role, kind, relative_path, media_type in ARTIFACT_SPECS
    )

    return RunRecord(
        schema_version=CURRENT_SCHEMA_VERSION,
        run_id=run_id,
        trace_id=trace_id,
        recorded_at=STARTED_AT + timedelta(seconds=3),
        git=GitRevision(
            commit_sha="d932cf7d932cf7d932cf7d932cf7d932cf7d932c",
            is_dirty=False,
        ),
        environment=ExecutionEnvironment(
            operating_system=OperatingSystem(
                name="Ubuntu",
                version="24.04",
                kernel_name="Linux",
                kernel_release="6.6.87.2-microsoft-standard-WSL2",
                kernel_version="#1 SMP PREEMPT_DYNAMIC",
            ),
            python_version="3.13.5",
            uv_version="uv 0.8.4",
            node_version="v24.18.0",
            pnpm_version="11.15.1",
            playwright_version="1.55.0",
            docker_version="Docker version 28.3.2, build 578ccf6",
            docker_compose_version="Docker Compose version v2.38.2",
            hardware=HardwareRuntime(
                cpu=CpuRuntime(
                    architecture="x86_64",
                    model="Example CPU",
                    logical_core_count=8,
                )
            ),
            browser=BrowserRuntime(
                name="chromium",
                version="140.0.7339.16",
                revision="1187",
            ),
            container_images=(
                ContainerImage(
                    service="lgtm",
                    component_version="0.27.1",
                    image_reference="grafana/otel-lgtm:0.27.1",
                    image_digest="sha256:" + "a" * 64,
                    repository_digests=("grafana/otel-lgtm@sha256:" + "b" * 64,),
                ),
                ContainerImage(
                    service="otel-collector",
                    component_version="0.156.0-healthcheck-v1",
                    image_reference=(
                        "kokochi-lab/otel-collector:0.156.0-healthcheck-v1"
                    ),
                    image_digest="sha256:" + "c" * 64,
                ),
            ),
        ),
        experiment=ExperimentDefinition(
            experiment_id="label-clarity",
            version="v1",
            source_path="config.yaml",
            definition_artifact_id="experiment-definition",
        ),
        fixture=FixtureDefinition(
            fixture_id="semantic-form",
            version="v1",
            source_path="fixtures/semantic-form.tsx",
            definition_artifact_id="fixture-definition",
        ),
        model=ModelConfiguration(
            provider="ollama",
            model_id="example-model:latest",
            model_digest="sha256:example",
            quantization="Q4_K_M",
            configuration_artifact_id="model-configuration",
            generation_parameters=ollama_generation_parameters(OLLAMA_REQUEST),
            configuration_sanitization=ConfigurationSanitization(),
        ),
        prompt=PromptReference(
            prompt_id="field-label",
            version="v1",
            source_path="prompts/v1.md",
            prompt_artifact_id="prompt",
        ),
        ground_truth=GroundTruthReference(
            version="v1",
            source_path="ground-truth/v1.json",
            artifact_id="ground-truth",
        ),
        input_collection=InputCollectionResult(
            collected_at=STARTED_AT,
            representation="aria-snapshot",
            browser_context=BrowserContext(
                viewport_width=1280,
                viewport_height=800,
                device_scale_factor=1.0,
                locale="en-US",
                timezone="UTC",
                color_scheme=ColorScheme.LIGHT,
                reduced_motion=ReducedMotion.REDUCE,
            ),
            artifact_ids=("collected-input",),
        ),
        provider_response=ProviderResponse(
            provider_request_id="provider-request-1",
            received_at=STARTED_AT + timedelta(seconds=2),
            latency_ms=1250.5,
            token_usage=TokenUsage(
                input_tokens=42,
                output_tokens=8,
                total_tokens=50,
            ),
            raw_response_artifact_id="raw-provider-response",
        ),
        normalized_response=NormalizedResponse(
            artifact_id="normalized-response",
        ),
        score=ScoreResult(
            scorer_id="exact-match",
            scorer_version="v1",
            artifact_id="score-result",
        ),
        execution=ExecutionResult(
            status=ExecutionStatus.COMPLETED,
            started_at=STARTED_AT,
            completed_at=STARTED_AT + timedelta(seconds=2),
            repetition_index=0,
            repetition_count=3,
            seed=SeedMetadata(
                provider_parameter="seed",
                requested_value=42,
                reported_value=42,
                support_status=SeedSupportStatus.SUPPORTED,
            ),
        ),
        artifacts=artifacts,
    )
