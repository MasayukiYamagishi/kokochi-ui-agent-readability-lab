from pathlib import Path

import yaml


REPOSITORY_ROOT = Path(__file__).parents[2]
OTEL_ROOT = REPOSITORY_ROOT / "observability" / "otel"


def load_yaml(name: str) -> dict[str, object]:
    return yaml.safe_load((OTEL_ROOT / name).read_text(encoding="utf-8"))


def test_collector_config_declares_resilient_three_signal_pipelines() -> None:
    config = load_yaml("collector-config.yaml")

    assert config["receivers"]["otlp"]["protocols"] == {
        "grpc": {"endpoint": "0.0.0.0:4317"},
        "http": {"endpoint": "0.0.0.0:4318"},
    }
    assert config["processors"]["memory_limiter"] == {
        "check_interval": "1s",
        "limit_mib": 192,
        "spike_limit_mib": 64,
    }
    exporter = config["exporters"]["otlp_grpc/lgtm"]
    assert exporter["timeout"] == "5s"
    assert exporter["sending_queue"] == {
        "enabled": True,
        "num_consumers": 2,
        "queue_size": 256,
        "sizer": "requests",
    }
    assert exporter["retry_on_failure"] == {
        "enabled": True,
        "initial_interval": "1s",
        "max_interval": "5s",
        "max_elapsed_time": "30s",
    }
    assert config["extensions"]["health_check"] == {
        "endpoint": "0.0.0.0:13133",
        "path": "/",
    }
    assert config["service"]["extensions"] == ["health_check"]

    for signal in ("traces", "metrics", "logs"):
        pipeline = config["service"]["pipelines"][signal]
        assert pipeline == {
            "receivers": ["otlp"],
            "processors": ["memory_limiter", "batch"],
            "exporters": ["otlp_grpc/lgtm"],
        }
    assert "debug" not in config["exporters"]


def test_debug_config_is_explicitly_opt_in() -> None:
    config = load_yaml("collector-config.debug.yaml")
    override = load_yaml("compose.debug.yaml")

    assert config["exporters"]["debug"] == {"verbosity": "basic"}
    for signal in ("traces", "metrics", "logs"):
        assert config["service"]["pipelines"][signal]["exporters"] == [
            "otlp_grpc/lgtm",
            "debug",
        ]
    assert override["services"]["otel-collector"]["command"] == [
        "--config=/etc/otelcol/config.debug.yaml"
    ]


def test_compose_pins_images_and_binds_observability_ports_to_loopback() -> None:
    compose = load_yaml("compose.yaml")
    services = compose["services"]

    assert services["lgtm"]["image"] == "grafana/otel-lgtm:0.27.1"
    assert (
        services["otel-collector"]["image"]
        == "kokochi-lab/otel-collector:0.156.0-healthcheck-v1"
    )
    assert services["otel-collector"]["healthcheck"]["test"] == [
        "CMD",
        "/collector-healthcheck",
        "http://127.0.0.1:13133/",
    ]
    assert services["otel-collector"]["depends_on"] == {
        "lgtm": {"condition": "service_healthy"}
    }
    assert compose["volumes"] == {"lgtm-data": None}

    published_ports = [
        port for service in services.values() for port in service.get("ports", ())
    ]
    assert published_ports
    assert all(str(port).startswith("127.0.0.1:") for port in published_ports)
