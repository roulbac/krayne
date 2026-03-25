"""Integration tests: full cluster lifecycle against a real k3d cluster with KubeRay.

Requirements:
  - Docker running
  - k3d CLI available on PATH
  - kubectl CLI available on PATH
  - helm CLI available on PATH
  - ``pip install testcontainers``

Run with:
  pytest tests/integration -m integration --timeout=600
"""

from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
import time
from pathlib import Path
from typing import Generator

import pytest

# Skip the entire module if Docker is not available
pytestmark = pytest.mark.integration

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

K3D_CLUSTER_NAME = "prism-integration-test"
KUBERAY_NAMESPACE = "default"
KUBERAY_HELM_REPO = "https://ray-project.github.io/kuberay-helm"


def _run(cmd: list[str], **kwargs) -> subprocess.CompletedProcess:
    """Run a subprocess, raising on failure."""
    return subprocess.run(cmd, check=True, capture_output=True, text=True, **kwargs)


def _cmd_exists(name: str) -> bool:
    return shutil.which(name) is not None


def _wait_for_crds(timeout: int = 120) -> None:
    """Wait until the RayCluster CRD is registered."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        result = subprocess.run(
            ["kubectl", "get", "crd", "rayclusters.ray.io"],
            capture_output=True,
            text=True,
        )
        if result.returncode == 0:
            return
        time.sleep(3)
    raise TimeoutError("RayCluster CRD not registered within timeout")


def _wait_for_deployment(name: str, namespace: str = "default", timeout: int = 180) -> None:
    """Wait until a deployment has at least one available replica."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        result = subprocess.run(
            [
                "kubectl", "get", "deployment", name,
                "-n", namespace,
                "-o", "jsonpath={.status.availableReplicas}",
            ],
            capture_output=True,
            text=True,
        )
        if result.returncode == 0 and result.stdout.strip() not in ("", "0", "null"):
            return
        time.sleep(5)
    raise TimeoutError(f"Deployment {name} not available within {timeout}s")


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(scope="session")
def k3d_cluster() -> Generator[str, None, None]:
    """Create a k3d cluster for the test session, install KubeRay, tear down after."""
    # Pre-flight checks
    for tool in ("docker", "k3d", "kubectl", "helm"):
        if not _cmd_exists(tool):
            pytest.skip(f"{tool} not found on PATH")

    # Check Docker is running
    result = subprocess.run(["docker", "info"], capture_output=True, text=True)
    if result.returncode != 0:
        pytest.skip("Docker is not running")

    # Create k3d cluster
    _run([
        "k3d", "cluster", "create", K3D_CLUSTER_NAME,
        "--agents", "1",
        "--wait",
        "--timeout", "120s",
        "--no-lb",
    ])

    try:
        # Merge kubeconfig so the kubernetes client can find it
        kubeconfig_path = os.environ.get("KUBECONFIG", str(Path.home() / ".kube" / "config"))
        _run(["k3d", "kubeconfig", "merge", K3D_CLUSTER_NAME, "--kubeconfig-merge-default"])

        # Install KubeRay operator via Helm
        _run(["helm", "repo", "add", "kuberay", KUBERAY_HELM_REPO])
        _run(["helm", "repo", "update"])
        _run([
            "helm", "install", "kuberay-operator", "kuberay/kuberay-operator",
            "--namespace", KUBERAY_NAMESPACE,
            "--wait",
            "--timeout", "180s",
        ])

        # Wait for the CRD and operator deployment to be available
        _wait_for_crds()
        _wait_for_deployment("kuberay-operator", KUBERAY_NAMESPACE)

        yield K3D_CLUSTER_NAME

    finally:
        # Tear down
        subprocess.run(
            ["k3d", "cluster", "delete", K3D_CLUSTER_NAME],
            capture_output=True,
            text=True,
        )


@pytest.fixture()
def kube_client(k3d_cluster):
    """Return a DefaultKubeClient connected to the k3d cluster."""
    from prism.kube.client import DefaultKubeClient

    return DefaultKubeClient()


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestClusterLifecycle:
    """End-to-end lifecycle: create → get → list → describe → scale → delete."""

    CLUSTER_NAME = "integ-test"
    NAMESPACE = "default"

    def test_full_lifecycle(self, kube_client):
        from prism.api import (
            create_cluster,
            delete_cluster,
            describe_cluster,
            get_cluster,
            list_clusters,
            scale_cluster,
        )
        from prism.config import ClusterConfig, HeadNodeConfig, WorkerGroupConfig

        # Use minimal resources so it runs on a tiny k3d cluster
        config = ClusterConfig(
            name=self.CLUSTER_NAME,
            namespace=self.NAMESPACE,
            head=HeadNodeConfig(cpus=1, memory="512Mi"),
            worker_groups=[
                WorkerGroupConfig(name="worker", replicas=1, cpus=1, memory="512Mi"),
            ],
        )

        try:
            # CREATE
            info = create_cluster(config, client=kube_client)
            assert info.name == self.CLUSTER_NAME
            assert info.namespace == self.NAMESPACE

            # GET
            info = get_cluster(self.CLUSTER_NAME, self.NAMESPACE, client=kube_client)
            assert info.name == self.CLUSTER_NAME

            # LIST
            clusters = list_clusters(self.NAMESPACE, client=kube_client)
            names = [c.name for c in clusters]
            assert self.CLUSTER_NAME in names

            # DESCRIBE
            details = describe_cluster(
                self.CLUSTER_NAME, self.NAMESPACE, client=kube_client
            )
            assert details.info.name == self.CLUSTER_NAME
            assert details.head.cpus == 1
            assert len(details.worker_groups) == 1

            # SCALE
            info = scale_cluster(
                self.CLUSTER_NAME,
                self.NAMESPACE,
                "worker",
                2,
                client=kube_client,
            )
            assert info.name == self.CLUSTER_NAME
            # Verify the patch took effect
            details = describe_cluster(
                self.CLUSTER_NAME, self.NAMESPACE, client=kube_client
            )
            assert details.worker_groups[0].replicas == 2

        finally:
            # DELETE (always clean up)
            delete_cluster(self.CLUSTER_NAME, self.NAMESPACE, client=kube_client)

        # Verify deletion
        from prism.errors import ClusterNotFoundError

        with pytest.raises(ClusterNotFoundError):
            get_cluster(self.CLUSTER_NAME, self.NAMESPACE, client=kube_client)
