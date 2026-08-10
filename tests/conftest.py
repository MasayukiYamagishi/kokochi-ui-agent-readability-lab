from collections.abc import Callable, Iterator
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from io import BytesIO
from pathlib import Path
import re
import shutil
import socket
import subprocess
import tempfile
from threading import Thread
import time
from typing import BinaryIO
from urllib.error import URLError
from urllib.parse import urlsplit
from urllib.request import urlopen

import pytest


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
FIXTURE_APP_ROOT = REPOSITORY_ROOT / "apps" / "fixtures-web"
FIXTURE_DIST = FIXTURE_APP_ROOT / "dist"
VITE_CLI = FIXTURE_APP_ROOT / "node_modules" / "vite" / "bin" / "vite.js"
FIXTURE_IDENTIFIER_PATTERN = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")

type FixtureUrl = Callable[[str | None, str | None], str]


class FixtureRequestHandler(SimpleHTTPRequestHandler):
    """Serve built assets and fall back to the SPA for extensionless routes."""

    def send_head(self) -> BytesIO | BinaryIO | None:
        requested_path = Path(urlsplit(self.path).path)
        resolved_path = Path(self.translate_path(self.path))
        if not resolved_path.exists() and requested_path.suffix == "":
            original_path = self.path
            self.path = "/index.html"
            try:
                return super().send_head()
            finally:
                self.path = original_path

        return super().send_head()


def pytest_addoption(parser: pytest.Parser) -> None:
    parser.addoption(
        "--run-external",
        action="store_true",
        default=False,
        help="run tests that require external network access",
    )


def pytest_collection_modifyitems(
    config: pytest.Config, items: list[pytest.Item]
) -> None:
    if config.getoption("--run-external"):
        return

    skip_external = pytest.mark.skip(reason="requires --run-external")
    for item in items:
        if "external" in item.keywords:
            item.add_marker(skip_external)


@pytest.fixture(scope="session")
def fixture_base_url() -> Iterator[str]:
    pnpm = shutil.which("pnpm")
    if pnpm is None:
        pytest.fail("pnpm is required to build the local fixture")

    build = subprocess.run(
        [pnpm, "--filter", "fixtures-web", "build:test"],
        cwd=REPOSITORY_ROOT,
        capture_output=True,
        check=False,
        encoding="utf-8",
        errors="replace",
        text=True,
    )
    if build.returncode != 0:
        pytest.fail(
            "Failed to build the local fixture before browser tests:\n"
            f"{build.stdout}\n{build.stderr}"
        )

    if not (FIXTURE_DIST / "404.html").is_file():
        pytest.fail("Fixture build did not emit dist/404.html for static hosts")

    handler = partial(FixtureRequestHandler, directory=FIXTURE_DIST)
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    server_thread = Thread(target=server.serve_forever, daemon=True)
    server_thread.start()

    try:
        yield f"http://127.0.0.1:{server.server_port}"
    finally:
        server.shutdown()
        server.server_close()
        server_thread.join()


@pytest.fixture(scope="session")
def fixture_url(fixture_base_url: str) -> FixtureUrl:
    """Resolve catalog, experiment, and fixture URLs with validated identifiers."""

    return create_fixture_url(fixture_base_url)


def create_fixture_url(fixture_base_url: str) -> FixtureUrl:
    """Create a validated URL resolver for a running fixture host."""

    def resolve(
        experiment_id: str | None = None,
        fixture_id: str | None = None,
    ) -> str:
        if experiment_id is None:
            if fixture_id is not None:
                raise ValueError("fixture_id requires experiment_id")
            return f"{fixture_base_url}/experiments"

        if not FIXTURE_IDENTIFIER_PATTERN.fullmatch(experiment_id):
            raise ValueError(f"Invalid experiment identifier: {experiment_id}")
        experiment_url = f"{fixture_base_url}/experiments/{experiment_id}"

        if fixture_id is None:
            return experiment_url
        if not FIXTURE_IDENTIFIER_PATTERN.fullmatch(fixture_id):
            raise ValueError(f"Invalid fixture identifier: {fixture_id}")
        return f"{experiment_url}/{fixture_id}"

    return resolve


def run_vite_server(arguments: list[str], label: str) -> Iterator[str]:
    """Start a Vite server on an available local port and stop it on teardown."""

    node = shutil.which("node")
    if node is None:
        pytest.fail(f"node is required to start the fixture {label} server")
    if not VITE_CLI.is_file():
        pytest.fail(f"Vite is not installed for the fixture {label} server")

    with socket.socket() as available_port:
        available_port.bind(("127.0.0.1", 0))
        port = available_port.getsockname()[1]

    fixture_base_url = f"http://127.0.0.1:{port}"
    with tempfile.TemporaryFile(mode="w+", encoding="utf-8") as server_log:
        process = subprocess.Popen(
            [
                node,
                str(VITE_CLI),
                *arguments,
                "--host",
                "127.0.0.1",
                "--port",
                str(port),
                "--strictPort",
            ],
            cwd=FIXTURE_APP_ROOT,
            stdout=server_log,
            stderr=subprocess.STDOUT,
            text=True,
        )

        try:
            deadline = time.monotonic() + 15
            while time.monotonic() < deadline:
                if process.poll() is not None:
                    server_log.seek(0)
                    pytest.fail(
                        f"Fixture {label} server exited before becoming ready:\n"
                        f"{server_log.read()}"
                    )
                try:
                    with urlopen(
                        f"{fixture_base_url}/experiments", timeout=0.5
                    ) as response:
                        if response.status == 200:
                            break
                except (TimeoutError, URLError):
                    time.sleep(0.1)
            else:
                server_log.seek(0)
                pytest.fail(
                    f"Fixture {label} server did not become ready:\n{server_log.read()}"
                )

            yield fixture_base_url
        finally:
            if process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=10)


@pytest.fixture(scope="session")
def fixture_dev_base_url() -> Iterator[str]:
    """Start the normal Vite development server with its development fixtures."""

    yield from run_vite_server([], "development")


@pytest.fixture(scope="session")
def fixture_dev_url(fixture_dev_base_url: str) -> FixtureUrl:
    """Resolve fixture URLs against the normal Vite development server."""

    return create_fixture_url(fixture_dev_base_url)


@pytest.fixture(scope="session")
def fixture_preview_base_url(fixture_base_url: str) -> Iterator[str]:
    """Serve the optimized fixture-test build through Vite preview."""

    if not fixture_base_url:
        pytest.fail("The optimized fixture build is unavailable")
    yield from run_vite_server(["preview"], "preview")


@pytest.fixture(scope="session")
def fixture_preview_url(fixture_preview_base_url: str) -> FixtureUrl:
    """Resolve fixture URLs against the local production preview server."""

    return create_fixture_url(fixture_preview_base_url)
