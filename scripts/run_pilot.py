"""Run the local-Ollama pilot CLI from an uninstalled source checkout."""

from pathlib import Path
import sys


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPOSITORY_ROOT / "src"))

from kokochi_ui_agent_readability_lab.pilot.cli import app  # noqa: E402


app()
