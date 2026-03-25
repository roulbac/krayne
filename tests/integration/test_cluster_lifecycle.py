"""Integration tests: full cluster lifecycle against a testcontainers K3S cluster with KubeRay.

Requirements:
  - Docker running
  - kubectl CLI available on PATH
  - ``uv sync``

Run with:
  pytest tests/integration -m integration --timeout=600
"""

from __future__ import annotations

import os
import subprocess
import tempfile
import time
from pathlib import Path
from typing import Generator

import pytest

pytestmark = pytest.mark.integration

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

K3S_IMAGE = "rancher/k3s:v1.35.2-k3s1"
HELM_IMAGE = "alpine/helm"
KUBERAY_NAMESPACE = "default"
KUBERAY_HELM_REPO = "https://ray-project.github.io/kuberay-helm"

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _run(cmd: list[str], **kwargs) -> subprocess.CompletedProcess:
    """Run a subprocess, raising on failure."""
    return subprocess.run(cmd, check=True, capture_output=True, text=True, **kwargs)


def _kubectl(kubeconfig: str, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["kubectl", "--kubeconfig", kubeconfig, *args],
        capture_output=True,
        text=True,
    )


def _helm(k3s_container_id: str, kubeconfig: str, *args: str) -> subprocess.CompletedProcess:
    """Run a helm command via the alpine/helm Docker image on the k3s container network."""
    return _run([
        "docker", "run", "--rm",
        "--network", f"container:{k3s_container_id}",
        "-v", f"{kubeconfig}:/root/.kube/config:ro",
        HELM_IMAGE,
        *args,
    ])


def _wait_for_crds(kubeconfig: str, timeout: int = 120) -> None:
    """Wait until the RayCluster CRD is registered."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if _kubectl(kubeconfig, "get", "crd", "rayclusters.ray.io").returncode == 0:
            return
        time.sleep(3)
    raise TimeoutError("RayCluster CRD not registered within timeout")


def _wait_for_deployment(
    name: str, kubeconfig: str, namespace: str = "default", timeout: int = 180
) -> None:
    """Wait until a deployment has at least one available replica."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        result = _kubectl(
            kubeconfig,
            "get", "deployment", name,
            "-n", namespace,
            "-o", "jsonpath={.status.availableReplicas}",
        )
        if result.returncode == 0 and result.stdout.strip() not in ("", "0", "null"):
            return
        time.sleep(5)
    raise TimeoutError(f"Deployment {name} not available within {timeout}s")


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(scope="session")
def k3s_cluster() -> Generator[str, None, None]:
    """Spin up a K3S container, install KubeRay via ``alpine/helm``, tear down after.

    Yields the path to a temporary kubeconfig file pointing at the K3S cluster.
    """
    from testcontainers.k3s import K3SContainer

    with K3SContainer(image=K3S_IMAGE, enable_cgroup_mount=False) as k3s:
        container_id = k3s.get_wrapped_container().id

        tmpdir = tempfile.mkdtemp(prefix="prism-integ-")
        host_kubeconfig = os.path.join(tmpdir, "kubeconfig")
        internal_kubeconfig = os.path.join(tmpdir, "kubeconfig-internal")

        with open(host_kubeconfig, "w") as f:
            f.write(k3s.config_yaml())

        raw = k3s.get_wrapped_container().exec_run(["cat", "/etc/rancher/k3s/k3s.yaml"])
        with open(internal_kubeconfig, "w") as f:
            f.write(raw.output.decode("utf-8"))

        try:
            _helm(
                container_id, internal_kubeconfig,
                "install", "kuberay-operator", "kuberay-operator",
                "--repo", KUBERAY_HELM_REPO,
                "--namespace", KUBERAY_NAMESPACE,
            )

            _wait_for_crds(host_kubeconfig)
            _wait_for_deployment("kuberay-operator", host_kubeconfig, KUBERAY_NAMESPACE, timeout=300)

            yield host_kubeconfig
        finally:
            for p in Path(tmpdir).iterdir():
                p.unlink()
            Path(tmpdir).rmdir()


@pytest.fixture()
def kube_client(k3s_cluster):
    """Return a DefaultKubeClient connected to the K3S cluster."""
    from prism.kube.client import DefaultKubeClient

    return DefaultKubeClient(kubeconfig=k3s_cluster)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestClusterLifecycle:
    """End-to-end lifecycle: create -> get -> list -> describe -> scale -> delete."""

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
