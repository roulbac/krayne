"""Typed specs for everything stratus can create.

Each spec renders to a plain SkyPilot YAML document (``to_yaml_dict``) so the
files stratus stores are directly usable with the ``sky`` CLI, and parses back
from one (``from_yaml_dict``). Keys stratus does not model are preserved in
``extra`` so hand edits made through the YAML escape hatch survive a round trip
through the forms.
"""

from __future__ import annotations

import re
from enum import Enum
from typing import Any, ClassVar

import yaml
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

NAME_RE = re.compile(r"^[a-z]([-a-z0-9]*[a-z0-9])?$")
_CPU_MEM_RE = re.compile(r"^\d+(\.\d+)?\+?$")
_ACCEL_RE = re.compile(r"^[A-Za-z0-9][-A-Za-z0-9_.]*(:\d+(\.\d+)?)?$")
_SIZE_RE = re.compile(r"^\d+(\.\d+)?\s*([KMGTP]i?B?|[KMGTP]i)?$", re.IGNORECASE)


class SpecKind(str, Enum):
    CLUSTER = "cluster"
    JOB = "job"
    SERVICE = "service"
    VOLUME = "volume"
    POOL = "pool"

    @property
    def folder(self) -> str:
        return self.value + "s"

    @property
    def title(self) -> str:
        return {"cluster": "Cluster", "job": "Managed job", "service": "Service", "volume": "Volume", "pool": "Pool"}[self.value]


def _validate_name(value: str) -> str:
    value = (value or "").strip()
    if not value:
        raise ValueError("name is required")
    if len(value) > 63 or not NAME_RE.match(value):
        raise ValueError("use lowercase letters, digits and '-', starting with a letter (max 63)")
    return value


def _clean_str(value: Any) -> str | None:
    if value is None:
        return None
    value = str(value).strip()
    return value or None


def _num_or_str(value: str) -> int | float | str:
    """``"4"`` → ``4``; ``"4+"`` stays a string, as SkyPilot expects."""
    try:
        f = float(value)
    except ValueError:
        return value
    return int(f) if f.is_integer() else f


def parse_kv_lines(text: str) -> dict[str, str]:
    """Parse ``KEY=value`` lines (blank lines and ``#`` comments ignored)."""
    out: dict[str, str] = {}
    for raw in (text or "").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if "=" not in line:
            raise ValueError(f"expected KEY=value, got {line!r}")
        key, val = line.split("=", 1)
        key = key.strip()
        if not key:
            raise ValueError(f"empty key in {line!r}")
        out[key] = val.strip()
    return out


def format_kv_lines(data: dict[str, Any]) -> str:
    return "\n".join(f"{k}={v}" for k, v in (data or {}).items())


def parse_mount_lines(text: str) -> dict[str, str]:
    """Parse ``/mount/path=volume-name`` lines."""
    mounts = parse_kv_lines(text)
    for path in mounts:
        if not path.startswith("/") and not path.startswith("~"):
            raise ValueError(f"mount path must be absolute: {path!r}")
    return mounts


def _split_csv(value: str | list[str] | None) -> list[str]:
    if value is None:
        return []
    if isinstance(value, list):
        return [str(v).strip() for v in value if str(v).strip()]
    return [p.strip() for p in str(value).split(",") if p.strip()]


class Resources(BaseModel):
    """The subset of SkyPilot ``resources`` stratus exposes in forms."""

    model_config = ConfigDict(extra="forbid")

    infra: str | None = None
    accelerators: str | None = None
    cpus: str | None = None
    memory: str | None = None
    disk_size: int | None = Field(default=None, ge=1)
    use_spot: bool = False
    image_id: str | None = None
    ports: list[str] = Field(default_factory=list)
    extra: dict[str, Any] = Field(default_factory=dict)

    @field_validator("infra", "accelerators", "cpus", "memory", "image_id", mode="before")
    @classmethod
    def _strip(cls, v: Any) -> str | None:
        return _clean_str(v)

    @field_validator("infra")
    @classmethod
    def _infra(cls, v: str | None) -> str | None:
        if v is not None and v.lower() in ("auto", "any", "*"):
            return None
        return v

    @field_validator("cpus", "memory")
    @classmethod
    def _cpu_mem(cls, v: str | None) -> str | None:
        if v is not None and not _CPU_MEM_RE.match(v):
            raise ValueError("use a number, optionally followed by '+' (e.g. 4 or 8+)")
        return v

    @field_validator("accelerators")
    @classmethod
    def _accel(cls, v: str | None) -> str | None:
        if v is not None and not _ACCEL_RE.match(v):
            raise ValueError("use NAME or NAME:COUNT (e.g. A100:1)")
        return v

    @field_validator("ports", mode="before")
    @classmethod
    def _ports(cls, v: Any) -> list[str]:
        ports = _split_csv(v)
        for p in ports:
            if not re.match(r"^\d+(-\d+)?$", p):
                raise ValueError(f"invalid port {p!r} (use 8080 or 8000-8010)")
        return ports

    def to_yaml_dict(self, autostop: dict[str, Any] | None = None, job_recovery: dict[str, Any] | None = None) -> dict[str, Any]:
        out: dict[str, Any] = {}
        if self.infra:
            out["infra"] = self.infra
        if self.accelerators:
            out["accelerators"] = self.accelerators
        if self.cpus:
            out["cpus"] = _num_or_str(self.cpus)
        if self.memory:
            out["memory"] = _num_or_str(self.memory)
        if self.disk_size:
            out["disk_size"] = self.disk_size
        if self.use_spot:
            out["use_spot"] = True
        if self.image_id:
            out["image_id"] = self.image_id
        if self.ports:
            out["ports"] = [_num_or_str(p) for p in self.ports]
        if autostop:
            out["autostop"] = autostop
        if job_recovery:
            out["job_recovery"] = job_recovery
        out.update(self.extra)
        return out

    @classmethod
    def from_yaml_dict(cls, data: dict[str, Any] | None) -> tuple["Resources", dict[str, Any]]:
        """Return the resources and the raw ``autostop``/``job_recovery`` blocks."""
        data = dict(data or {})
        special = {k: data.pop(k) for k in ("autostop", "job_recovery") if k in data}
        known = {}
        for key in ("infra", "accelerators", "cpus", "memory", "disk_size", "use_spot", "image_id", "ports"):
            if key in data:
                known[key] = data.pop(key)
        # Legacy cloud/region/zone → infra string.
        if "infra" not in known and "cloud" in data:
            parts = [str(data.pop("cloud"))]
            for k in ("region", "zone"):
                if k in data:
                    parts.append(str(data.pop(k)))
            known["infra"] = "/".join(parts)
        if isinstance(known.get("accelerators"), dict):
            # {A100: 1} → "A100:1"
            (name, count), = known["accelerators"].items()
            known["accelerators"] = f"{name}:{count}"
        for key in ("cpus", "memory"):
            if key in known and known[key] is not None:
                known[key] = str(known[key])
        if isinstance(known.get("ports"), (int, str)):
            known["ports"] = [str(known["ports"])]
        if isinstance(known.get("ports"), list):
            known["ports"] = [str(p) for p in known["ports"]]
        return cls(**known, extra=data), special

    def summary(self) -> str:
        bits = []
        if self.accelerators:
            bits.append(self.accelerators)
        if self.cpus:
            bits.append(f"{self.cpus} CPU")
        if self.memory:
            bits.append(f"{self.memory} GB")
        if self.use_spot:
            bits.append("spot")
        return ", ".join(bits) or "default"


_TASK_KNOWN = ("name", "resources", "num_nodes", "workdir", "setup", "run", "envs", "volumes", "service")


class _TaskSpec(BaseModel):
    """Fields shared by clusters, managed jobs and services (all SkyPilot tasks)."""

    model_config = ConfigDict(extra="forbid")

    kind: ClassVar[SpecKind]

    name: str
    resources: Resources = Field(default_factory=Resources)
    num_nodes: int = Field(default=1, ge=1, le=512)
    workdir: str | None = None
    setup: str | None = None
    run: str | None = None
    envs: dict[str, str] = Field(default_factory=dict)
    volumes: dict[str, str] = Field(default_factory=dict)
    extra: dict[str, Any] = Field(default_factory=dict)

    @field_validator("name")
    @classmethod
    def _name_ok(cls, v: str) -> str:
        return _validate_name(v)

    @field_validator("workdir", "setup", "run", mode="before")
    @classmethod
    def _strip_text(cls, v: Any) -> str | None:
        if v is None:
            return None
        v = str(v).rstrip()
        return v if v.strip() else None

    def _task_dict(self, resources: dict[str, Any]) -> dict[str, Any]:
        out: dict[str, Any] = {"name": self.name}
        if resources:
            out["resources"] = resources
        if self.num_nodes != 1:
            out["num_nodes"] = self.num_nodes
        if self.workdir:
            out["workdir"] = self.workdir
        if self.envs:
            out["envs"] = dict(self.envs)
        if self.volumes:
            out["volumes"] = dict(self.volumes)
        if self.setup:
            out["setup"] = _literal(self.setup)
        if self.run:
            out["run"] = _literal(self.run)
        out.update(self.extra)
        return out

    @classmethod
    def _task_kwargs(cls, name: str, data: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
        data = dict(data or {})
        resources, special = Resources.from_yaml_dict(data.pop("resources", None))
        kwargs: dict[str, Any] = {"name": name, "resources": resources}
        for key in ("num_nodes", "workdir", "setup", "run"):
            if key in data:
                kwargs[key] = data.pop(key)
        if "envs" in data:
            kwargs["envs"] = {str(k): "" if v is None else str(v) for k, v in (data.pop("envs") or {}).items()}
        if "volumes" in data:
            kwargs["volumes"] = {str(k): str(v) for k, v in (data.pop("volumes") or {}).items()}
        data.pop("name", None)
        return kwargs, {"special": special, "rest": data}

    def to_yaml(self) -> str:
        return dump_yaml(self.to_yaml_dict())

    def to_yaml_dict(self) -> dict[str, Any]:  # pragma: no cover - abstract
        raise NotImplementedError


class ClusterSpec(_TaskSpec):
    kind: ClassVar[SpecKind] = SpecKind.CLUSTER

    autostop_minutes: int | None = Field(default=None, ge=0)
    autostop_down: bool = False

    def to_yaml_dict(self) -> dict[str, Any]:
        autostop = None
        if self.autostop_minutes is not None:
            autostop = {"idle_minutes": self.autostop_minutes}
            if self.autostop_down:
                autostop["down"] = True
        return self._task_dict(self.resources.to_yaml_dict(autostop=autostop))

    @classmethod
    def from_yaml_dict(cls, name: str, data: dict[str, Any]) -> "ClusterSpec":
        kwargs, leftovers = cls._task_kwargs(name, data)
        autostop = leftovers["special"].get("autostop")
        if "job_recovery" in leftovers["special"]:
            kwargs["resources"].extra["job_recovery"] = leftovers["special"]["job_recovery"]
        if isinstance(autostop, bool):
            kwargs["autostop_minutes"] = 5 if autostop else None
        elif isinstance(autostop, (int, str)) and str(autostop).isdigit():
            kwargs["autostop_minutes"] = int(autostop)
        elif isinstance(autostop, dict):
            kwargs["autostop_minutes"] = autostop.get("idle_minutes", 5)
            kwargs["autostop_down"] = bool(autostop.get("down", False))
        return cls(**kwargs, extra=leftovers["rest"])


class JobSpec(_TaskSpec):
    """A SkyPilot managed job (``sky jobs launch``)."""

    kind: ClassVar[SpecKind] = SpecKind.JOB

    max_restarts_on_errors: int = Field(default=0, ge=0, le=100)
    recovery_strategy: str | None = None
    # Target pool (`sky jobs launch --pool`). Not part of the task YAML;
    # the spec store keeps it in a `# stratus: pool=<name>` header comment.
    pool: str | None = None

    @model_validator(mode="after")
    def _needs_run(self) -> "JobSpec":
        if not self.run:
            raise ValueError("run: a managed job needs a command to run")
        return self

    @field_validator("pool", mode="before")
    @classmethod
    def _pool(cls, v: Any) -> str | None:
        v = _clean_str(v)
        return _validate_name(v) if v else None

    @field_validator("recovery_strategy", mode="before")
    @classmethod
    def _strategy(cls, v: Any) -> str | None:
        v = _clean_str(v)
        if v is None or v.lower() in ("default", "auto"):
            return None
        v = v.upper()
        if v not in ("FAILOVER", "EAGER_NEXT_REGION"):
            raise ValueError("strategy must be FAILOVER or EAGER_NEXT_REGION")
        return v

    def to_yaml_dict(self) -> dict[str, Any]:
        recovery: dict[str, Any] = {}
        if self.recovery_strategy:
            recovery["strategy"] = self.recovery_strategy
        if self.max_restarts_on_errors:
            recovery["max_restarts_on_errors"] = self.max_restarts_on_errors
        return self._task_dict(self.resources.to_yaml_dict(job_recovery=recovery or None))

    @classmethod
    def from_yaml_dict(cls, name: str, data: dict[str, Any]) -> "JobSpec":
        kwargs, leftovers = cls._task_kwargs(name, data)
        recovery = leftovers["special"].get("job_recovery")
        if "autostop" in leftovers["special"]:
            kwargs["resources"].extra["autostop"] = leftovers["special"]["autostop"]
        if isinstance(recovery, str):
            kwargs["recovery_strategy"] = recovery
        elif isinstance(recovery, dict):
            kwargs["recovery_strategy"] = recovery.get("strategy")
            kwargs["max_restarts_on_errors"] = int(recovery.get("max_restarts_on_errors", 0) or 0)
        return cls(**kwargs, extra=leftovers["rest"])


class ServiceSpec(_TaskSpec):
    """A SkyServe service (``sky serve up``)."""

    kind: ClassVar[SpecKind] = SpecKind.SERVICE

    port: int = Field(default=8080, ge=1, le=65535)
    readiness_path: str = "/"
    initial_delay_seconds: int = Field(default=1200, ge=0)
    min_replicas: int = Field(default=1, ge=0, le=1000)
    max_replicas: int | None = Field(default=None, ge=1, le=1000)
    target_qps_per_replica: float | None = Field(default=None, gt=0)
    service_extra: dict[str, Any] = Field(default_factory=dict)

    @field_validator("readiness_path")
    @classmethod
    def _path(cls, v: str) -> str:
        v = (v or "/").strip()
        if not v.startswith("/"):
            raise ValueError("readiness path must start with '/'")
        return v

    @model_validator(mode="after")
    def _check(self) -> "ServiceSpec":
        if not self.run:
            raise ValueError("run: a service needs a command that starts the server")
        if self.max_replicas is not None:
            if self.max_replicas < self.min_replicas:
                raise ValueError("max_replicas: must be >= min replicas")
            if self.target_qps_per_replica is None:
                raise ValueError("target_qps_per_replica: required when max replicas is set (autoscaling)")
        return self

    def to_yaml_dict(self) -> dict[str, Any]:
        service: dict[str, Any] = {
            "ports": self.port,
            "readiness_probe": {"path": self.readiness_path, "initial_delay_seconds": self.initial_delay_seconds},
        }
        if self.max_replicas is None:
            service["replicas"] = self.min_replicas
        else:
            service["replica_policy"] = {
                "min_replicas": self.min_replicas,
                "max_replicas": self.max_replicas,
                "target_qps_per_replica": self.target_qps_per_replica,
            }
        service.update(self.service_extra)
        # SkyServe needs the port in both places: `service.ports` tells the load
        # balancer where to forward, `resources.ports` opens it on each replica.
        ports = list(self.resources.ports)
        if str(self.port) not in ports:
            ports.insert(0, str(self.port))
        out = self._task_dict(self.resources.model_copy(update={"ports": ports}).to_yaml_dict())
        # Put `service:` right after `name:` for readability.
        return {"name": out.pop("name"), "service": service, **out}

    @classmethod
    def from_yaml_dict(cls, name: str, data: dict[str, Any]) -> "ServiceSpec":
        data = dict(data or {})
        service = dict(data.pop("service", None) or {})
        kwargs, leftovers = cls._task_kwargs(name, data)
        for k, v in leftovers["special"].items():
            kwargs["resources"].extra[k] = v
        if service.get("ports") is not None:
            kwargs["port"] = int(service.pop("ports"))
            # The service port is re-added to resources.ports on render.
            rest = [p for p in kwargs["resources"].ports if p != str(kwargs["port"])]
            kwargs["resources"] = kwargs["resources"].model_copy(update={"ports": rest})
        elif len(kwargs["resources"].ports) == 1 and kwargs["resources"].ports[0].isdigit():
            # Older style: the single resources port is the service port.
            kwargs["port"] = int(kwargs["resources"].ports[0])
            kwargs["resources"] = kwargs["resources"].model_copy(update={"ports": []})
        probe = service.pop("readiness_probe", None)
        if isinstance(probe, str):
            kwargs["readiness_path"] = probe
        elif isinstance(probe, dict):
            kwargs["readiness_path"] = probe.get("path", "/")
            if "initial_delay_seconds" in probe:
                kwargs["initial_delay_seconds"] = probe["initial_delay_seconds"]
        if "replicas" in service:
            kwargs["min_replicas"] = int(service.pop("replicas"))
        policy = service.pop("replica_policy", None)
        if isinstance(policy, dict):
            kwargs["min_replicas"] = int(policy.get("min_replicas", 1))
            if policy.get("max_replicas") is not None:
                kwargs["max_replicas"] = int(policy["max_replicas"])
            if policy.get("target_qps_per_replica") is not None:
                kwargs["target_qps_per_replica"] = float(policy["target_qps_per_replica"])
        return cls(**kwargs, service_extra=service, extra=leftovers["rest"])


class PoolSpec(_TaskSpec):
    """A pool of pre-provisioned workers for managed jobs (``sky jobs pool apply``).

    Pools have setup but no ``run``: jobs submitted with ``--pool`` run on
    the workers.
    """

    kind: ClassVar[SpecKind] = SpecKind.POOL

    workers: int = Field(default=1, ge=1, le=1000)
    pool_extra: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _no_run(self) -> "PoolSpec":
        if self.run:
            raise ValueError("run: pools don't run commands; submit managed jobs to the pool instead")
        return self

    def to_yaml_dict(self) -> dict[str, Any]:
        out = self._task_dict(self.resources.to_yaml_dict())
        pool = {"workers": self.workers, **self.pool_extra}
        return {"name": out.pop("name"), "pool": pool, **out}

    @classmethod
    def from_yaml_dict(cls, name: str, data: dict[str, Any]) -> "PoolSpec":
        data = dict(data or {})
        pool = data.pop("pool", None)
        pool = dict(pool) if isinstance(pool, dict) else {}
        kwargs, leftovers = cls._task_kwargs(name, data)
        for k, v in leftovers["special"].items():
            kwargs["resources"].extra[k] = v
        if "workers" in pool:
            kwargs["workers"] = int(pool.pop("workers"))
        return cls(**kwargs, pool_extra=pool, extra=leftovers["rest"])


class VolumeSpec(BaseModel):
    """A SkyPilot volume (``sky volumes apply``)."""

    model_config = ConfigDict(extra="forbid")
    kind: ClassVar[SpecKind] = SpecKind.VOLUME

    name: str
    type: str = "k8s-pvc"
    infra: str | None = "k8s"
    size: str = "10Gi"
    storage_class: str | None = None
    access_mode: str | None = None
    labels: dict[str, str] = Field(default_factory=dict)
    config_extra: dict[str, Any] = Field(default_factory=dict)
    extra: dict[str, Any] = Field(default_factory=dict)

    @field_validator("name")
    @classmethod
    def _name_ok(cls, v: str) -> str:
        return _validate_name(v)

    @field_validator("infra", "storage_class", "access_mode", mode="before")
    @classmethod
    def _strip(cls, v: Any) -> str | None:
        return _clean_str(v)

    @field_validator("size")
    @classmethod
    def _size(cls, v: str) -> str:
        v = (v or "").strip()
        if not _SIZE_RE.match(v):
            raise ValueError("use a size like 10Gi, 100GB or 50")
        return v

    @field_validator("access_mode")
    @classmethod
    def _mode(cls, v: str | None) -> str | None:
        allowed = ("ReadWriteOnce", "ReadWriteOncePod", "ReadWriteMany", "ReadOnlyMany")
        if v is not None and v not in allowed:
            raise ValueError(f"access mode must be one of {', '.join(allowed)}")
        return v

    def to_yaml_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {"name": self.name, "type": self.type}
        if self.infra:
            out["infra"] = self.infra
        out["size"] = self.size
        config = dict(self.config_extra)
        if self.storage_class:
            config["storage_class_name"] = self.storage_class
        if self.access_mode:
            config["access_mode"] = self.access_mode
        if config:
            out["config"] = config
        if self.labels:
            out["labels"] = dict(self.labels)
        out.update(self.extra)
        return out

    def to_yaml(self) -> str:
        return dump_yaml(self.to_yaml_dict())

    @classmethod
    def from_yaml_dict(cls, name: str, data: dict[str, Any]) -> "VolumeSpec":
        data = dict(data or {})
        data.pop("name", None)
        config = dict(data.pop("config", None) or {})
        kwargs: dict[str, Any] = {"name": name}
        for key in ("type", "infra", "size"):
            if key in data:
                kwargs[key] = str(data.pop(key))
        if "storage_class_name" in config:
            kwargs["storage_class"] = config.pop("storage_class_name")
        if "access_mode" in config:
            kwargs["access_mode"] = config.pop("access_mode")
        if "labels" in data:
            kwargs["labels"] = {str(k): str(v) for k, v in (data.pop("labels") or {}).items()}
        return cls(**kwargs, config_extra=config, extra=data)


Spec = ClusterSpec | JobSpec | ServiceSpec | VolumeSpec | PoolSpec

SPEC_TYPES: dict[SpecKind, type] = {
    SpecKind.CLUSTER: ClusterSpec,
    SpecKind.JOB: JobSpec,
    SpecKind.SERVICE: ServiceSpec,
    SpecKind.VOLUME: VolumeSpec,
    SpecKind.POOL: PoolSpec,
}

_META_RE = re.compile(r"^#\s*stratus:\s*(.+)$", re.MULTILINE)


def read_meta(text: str) -> dict[str, str]:
    """Parse ``# stratus: key=value ...`` header comments."""
    meta: dict[str, str] = {}
    for m in _META_RE.finditer(text):
        for part in m.group(1).split():
            if "=" in part:
                k, v = part.split("=", 1)
                meta[k.strip()] = v.strip()
    return meta


def write_meta(spec: "Spec") -> str:
    """Header comment lines carrying stratus-only settings ('' if none)."""
    if isinstance(spec, JobSpec) and spec.pool:
        return f"# stratus: pool={spec.pool}\n"
    return ""


def spec_from_yaml(kind: SpecKind, name: str, text: str) -> Spec:
    data = yaml.safe_load(text) if text.strip() else {}
    if data is None:
        data = {}
    if not isinstance(data, dict):
        raise ValueError("top level of the YAML must be a mapping")
    spec = SPEC_TYPES[kind].from_yaml_dict(name, data)
    if kind is SpecKind.JOB:
        pool = read_meta(text).get("pool")
        if pool:
            spec = JobSpec.model_validate({**spec.model_dump(), "pool": pool})
    return spec


# ── YAML rendering ──────────────────────────────────────────────


class _Literal(str):
    """Marker for strings that should render as YAML block scalars."""


def _literal(text: str) -> str:
    return _Literal(text if text.endswith("\n") else text + "\n") if "\n" in text.strip() else text


class _Dumper(yaml.SafeDumper):
    pass


def _literal_repr(dumper: yaml.SafeDumper, data: _Literal):
    return dumper.represent_scalar("tag:yaml.org,2002:str", str(data), style="|")


_Dumper.add_representer(_Literal, _literal_repr)


def dump_yaml(data: dict[str, Any]) -> str:
    return yaml.dump(data, Dumper=_Dumper, sort_keys=False, default_flow_style=False, allow_unicode=True)
