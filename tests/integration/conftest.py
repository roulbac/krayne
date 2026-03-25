from __future__ import annotations

import pytest

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
