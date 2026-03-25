from __future__ import annotations

from pydantic import BaseModel, Field, field_validator, model_validator

DEFAULT_CPUS = "1"
DEFAULT_MEMORY = "2Gi"
DEFAULT_HEAD_MEMORY = "4Gi"


class ServicesConfig(BaseModel):
    """Services to enable on the cluster."""

    notebook: bool = True
    code_server: bool = True
    ssh: bool = True


class HeadNodeConfig(BaseModel):
    """Head node resource configuration."""

    cpus: str = DEFAULT_CPUS
    memory: str = DEFAULT_HEAD_MEMORY
    gpus: int = 0
    image: str | None = None

    @field_validator("cpus", mode="before")
    @classmethod
    def _coerce_cpus(cls, v: object) -> str:
        return str(v)


class AutoscalerConfig(BaseModel):
    """Ray v2 autoscaler configuration."""

    enabled: bool = True
    idle_timeout_seconds: int = 60
    upscaling_mode: str = "Default"
    cpu: str = "500m"
    memory: str = "512Mi"


class WorkerGroupConfig(BaseModel):
    """Worker group configuration."""

    name: str = "worker"
    replicas: int = 0
    min_replicas: int = 0
    max_replicas: int = 1
    cpus: str = DEFAULT_CPUS
    memory: str = DEFAULT_MEMORY
    gpus: int = 0
    gpu_type: str = "t4"
    image: str | None = None

    @field_validator("cpus", mode="before")
    @classmethod
    def _coerce_cpus(cls, v: object) -> str:
        return str(v)

    @model_validator(mode="after")
    def _validate_replicas(self) -> WorkerGroupConfig:
        # Auto-adjust max_replicas when replicas exceeds it (backward compat)
        if self.max_replicas < self.replicas:
            self.max_replicas = self.replicas
        if self.min_replicas > self.replicas:
            raise ValueError(
                f"min_replicas ({self.min_replicas}) must be <= replicas ({self.replicas})"
            )
        if self.min_replicas > self.max_replicas:
            raise ValueError(
                f"min_replicas ({self.min_replicas}) must be <= max_replicas ({self.max_replicas})"
            )
        return self


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
    autoscaler: AutoscalerConfig = Field(default_factory=AutoscalerConfig)
