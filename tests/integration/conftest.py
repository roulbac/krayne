from __future__ import annotations

import contextlib
import os
import signal

import pytest

from krayne import tunnel_state
from krayne.errors import SandboxNotFoundError
from krayne.kube.client import DefaultKubeClient
from krayne.sandbox.manager import setup_sandbox, teardown_sandbox


def _maybe_teardown_sandbox() -> None:
    """Tear down any existing sandbox."""
    try:
        teardown_sandbox()
    except SandboxNotFoundError:
        pass

@pytest.fixture(scope="session")
def sandbox_kubeconfig() -> str:
    """Tear down any existing sandbox, set up a fresh one, then tear down after tests."""
    _maybe_teardown_sandbox()
    kubeconfig = setup_sandbox()
    yield kubeconfig  # type: ignore[misc]
    teardown_sandbox()


@pytest.fixture(scope="session")
def kube_client(sandbox_kubeconfig: str) -> DefaultKubeClient:
    """Return a DefaultKubeClient connected to the sandbox cluster."""
    return DefaultKubeClient(kubeconfig=sandbox_kubeconfig)


@pytest.fixture(scope="session", autouse=True)
def _reap_leaked_managers():
    """Best-effort SIGKILL of any manager subprocesses left over after the run.

    Tests are expected to call ``stop_tunnels`` in their own teardowns; this
    is a belt-and-braces safety net so a flaky test doesn't leak a long-lived
    Python process listening on a host port.
    """
    yield
    tunnel_dir = tunnel_state.TUNNEL_DIR
    if not tunnel_dir.exists():
        return
    for state_file in tunnel_dir.glob("*/*.json"):
        try:
            cluster = state_file.stem
            namespace = state_file.parent.name
            state = tunnel_state.load_state(cluster, namespace)
        except Exception:
            continue
        if state is None or state.manager is None:
            continue
        with contextlib.suppress(OSError):
            os.kill(state.manager.pid, signal.SIGKILL)
