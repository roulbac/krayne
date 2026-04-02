from __future__ import annotations

import hashlib
import subprocess
from dataclasses import dataclass

PORT_RANGE_START = 10000
PORT_RANGE_END = 60000

# service name -> (remote port, URL scheme)
SERVICE_PORTS: dict[str, tuple[int, str]] = {
    "dashboard": (8265, "http"),
    "client": (10001, "ray"),
    "notebook": (8888, "http"),
    "vscode": (8080, "http"),
    "ssh": (22, "ssh"),
}


@dataclass(frozen=True)
class TunnelInfo:
    """Metadata for a single port-forward tunnel."""

    service: str
    remote_port: int
    local_port: int
    local_url: str


def local_port_for(cluster_name: str, namespace: str, service_name: str) -> int:
    """Return a deterministic local port for the given (cluster, namespace, service) triple."""
    key = f"{cluster_name}/{namespace}/{service_name}"
    h = int(hashlib.sha256(key.encode()).hexdigest(), 16)
    return PORT_RANGE_START + (h % (PORT_RANGE_END - PORT_RANGE_START))


def detect_services(obj: dict) -> list[str]:
    """Detect which services are exposed on the head node by inspecting port names."""
    containers = (
        obj.get("spec", {})
        .get("headGroupSpec", {})
        .get("template", {})
        .get("spec", {})
        .get("containers", [])
    )
    port_names: set[str] = set()
    for container in containers:
        for port in container.get("ports", []):
            name = port.get("name")
            if name:
                port_names.add(name)
    return [name for name in SERVICE_PORTS if name in port_names]


def start_tunnels(
    cluster_name: str,
    namespace: str,
    services: list[str],
    *,
    kubeconfig: str | None = None,
) -> tuple[list[TunnelInfo], list[subprocess.Popen]]:
    """Start ``kubectl port-forward`` processes for each service.

    Port-forwards to the head Service (``svc/{cluster_name}-head-svc``).
    Returns tunnel info and subprocess handles.  The caller is responsible
    for terminating the subprocesses.
    """
    svc_target = f"svc/{cluster_name}-head-svc"
    tunnels: list[TunnelInfo] = []
    processes: list[subprocess.Popen] = []

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
            stderr=subprocess.PIPE,
        )
        processes.append(proc)

        if scheme == "ssh":
            local_url = f"{scheme}://localhost:{lport}"
        else:
            local_url = f"{scheme}://localhost:{lport}"

        tunnels.append(TunnelInfo(
            service=service,
            remote_port=remote_port,
            local_port=lport,
            local_url=local_url,
        ))

    return tunnels, processes
