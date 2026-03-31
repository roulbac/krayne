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
from prism.config import DEFAULT_CPUS, ClusterConfig
from prism.errors import ClusterNotFoundError

pytestmark = pytest.mark.integration


class TestClusterLifecycle:
    """End-to-end lifecycle: create -> get -> list -> describe -> scale -> delete."""

    CLUSTER_NAME = "integ-test"
    NAMESPACE = "default"

    def test_full_lifecycle(self, kube_client):
        config = ClusterConfig(
            name=self.CLUSTER_NAME,
            namespace=self.NAMESPACE,
        )

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
        assert details.head.cpus == DEFAULT_CPUS
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
