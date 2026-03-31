"""Pydantic models for cluster configuration."""

from __future__ import annotations

from pydantic import BaseModel, Field, field_validator

# ---------------------------------------------------------------------------
# Resource defaults — single source of truth for models, CLI, and tests
# ---------------------------------------------------------------------------

DEFAULT_CPUS = "1"
DEFAULT_MEMORY = "2Gi"


class ServicesConfig(BaseModel):
    """Services to enable on the cluster."""

    notebook: bool = True
    vscode_server: bool = False
    ssh: bool = True
    tutorials: bool = False


class HeadNodeConfig(BaseModel):
    """Head node resource configuration."""

    cpus: str = DEFAULT_CPUS
    memory: str = DEFAULT_MEMORY
    gpus: int = 0
    image: str | None = None

    @field_validator("cpus", mode="before")
    @classmethod
    def _coerce_cpus(cls, v: object) -> str:
        return str(v)


class WorkerGroupConfig(BaseModel):
    """Worker group configuration."""

    name: str = "worker"
    replicas: int = 1
    cpus: str = DEFAULT_CPUS
    memory: str = DEFAULT_MEMORY
    gpus: int = 0
    gpu_type: str = "t4"
    image: str | None = None

    @field_validator("cpus", mode="before")
    @classmethod
    def _coerce_cpus(cls, v: object) -> str:
        return str(v)


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
