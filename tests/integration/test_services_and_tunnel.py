"""Integration tests: services health & tunnel port-forwarding against the krayne sandbox.

Requirements:
  - Docker running
  - kubectl CLI available on PATH
  - ``uv sync``

Run with:
  pytest tests/integration/test_services_and_tunnel.py -m integration --timeout=600
"""

from __future__ import annotations

import socket
import time
from urllib.error import URLError
from urllib.request import urlopen

import pytest

from krayne.api import create_cluster, delete_cluster, get_cluster, get_cluster_services
from krayne.config import ClusterConfig
from krayne.config.models import HeadNodeConfig, ServicesConfig, WorkerGroupConfig
from krayne.tunnel import (
    detect_services,
    is_tunnel_active,
    local_port_for,
    start_tunnels,
    stop_tunnels,
)

pytestmark = pytest.mark.integration

# Generous wait: k3s image pulls + pod scheduling + service installation
_CLUSTER_READY_TIMEOUT = 300
_SERVICE_STARTUP_GRACE = 60
_POLL_INTERVAL = 3


def _wait_for_ready(name: str, namespace: str, client, timeout: int) -> None:
    """Poll until the cluster reaches ready/running or timeout expires."""
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
    from krayne.errors import ClusterNotFoundError

    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            get_cluster(name, namespace, client=client)
        except ClusterNotFoundError:
            return
        time.sleep(_POLL_INTERVAL)


def _http_probe(url: str, timeout: int = 5) -> int:
    """Return the HTTP status code, or 0 on connection failure."""
    try:
        resp = urlopen(url, timeout=timeout)  # noqa: S310
        return resp.status
    except URLError:
        return 0
    except Exception:
        return 0


def _tcp_probe(host: str, port: int, timeout: int = 5) -> bytes:
    """Connect and read the first bytes (e.g. SSH banner). Empty on failure."""
    try:
        with socket.create_connection((host, port), timeout=timeout) as sock:
            return sock.recv(256)
    except (OSError, TimeoutError):
        return b""


def _retry(probe, *, retries: int = 20, delay: float = 3.0):
    """Retry a probe function until it returns a truthy value."""
    for i in range(retries):
        result = probe()
        if result:
            return result
        time.sleep(delay)
    return probe()


class TestServicesAndTunnel:
    """Create a cluster with all services, tunnel to it, and verify health."""

    CLUSTER_NAME = "integ-svc-test"
    NAMESPACE = "default"

    @pytest.fixture(autouse=True, scope="class")
    def _cluster(self, kube_client, sandbox_kubeconfig):
        """Create a cluster with all services enabled once for the class."""
        self.__class__._kubeconfig = sandbox_kubeconfig
        self.__class__._client = kube_client
        config = ClusterConfig(
            name=self.CLUSTER_NAME,
            namespace=self.NAMESPACE,
            head=HeadNodeConfig(cpus="500m", memory="4Gi"),
            worker_groups=[WorkerGroupConfig(cpus="500m", memory="1Gi")],
            services=ServicesConfig(notebook=True, code_server=True, ssh=True),
        )
        try:
            create_cluster(config, client=kube_client)
            _wait_for_ready(
                self.CLUSTER_NAME, self.NAMESPACE, kube_client, _CLUSTER_READY_TIMEOUT
            )
            # Extra grace period for lifecycle hooks to start services
            time.sleep(_SERVICE_STARTUP_GRACE)
            yield
        finally:
            stop_tunnels(self.CLUSTER_NAME, self.NAMESPACE)
            try:
                delete_cluster(self.CLUSTER_NAME, self.NAMESPACE, client=kube_client)
                _wait_for_deleted(self.CLUSTER_NAME, self.NAMESPACE, kube_client)
            except Exception:
                pass

    @pytest.fixture(autouse=True)
    def _cleanup_tunnels(self):
        """Clean up tunnels after each test to avoid cross-test interference."""
        yield
        stop_tunnels(self.CLUSTER_NAME, self.NAMESPACE)

    # -- Service detection --------------------------------------------------

    def test_services_detected(self):
        """get_cluster_services returns all 5 services for an all-services cluster."""
        services = get_cluster_services(
            self.CLUSTER_NAME, self.NAMESPACE, client=self._client
        )
        assert set(services) == {"dashboard", "client", "notebook", "code-server", "ssh"}

    # -- Service URLs populated in ClusterInfo ------------------------------

    def test_service_urls_populated(self):
        info = get_cluster(self.CLUSTER_NAME, self.NAMESPACE, client=self._client)
        assert info.dashboard_url is not None
        assert info.client_url is not None
        assert info.notebook_url is not None
        assert info.code_server_url is not None
        assert info.ssh_url is not None

    # -- Tunnel lifecycle ---------------------------------------------------

    def test_tunnel_start_and_stop(self):
        """tun-open creates tunnels, tun-close tears them down."""
        services = get_cluster_services(
            self.CLUSTER_NAME, self.NAMESPACE, client=self._client
        )
        tunnels = start_tunnels(
            self.CLUSTER_NAME,
            self.NAMESPACE,
            services,
            kubeconfig=self._kubeconfig,
        )
        assert len(tunnels) == len(services)
        assert is_tunnel_active(self.CLUSTER_NAME, self.NAMESPACE)

        # Stop and verify
        assert stop_tunnels(self.CLUSTER_NAME, self.NAMESPACE) is True
        assert not is_tunnel_active(self.CLUSTER_NAME, self.NAMESPACE)

    def test_tunnel_start_idempotent(self):
        """Starting an already-active tunnel returns the same info."""
        services = get_cluster_services(
            self.CLUSTER_NAME, self.NAMESPACE, client=self._client
        )
        tunnels1 = start_tunnels(
            self.CLUSTER_NAME, self.NAMESPACE, services,
            kubeconfig=self._kubeconfig,
        )
        tunnels2 = start_tunnels(
            self.CLUSTER_NAME, self.NAMESPACE, services,
            kubeconfig=self._kubeconfig,
        )
        assert tunnels1 == tunnels2

    def test_tunnel_stop_idempotent(self):
        """Stopping a non-existent tunnel is a no-op."""
        assert stop_tunnels(self.CLUSTER_NAME, self.NAMESPACE) is False

    def test_tunnel_ports_are_deterministic(self):
        """Same cluster always gets the same local ports."""
        for svc in ("dashboard", "client", "notebook", "code-server", "ssh"):
            p1 = local_port_for(self.CLUSTER_NAME, self.NAMESPACE, svc)
            p2 = local_port_for(self.CLUSTER_NAME, self.NAMESPACE, svc)
            assert p1 == p2

    # -- Health checks via tunnel -------------------------------------------

    def test_dashboard_reachable_via_tunnel(self):
        """Ray dashboard /api/version is reachable through the tunnel."""
        tunnels = start_tunnels(
            self.CLUSTER_NAME, self.NAMESPACE, ["dashboard"],
            kubeconfig=self._kubeconfig,
        )
        lport = tunnels[0].local_port
        url = f"http://localhost:{lport}/api/version"
        status = _retry(lambda: _http_probe(url))
        assert status == 200, f"Dashboard probe returned {status}"

    def test_notebook_reachable_via_tunnel(self):
        """Jupyter /api/status is reachable through the tunnel."""
        tunnels = start_tunnels(
            self.CLUSTER_NAME, self.NAMESPACE, ["notebook"],
            kubeconfig=self._kubeconfig,
        )
        lport = tunnels[0].local_port
        url = f"http://localhost:{lport}/api/status"
        status = _retry(lambda: _http_probe(url))
        assert status == 200, f"Notebook probe returned {status}"

    def test_code_server_reachable_via_tunnel(self):
        """code-server /healthz is reachable through the tunnel."""
        tunnels = start_tunnels(
            self.CLUSTER_NAME, self.NAMESPACE, ["code-server"],
            kubeconfig=self._kubeconfig,
        )
        lport = tunnels[0].local_port
        url = f"http://localhost:{lport}/healthz"
        status = _retry(lambda: _http_probe(url))
        assert status == 200, f"Code Server probe returned {status}"

    def test_ssh_reachable_via_tunnel(self):
        """sshd returns an SSH banner through the tunnel.

        Skipped when the Ray image does not ship openssh-server (e.g. the
        default ``rayproject/ray`` image).
        """
        tunnels = start_tunnels(
            self.CLUSTER_NAME, self.NAMESPACE, ["ssh"],
            kubeconfig=self._kubeconfig,
        )
        lport = tunnels[0].local_port
        banner = _retry(lambda: _tcp_probe("localhost", lport), retries=5, delay=2.0)
        if not banner:
            pytest.skip("sshd not available in the Ray image")
        assert banner.startswith(b"SSH-"), (
            f"Expected SSH banner, got: {banner!r}"
        )

    def test_all_services_via_single_tunnel_session(self):
        """All services reachable in one tunnel session."""
        services = get_cluster_services(
            self.CLUSTER_NAME, self.NAMESPACE, client=self._client
        )
        tunnels = start_tunnels(
            self.CLUSTER_NAME, self.NAMESPACE, services,
            kubeconfig=self._kubeconfig,
        )
        time.sleep(3)  # Let all port-forwards bind

        tunnel_map = {t.service: t for t in tunnels}

        # Dashboard
        t = tunnel_map["dashboard"]
        assert _retry(lambda: _http_probe(f"http://localhost:{t.local_port}/api/version")) == 200

        # Notebook
        t = tunnel_map["notebook"]
        assert _retry(lambda: _http_probe(f"http://localhost:{t.local_port}/api/status")) == 200

        # Code Server
        t = tunnel_map["code-server"]
        assert _retry(lambda: _http_probe(f"http://localhost:{t.local_port}/healthz")) == 200

        # SSH — may not be available if the Ray image lacks openssh-server
        t = tunnel_map["ssh"]
        banner = _retry(lambda: _tcp_probe("localhost", t.local_port), retries=5, delay=2.0)
        if banner:
            assert banner.startswith(b"SSH-")
