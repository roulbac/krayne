"""Backend protocol and the plain row types the TUI renders.

The TUI only talks to a :class:`Backend`. :mod:`stratus.backend.skypilot`
implements it against a SkyPilot API server; :mod:`stratus.backend.fake` is an
in-memory simulation used by ``stratus --demo`` and the tests.

Long-running operations (launch, down, ...) return a SkyPilot request id
immediately; progress is followed with :meth:`Backend.stream_request_logs`.
"""

from __future__ import annotations

import threading
from dataclasses import dataclass, field
from typing import Any, Callable, Protocol

from stratus.specs.models import ClusterSpec, JobSpec, ServiceSpec, VolumeSpec

LogSink = Callable[[str], None]


class BackendError(Exception):
    """Anything that went wrong talking to SkyPilot, with a user-facing message."""


class ServerUnavailableError(BackendError):
    pass


@dataclass(frozen=True)
class ServerInfo:
    endpoint: str
    healthy: bool
    version: str | None = None
    api_version: str | None = None
    user: str | None = None
    error: str | None = None


@dataclass(frozen=True)
class ClusterRow:
    name: str
    status: str
    infra: str
    resources: str
    launched_at: float | None
    autostop: int  # minutes; -1 = off
    to_down: bool
    user: str
    workspace: str = "default"
    last_event: str | None = None
    creation_yaml: str | None = None
    details: dict[str, Any] = field(default_factory=dict, compare=False)


@dataclass(frozen=True)
class ClusterJobRow:
    cluster: str
    job_id: int
    name: str
    status: str
    submitted_at: float | None
    start_at: float | None
    end_at: float | None
    resources: str
    user: str


@dataclass(frozen=True)
class ManagedJobRow:
    job_id: int
    name: str
    status: str
    resources: str
    infra: str
    submitted_at: float | None
    duration: float | None
    recoveries: int
    user: str
    details: str | None = None
    user_yaml: str | None = None


@dataclass(frozen=True)
class ReplicaRow:
    service: str
    replica_id: int
    status: str
    version: int | None
    infra: str
    resources: str
    endpoint: str | None
    launched_at: float | None


@dataclass(frozen=True)
class ServiceRow:
    name: str
    status: str
    endpoint: str | None
    ready_replicas: int
    total_replicas: int
    versions: str
    uptime: float | None  # unix time the service became ready
    replicas: tuple[ReplicaRow, ...] = ()
    yaml: str | None = None


@dataclass(frozen=True)
class VolumeRow:
    name: str
    type: str
    infra: str
    size: str
    status: str
    used_by: tuple[str, ...]
    launched_at: float | None
    user: str
    creation_yaml: str | None = None


@dataclass(frozen=True)
class RequestRow:
    request_id: str
    name: str
    status: str
    created_at: float | None
    finished_at: float | None
    cluster: str | None
    user: str
    status_msg: str | None = None


@dataclass(frozen=True)
class InfraRow:
    name: str
    enabled: bool
    detail: str = ""


class Backend(Protocol):
    """Everything the TUI needs from SkyPilot."""

    endpoint: str

    def server_info(self) -> ServerInfo: ...

    # listing
    def list_clusters(self, refresh: bool = False) -> list[ClusterRow]: ...
    def list_cluster_jobs(self, cluster: str) -> list[ClusterJobRow]: ...
    def list_managed_jobs(self) -> list[ManagedJobRow]: ...
    def list_services(self) -> list[ServiceRow]: ...
    def list_volumes(self) -> list[VolumeRow]: ...
    def list_requests(self, limit: int = 100) -> list[RequestRow]: ...
    def list_infra(self) -> list[InfraRow]: ...

    # clusters
    def launch_cluster(self, spec: ClusterSpec) -> str: ...
    def exec_on_cluster(self, cluster: str, command: str) -> str: ...
    def stop_cluster(self, cluster: str) -> str: ...
    def start_cluster(self, cluster: str) -> str: ...
    def down_cluster(self, cluster: str) -> str: ...
    def set_autostop(self, cluster: str, minutes: int, down: bool) -> str: ...
    def cancel_cluster_job(self, cluster: str, job_id: int) -> str: ...
    def ssh_argv(self, cluster: str) -> list[str]: ...

    # managed jobs
    def launch_managed_job(self, spec: JobSpec) -> str: ...
    def cancel_managed_job(self, job_id: int) -> str: ...

    # services
    def serve_up(self, spec: ServiceSpec) -> str: ...
    def serve_update(self, spec: ServiceSpec) -> str: ...
    def serve_down(self, name: str) -> str: ...
    def terminate_replica(self, service: str, replica_id: int) -> str: ...

    # volumes
    def apply_volume(self, spec: VolumeSpec) -> str: ...
    def delete_volume(self, name: str) -> str: ...

    # requests
    def cancel_request(self, request_id: str) -> None: ...

    # logs: block until done or `stop` is set, feeding lines to `sink`
    def stream_request_logs(self, request_id: str, sink: LogSink, stop: threading.Event) -> None: ...
    def stream_cluster_job_logs(self, cluster: str, job_id: int | None, sink: LogSink, stop: threading.Event) -> None: ...
    def stream_managed_job_logs(self, job_id: int, sink: LogSink, stop: threading.Event, controller: bool = False, follow: bool = True) -> None: ...
    def stream_service_logs(self, service: str, target: str, replica_id: int | None, sink: LogSink, stop: threading.Event) -> None: ...
