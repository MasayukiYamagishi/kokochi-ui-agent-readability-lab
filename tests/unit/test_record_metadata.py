import subprocess

import kokochi_ui_agent_readability_lab.records.metadata as record_metadata
from kokochi_ui_agent_readability_lab.records import (
    BrowserRuntime,
    ContainerImage,
    collect_compose_container_images,
    collect_execution_environment,
    collect_git_revision,
    collect_nvidia_gpus,
    new_run_id,
)


CONTAINER_IMAGES = (
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
        image_reference="kokochi-lab/otel-collector:0.156.0-healthcheck-v1",
        image_digest="sha256:" + "c" * 64,
    ),
)


def command_runner(
    command: list[str],
    **_: object,
) -> subprocess.CompletedProcess[str]:
    outputs = {
        ("git", "rev-parse", "HEAD"): ("d932cf7d932cf7d932cf7d932cf7d932cf7d932c\n"),
        ("git", "status", "--porcelain", "--untracked-files=normal"): "",
        ("uv", "--version"): "uv 0.8.4\n",
        ("node", "--version"): "v24.18.0\n",
        ("pnpm", "--version"): "11.15.1\n",
        ("docker", "--version"): "Docker version 28.3.2, build 578ccf6\n",
        ("docker", "compose", "version"): "Docker Compose version v2.38.2\n",
    }
    return subprocess.CompletedProcess(
        args=command,
        returncode=0,
        stdout=outputs[tuple(command)],
        stderr="",
    )


def test_collect_git_revision_records_commit_and_dirty_state(tmp_path) -> None:
    revision = collect_git_revision(tmp_path, runner=command_runner)

    assert revision.commit_sha == "d932cf7d932cf7d932cf7d932cf7d932cf7d932c"
    assert revision.is_dirty is False


def test_command_output_resolves_a_platform_command_shim(monkeypatch) -> None:
    observed_commands: list[list[str]] = []

    monkeypatch.setattr(
        record_metadata.shutil,
        "which",
        lambda command: "C:/tools/pnpm.CMD" if command == "pnpm" else None,
    )

    def resolved_runner(
        command: list[str],
        **_: object,
    ) -> subprocess.CompletedProcess[str]:
        observed_commands.append(command)
        return subprocess.CompletedProcess(
            args=command,
            returncode=0,
            stdout="11.15.1\n",
            stderr="",
        )

    monkeypatch.setattr(record_metadata.subprocess, "run", resolved_runner)

    assert record_metadata._command_output(("pnpm", "--version")) == "11.15.1"
    assert observed_commands == [["C:/tools/pnpm.CMD", "--version"]]


def test_collect_execution_environment_records_required_versions() -> None:
    environment = collect_execution_environment(
        BrowserRuntime(
            name="chromium",
            version="140.0.7339.16",
            revision="1187",
        ),
        container_images=CONTAINER_IMAGES,
        runner=command_runner,
        package_version=lambda package: (
            "1.55.0" if package == "playwright" else "unexpected"
        ),
    )

    assert environment.python_version
    assert environment.uv_version == "uv 0.8.4"
    assert environment.node_version == "v24.18.0"
    assert environment.pnpm_version == "11.15.1"
    assert environment.playwright_version == "1.55.0"
    assert environment.browser.revision == "1187"
    assert environment.docker_version.startswith("Docker version 28.3.2")
    assert environment.docker_compose_version.endswith("v2.38.2")
    assert environment.hardware.cpu.architecture
    assert environment.hardware.cpu.model
    assert environment.hardware.cpu.logical_core_count >= 1
    assert environment.hardware.gpus == ()
    assert environment.operating_system.kernel_release
    assert environment.container_images == CONTAINER_IMAGES


def test_collect_compose_container_images_records_tags_and_actual_digests(
    tmp_path,
) -> None:
    collector_digest = "sha256:" + "a" * 64
    lgtm_digest = "sha256:" + "b" * 64
    lgtm_repository_digest = "grafana/otel-lgtm@sha256:" + "c" * 64
    compose_file = tmp_path / "compose.yaml"
    inspected_image_targets: list[str] = []

    def docker_runner(
        command: list[str],
        **_: object,
    ) -> subprocess.CompletedProcess[str]:
        if command[:2] == ["docker", "compose"] and "config" in command:
            stdout = (
                '{"services":{'
                '"otel-collector":{"image":"kokochi-lab/otel-collector:'
                '0.156.0-healthcheck-v1"},'
                '"lgtm":{"image":"grafana/otel-lgtm:0.27.1"}}}'
            )
        elif command[:2] == ["docker", "compose"] and "ps" in command:
            stdout = f"{command[-1]}-container\n"
        elif command[:3] == ["docker", "container", "inspect"]:
            image_id = (
                collector_digest
                if command[3] == "otel-collector-container"
                else lgtm_digest
            )
            stdout = '{"Image":"' + image_id + '"}'
        else:
            inspected_image_targets.append(command[3])
            if command[3] == collector_digest:
                stdout = '{"Id":"' + collector_digest + '","RepoDigests":null}'
            else:
                stdout = (
                    '{"Id":"'
                    + lgtm_digest
                    + '","RepoDigests":["'
                    + lgtm_repository_digest
                    + '"]}'
                )
        return subprocess.CompletedProcess(
            args=command,
            returncode=0,
            stdout=stdout,
            stderr="",
        )

    images = collect_compose_container_images(compose_file, runner=docker_runner)

    assert [image.service for image in images] == ["lgtm", "otel-collector"]
    assert images[0].image_reference == "grafana/otel-lgtm:0.27.1"
    assert images[0].component_version == "0.27.1"
    assert images[0].image_digest == lgtm_digest
    assert images[0].repository_digests == (lgtm_repository_digest,)
    assert images[1].image_digest == collector_digest
    assert images[1].component_version == "0.156.0-healthcheck-v1"
    assert images[1].repository_digests == ()
    assert inspected_image_targets == [lgtm_digest, collector_digest]


def test_collect_nvidia_gpus_records_model_driver_and_memory() -> None:
    def nvidia_runner(
        command: list[str],
        **_: object,
    ) -> subprocess.CompletedProcess[str]:
        assert command == [
            "nvidia-smi",
            "--query-gpu=name,driver_version,memory.total",
            "--format=csv,noheader,nounits",
        ]
        return subprocess.CompletedProcess(
            args=command,
            returncode=0,
            stdout="NVIDIA GeForce RTX 4090, 580.88, 24564\n",
            stderr="",
        )

    gpus = collect_nvidia_gpus(runner=nvidia_runner)

    assert len(gpus) == 1
    assert gpus[0].vendor == "NVIDIA"
    assert gpus[0].model == "NVIDIA GeForce RTX 4090"
    assert gpus[0].driver_version == "580.88"
    assert gpus[0].memory_mib == 24564


def test_collect_execution_environment_records_linux_distribution(
    monkeypatch,
) -> None:
    monkeypatch.setattr(record_metadata.platform, "system", lambda: "Linux")
    monkeypatch.setattr(record_metadata.platform, "release", lambda: "6.6.87.2")
    monkeypatch.setattr(
        record_metadata.platform,
        "version",
        lambda: "#1 SMP PREEMPT_DYNAMIC",
    )
    monkeypatch.setattr(
        record_metadata.platform,
        "freedesktop_os_release",
        lambda: {"NAME": "Ubuntu", "VERSION_ID": "24.04"},
    )

    environment = collect_execution_environment(
        BrowserRuntime(
            name="chromium",
            version="140.0.7339.16",
            revision="1187",
        ),
        container_images=CONTAINER_IMAGES,
        runner=command_runner,
        package_version=lambda _package: "1.55.0",
    )

    assert environment.operating_system.name == "Ubuntu"
    assert environment.operating_system.version == "24.04"
    assert environment.operating_system.kernel_name == "Linux"
    assert environment.operating_system.kernel_release == "6.6.87.2"


def test_new_run_id_returns_unique_uuids() -> None:
    first_run_id = new_run_id()
    second_run_id = new_run_id()

    assert first_run_id != second_run_id
    assert first_run_id.int != 0
    assert second_run_id.int != 0
