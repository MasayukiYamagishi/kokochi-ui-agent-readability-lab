"""Command-line interface for reviewing and running the local pilot."""

from __future__ import annotations

from datetime import datetime
import hashlib
from pathlib import Path

import typer

from kokochi_ui_agent_readability_lab.pilot.runner import (
    PilotAuthorization,
    PilotRunRequest,
    describe_pilot,
    execute_pilot,
)
from kokochi_ui_agent_readability_lab.records import parse_pilot_plan


app = typer.Typer(
    name="kokochi-pilot",
    no_args_is_help=True,
    help="Review or execute an explicitly authorized local-Ollama pilot.",
)

DEFAULT_PLAN_PATH = Path("experiments/form-group-membership-reconstruction/pilot-plan.yaml")


def _aware_datetime(value: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError as error:
        raise typer.BadParameter("must be an ISO 8601 datetime") from error
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise typer.BadParameter("must include an explicit UTC offset")
    return parsed


@app.command("plan")
def plan_command(
    repository_root: Path = typer.Option(Path("."), exists=True, file_okay=False),
    plan_path: Path = typer.Option(DEFAULT_PLAN_PATH, exists=True, dir_okay=False),
) -> None:
    """Print the preregistration gate and exact planned cells without inference."""

    description = describe_pilot(repository_root.resolve(), plan_path.resolve())
    typer.echo(description.model_dump_json(indent=2))


@app.command("run")
def run_command(
    authorization_record_url: str = typer.Option(
        ...,
        help="Repository issue comment URL containing explicit pilot authorization.",
    ),
    approved_by: str = typer.Option(...),
    authorized_at: str = typer.Option(
        ...,
        help="ISO 8601 timestamp of the authorization comment.",
    ),
    repetition: list[int] = typer.Option(
        ...,
        "--repetition",
        min=1,
        help="One-based pilot repetition to run; repeat the option as needed.",
    ),
    repository_root: Path = typer.Option(Path("."), exists=True, file_okay=False),
    plan_path: Path = typer.Option(DEFAULT_PLAN_PATH, exists=True, dir_okay=False),
    fixture_base_url: str = typer.Option("http://127.0.0.1:5173"),
    ollama_endpoint: str = typer.Option(
        "http://127.0.0.1:11434",
        envvar="OLLAMA_BASE_URL",
    ),
    confirm_cpu_only_ollama: bool = typer.Option(
        False,
        "--confirm-cpu-only-ollama",
        help="Confirm Ollama was started with GPU acceleration disabled.",
    ),
    ollama_timeout_seconds: float = typer.Option(120.0, min=0.001),
) -> None:
    """Execute only the selected, explicitly authorized pilot repetitions."""

    resolved_repository_root = repository_root.resolve()
    resolved_plan_path = plan_path.resolve()
    plan_content = resolved_plan_path.read_bytes()
    plan = parse_pilot_plan(plan_content)
    authorization = PilotAuthorization.model_validate(
        {
            "scope": f"local-ollama-{plan.result_tier}",
            "experiment_id": plan.experiment_id,
            "approved_by": approved_by,
            "authorized_at": _aware_datetime(authorized_at),
            "record_url": authorization_record_url,
            "pilot_plan_sha256": hashlib.sha256(plan_content).hexdigest(),
        }
    )
    request = PilotRunRequest(
        repository_root=resolved_repository_root,
        plan_path=resolved_plan_path,
        fixture_base_url=fixture_base_url,
        ollama_endpoint=ollama_endpoint,
        authorization=authorization,
        repetition_indices=tuple(value - 1 for value in repetition),
        confirm_cpu_only_ollama=confirm_cpu_only_ollama,
        ollama_timeout_seconds=ollama_timeout_seconds,
    )
    summary = execute_pilot(request)
    typer.echo(summary.model_dump_json(indent=2))
    if any(outcome.status.value == "failed" for outcome in summary.outcomes):
        raise typer.Exit(code=2)


if __name__ == "__main__":
    app()


