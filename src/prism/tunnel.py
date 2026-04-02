from __future__ import annotations

import hashlib
import json
import os
import signal
import subprocess
from dataclasses import asdict, dataclass
from pathlib import Path

from prism.config.settings import PRISM_DIR

PORT_RANGE_START = 10000
PORT_RANGE_END = 60000

TUNNEL_DIR = PRISM_DIR / "tunnels"

# service name -> (remote port, URL scheme)
SERVICE_PORTS: dict[str, tuple[int, str]] = {
    "dashboard": (8265, "http"),
    "client": (10001, "ray"),
    "notebook": (8888, "http"),
    "code-server": (8443, "http"),
    "ssh": (22, "ssh"),
}


@dataclass(frozen=True)
class TunnelInfo:
    """Metadata for a single port-forward tunnel."""

    service: str
    remote_port: int
    local_port: int
    local_url: str


@dataclass
class TunnelState:
    """Persisted state for an active tunnel session."""

    cluster_name: str
    namespace: str
    tunnels: list[TunnelInfo]
    pids: list[int]


def _state_path(cluster_name: str, namespace: str) -> Path:
    return TUNNEL_DIR / namespace / f"{cluster_name}.json"


def _pid_alive(pid: int) -> bool:
    """Check whether a process with *pid* is still running."""
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


def local_port_for(cluster_name: str, namespace: str, service_name: str) -> int:
    """Return a deterministic local port for the given (cluster, namespace, service) triple."""
    key = f"{cluster_name}/{namespace}/{service_name}"
    h = int(hashlib.sha256(key.encode()).hexdigest(), 16)
    return PORT_RANGE_START + (h % (PORT_RANGE_END - PORT_RANGE_START))


def detect_services(obj: dict) -> list[str]:
    """Detect which services are exposed on the head node by inspecting port names."""
    head_spec = obj.get("spec", {}).get("headGroupSpec", {})
    containers = head_spec.get("template", {}).get("spec", {}).get("containers", [])
    port_names: set[str] = set()
    for container in containers:
        for port in container.get("ports", []):
            name = port.get("name")
            if name:
                port_names.add(name)
    # Also check headService.spec.ports for extra service ports
    for port in head_spec.get("headService", {}).get("spec", {}).get("ports", []):
        name = port.get("name")
        if name:
            port_names.add(name)
    return [name for name in SERVICE_PORTS if name in port_names]


def load_tunnel_state(cluster_name: str, namespace: str) -> TunnelState | None:
    """Load persisted tunnel state, returning ``None`` if absent or stale."""
    path = _state_path(cluster_name, namespace)
    if not path.exists():
        return None
    try:
        raw = json.loads(path.read_text())
        tunnels = [TunnelInfo(**t) for t in raw["tunnels"]]
        pids = raw["pids"]
        return TunnelState(
            cluster_name=raw["cluster_name"],
            namespace=raw["namespace"],
            tunnels=tunnels,
            pids=pids,
        )
    except (json.JSONDecodeError, KeyError, TypeError):
        path.unlink(missing_ok=True)
        return None


def _save_tunnel_state(state: TunnelState) -> None:
    path = _state_path(state.cluster_name, state.namespace)
    path.parent.mkdir(parents=True, exist_ok=True)
    data = {
        "cluster_name": state.cluster_name,
        "namespace": state.namespace,
        "tunnels": [asdict(t) for t in state.tunnels],
        "pids": state.pids,
    }
    path.write_text(json.dumps(data, indent=2))


def _remove_tunnel_state(cluster_name: str, namespace: str) -> None:
    path = _state_path(cluster_name, namespace)
    path.unlink(missing_ok=True)


def is_tunnel_active(cluster_name: str, namespace: str) -> bool:
    """Return ``True`` if a tunnel session is running for this cluster."""
    state = load_tunnel_state(cluster_name, namespace)
    if state is None:
        return False
    if all(_pid_alive(pid) for pid in state.pids):
        return True
    # Some processes died — clean up the stale state
    stop_tunnels(cluster_name, namespace)
    return False


def _resolve_kubeconfig(kubeconfig: str | None) -> str | None:
    """Resolve kubeconfig from prism settings when not explicitly provided."""
    if kubeconfig is not None:
        return kubeconfig
    from prism.config.settings import load_prism_settings

    settings = load_prism_settings()
    return settings.kubeconfig


def start_tunnels(
    cluster_name: str,
    namespace: str,
    services: list[str],
    *,
    kubeconfig: str | None = None,
) -> list[TunnelInfo]:
    """Start ``kubectl port-forward`` processes for each service.

    Port-forwards to the head Service (``svc/{cluster_name}-head-svc``).
    Processes are daemonised (detached).  State is persisted to disk so
    that :func:`stop_tunnels` can clean up later.

    **Idempotent** — if a tunnel is already active for this cluster the
    existing tunnel info is returned without spawning new processes.
    """
    if is_tunnel_active(cluster_name, namespace):
        state = load_tunnel_state(cluster_name, namespace)
        assert state is not None  # guarded by is_tunnel_active
        return state.tunnels

    kubeconfig = _resolve_kubeconfig(kubeconfig)
    svc_target = f"svc/{cluster_name}-head-svc"
    tunnels: list[TunnelInfo] = []
    pids: list[int] = []

    for service in services:
        if service not in SERVICE_PORTS:
            continue
        remote_port, scheme = SERVICE_PORTS[service]
        lport = local_port_for(cluster_name, namespace, service)

        cmd = [
            "kubectl", "port-forward",
            "-n", namespace,
            svc_target,
            f"{lport}:{remote_port}",
        ]
        if kubeconfig:
            cmd.extend(["--kubeconfig", kubeconfig])

        proc = subprocess.Popen(
            cmd,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            # Detach from parent so the tunnel survives CLI exit
            start_new_session=True,
        )
        pids.append(proc.pid)

        local_url = f"{scheme}://localhost:{lport}"
        tunnels.append(TunnelInfo(
            service=service,
            remote_port=remote_port,
            local_port=lport,
            local_url=local_url,
        ))

    state = TunnelState(
        cluster_name=cluster_name,
        namespace=namespace,
        tunnels=tunnels,
        pids=pids,
    )
    _save_tunnel_state(state)
    return tunnels


def stop_tunnels(cluster_name: str, namespace: str) -> bool:
    """Terminate all port-forward processes for a tunnel session.

    **Idempotent** — returns ``True`` if a tunnel was stopped,
    ``False`` if no tunnel was active.
    """
    state = load_tunnel_state(cluster_name, namespace)
    if state is None:
        _remove_tunnel_state(cluster_name, namespace)
        return False

    for pid in state.pids:
        try:
            os.kill(pid, signal.SIGTERM)
        except OSError:
            pass  # already dead

    _remove_tunnel_state(cluster_name, namespace)
    return True
