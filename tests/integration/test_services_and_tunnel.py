"""Integration tests: services health & tunnel port-forwarding against the prism sandbox.

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

from prism.api import create_cluster, delete_cluster, get_cluster, get_cluster_services
from prism.config import ClusterConfig
from prism.config.models import ServicesConfig
from prism.tunnel import (
    detect_services,
    is_tunnel_active,
    local_port_for,
    start_tunnels,
    stop_tunnels,
)

pytestmark = pytest.mark.integration

# Generous wait: k3s image pulls + pod scheduling + sidecar startup
_CLUSTER_READY_TIMEOUT = 300
_SERVICE_STARTUP_GRACE = 30
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


def _http_probe(url: str, timeout: int = 5) -> int:
    """Return the HTTP status code, or -1 on connection failure."""
    try:
        resp = urlopen(url, timeout=timeout)  # noqa: S310
        return resp.status
    except URLError:
        return -1
    except Exception:
        return -1


def _tcp_probe(host: str, port: int, timeout: int = 5) -> bytes:
    """Connect and read the first bytes (e.g. SSH banner). Empty on failure."""
    try:
        with socket.create_connection((host, port), timeout=timeout) as sock:
            return sock.recv(256)
    except (OSError, TimeoutError):
        return b""


def _retry(probe, *, retries: int = 10, delay: float = 3.0):
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

    @pytest.fixture(autouse=True)
    def _cluster(self, kube_client, sandbox_kubeconfig):
        """Create a cluster with all services enabled, yield, then delete."""
        self.kubeconfig = sandbox_kubeconfig
        config = ClusterConfig(
            name=self.CLUSTER_NAME,
            namespace=self.NAMESPACE,
            services=ServicesConfig(notebook=True, vscode_server=True, ssh=True),
        )
        create_cluster(config, client=kube_client)
        _wait_for_ready(
            self.CLUSTER_NAME, self.NAMESPACE, kube_client, _CLUSTER_READY_TIMEOUT
        )
        # Extra grace period for sidecar containers and lifecycle hooks
        time.sleep(_SERVICE_STARTUP_GRACE)
        self.client = kube_client
        yield
        # Ensure tunnels are cleaned up before deleting the cluster
        stop_tunnels(self.CLUSTER_NAME, self.NAMESPACE)
        delete_cluster(self.CLUSTER_NAME, self.NAMESPACE, client=kube_client)

    # -- Service detection --------------------------------------------------

    def test_services_detected(self):
        """get_cluster_services returns all 5 services for an all-services cluster."""
        services = get_cluster_services(
            self.CLUSTER_NAME, self.NAMESPACE, client=self.client
        )
        assert set(services) == {"dashboard", "client", "notebook", "vscode", "ssh"}

    # -- Service URLs populated in ClusterInfo ------------------------------

    def test_service_urls_populated(self):
        info = get_cluster(self.CLUSTER_NAME, self.NAMESPACE, client=self.client)
        assert info.dashboard_url is not None
        assert info.client_url is not None
        assert info.notebook_url is not None
        assert info.vscode_url is not None
        assert info.ssh_url is not None

    # -- Tunnel lifecycle ---------------------------------------------------

    def test_tunnel_start_and_stop(self):
        """tun-start creates tunnels, tun-close tears them down."""
        services = get_cluster_services(
            self.CLUSTER_NAME, self.NAMESPACE, client=self.client
        )
        tunnels = start_tunnels(
            self.CLUSTER_NAME,
            self.NAMESPACE,
            services,
            kubeconfig=self.kubeconfig,
        )
        assert len(tunnels) == len(services)
        assert is_tunnel_active(self.CLUSTER_NAME, self.NAMESPACE)

        # Stop and verify
        assert stop_tunnels(self.CLUSTER_NAME, self.NAMESPACE) is True
        assert not is_tunnel_active(self.CLUSTER_NAME, self.NAMESPACE)

    def test_tunnel_start_idempotent(self):
        """Starting an already-active tunnel returns the same info."""
        services = get_cluster_services(
            self.CLUSTER_NAME, self.NAMESPACE, client=self.client
        )
        tunnels1 = start_tunnels(
            self.CLUSTER_NAME, self.NAMESPACE, services,
            kubeconfig=self.kubeconfig,
        )
        tunnels2 = start_tunnels(
            self.CLUSTER_NAME, self.NAMESPACE, services,
            kubeconfig=self.kubeconfig,
        )
        assert tunnels1 == tunnels2

    def test_tunnel_stop_idempotent(self):
        """Stopping a non-existent tunnel is a no-op."""
        assert stop_tunnels(self.CLUSTER_NAME, self.NAMESPACE) is False

    def test_tunnel_ports_are_deterministic(self):
        """Same cluster always gets the same local ports."""
        for svc in ("dashboard", "client", "notebook", "vscode", "ssh"):
            p1 = local_port_for(self.CLUSTER_NAME, self.NAMESPACE, svc)
            p2 = local_port_for(self.CLUSTER_NAME, self.NAMESPACE, svc)
            assert p1 == p2

    # -- Health checks via tunnel -------------------------------------------

    def test_dashboard_reachable_via_tunnel(self):
        """Ray dashboard /api/version is reachable through the tunnel."""
        tunnels = start_tunnels(
            self.CLUSTER_NAME, self.NAMESPACE, ["dashboard"],
            kubeconfig=self.kubeconfig,
        )
        lport = tunnels[0].local_port
        url = f"http://localhost:{lport}/api/version"
        status = _retry(lambda: _http_probe(url))
        assert status == 200, f"Dashboard probe returned {status}"

    def test_notebook_reachable_via_tunnel(self):
        """Jupyter /api/status is reachable through the tunnel."""
        tunnels = start_tunnels(
            self.CLUSTER_NAME, self.NAMESPACE, ["notebook"],
            kubeconfig=self.kubeconfig,
        )
        lport = tunnels[0].local_port
        url = f"http://localhost:{lport}/api/status"
        status = _retry(lambda: _http_probe(url))
        assert status == 200, f"Notebook probe returned {status}"

    def test_vscode_reachable_via_tunnel(self):
        """code-server /healthz is reachable through the tunnel."""
        tunnels = start_tunnels(
            self.CLUSTER_NAME, self.NAMESPACE, ["vscode"],
            kubeconfig=self.kubeconfig,
        )
        lport = tunnels[0].local_port
        url = f"http://localhost:{lport}/healthz"
        status = _retry(lambda: _http_probe(url))
        assert status == 200, f"VS Code probe returned {status}"

    def test_ssh_reachable_via_tunnel(self):
        """sshd returns an SSH banner through the tunnel."""
        tunnels = start_tunnels(
            self.CLUSTER_NAME, self.NAMESPACE, ["ssh"],
            kubeconfig=self.kubeconfig,
        )
        lport = tunnels[0].local_port
        banner = _retry(lambda: _tcp_probe("localhost", lport))
        assert banner.startswith(b"SSH-"), (
            f"Expected SSH banner, got: {banner!r}"
        )

    def test_all_services_via_single_tunnel_session(self):
        """All services reachable in one tunnel session."""
        services = get_cluster_services(
            self.CLUSTER_NAME, self.NAMESPACE, client=self.client
        )
        tunnels = start_tunnels(
            self.CLUSTER_NAME, self.NAMESPACE, services,
            kubeconfig=self.kubeconfig,
        )
        time.sleep(3)  # Let all port-forwards bind

        tunnel_map = {t.service: t for t in tunnels}

        # Dashboard
        t = tunnel_map["dashboard"]
        assert _retry(lambda: _http_probe(f"http://localhost:{t.local_port}/api/version")) == 200

        # Notebook
        t = tunnel_map["notebook"]
        assert _retry(lambda: _http_probe(f"http://localhost:{t.local_port}/api/status")) == 200

        # VS Code
        t = tunnel_map["vscode"]
        assert _retry(lambda: _http_probe(f"http://localhost:{t.local_port}/healthz")) == 200

        # SSH
        t = tunnel_map["ssh"]
        banner = _retry(lambda: _tcp_probe("localhost", t.local_port))
        assert banner.startswith(b"SSH-")
