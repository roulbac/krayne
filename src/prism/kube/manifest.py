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
_VSCODE_VERSION = os.environ.get("PRISM_VSCODE_VERSION", "latest")
VSCODE_IMAGE = f"codercom/code-server:{_VSCODE_VERSION}"
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
    resources: dict[str, dict[str, str | int]] = {
        "requests": {"cpu": head.cpus, "memory": head.memory},
        "limits": {"cpu": head.cpus, "memory": head.memory},
    }
    if head.gpus > 0:
        resources["limits"]["nvidia.com/gpu"] = head.gpus
        resources["requests"]["nvidia.com/gpu"] = head.gpus

    ports: list[dict] = [
        {"containerPort": 6379, "name": "gcs-server"},
        {"containerPort": 8265, "name": "dashboard"},
        {"containerPort": 10001, "name": "client"},
    ]
    if services.notebook:
        ports.append({"containerPort": 8888, "name": "notebook"})
    if services.ssh:
        ports.append({"containerPort": 22, "name": "ssh"})

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

    if services.vscode_server:
        containers.append({
            "name": "vscode",
            "image": VSCODE_IMAGE,
            "ports": [{"containerPort": 8080, "name": "vscode"}],
            "args": ["--auth", "none", "--bind-addr", "0.0.0.0:8080"],
        })

    return {
        "serviceType": "ClusterIP",
        "enableIngress": False,
        "rayStartParams": {"dashboard-host": "0.0.0.0"},
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
