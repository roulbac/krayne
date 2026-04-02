"""Integration tests: full cluster lifecycle against the prism sandbox (local k3s + KubeRay).

Requirements:
  - Docker running
  - kubectl CLI available on PATH
  - ``uv sync``

Run with:
  pytest tests/integration -m integration --timeout=600
"""

from __future__ import annotations

import pytest

from prism.api import (
    create_cluster,
    delete_cluster,
    describe_cluster,
    get_cluster,
    list_clusters,
    scale_cluster,
)
from prism.config import ClusterConfig
from prism.config.models import HeadNodeConfig, WorkerGroupConfig
from prism.errors import ClusterNotFoundError

pytestmark = pytest.mark.integration

_POLL_INTERVAL = 3


def _wait_for_deleted(name: str, namespace: str, client, timeout: int = 60) -> None:
    """Poll until the cluster no longer exists."""
    import time

    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            get_cluster(name, namespace, client=client)
        except ClusterNotFoundError:
            return
        time.sleep(_POLL_INTERVAL)


class TestClusterLifecycle:
    """End-to-end lifecycle: create -> get -> list -> describe -> scale -> delete."""

    CLUSTER_NAME = "integ-test"
    NAMESPACE = "default"

    def test_full_lifecycle(self, kube_client):
        config = ClusterConfig(
            name=self.CLUSTER_NAME,
            namespace=self.NAMESPACE,
            head=HeadNodeConfig(cpus="500m", memory="4Gi"),
            worker_groups=[WorkerGroupConfig(cpus="500m", memory="1Gi")],
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
            assert details.head.cpus == "500m"
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

            # DELETE
            delete_cluster(self.CLUSTER_NAME, self.NAMESPACE, client=kube_client)

            with pytest.raises(ClusterNotFoundError):
                get_cluster(self.CLUSTER_NAME, self.NAMESPACE, client=kube_client)
        finally:
            try:
                delete_cluster(self.CLUSTER_NAME, self.NAMESPACE, client=kube_client)
                _wait_for_deleted(self.CLUSTER_NAME, self.NAMESPACE, kube_client)
            except Exception:
                pass
