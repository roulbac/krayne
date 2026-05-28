"""Public tunnel API — thin shims over the per-cluster manager subprocess.

`start_tunnels` writes the desired-tunnel set into the state file under
``~/.krayne/tunnels/<ns>/<cluster>.json``, ensures the manager subprocess
(``python -m krayne._manager``) is running, and blocks until each
requested service reports ``OPEN`` (or the deadline expires).

`stop_tunnels` clears the desired set; the manager observes the empty
set and self-exits after a few idle seconds. Per-service teardown via
`stop_tunnel_service`.

The module preserves the function names + signatures used today by the
CLI (`cli/tunnel.py`, `cli/submit.py`, `cli/clusters.py`), the SDK
(`api/clusters.py`, `api/types.py`), and the TUI screens. The underlying
mechanism changes wholesale; the surface does not.

NOTE: Callers must not invoke `start_tunnels` from inside an event loop
that owns user-visible UI rendering — it blocks for up to 30s. The TUI
calls it from a Textual worker thread, which is fine.
"""

from __future__ import annotations

import hashlib
import socket
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from urllib.parse import urlparse

import tenacity

from krayne import tunnel_state
from krayne.errors import KrayneError
from krayne.tunnel_state import (
    TUNNEL_DIR,
    KubeConfigRef,
    ServiceState,
    TunnelInfo,
    TunnelState,
)

PORT_RANGE_START = 10000
PORT_RANGE_END = 60000

# service name -> (remote port, URL scheme)
SERVICE_PORTS: dict[str, tuple[int, str]] = {
    "dashboard": (8265, "http"),
    "client": (10001, "ray"),
    "notebook": (8888, "http"),
    "code-server": (8443, "http"),
    "ssh": (22, "ssh"),
}


# --- Pure helpers (unchanged across the refactor) ----------------------------


def local_port_for(cluster_name: str, namespace: str, service_name: str) -> int:
    """Return a deterministic local port for the given (cluster, ns, service)."""
    key = f"{cluster_name}/{namespace}/{service_name}"
    h = int(hashlib.sha256(key.encode()).hexdigest(), 16)
    return PORT_RANGE_START + (h % (PORT_RANGE_END - PORT_RANGE_START))


def head_port_names(obj: dict) -> set[str]:
    """Collect named ports from head containers and headService spec."""
    head_spec = obj.get("spec", {}).get("headGroupSpec", {})
    containers = head_spec.get("template", {}).get("spec", {}).get("containers", [])
    names: set[str] = set()
    for container in containers:
        for port in container.get("ports", []):
            name = port.get("name")
            if name:
                names.add(name)
    for port in head_spec.get("headService", {}).get("spec", {}).get("ports", []):
        name = port.get("name")
        if name:
            names.add(name)
    return names


def detect_services(obj: dict) -> list[str]:
    """Detect which services are exposed on the head node by inspecting port names."""
    names = head_port_names(obj)
    return [name for name in SERVICE_PORTS if name in names]


def check_service_health(
    *,
    cluster_status: str,
    head_ip: str | None,
    declared_services: list[str],
    tunnel_map: dict[str, str],
    timeout: float = 0.5,
) -> dict[str, str]:
    """Probe each declared service and return ``available`` / ``pending`` / ``unreachable``."""
    if cluster_status != "ready":
        return {svc: "pending" for svc in declared_services}

    targets: dict[str, tuple[str, int]] = {}
    for svc in declared_services:
        target = _probe_target(svc, head_ip, tunnel_map.get(svc))
        if target is not None:
            targets[svc] = target

    results: dict[str, str] = {}
    if targets:
        with ThreadPoolExecutor(max_workers=len(targets)) as pool:
            futures = {
                svc: pool.submit(_tcp_probe, host, port, timeout)
                for svc, (host, port) in targets.items()
            }
            for svc, fut in futures.items():
                results[svc] = "available" if fut.result() else "unreachable"

    for svc in declared_services:
        results.setdefault(svc, "available")

    return results


def _probe_target(
    service: str,
    head_ip: str | None,
    tunnel_url: str | None,
) -> tuple[str, int] | None:
    if tunnel_url:
        parsed = urlparse(tunnel_url)
        if parsed.hostname and parsed.port:
            return (parsed.hostname, parsed.port)
    if head_ip and service in SERVICE_PORTS:
        return (head_ip, SERVICE_PORTS[service][0])
    return None


def _tcp_probe(host: str, port: int, timeout: float) -> bool:
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def wait_for_tunnel_ready(
    tunnel: TunnelInfo,
    *,
    timeout: float = 30.0,
    interval: float = 0.5,
) -> bool:
    """Poll *tunnel*'s local TCP port until it accepts connections.

    Retained for callers that want a final TCP-level readiness check
    independent of the state file (e.g. `cli/submit.py`). `start_tunnels`
    already waits on the manager's published status before returning, so
    this is normally a fast no-op.
    """

    @tenacity.retry(
        stop=tenacity.stop_after_delay(timeout),
        wait=tenacity.wait_fixed(interval),
        retry=tenacity.retry_if_result(lambda reachable: not reachable),
        retry_error_callback=lambda _state: False,
    )
    def _probe() -> bool:
        return _tcp_probe("localhost", tunnel.local_port, timeout=1.0)

    return _probe()


# --- Settings resolution ----------------------------------------------------


def _resolve_kube_settings(
    kubeconfig: str | None, context: str | None = None
) -> tuple[str | None, str | None]:
    """Resolve kubeconfig and context from krayne settings when neither is explicit."""
    if kubeconfig is not None or context is not None:
        return kubeconfig, context
    from krayne.config.settings import load_krayne_settings

    settings = load_krayne_settings()
    return settings.kubeconfig, settings.kube_context


# --- Public API: state queries ---------------------------------------------


def load_tunnel_state(cluster_name: str, namespace: str) -> TunnelState | None:
    """Load the persisted state, returning ``None`` if absent or stale.

    Legacy v1 files are reaped (their kubectl PIDs are SIGTERMed and the
    file is removed); ``None`` is returned in that case.
    """
    return tunnel_state.load_state(cluster_name, namespace)


def is_tunnel_active(cluster_name: str, namespace: str) -> bool:
    """Return ``True`` iff a healthy manager owns the tunnel session."""
    state = tunnel_state.load_state(cluster_name, namespace)
    if state is None:
        return False
    if not tunnel_state.manager_alive(state.manager):
        # Manager dropped: clear any stale `manager:` entry so the next
        # start_tunnels respawns cleanly.
        if state.manager is not None:
            tunnel_state.update(
                cluster_name, namespace,
                lambda s: _clear_dead_manager(s),
            )
        return False
    return True


def _clear_dead_manager(state: TunnelState) -> TunnelState:
    state.manager = None
    state.status = {}
    return state


# --- Public API: lifecycle --------------------------------------------------


def start_tunnels(
    cluster_name: str,
    namespace: str,
    services: list[str],
    *,
    kubeconfig: str | None = None,
    context: str | None = None,
    timeout: float = 30.0,
    wait: bool = True,
) -> list[TunnelInfo]:
    """Ensure tunnels for *services* are open, spawning the manager if needed.

    Writes the desired set into the state file, spawns the per-cluster
    manager subprocess (``python -m krayne._manager``) if one isn't already
    alive, then (when ``wait=True``) blocks until each requested service
    reports ``OPEN`` (or raises on timeout). With ``wait=False`` the
    desired state is written and the manager spawned, but readiness is
    not waited on — the caller is responsible.

    **Idempotent** — if a manager is already running with the desired set
    open, returns immediately.
    """
    resolved_kubeconfig, resolved_context = _resolve_kube_settings(
        kubeconfig, context,
    )

    desired = [
        _build_tunnel_info(cluster_name, namespace, svc)
        for svc in services
        if svc in SERVICE_PORTS
    ]
    desired_services = {t.service for t in desired}

    # Write desired state under the lock; preserves existing entries for
    # other services that were already requested.
    def _merge(state: TunnelState) -> TunnelState:
        state.cluster_name = cluster_name
        state.namespace = namespace
        state.kube_config = KubeConfigRef(
            kubeconfig=resolved_kubeconfig,
            context=resolved_context,
        )
        by_svc = {t.service: t for t in state.desired_tunnels}
        for info in desired:
            by_svc[info.service] = info
        state.desired_tunnels = list(by_svc.values())
        return state

    state = tunnel_state.update(cluster_name, namespace, _merge)

    if not desired:
        return []

    _ensure_manager_running(state)

    if not wait:
        return desired

    try:
        tunnel_state.wait_until_open(
            cluster_name, namespace, desired_services, timeout=timeout,
        )
    except TimeoutError as exc:
        raise KrayneError(str(exc)) from exc

    final = tunnel_state.load_state(cluster_name, namespace)
    failed = [
        svc for svc in desired_services
        if final is not None
        and final.status.get(svc) is not None
        and final.status[svc].state == ServiceState.FAILED
    ]
    if failed:
        last_errors = ", ".join(
            f"{svc}: {final.status[svc].last_error or '?'}"
            for svc in failed if final is not None
        )
        raise KrayneError(
            f"Tunnel manager could not open services {failed}: {last_errors}"
        )

    return desired


def stop_tunnels(cluster_name: str, namespace: str) -> bool:
    """Clear the desired-tunnel set. The manager self-exits when idle.

    Returns ``True`` if state existed (i.e. there was something to stop),
    ``False`` if no session was active.
    """
    state = tunnel_state.load_state(cluster_name, namespace)
    if state is None or (
        not state.desired_tunnels and state.manager is None
    ):
        tunnel_state.delete_state(cluster_name, namespace)
        return False

    def _clear(s: TunnelState) -> TunnelState:
        s.desired_tunnels = []
        return s

    tunnel_state.update(cluster_name, namespace, _clear)

    # Best-effort: signal the manager so it exits without waiting out the
    # full idle window. Even without this, it would notice on the next tick.
    if state.manager is not None:
        try:
            import os
            import signal as _signal

            os.kill(state.manager.pid, _signal.SIGTERM)
        except OSError:
            pass

    # Give the manager a brief moment to clear its slot, then remove the
    # state file. Tolerate slow shutdown.
    for _ in range(20):  # ~2s
        cur = tunnel_state.load_state(cluster_name, namespace)
        if cur is None or cur.manager is None:
            break
        time.sleep(0.1)
    tunnel_state.delete_state(cluster_name, namespace)
    return True


def stop_tunnel_service(cluster_name: str, namespace: str, service: str) -> bool:
    """Remove a single service from the desired set.

    Returns ``True`` if the service was in the desired set, ``False``
    otherwise. The manager closes the corresponding forwarder on its
    next reconcile tick.
    """
    state = tunnel_state.load_state(cluster_name, namespace)
    if state is None:
        return False
    if not any(t.service == service for t in state.desired_tunnels):
        return False

    def _drop(s: TunnelState) -> TunnelState:
        s.desired_tunnels = [t for t in s.desired_tunnels if t.service != service]
        return s

    tunnel_state.update(cluster_name, namespace, _drop)

    # If that was the last desired tunnel, fully tear down so the next
    # start_tunnels behaves like a clean start.
    remaining = tunnel_state.load_state(cluster_name, namespace)
    if remaining is None or not remaining.desired_tunnels:
        stop_tunnels(cluster_name, namespace)
    return True


# --- Manager spawning -------------------------------------------------------


def _build_tunnel_info(
    cluster_name: str, namespace: str, service: str,
) -> TunnelInfo:
    remote_port, scheme = SERVICE_PORTS[service]
    lport = local_port_for(cluster_name, namespace, service)
    return TunnelInfo(
        service=service,
        remote_port=remote_port,
        local_port=lport,
        local_url=f"{scheme}://localhost:{lport}",
    )


def _ensure_manager_running(state: TunnelState) -> None:
    """Spawn the per-cluster manager subprocess if one isn't alive.

    Detached via ``start_new_session=True`` (matches the historic kubectl
    spawn pattern) so it outlives the CLI. stdin/stdout/stderr are
    redirected to ``/dev/null``; the manager re-opens its own stderr to
    the per-cluster log file via ``RotatingFileHandler``.
    """
    if tunnel_state.manager_alive(state.manager):
        return

    subprocess.Popen(  # noqa: S603 — args are constructed from trusted strings
        [sys.executable, "-m", "krayne._manager",
         state.cluster_name, state.namespace],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
        close_fds=True,
    )

    # Wait briefly for the new manager to claim the slot.
    deadline = time.monotonic() + 5.0
    while time.monotonic() < deadline:
        cur = tunnel_state.load_state(state.cluster_name, state.namespace)
        if cur is not None and tunnel_state.manager_alive(cur.manager):
            return
        time.sleep(0.1)
    raise KrayneError(
        f"Tunnel manager for '{state.cluster_name}/{state.namespace}' "
        f"did not start within 5s. Check "
        f"{tunnel_state.log_path(state.cluster_name, state.namespace)}."
    )


__all__ = [
    "PORT_RANGE_END",
    "PORT_RANGE_START",
    "SERVICE_PORTS",
    "TUNNEL_DIR",
    "TunnelInfo",
    "TunnelState",
    "check_service_health",
    "detect_services",
    "head_port_names",
    "is_tunnel_active",
    "load_tunnel_state",
    "local_port_for",
    "start_tunnels",
    "stop_tunnel_service",
    "stop_tunnels",
    "wait_for_tunnel_ready",
]
