from __future__ import annotations

from dataclasses import dataclass


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
    vscode_url: str | None
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
