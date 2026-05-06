from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from krayne.tunnel import TunnelInfo


@dataclass(frozen=True)
class ClusterInfo:
    """Summary information about a Ray cluster."""

    name: str
    namespace: str
    status: str
    head_ip: str | None
    dashboard_url: str | None
    client_url: str | None
    notebook_url: str | None
    code_server_url: str | None
    ssh_url: str | None
    num_workers: int
    autoscaling_enabled: bool
    created_at: str


@dataclass(frozen=True)
class HeadNodeInfo:
    """Head node resource details."""

    cpus: str
    memory: str
    gpus: int
    image: str
    runs_tasks: bool = False


@dataclass(frozen=True)
class WorkerGroupInfo:
    """Worker group resource details."""

    name: str
    replicas: int
    min_replicas: int
    max_replicas: int
    cpus: str
    memory: str
    gpus: int


@dataclass(frozen=True)
class ClusterDetails:
    """Extended cluster information with resource breakdown."""

    info: ClusterInfo
    head: HeadNodeInfo
    worker_groups: list[WorkerGroupInfo]
    ray_version: str
    python_version: str


_TUNNEL_URL_SERVICES: tuple[str, ...] = (
    "dashboard",
    "client",
    "notebook",
    "code-server",
    "ssh",
)


@dataclass(frozen=True)
class TunnelSession:
    """Active tunnel session with local URLs for all forwarded services.

    Exposes ``<service>_url`` attributes (with ``-`` mapped to ``_``) for each
    service in :data:`_TUNNEL_URL_SERVICES` — e.g. ``dashboard_url``,
    ``code_server_url`` — returning the local URL or ``None`` if not tunneled.
    """

    cluster_name: str
    namespace: str
    tunnels: list[TunnelInfo]

    def _url_for(self, service: str) -> str | None:
        for t in self.tunnels:
            if t.service == service:
                return t.local_url
        return None

    def __getattr__(self, name: str) -> str | None:
        if name.endswith("_url"):
            service = name[: -len("_url")].replace("_", "-")
            if service in _TUNNEL_URL_SERVICES:
                return self._url_for(service)
        raise AttributeError(name)
