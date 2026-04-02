from __future__ import annotations

import os
import platform

from prism.config.models import ClusterConfig, HeadNodeConfig, ServicesConfig, WorkerGroupConfig

_RAY_VERSION = os.environ.get("PRISM_RAY_VERSION", "latest")
RAY_IMAGE = (
    f"rayproject/ray:{_RAY_VERSION}-aarch64"
    if platform.machine() == "arm64"
    else f"rayproject/ray:{_RAY_VERSION}"
)
RAYCLUSTER_API_VERSION = "ray.io/v1"
RAYCLUSTER_KIND = "RayCluster"


def build_manifest(config: ClusterConfig) -> dict:
    """Convert a *ClusterConfig* into a KubeRay ``RayCluster`` custom-resource dict."""
    return {
        "apiVersion": RAYCLUSTER_API_VERSION,
        "kind": RAYCLUSTER_KIND,
        "metadata": {
            "name": config.name,
            "namespace": config.namespace,
            "labels": {
                "app.kubernetes.io/managed-by": "prism",
                "app.kubernetes.io/name": config.name,
            },
        },
        "spec": {
            "headGroupSpec": _build_head_spec(config.head, config.services),
            "workerGroupSpecs": [
                _build_worker_spec(wg) for wg in config.worker_groups
            ],
        },
    }


def _build_head_spec(head: HeadNodeConfig, services: ServicesConfig) -> dict:
    image = head.image or RAY_IMAGE
    # Only set requests (not limits) for CPU/memory so the head can burst
    # during service installation (notebook, code-server).  GPU limits are
    # still set since they must be equal to requests.
    resources: dict[str, dict[str, str | int]] = {
        "requests": {"cpu": head.cpus, "memory": head.memory},
    }
    if head.gpus > 0:
        resources["limits"] = {"nvidia.com/gpu": head.gpus}
        resources["requests"]["nvidia.com/gpu"] = head.gpus

    # Only declare Ray-internal ports on the container. KubeRay auto-adds
    # all named container ports to the head Service, so notebook/ssh/code-server
    # are declared only in headService.spec.ports to avoid duplicates.
    ports: list[dict] = [
        {"containerPort": 6379, "name": "gcs-server"},
        {"containerPort": 8265, "name": "dashboard"},
        {"containerPort": 10001, "name": "client"},
    ]

    containers: list[dict] = [
        {
            "name": "ray-head",
            "image": image,
            "resources": resources,
            "ports": ports,
        }
    ]

    # Build a postStart lifecycle hook to start optional services.
    startup_cmds: list[str] = []
    if services.notebook:
        startup_cmds.append(
            "(uv pip install --system notebook"
            " && nohup jupyter notebook"
            " --ip=0.0.0.0 --port=8888 --no-browser --allow-root"
            " --NotebookApp.token=''"
            " > /tmp/jupyter.log 2>&1) &"
        )
    if services.code_server:
        startup_cmds.append(
            "(sudo apt-get update -qq && sudo apt-get install -y -qq curl > /dev/null"
            " && curl -fsSL https://code-server.dev/install.sh | sh"
            " && nohup code-server"
            " --auth none --bind-addr 0.0.0.0:8443"
            " > /tmp/code-server.log 2>&1) &"
        )
    if services.ssh:
        startup_cmds.append(
            "(which sshd && mkdir -p /run/sshd && /usr/sbin/sshd) || true"
        )
    if startup_cmds:
        containers[0]["lifecycle"] = {
            "postStart": {
                "exec": {
                    "command": [
                        "/bin/sh", "-c", "\n".join(startup_cmds),
                    ]
                }
            }
        }

    # KubeRay auto-adds ports from the ray-head container to the Service,
    # so only declare extra (non-Ray) service ports here to avoid duplicates.
    extra_svc_ports: list[dict] = []
    if services.notebook:
        extra_svc_ports.append({"name": "notebook", "port": 8888, "targetPort": 8888, "protocol": "TCP"})
    if services.ssh:
        extra_svc_ports.append({"name": "ssh", "port": 22, "targetPort": 22, "protocol": "TCP"})
    if services.code_server:
        extra_svc_ports.append({"name": "code-server", "port": 8443, "targetPort": 8443, "protocol": "TCP"})

    head_service: dict = {"spec": {"type": "ClusterIP"}}
    if extra_svc_ports:
        head_service["spec"]["ports"] = extra_svc_ports

    return {
        "rayStartParams": {"dashboard-host": "0.0.0.0"},
        "headService": head_service,
        "template": {
            "spec": {
                "containers": containers,
            }
        },
    }


def _build_worker_spec(wg: WorkerGroupConfig) -> dict:
    image = wg.image or RAY_IMAGE
    resources: dict[str, dict[str, str | int]] = {
        "requests": {"cpu": wg.cpus, "memory": wg.memory},
        "limits": {"cpu": wg.cpus, "memory": wg.memory},
    }
    node_selector: dict[str, str] = {}
    if wg.gpus > 0:
        resources["limits"]["nvidia.com/gpu"] = wg.gpus
        resources["requests"]["nvidia.com/gpu"] = wg.gpus
        node_selector["cloud.google.com/gke-accelerator"] = wg.gpu_type

    spec: dict = {
        "containers": [
            {
                "name": "ray-worker",
                "image": image,
                "resources": resources,
            }
        ],
    }
    if node_selector:
        spec["nodeSelector"] = node_selector

    return {
        "groupName": wg.name,
        "replicas": wg.replicas,
        "minReplicas": wg.replicas,
        "maxReplicas": wg.replicas,
        "rayStartParams": {},
        "template": {"spec": spec},
    }
