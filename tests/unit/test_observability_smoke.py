from importlib.util import module_from_spec, spec_from_file_location
import os
from pathlib import Path
from types import ModuleType

import pytest


REPOSITORY_ROOT = Path(__file__).parents[2]
SMOKE_TEST_PATH = REPOSITORY_ROOT / "observability" / "otel" / "smoke_test.py"


def load_smoke_test() -> ModuleType:
    spec = spec_from_file_location("observability_smoke_test", SMOKE_TEST_PATH)
    if spec is None or spec.loader is None:
        raise AssertionError("could not load observability smoke test")
    module = module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_local_otlp_environment_overrides_inherited_signal_endpoints(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    smoke_test = load_smoke_test()
    for signal in ("TRACES", "METRICS", "LOGS"):
        monkeypatch.setenv(
            f"OTEL_EXPORTER_OTLP_{signal}_ENDPOINT",
            "https://collector.example.invalid",
        )
        monkeypatch.setenv(f"OTEL_EXPORTER_OTLP_{signal}_PROTOCOL", "grpc")
    monkeypatch.setenv(
        "OTEL_EXPORTER_OTLP_ENDPOINT",
        "https://collector.example.invalid",
    )
    monkeypatch.setenv("OTEL_EXPORTER_OTLP_PROTOCOL", "grpc")
    monkeypatch.setenv("OTEL_EXPORTER_OTLP_HEADERS", "authorization=secret")
    monkeypatch.setenv("OTEL_EXPORTER_OTLP_COMPRESSION", "gzip")

    smoke_test.configure_local_otlp_environment()

    assert os.environ["OTEL_EXPORTER_OTLP_ENDPOINT"] == "http://127.0.0.1:4318"
    assert os.environ["OTEL_EXPORTER_OTLP_PROTOCOL"] == "http/protobuf"
    assert "OTEL_EXPORTER_OTLP_HEADERS" not in os.environ
    assert "OTEL_EXPORTER_OTLP_COMPRESSION" not in os.environ
    for signal, path in (
        ("TRACES", "traces"),
        ("METRICS", "metrics"),
        ("LOGS", "logs"),
    ):
        assert os.environ[f"OTEL_EXPORTER_OTLP_{signal}_ENDPOINT"] == (
            f"http://127.0.0.1:4318/v1/{path}"
        )
        assert os.environ[f"OTEL_EXPORTER_OTLP_{signal}_PROTOCOL"] == "http/protobuf"


def test_smoke_service_name_is_unique_for_each_run() -> None:
    smoke_test = load_smoke_test()

    first = smoke_test.smoke_service_name("run-one")
    second = smoke_test.smoke_service_name("run-two")

    assert first != second
    assert first.endswith("run-one")
    assert second.endswith("run-two")
