"""Integration test: cluster with all optional services disabled.

Verifies that disabling services results in no extra ports, URLs, or
lifecycle hooks.

Run with:
  pytest tests/integration/test_services_disabled.py -m integration --timeout=600
"""

from __future__ import annotations

import time

import pytest

from prism.api import create_cluster, delete_cluster, get_cluster, get_cluster_services
from prism.config import ClusterConfig
from prism.config.models import HeadNodeConfig, ServicesConfig, WorkerGroupConfig

pytestmark = pytest.mark.integration

_CLUSTER_READY_TIMEOUT = 300
_POLL_INTERVAL = 3


def _wait_for_ready(name: str, namespace: str, client, timeout: int) -> None:
    deadline = time.monotonic() + timeout
    while True:
        info = get_cluster(name, namespace, client=client)
        if info.status in ("ready", "running"):
            return
        if time.monotonic() >= deadline:
            pytest.fail(
                f"Cluster '{name}' did not become ready within {timeout}s "
                f"(last status: {info.status})"
            )
        time.sleep(_POLL_INTERVAL)


def _wait_for_deleted(name: str, namespace: str, client, timeout: int = 60) -> None:
    """Poll until the cluster no longer exists."""
    from prism.errors import ClusterNotFoundError

    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            get_cluster(name, namespace, client=client)
        except ClusterNotFoundError:
            return
        time.sleep(_POLL_INTERVAL)


class TestServicesDisabled:
    """Cluster with all optional services turned off."""

    CLUSTER_NAME = "integ-no-svc"
    NAMESPACE = "default"

    @pytest.fixture(autouse=True, scope="class")
    def _cluster(self, kube_client, sandbox_kubeconfig):
        self.__class__._client = kube_client
        config = ClusterConfig(
            name=self.CLUSTER_NAME,
            namespace=self.NAMESPACE,
            head=HeadNodeConfig(cpus="500m", memory="4Gi"),
            worker_groups=[WorkerGroupConfig(cpus="500m", memory="1Gi")],
            services=ServicesConfig(notebook=False, code_server=False, ssh=False),
        )
        try:
            create_cluster(config, client=kube_client)
            _wait_for_ready(
                self.CLUSTER_NAME, self.NAMESPACE, kube_client, _CLUSTER_READY_TIMEOUT
            )
            yield
        finally:
            try:
                delete_cluster(self.CLUSTER_NAME, self.NAMESPACE, client=kube_client)
                _wait_for_deleted(self.CLUSTER_NAME, self.NAMESPACE, kube_client)
            except Exception:
                pass

    def test_only_base_services_detected(self):
        services = get_cluster_services(
            self.CLUSTER_NAME, self.NAMESPACE, client=self._client
        )
        assert set(services) == {"dashboard", "client"}

    def test_optional_urls_are_none(self):
        info = get_cluster(self.CLUSTER_NAME, self.NAMESPACE, client=self._client)
        assert info.notebook_url is None
        assert info.code_server_url is None
        assert info.ssh_url is None
        # Base URLs should still be present
        assert info.dashboard_url is not None
        assert info.client_url is not None
