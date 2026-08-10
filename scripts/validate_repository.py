"""Run the shared repository validator from an uninstalled source checkout."""

from pathlib import Path
import sys


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPOSITORY_ROOT / "src"))

from kokochi_ui_agent_readability_lab.validation.repository import main  # noqa: E402


raise SystemExit(main([str(REPOSITORY_ROOT)]))
