"""Declarative form schemas and spec ⇄ form-value conversion.

Forms are flat ``{key: str|bool}`` dicts; each kind knows how to turn one into
a validated spec (and back). Unknown YAML keys carried by an existing spec are
preserved through ``base`` so editing in a form never drops hand edits.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal

from pydantic import ValidationError

from twisk.specs.models import (
    ClusterSpec,
    JobSpec,
    PoolSpec,
    Resources,
    ServiceSpec,
    Spec,
    SpecKind,
    VolumeSpec,
    _validate_name,
    format_kv_lines,
    parse_kv_lines,
    parse_mount_lines,
)

FieldType = Literal["text", "int", "bool", "select", "area"]

ACCELERATORS = [
    "H100:1", "H100:8", "H200:8", "B200:8", "A100:1", "A100:8", "A100-80GB:1", "A100-80GB:8",
    "L40S:1", "L4:1", "L4:4", "A10G:1", "A10:1", "T4:1", "V100:1", "RTX4090:1", "MI300X:8", "tpu-v5e-8",
]
INFRA_HINTS = ["k8s", "aws", "gcp", "azure", "lambda", "nebius", "runpod", "kubernetes", "ssh"]


@dataclass(frozen=True)
class FormField:
    key: str
    label: str
    type: FieldType = "text"
    section: str = "General"
    placeholder: str = ""
    help: str = ""
    options: tuple[tuple[str, str], ...] = ()
    suggestions: tuple[str, ...] = ()
    required: bool = False


@dataclass(frozen=True)
class FormSchema:
    kind: SpecKind
    fields: tuple[FormField, ...]
    sections: tuple[str, ...] = field(default=())

    def ordered_sections(self) -> list[str]:
        seen: list[str] = []
        for f in self.fields:
            if f.section not in seen:
                seen.append(f.section)
        return seen


# ── shared field groups ─────────────────────────────────────

def _resource_fields(section: str = "Resources", ports_help: str = "") -> tuple[FormField, ...]:
    return (
        FormField("infra", "Infra", section=section, placeholder="auto (cheapest enabled)",
                  help="k8s, aws, gcp/us-central1, k8s/my-context … empty = let SkyPilot choose", suggestions=tuple(INFRA_HINTS)),
        FormField("accelerators", "Accelerators", section=section, placeholder="none",
                  help="GPU/TPU as NAME:COUNT, e.g. A100:1, H100:8", suggestions=tuple(ACCELERATORS)),
        FormField("cpus", "vCPUs", section=section, placeholder="default", help="number, or N+ for 'at least N'"),
        FormField("memory", "Memory (GB)", section=section, placeholder="default", help="number, or N+ for 'at least N'"),
        FormField("disk_size", "Disk (GB)", "int", section=section, placeholder="default"),
        FormField("use_spot", "Use spot/preemptible", "bool", section=section),
        FormField("image_id", "Image", section=section, placeholder="SkyPilot default",
                  help="docker:<image> for containers, or a cloud image id"),
        FormField("ports", "Ports", section=section, placeholder="none", help=ports_help or "comma-separated, e.g. 8888, 6006"),
    )


def _task_fields(run_required: bool, with_run: bool = True) -> tuple[FormField, ...]:
    run = (
        FormField("run", "Run command", "area", section="Workload", required=run_required,
                  help="the main command" + ("" if run_required else " (optional for clusters)")),
    ) if with_run else ()
    return (
        FormField("num_nodes", "Nodes", "int", section="Workload", placeholder="1"),
        FormField("workdir", "Workdir", section="Workload", placeholder="none",
                  help="local directory synced to ~/sky_workdir (absolute path or ~/…)"),
        FormField("setup", "Setup commands", "area", section="Workload", help="runs once when nodes are provisioned"),
        *run,
        FormField("envs", "Environment", "area", section="Environment & storage", help="KEY=value, one per line"),
        FormField("volumes", "Volume mounts", "area", section="Environment & storage",
                  help="/mount/path=volume-name, one per line (see :volumes)"),
    )


SCHEMAS: dict[SpecKind, FormSchema] = {
    SpecKind.CLUSTER: FormSchema(
        SpecKind.CLUSTER,
        (
            FormField("name", "Cluster name", required=True, placeholder="my-cluster", help="lowercase, digits, '-'"),
            *_resource_fields(),
            FormField("autostop_minutes", "Autostop after idle (min)", "int", section="Lifecycle", placeholder="off",
                      help="stop the cluster after N idle minutes; empty = never (Kubernetes: needs tear down)"),
            FormField("autostop_down", "Tear down instead of stop", "bool", section="Lifecycle"),
            *_task_fields(run_required=False),
        ),
    ),
    SpecKind.JOB: FormSchema(
        SpecKind.JOB,
        (
            FormField("name", "Job name", required=True, placeholder="train-model"),
            *_resource_fields(),
            FormField("recovery_strategy", "Recovery strategy", "select", section="Recovery",
                      options=(("Default (FAILOVER)", ""), ("FAILOVER", "FAILOVER"), ("EAGER_NEXT_REGION", "EAGER_NEXT_REGION"))),
            FormField("max_restarts_on_errors", "Restarts on user errors", "int", section="Recovery", placeholder="0",
                      help="re-run the job this many times if it exits non-zero (preemptions are always recovered)"),
            FormField("pool", "Run in pool", section="Recovery", placeholder="none (dedicated cluster)",
                      help="submit to a pool's warm workers instead of provisioning (see :pools)"),
            *_task_fields(run_required=True),
        ),
    ),
    SpecKind.SERVICE: FormSchema(
        SpecKind.SERVICE,
        (
            FormField("name", "Service name", required=True, placeholder="llm-api"),
            *_resource_fields(ports_help="extra ports to open on replicas (the service port is opened for you)"),
            FormField("port", "Service port", "int", section="Service", required=True, placeholder="8080",
                      help="the port your server listens on; the load balancer forwards to it"),
            FormField("readiness_path", "Readiness path", section="Service", placeholder="/", help="HTTP GET path that returns 200 when ready"),
            FormField("initial_delay_seconds", "Readiness grace (s)", "int", section="Service", placeholder="1200"),
            FormField("min_replicas", "Replicas (min)", "int", section="Service", placeholder="1"),
            FormField("max_replicas", "Max replicas", "int", section="Service", placeholder="no autoscaling",
                      help="set to enable autoscaling"),
            FormField("target_qps_per_replica", "Target QPS / replica", section="Service", placeholder="-",
                      help="autoscaling target, required with max replicas"),
            *_task_fields(run_required=True),
        ),
    ),
    SpecKind.POOL: FormSchema(
        SpecKind.POOL,
        (
            FormField("name", "Pool name", required=True, placeholder="eval-pool"),
            FormField("workers", "Workers", "int", section="Pool", required=True, placeholder="1",
                      help="worker clusters kept warm; managed jobs submitted with this pool run on them"),
            *_resource_fields(),
            *_task_fields(run_required=False, with_run=False),
        ),
    ),
    SpecKind.VOLUME: FormSchema(
        SpecKind.VOLUME,
        (
            FormField("name", "Volume name", required=True, placeholder="datasets"),
            FormField("type", "Type", "select", options=(
                ("Kubernetes PVC (k8s-pvc)", "k8s-pvc"),
                ("RunPod network volume", "runpod-network-volume"),
            )),
            FormField("infra", "Infra", placeholder="k8s", help="k8s, k8s/<context>, runpod/<region>…", suggestions=("k8s", "kubernetes", "runpod")),
            FormField("size", "Size", required=True, placeholder="10Gi", help="e.g. 10Gi, 100GB"),
            FormField("storage_class", "Storage class", section="Kubernetes", placeholder="cluster default"),
            FormField("access_mode", "Access mode", "select", section="Kubernetes", options=(
                ("Default (ReadWriteOnce)", ""), ("ReadWriteOnce", "ReadWriteOnce"), ("ReadWriteMany", "ReadWriteMany"),
                ("ReadOnlyMany", "ReadOnlyMany"), ("ReadWriteOncePod", "ReadWriteOncePod"),
            )),
            FormField("labels", "Labels", "area", section="Kubernetes", help="key=value, one per line"),
        ),
    ),
}


DEFAULTS: dict[SpecKind, dict[str, Any]] = {
    SpecKind.CLUSTER: {"cpus": "1", "memory": "2"},
    SpecKind.JOB: {"cpus": "1", "memory": "2", "run": 'echo "hello from a managed job"; sleep 30'},
    SpecKind.SERVICE: {"cpus": "1", "memory": "2", "port": "8080", "readiness_path": "/",
                       "initial_delay_seconds": "300", "min_replicas": "1",
                       "run": "python3 -m http.server 8080"},
    SpecKind.VOLUME: {"type": "k8s-pvc", "infra": "k8s", "size": "1Gi"},
    SpecKind.POOL: {"cpus": "1", "memory": "2", "workers": "1", "setup": 'echo "worker ready"'},
}


class FormError(Exception):
    def __init__(self, errors: dict[str, str]) -> None:
        super().__init__("; ".join(f"{k}: {v}" for k, v in errors.items()))
        self.errors = errors


def _s(v: Any) -> str:
    return "" if v is None else str(v)


def spec_to_form(spec: Spec) -> dict[str, Any]:
    if isinstance(spec, VolumeSpec):
        return {
            "name": spec.name, "type": spec.type, "infra": _s(spec.infra), "size": spec.size,
            "storage_class": _s(spec.storage_class), "access_mode": _s(spec.access_mode),
            "labels": format_kv_lines(spec.labels),
        }
    r = spec.resources
    out: dict[str, Any] = {
        "name": spec.name, "infra": _s(r.infra), "accelerators": _s(r.accelerators), "cpus": _s(r.cpus),
        "memory": _s(r.memory), "disk_size": _s(r.disk_size), "use_spot": r.use_spot, "image_id": _s(r.image_id),
        "ports": ", ".join(r.ports), "num_nodes": "" if spec.num_nodes == 1 else str(spec.num_nodes),
        "workdir": _s(spec.workdir), "setup": _s(spec.setup), "run": _s(spec.run),
        "envs": format_kv_lines(spec.envs), "volumes": format_kv_lines(spec.volumes),
    }
    if isinstance(spec, ClusterSpec):
        out["autostop_minutes"] = _s(spec.autostop_minutes)
        out["autostop_down"] = spec.autostop_down
    elif isinstance(spec, PoolSpec):
        out["workers"] = str(spec.workers)
    elif isinstance(spec, JobSpec):
        out["pool"] = _s(spec.pool)
        out["recovery_strategy"] = _s(spec.recovery_strategy)
        out["max_restarts_on_errors"] = "" if not spec.max_restarts_on_errors else str(spec.max_restarts_on_errors)
    elif isinstance(spec, ServiceSpec):
        out.update(
            port=str(spec.port), readiness_path=spec.readiness_path, initial_delay_seconds=str(spec.initial_delay_seconds),
            min_replicas=str(spec.min_replicas), max_replicas=_s(spec.max_replicas),
            target_qps_per_replica=_s(spec.target_qps_per_replica),
        )
    return out


def default_form(kind: SpecKind) -> dict[str, Any]:
    return dict(DEFAULTS[kind])


def _opt_int(values: dict[str, Any], key: str, errors: dict[str, str]) -> int | None:
    raw = str(values.get(key, "") or "").strip()
    if not raw:
        return None
    try:
        return int(raw)
    except ValueError:
        errors[key] = "must be a whole number"
        return None


def _opt_float(values: dict[str, Any], key: str, errors: dict[str, str]) -> float | None:
    raw = str(values.get(key, "") or "").strip()
    if not raw:
        return None
    try:
        return float(raw)
    except ValueError:
        errors[key] = "must be a number"
        return None


def _lines(values: dict[str, Any], key: str, parser, errors: dict[str, str]) -> dict[str, str]:
    try:
        return parser(str(values.get(key, "") or ""))
    except ValueError as exc:
        errors[key] = str(exc)
        return {}


_ERROR_KEY = {
    ("resources",): "infra",
    ("autostop_minutes",): "autostop_minutes",
}


def _map_validation(exc: ValidationError, keys: set[str]) -> dict[str, str]:
    errors: dict[str, str] = {}
    for e in exc.errors():
        loc = [str(p) for p in e.get("loc", ())]
        msg = str(e.get("msg", "invalid"))
        msg = msg.removeprefix("Value error, ")
        key = next((p for p in reversed(loc) if p in keys), None)
        if key is None and ":" in msg:
            head, rest = msg.split(":", 1)
            if head.strip() in keys:
                key, msg = head.strip(), rest.strip()
        errors[key or "_form"] = msg
    return errors


def form_to_spec(kind: SpecKind, values: dict[str, Any], base: Spec | None = None) -> Spec:
    """Build a validated spec, raising :class:`FormError` keyed by field."""
    keys = {f.key for f in SCHEMAS[kind].fields}
    errors: dict[str, str] = {}
    name = str(values.get("name", "") or "").strip()
    try:
        _validate_name(name)
    except ValueError as exc:
        errors["name"] = str(exc)

    try:
        if kind is SpecKind.VOLUME:
            labels = _lines(values, "labels", parse_kv_lines, errors)
            kwargs: dict[str, Any] = dict(
                name=name, type=values.get("type") or "k8s-pvc", infra=values.get("infra") or None,
                size=str(values.get("size", "") or "").strip(), storage_class=values.get("storage_class") or None,
                access_mode=values.get("access_mode") or None, labels=labels,
            )
            if isinstance(base, VolumeSpec):
                kwargs.update(config_extra=base.config_extra, extra=base.extra)
            if errors:
                raise FormError(errors)
            return VolumeSpec(**kwargs)

        disk = _opt_int(values, "disk_size", errors)
        res_extra = base.resources.extra if base is not None and hasattr(base, "resources") else {}
        try:
            resources = Resources(
                infra=values.get("infra"), accelerators=values.get("accelerators"), cpus=values.get("cpus"),
                memory=values.get("memory"), disk_size=disk, use_spot=bool(values.get("use_spot")),
                image_id=values.get("image_id"), ports=values.get("ports") or [], extra=dict(res_extra),
            )
        except ValidationError as exc:
            errors.update(_map_validation(exc, keys))
            resources = Resources()
        num_nodes = _opt_int(values, "num_nodes", errors) or 1
        task = dict(
            name=name, resources=resources, num_nodes=num_nodes, workdir=values.get("workdir") or None,
            setup=values.get("setup") or None, run=(values.get("run") or None) if kind is not SpecKind.POOL else None,
            envs=_lines(values, "envs", parse_kv_lines, errors),
            volumes=_lines(values, "volumes", parse_mount_lines, errors),
        )
        if base is not None and type(base).kind is kind:
            task["extra"] = dict(base.extra)
        if kind is SpecKind.CLUSTER:
            autostop = _opt_int(values, "autostop_minutes", errors)
            down = bool(values.get("autostop_down"))
            infra = str(values.get("infra") or "").strip().lower()
            if autostop is not None and not down and infra.startswith(("k8s", "kubernetes")):
                errors["autostop_down"] = "Kubernetes can't stop clusters, only tear them down — turn this on"
            spec_kwargs = dict(task, autostop_minutes=autostop, autostop_down=down)
            cls = ClusterSpec
        elif kind is SpecKind.POOL:
            spec_kwargs = dict(task, workers=_opt_int(values, "workers", errors) or 1)
            if isinstance(base, PoolSpec):
                spec_kwargs["pool_extra"] = dict(base.pool_extra)
            cls = PoolSpec
        elif kind is SpecKind.JOB:
            spec_kwargs = dict(
                task, pool=values.get("pool") or None, recovery_strategy=values.get("recovery_strategy") or None,
                max_restarts_on_errors=_opt_int(values, "max_restarts_on_errors", errors) or 0,
            )
            cls = JobSpec
        else:
            spec_kwargs = dict(
                task, port=_opt_int(values, "port", errors) or 8080, readiness_path=values.get("readiness_path") or "/",
                initial_delay_seconds=_opt_int(values, "initial_delay_seconds", errors) or 1200,
                min_replicas=_opt_int(values, "min_replicas", errors) if str(values.get("min_replicas", "")).strip() else 1,
                max_replicas=_opt_int(values, "max_replicas", errors),
                target_qps_per_replica=_opt_float(values, "target_qps_per_replica", errors),
            )
            if isinstance(base, ServiceSpec):
                spec_kwargs["service_extra"] = dict(base.service_extra)
            cls = ServiceSpec
        if errors:
            raise FormError(errors)
        return cls(**spec_kwargs)
    except ValidationError as exc:
        errors.update(_map_validation(exc, keys))
        raise FormError(errors) from None
