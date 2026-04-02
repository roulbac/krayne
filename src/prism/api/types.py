from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from prism.tunnel import TunnelInfo


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
    created_at: str


@dataclass(frozen=True)
class HeadNodeInfo:
    """Head node resource details."""

    cpus: str
    memory: str
    gpus: int
    image: str


@dataclass(frozen=True)
class WorkerGroupInfo:
    """Worker group resource details."""

    name: str
    replicas: int
    cpus: str
    memory: str
    gpus: int
    gpu_type: str | None


@dataclass(frozen=True)
class ClusterDetails:
    """Extended cluster information with resource breakdown."""

    info: ClusterInfo
    head: HeadNodeInfo
    worker_groups: list[WorkerGroupInfo]
    ray_version: str
    python_version: str


@dataclass(frozen=True)
class TunnelSession:
    """Active tunnel session with local URLs for all forwarded services."""

    cluster_name: str
    namespace: str
    tunnels: list[TunnelInfo]

    def _url_for(self, service: str) -> str | None:
        for t in self.tunnels:
            if t.service == service:
                return t.local_url
        return None

    @property
    def dashboard_url(self) -> str | None:
        return self._url_for("dashboard")

    @property
    def client_url(self) -> str | None:
        return self._url_for("client")

    @property
    def notebook_url(self) -> str | None:
        return self._url_for("notebook")

    @property
    def code_server_url(self) -> str | None:
        return self._url_for("code-server")

    @property
    def ssh_url(self) -> str | None:
        return self._url_for("ssh")
