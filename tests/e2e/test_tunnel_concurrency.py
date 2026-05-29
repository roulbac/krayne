"""End-to-end: tunnels must stay healthy under concurrent load.

This is the test that would have caught the shared-``ApiClient`` race fixed in
``DefaultKubeClient.portforward``. The integration suite only ever opens *one*
service and probes it with *sequential, retried* requests, so it never makes two
client operations overlap — exactly the condition the bug needs.

Here we open all five services at once (so multiple forwarders plus the head-pod
watch share the manager's kube client) and then fire many requests in parallel,
mimicking a browser hammering the Ray dashboard. The assertions are strict — no
retries papering over failures — and we also assert the manager log stays free of
the ``Missing required parameter `ports``` error that the race produces.

Requirements:
  - Docker running
  - kubectl CLI available on PATH
  - ``uv sync``

Run with:
  pytest tests/e2e -m e2e --timeout=600
"""

from __future__ import annotations

import time
from concurrent.futures import ThreadPoolExecutor
from urllib.error import URLError
from urllib.request import urlopen

import pytest

from krayne import tunnel_state
from krayne.api import create_cluster, delete_cluster, get_cluster, get_cluster_services
from krayne.config import ClusterConfig
from krayne.config.models import HeadNodeConfig, ServicesConfig, WorkerGroupConfig
from krayne.tunnel import start_tunnels, stop_tunnels
from krayne.tunnel_state import ServiceState

pytestmark = pytest.mark.e2e

_CLUSTER_READY_TIMEOUT = 300
_SERVICE_STARTUP_GRACE = 60
_POLL_INTERVAL = 3

# HTTP services and a cheap, always-200 endpoint on each.
_HTTP_ENDPOINTS = {
    "dashboard": "/api/version",
    "notebook": "/api/status",
    "code-server": "/healthz",
}

# Concurrency knobs. The dashboard is weighted heavily because that's the real
# browser pattern (many parallel XHRs); every urlopen is a fresh connection, so
# each one drives a fresh port-forward handshake and maximises swap contention.
_CONCURRENT_WORKERS = 24
_REQUEST_PLAN = ["dashboard"] * 48 + ["notebook"] * 12 + ["code-server"] * 12
_REQUEST_TIMEOUT = 10


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
    from krayne.errors import ClusterNotFoundError

    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            get_cluster(name, namespace, client=client)
        except ClusterNotFoundError:
            return
        time.sleep(_POLL_INTERVAL)


def _http_request(url: str) -> tuple[bool, str]:
    """Make a single request. Returns ``(ok, detail)`` — never retries."""
    try:
        resp = urlopen(url, timeout=_REQUEST_TIMEOUT)  # noqa: S310
        status = resp.status
        return (status == 200, f"{url} -> {status}")
    except URLError as exc:
        return (False, f"{url} -> URLError({exc.reason})")
    except Exception as exc:  # noqa: BLE001 — surface anything (resets, EOF, ...)
        return (False, f"{url} -> {type(exc).__name__}({exc})")


def _wait_http_ready(url: str, *, retries: int = 30, delay: float = 3.0) -> None:
    """Warm-up gate: retry until the endpoint serves 200 once.

    Separates genuine service cold-start from the race we're testing, so the
    strict concurrent phase only fails on real routing breakage.
    """
    for _ in range(retries):
        ok, _detail = _http_request(url)
        if ok:
            return
        time.sleep(delay)
    pytest.fail(f"Service never became ready: {url}")


class TestTunnelConcurrency:
    CLUSTER_NAME = "e2e-tunnel-concurrency"
    NAMESPACE = "default"

    @pytest.fixture(autouse=True, scope="class")
    def _cluster(self, kube_client, sandbox_kubeconfig):
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
        yield
        stop_tunnels(self.CLUSTER_NAME, self.NAMESPACE)

    def test_concurrent_load_across_all_services(self):
        services = get_cluster_services(
            self.CLUSTER_NAME, self.NAMESPACE, client=self._client
        )
        assert set(services) == {
            "dashboard", "client", "notebook", "code-server", "ssh"
        }

        tunnels = start_tunnels(
            self.CLUSTER_NAME, self.NAMESPACE, services,
            kubeconfig=self._kubeconfig,
        )
        ports = {t.service: t.local_port for t in tunnels}

        urls = {
            svc: f"http://localhost:{ports[svc]}{path}"
            for svc, path in _HTTP_ENDPOINTS.items()
        }

        # Warm up each HTTP service so cold-start can't be mistaken for the race.
        for url in urls.values():
            _wait_http_ready(url)

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
            "Manager logged the shared-ApiClient port-forward race "
            "(`Missing required parameter \\`ports\\``) while serving "
            "concurrent traffic — see the manager log tail above."
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
