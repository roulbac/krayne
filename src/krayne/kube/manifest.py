from __future__ import annotations

import platform
import sys
from functools import lru_cache

from kubernetes.utils.quantity import parse_quantity

from krayne.config.models import AutoscalerConfig, ClusterConfig, HeadNodeConfig, ServicesConfig, WorkerGroupConfig
from krayne.tunnel import SERVICE_PORTS

RAYCLUSTER_API_VERSION = "ray.io/v1"
RAYCLUSTER_KIND = "RayCluster"

CODE_SERVER_VERSION = "4.96.4"
_CS_ARCH = "arm64" if platform.machine() in ("arm64", "aarch64") else "amd64"
_CS_TARBALL = f"code-server-{CODE_SERVER_VERSION}-linux-{_CS_ARCH}.tar.gz"
_CS_URL = f"https://github.com/coder/code-server/releases/download/v{CODE_SERVER_VERSION}/{_CS_TARBALL}"
_CS_DIR = f"/tmp/code-server-{CODE_SERVER_VERSION}-linux-{_CS_ARCH}"


@lru_cache(maxsize=1)
def _get_ray_image() -> str:
    # `import ray` mutates termios (Ray's import chain calls tcsetattr for
    # color/signal setup), which corrupts Textual's raw-input + mouse-tracking
    # mode if this runs inside a TUI worker thread. Result is cached so that
    # pre-warming at TUI startup is enough to keep later calls pure.
    import ray

    ray_version = ray.__version__
    py_tag = f"py{sys.version_info.major}{sys.version_info.minor}"
    return f"rayproject/ray:{ray_version}-{py_tag}"


HEAD_MIN_CPUS = "1"
HEAD_MIN_MEMORY = "4Gi"


def _max_quantity(a: str, b: str) -> str:
    """Return whichever K8s quantity string is larger; preserves the original format."""
    return a if parse_quantity(a) >= parse_quantity(b) else b


def _cpus_to_ray(cpus: str) -> str:
    """Convert a K8s CPU quantity (e.g. '500m', '2') to a Ray num-cpus string."""
    qty = parse_quantity(cpus)
    return str(int(qty)) if qty == int(qty) else str(float(qty))


def build_manifest(config: ClusterConfig) -> dict:
    """Convert a *ClusterConfig* into a KubeRay ``RayCluster`` custom-resource dict."""
    spec: dict = {
        "headGroupSpec": _build_head_spec(config.head, config.services),
        "workerGroupSpecs": [
            _build_worker_spec(wg) for wg in config.worker_groups
        ],
    }

    if config.autoscaler.enabled:
        spec["enableInTreeAutoscaling"] = True
        spec["autoscalerOptions"] = _build_autoscaler_options(config.autoscaler)

    return {
        "apiVersion": RAYCLUSTER_API_VERSION,
        "kind": RAYCLUSTER_KIND,
        "metadata": {
            "name": config.name,
            "namespace": config.namespace,
            "labels": {
                "app.kubernetes.io/managed-by": "krayne",
                "app.kubernetes.io/name": config.name,
            },
        },
        "spec": spec,
    }


def _build_autoscaler_options(autoscaler: AutoscalerConfig) -> dict:
    """Build the ``autoscalerOptions`` section for the RayCluster spec."""
    return {
        "upscalingMode": autoscaler.upscaling_mode,
        "idleTimeoutSeconds": autoscaler.idle_timeout_seconds,
        "resources": {
            "limits": {"cpu": autoscaler.cpu, "memory": autoscaler.memory},
            "requests": {"cpu": autoscaler.cpu, "memory": autoscaler.memory},
        },
    }


def _build_head_spec(head: HeadNodeConfig, services: ServicesConfig) -> dict:
    image = head.image or _get_ray_image()
    # Head is always a control plane (rayStartParams.num-cpus="0"), but it still
    # needs real CPU/memory to run GCS, autoscaler, dashboard, and the postStart
    # services (jupyter/code-server/sshd). Clamp to a minimum so the pod can boot.
    cpus = _max_quantity(head.cpus, HEAD_MIN_CPUS)
    memory = _max_quantity(head.memory, HEAD_MIN_MEMORY)
    # requests == limits → Guaranteed QoS class.
    resources: dict[str, dict[str, str | int]] = {
        "requests": {"cpu": cpus, "memory": memory},
        "limits": {"cpu": cpus, "memory": memory},
    }

    # Only declare Ray-internal ports on the container. KubeRay auto-adds
    # all named container ports to the head Service, so notebook/ssh/code-server
    # are declared only in headService.spec.ports to avoid duplicates.
    ports: list[dict] = [
        {"containerPort": 6379, "name": "gcs-server"},
        {"containerPort": 8265, "name": "dashboard"},
        {"containerPort": 10001, "name": "client"},
    ]

    ray_head: dict = {
        "name": "ray-head",
        "image": image,
        "ports": ports,
        "resources": resources,
    }

    startup_cmds: list[str] = []
    if services.notebook:
        startup_cmds.append(
            "(pip install -q notebook"
            " && nohup jupyter notebook"
            " --ip=0.0.0.0 --port=8888 --no-browser --allow-root"
            " --NotebookApp.token=''"
            " > /tmp/jupyter.log 2>&1) &"
        )
    if services.code_server:
        startup_cmds.append(
            f"(wget -qO- {_CS_URL} | tar -xz -C /tmp"
            f" && nohup {_CS_DIR}/bin/code-server"
            " --auth none --bind-addr 0.0.0.0:8443"
            " > /tmp/code-server.log 2>&1) &"
        )
    if services.ssh:
        startup_cmds.append(
            "(which sshd && mkdir -p /run/sshd && /usr/sbin/sshd) || true"
        )
    if startup_cmds:
        ray_head["lifecycle"] = {
            "postStart": {
                "exec": {
                    "command": [
                        "/bin/sh", "-c", "\n".join(startup_cmds),
                    ]
                }
            }
        }

    containers: list[dict] = [ray_head]

    # KubeRay auto-adds ports from the ray-head container to the Service,
    # so only declare extra (non-Ray) service ports here to avoid duplicates.
    extra_svc_ports: list[dict] = [
        {"name": name, "port": SERVICE_PORTS[name][0], "targetPort": SERVICE_PORTS[name][0], "protocol": "TCP"}
        for name, enabled in (
            ("notebook", services.notebook),
            ("ssh", services.ssh),
            ("code-server", services.code_server),
        )
        if enabled
    ]

    head_service: dict = {"spec": {"type": "ClusterIP"}}
    if extra_svc_ports:
        head_service["spec"]["ports"] = extra_svc_ports

    pod_spec: dict = {
        "containers": containers,
    }

    # num-cpus=0 makes the head a control plane (Ray won't schedule tasks there).
    # When runs_tasks is True, advertise the same CPU count as the K8s container.
    num_cpus = _cpus_to_ray(cpus) if head.runs_tasks else "0"

    return {
        "rayStartParams": {"dashboard-host": "0.0.0.0", "num-cpus": num_cpus},
        "headService": head_service,
        "template": {
            "spec": pod_spec,
        },
    }


def _build_worker_spec(wg: WorkerGroupConfig) -> dict:
    image = wg.image or _get_ray_image()
    # requests == limits → Guaranteed QoS. KubeRay autodetects num-cpus, memory,
    # and (per the resource section of the docs) num-gpus from these limits.
    requests: dict[str, str | int] = {"cpu": wg.cpus, "memory": wg.memory}
    limits: dict[str, str | int] = {"cpu": wg.cpus, "memory": wg.memory}
    if wg.gpus > 0:
        requests["nvidia.com/gpu"] = wg.gpus
        limits["nvidia.com/gpu"] = wg.gpus

    container = {
        "name": "ray-worker",
        "image": image,
        "resources": {"requests": requests, "limits": limits},
    }

    return {
        "groupName": wg.name,
        "replicas": wg.replicas,
        "minReplicas": wg.min_replicas,
        "maxReplicas": wg.max_replicas,
        "rayStartParams": {},
        "template": {"spec": {"containers": [container]}},
    }
