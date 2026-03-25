from __future__ import annotations

import json
import subprocess

import pytest


def _resolve_docker_host() -> str | None:
    """Read the Docker socket URI from the active ``docker`` CLI context."""
    try:
        raw = subprocess.run(
            ["docker", "context", "inspect"],
            capture_output=True, text=True, check=True,
        )
        ctx = json.loads(raw.stdout)
        return ctx[0]["Endpoints"]["docker"]["Host"] or None
    except (subprocess.CalledProcessError, KeyError, IndexError, json.JSONDecodeError):
        return None


@pytest.fixture(scope="session", autouse=True)
def _docker_env(tmp_path_factory: pytest.TempPathFactory) -> None:
    """Expose the Docker socket to the Python Docker SDK and testcontainers.

    Many Docker distributions (Rancher Desktop, Colima, etc.) route through a
    non-default socket that the CLI resolves via contexts.  The Python SDK and
    testcontainers do not read Docker contexts, so we bridge the gap here.
    """
    mp = pytest.MonkeyPatch()

    docker_host = _resolve_docker_host()
    if docker_host:
        mp.setenv("DOCKER_HOST", docker_host)

    # The Reaper (Ryuk) container mounts the Docker socket.  On macOS the host
    # socket path (e.g. ~/.rd/docker.sock) doesn't exist inside the VM —
    # /var/run/docker.sock does.
    mp.setenv("TESTCONTAINERS_DOCKER_SOCKET_OVERRIDE", "/var/run/docker.sock")

    yield  # type: ignore[misc]
    mp.undo()
