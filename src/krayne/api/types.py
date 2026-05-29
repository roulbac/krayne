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

    def is_alive(self) -> bool:
        """Return ``True`` iff the per-cluster tunnel manager is healthy.

        Reads ``~/.krayne/tunnels/<ns>/<cluster>.json`` and checks the
        manager's PID + heartbeat freshness.
        """
        from krayne.tunnel import is_tunnel_active

        return is_tunnel_active(self.cluster_name, self.namespace)

    def wait_ready(
        self, timeout: float = 30.0, *, services: list[str] | None = None,
    ) -> bool:
        """Block until the requested services are actually responding.

        Two phases: first wait for the manager to report each tunnel's local
        listener bound (``OPEN``), then probe each service end-to-end through
        its tunnel (an HTTP request for the dashboard/notebook/code-server, an
        SSH banner read, a connect-through for the Ray client) until it
        answers. Returns ``True`` only when every requested service responds,
        ``False`` on timeout — so a service that crashed inside the head pod
        surfaces as ``False`` rather than a spuriously-ready session.

        By default this waits for every service in the session. Pass
        *services* to wait for only a subset (e.g. ``["dashboard"]`` when
        that's all you need) so a slow-to-bootstrap auxiliary service like
        code-server doesn't hold up readiness for the rest.

        Note the Ray client (gRPC) probe is best-effort; see
        :func:`krayne.tunnel.probe_service`.
        """
        import time

        from krayne import tunnel_state
        from krayne.tunnel import wait_for_tunnel_ready

        tunnels = self.tunnels
        if services is not None:
            wanted = set(services)
            tunnels = [t for t in tunnels if t.service in wanted]

        deadline = time.monotonic() + timeout
        try:
            tunnel_state.wait_until_open(
                self.cluster_name,
                self.namespace,
                {t.service for t in tunnels},
                timeout=timeout,
            )
        except TimeoutError:
            return False
        for tunnel in tunnels:
            remaining = deadline - time.monotonic()
            if remaining <= 0 or not wait_for_tunnel_ready(
                tunnel, timeout=remaining,
            ):
                return False
        return True

    def __getattr__(self, name: str) -> str | None:
        if name.endswith("_url"):
            service = name[: -len("_url")].replace("_", "-")
            if service in _TUNNEL_URL_SERVICES:
                return self._url_for(service)
        raise AttributeError(name)
