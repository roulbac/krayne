"""Pydantic models for cluster configuration."""

from __future__ import annotations

from pydantic import BaseModel, Field


class ServicesConfig(BaseModel):
    """Services to enable on the cluster."""

    notebook: bool = True
    vscode_server: bool = False
    ssh: bool = True
    tutorials: bool = False


class HeadNodeConfig(BaseModel):
    """Head node resource configuration."""

    cpus: int = 15
    memory: str = "48Gi"
    gpus: int = 0
    image: str | None = None


class WorkerGroupConfig(BaseModel):
    """Worker group configuration."""

    name: str = "worker"
    replicas: int = 1
    cpus: int = 15
    memory: str = "48Gi"
    gpus: int = 0
    gpu_type: str = "t4"
    image: str | None = None


class ClusterConfig(BaseModel):
    """Top-level cluster configuration."""

    model_config = {"extra": "forbid"}

    name: str
    namespace: str = "default"
    head: HeadNodeConfig = Field(default_factory=HeadNodeConfig)
    worker_groups: list[WorkerGroupConfig] = Field(
        default_factory=lambda: [WorkerGroupConfig()]
    )
    services: ServicesConfig = Field(default_factory=ServicesConfig)
