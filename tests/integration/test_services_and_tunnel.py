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
from concurrent.futures import ThreadPoolExecutor
from urllib.error import URLError
from urllib.request import urlopen

import pytest

from krayne import tunnel_state
from krayne.api import create_cluster, delete_cluster, get_cluster, get_cluster_services
from krayne.config import ClusterConfig
from krayne.config.models import HeadNodeConfig, ServicesConfig, WorkerGroupConfig
from krayne.tunnel import (
    is_tunnel_active,
    start_tunnels,
    stop_tunnels,
)
from krayne.tunnel_state import ServiceState

pytestmark = pytest.mark.integration

# Generous wait: k3s image pulls + pod scheduling + service installation
_CLUSTER_READY_TIMEOUT = 300
_SERVICE_STARTUP_GRACE = 60
_POLL_INTERVAL = 3

# HTTP services and a cheap, always-200 endpoint on each.
_HTTP_ENDPOINTS = {
    "dashboard": "/api/version",
    "notebook": "/api/status",
    "code-server": "/healthz",
}

# Concurrency knobs for the load test. The dashboard is weighted heavily because
# that's the real browser pattern (many parallel XHRs); every urlopen is a fresh
# connection, so each one drives a fresh port-forward handshake and maximises
# contention on the manager's shared kube client.
_CONCURRENT_WORKERS = 24
_REQUEST_PLAN = ["dashboard"] * 48 + ["notebook"] * 12 + ["code-server"] * 12
_CONCURRENT_REQUEST_TIMEOUT = 10


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


def _http_request(url: str) -> tuple[bool, str]:
    """Make a single request with no retry. Returns ``(ok, detail)``.

    Unlike :func:`_http_probe` this surfaces the failure mode (reset, EOF, ...)
    so the concurrent load test can assert on it instead of masking it.
    """
    try:
        resp = urlopen(url, timeout=_CONCURRENT_REQUEST_TIMEOUT)  # noqa: S310
        return (resp.status == 200, f"{url} -> {resp.status}")
    except URLError as exc:
        return (False, f"{url} -> URLError({exc.reason})")
    except Exception as exc:  # noqa: BLE001 — surface anything (resets, EOF, ...)
        return (False, f"{url} -> {type(exc).__name__}({exc})")


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
            head=HeadNodeConfig(cpus="1", memory="4Gi"),
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

        The head-pod postStart bootstraps openssh-server on first boot, so
        this is expected to work on the default ``rayproject/ray`` image.
        Bootstrap can take ~30s after the cluster is reported ready, so the
        probe gets generous retry budget.
        """
        tunnels = start_tunnels(
            self.CLUSTER_NAME, self.NAMESPACE, ["ssh"],
            kubeconfig=self._kubeconfig,
        )
        lport = tunnels[0].local_port
        banner = _retry(lambda: _tcp_probe("localhost", lport), retries=30, delay=2.0)
        assert banner and banner.startswith(b"SSH-"), (
            f"Expected SSH banner, got: {banner!r}"
        )

    # -- Concurrency regression ---------------------------------------------

    def test_concurrent_load_across_services(self):
        """Tunnels must stay healthy under concurrent load.

        Regression for the shared-``ApiClient`` port-forward race: the other
        tests here open one service and probe it sequentially with retries, so
        two client operations never overlap — the exact condition the bug needs.
        This opens all services at once (multiple forwarders + the head-pod
        watch share the manager's kube client) and fires many parallel,
        no-retry requests, mimicking a browser hammering the dashboard.
        """
        services = get_cluster_services(
            self.CLUSTER_NAME, self.NAMESPACE, client=self._client
        )
        tunnels = start_tunnels(
            self.CLUSTER_NAME, self.NAMESPACE, services,
            kubeconfig=self._kubeconfig,
        )
        ports = {t.service: t.local_port for t in tunnels}
        urls = {
            svc: f"http://localhost:{ports[svc]}{path}"
            for svc, path in _HTTP_ENDPOINTS.items()
        }

        # Warm up each service so cold-start can't be mistaken for the race.
        for url in urls.values():
            status = _retry(lambda u=url: _http_probe(u))
            assert status == 200, f"Service never became ready: {url} ({status})"

        # Only count routing errors produced from here on.
        log_file = tunnel_state.log_path(self.CLUSTER_NAME, self.NAMESPACE)
        log_offset = log_file.stat().st_size if log_file.exists() else 0

        # Strict concurrent phase: many parallel requests, no retries.
        request_urls = [urls[svc] for svc in _REQUEST_PLAN]
        with ThreadPoolExecutor(max_workers=_CONCURRENT_WORKERS) as pool:
            results = list(pool.map(_http_request, request_urls))

        failures = [detail for ok, detail in results if not ok]
        assert not failures, (
            f"{len(failures)}/{len(results)} concurrent requests failed:\n"
            + "\n".join(failures[:20])
        )

        # The race surfaces as this exact error when a normal list/watch call
        # picks up the port-forward request shim off the shared client.
        new_log = ""
        if log_file.exists():
            with log_file.open("r", encoding="utf-8", errors="replace") as fh:
                fh.seek(log_offset)
                new_log = fh.read()
        assert "Missing required parameter `ports`" not in new_log, (
            "Manager logged the shared-ApiClient port-forward race while "
            "serving concurrent traffic."
        )

        # No forwarder should have exhausted its restart budget.
        state = tunnel_state.load_state(self.CLUSTER_NAME, self.NAMESPACE)
        assert state is not None
        failed = {
            svc: st.last_error
            for svc, st in state.status.items()
            if st.state == ServiceState.FAILED
        }
        assert not failed, f"Services entered FAILED state: {failed}"

    # NOTE: The manager's "re-bind forwarders after the head pod is replaced"
    # regression is covered deterministically by tests/unit/test_manager.py.
    # It used to live here as an e2e test that killed the head pod and waited
    # for KubeRay to reschedule under memory pressure — flaky on CI for reasons
    # outside krayne's code (scheduler + Ray dashboard cold-start timing).
