"""Collection of reproducibility metadata for experiment records."""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
import csv
from importlib.metadata import version as distribution_version
import json
import os
from pathlib import Path
import platform
import shutil
import subprocess
from uuid import UUID, uuid4

from kokochi_ui_agent_readability_lab.records.schema import (
    BrowserRuntime,
    ContainerImage,
    CpuRuntime,
    ExecutionEnvironment,
    GitRevision,
    GpuRuntime,
    HardwareRuntime,
    OperatingSystem,
)


CommandRunner = Callable[..., subprocess.CompletedProcess[str]]


class MetadataCollectionError(RuntimeError):
    """Raised when required runtime metadata cannot be collected."""


def _resolve_command(
    command: Sequence[str],
    runner: CommandRunner | None,
) -> list[str]:
    arguments = list(command)
    if runner is not None:
        return arguments
    executable = shutil.which(arguments[0])
    if executable is None:
        raise MetadataCollectionError(f"command is not installed: {arguments[0]}")
    arguments[0] = executable
    return arguments


def _command_output(
    command: Sequence[str],
    *,
    cwd: Path | None = None,
    runner: CommandRunner | None = None,
) -> str:
    run = runner or subprocess.run
    result = run(
        _resolve_command(command, runner),
        cwd=cwd,
        capture_output=True,
        check=False,
        encoding="utf-8",
        errors="replace",
        text=True,
    )
    if result.returncode != 0:
        detail = result.stderr.strip() or result.stdout.strip() or "unknown error"
        raise MetadataCollectionError(f"{' '.join(command)} failed: {detail}")

    output = result.stdout.strip()
    if not output:
        raise MetadataCollectionError(f"{' '.join(command)} returned no output")
    return output


def collect_git_revision(
    repository_root: Path,
    *,
    runner: CommandRunner | None = None,
) -> GitRevision:
    """Collect the exact commit and whether uncommitted changes were present."""

    commit_sha = _command_output(
        ("git", "rev-parse", "HEAD"),
        cwd=repository_root,
        runner=runner,
    )
    status = _command_output_allow_empty(
        ("git", "status", "--porcelain", "--untracked-files=normal"),
        cwd=repository_root,
        runner=runner,
    )
    return GitRevision(commit_sha=commit_sha, is_dirty=bool(status))


def _command_output_allow_empty(
    command: Sequence[str],
    *,
    cwd: Path | None = None,
    runner: CommandRunner | None = None,
) -> str:
    run = runner or subprocess.run
    result = run(
        _resolve_command(command, runner),
        cwd=cwd,
        capture_output=True,
        check=False,
        encoding="utf-8",
        errors="replace",
        text=True,
    )
    if result.returncode != 0:
        detail = result.stderr.strip() or result.stdout.strip() or "unknown error"
        raise MetadataCollectionError(f"{' '.join(command)} failed: {detail}")
    return result.stdout.strip()


def collect_execution_environment(
    browser: BrowserRuntime,
    *,
    container_images: Sequence[ContainerImage],
    gpus: Sequence[GpuRuntime] = (),
    runner: CommandRunner | None = None,
    package_version: Callable[[str], str] = distribution_version,
) -> ExecutionEnvironment:
    """Collect host and tool versions; browser details come from the live session."""

    return ExecutionEnvironment(
        operating_system=_collect_operating_system(),
        python_version=platform.python_version(),
        uv_version=_command_output(("uv", "--version"), runner=runner),
        node_version=_command_output(("node", "--version"), runner=runner),
        pnpm_version=_command_output(("pnpm", "--version"), runner=runner),
        playwright_version=package_version("playwright"),
        docker_version=_command_output(("docker", "--version"), runner=runner),
        docker_compose_version=_command_output(
            ("docker", "compose", "version"),
            runner=runner,
        ),
        hardware=HardwareRuntime(cpu=_collect_cpu(), gpus=tuple(gpus)),
        browser=browser,
        container_images=tuple(container_images),
    )


def collect_nvidia_gpus(
    *,
    runner: CommandRunner | None = None,
) -> tuple[GpuRuntime, ...]:
    """Collect NVIDIA GPUs used by a run through the stable nvidia-smi CSV API."""

    output = _command_output(
        (
            "nvidia-smi",
            "--query-gpu=name,driver_version,memory.total",
            "--format=csv,noheader,nounits",
        ),
        runner=runner,
    )
    gpus: list[GpuRuntime] = []
    for index, row in enumerate(csv.reader(output.splitlines())):
        if len(row) != 3:
            raise MetadataCollectionError(
                f"nvidia-smi returned an invalid GPU row at index {index}"
            )
        model, driver_version, memory_mib = (value.strip() for value in row)
        try:
            parsed_memory = int(memory_mib)
        except ValueError as error:
            raise MetadataCollectionError(
                f"nvidia-smi returned invalid GPU memory at index {index}"
            ) from error
        gpus.append(
            GpuRuntime(
                vendor="NVIDIA",
                model=model,
                driver_version=driver_version,
                memory_mib=parsed_memory,
            )
        )
    if not gpus:
        raise MetadataCollectionError("nvidia-smi returned no GPUs")
    return tuple(gpus)


def collect_compose_container_images(
    compose_file: Path,
    *,
    runner: CommandRunner | None = None,
) -> tuple[ContainerImage, ...]:
    """Record the images used by the currently running Compose containers."""

    resolved_compose_file = compose_file.resolve()
    raw_config = _command_output(
        (
            "docker",
            "compose",
            "-f",
            str(resolved_compose_file),
            "config",
            "--format",
            "json",
        ),
        cwd=resolved_compose_file.parent,
        runner=runner,
    )
    try:
        config = json.loads(raw_config)
        services = config["services"]
        references = {
            service: definition["image"] for service, definition in services.items()
        }
    except (KeyError, TypeError, json.JSONDecodeError) as error:
        raise MetadataCollectionError(
            "docker compose config did not return service image references"
        ) from error

    if not references or not all(
        isinstance(service, str) and isinstance(reference, str)
        for service, reference in references.items()
    ):
        raise MetadataCollectionError(
            "docker compose config returned invalid service image references"
        )

    images: list[ContainerImage] = []
    for service, reference in sorted(references.items()):
        raw_container_ids = _command_output(
            (
                "docker",
                "compose",
                "-f",
                str(resolved_compose_file),
                "ps",
                "-q",
                service,
            ),
            cwd=resolved_compose_file.parent,
            runner=runner,
        )
        container_ids = tuple(
            container_id.strip()
            for container_id in raw_container_ids.splitlines()
            if container_id.strip()
        )
        if len(container_ids) != 1:
            raise MetadataCollectionError(
                f"expected one running Compose container for service {service!r}, "
                f"found {len(container_ids)}"
            )

        raw_container = _command_output(
            (
                "docker",
                "container",
                "inspect",
                container_ids[0],
                "--format",
                "{{json .}}",
            ),
            runner=runner,
        )
        try:
            container = json.loads(raw_container)
            image_id = container["Image"]
        except (KeyError, TypeError, json.JSONDecodeError) as error:
            raise MetadataCollectionError(
                f"docker container inspect returned invalid metadata for {service!r}"
            ) from error
        if not isinstance(image_id, str):
            raise MetadataCollectionError(
                f"docker container inspect returned an invalid image ID for {service!r}"
            )

        raw_image = _command_output(
            ("docker", "image", "inspect", image_id, "--format", "{{json .}}"),
            runner=runner,
        )
        image = _container_image_from_inspection(
            service,
            reference,
            raw_image,
        )
        if image.image_digest != image_id:
            raise MetadataCollectionError(
                f"running container image changed while collecting service {service!r}"
            )
        images.append(image)
    return tuple(images)


def collect_container_images(
    image_references: Mapping[str, str],
    *,
    runner: CommandRunner | None = None,
) -> tuple[ContainerImage, ...]:
    """Inspect local Docker images and retain immutable content identities."""

    images: list[ContainerImage] = []
    for service, reference in sorted(image_references.items()):
        raw_inspection = _command_output(
            ("docker", "image", "inspect", reference, "--format", "{{json .}}"),
            runner=runner,
        )
        images.append(
            _container_image_from_inspection(service, reference, raw_inspection)
        )
    return tuple(images)


def _container_image_from_inspection(
    service: str,
    reference: str,
    raw_inspection: str,
) -> ContainerImage:
    try:
        inspection = json.loads(raw_inspection)
        image_digest = inspection["Id"]
        repository_digests = tuple(sorted(inspection.get("RepoDigests") or ()))
    except (KeyError, TypeError, json.JSONDecodeError) as error:
        raise MetadataCollectionError(
            f"docker image inspect returned invalid metadata for {reference!r}"
        ) from error

    try:
        return ContainerImage(
            service=service,
            component_version=_component_version_from_reference(reference),
            image_reference=reference,
            image_digest=image_digest,
            repository_digests=repository_digests,
        )
    except ValueError as error:
        raise MetadataCollectionError(
            f"docker image inspect returned invalid digests for {reference!r}"
        ) from error


def _component_version_from_reference(reference: str) -> str:
    reference_without_digest = reference.split("@", maxsplit=1)[0]
    final_segment = reference_without_digest.rsplit("/", maxsplit=1)[-1]
    if ":" not in final_segment:
        raise MetadataCollectionError(
            f"container image reference must include a version tag: {reference!r}"
        )
    version = final_segment.rsplit(":", maxsplit=1)[1]
    if not version or version == "latest":
        raise MetadataCollectionError(
            "container image reference must use an explicit non-latest version tag: "
            f"{reference!r}"
        )
    return version


def _collect_cpu() -> CpuRuntime:
    architecture = platform.machine() or "unknown-architecture"
    model = platform.processor().strip()
    if not model and platform.system() == "Linux":
        try:
            cpu_info = Path("/proc/cpuinfo").read_text(
                encoding="utf-8",
                errors="replace",
            )
        except OSError:
            cpu_info = ""
        for line in cpu_info.splitlines():
            if line.lower().startswith("model name") and ":" in line:
                model = line.split(":", maxsplit=1)[1].strip()
                break
    logical_core_count = os.cpu_count()
    if logical_core_count is None or logical_core_count < 1:
        raise MetadataCollectionError("could not determine the logical CPU count")
    return CpuRuntime(
        architecture=architecture,
        model=model or architecture,
        logical_core_count=logical_core_count,
    )


def _collect_operating_system() -> OperatingSystem:
    kernel_name = platform.system()
    kernel_release = platform.release()
    kernel_version = platform.version()
    name = kernel_name
    version = kernel_version

    if kernel_name == "Linux":
        try:
            os_release = platform.freedesktop_os_release()
        except OSError:
            os_release = {}
        name = os_release.get("NAME") or kernel_name
        version = (
            os_release.get("VERSION_ID") or os_release.get("VERSION") or kernel_version
        )

    return OperatingSystem(
        name=name,
        version=version,
        kernel_name=kernel_name,
        kernel_release=kernel_release,
        kernel_version=kernel_version,
    )


def new_run_id() -> UUID:
    """Create a run identifier; OpenTelemetry owns the trace identifier."""

    return uuid4()
