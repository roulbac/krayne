"""Pure-function manifest builder: ClusterConfig -> KubeRay RayCluster dict."""

from __future__ import annotations

import platform

from prism.config.models import ClusterConfig, HeadNodeConfig, WorkerGroupConfig

_RAY_VERSION = "2.41.0"
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
            "headGroupSpec": _build_head_spec(config.head),
            "workerGroupSpecs": [
                _build_worker_spec(wg) for wg in config.worker_groups
            ],
        },
    }


def _build_head_spec(head: HeadNodeConfig) -> dict:
    image = head.image or RAY_IMAGE
    resources: dict[str, dict[str, str | int]] = {
        "requests": {"cpu": head.cpus, "memory": head.memory},
        "limits": {"cpu": head.cpus, "memory": head.memory},
    }
    if head.gpus > 0:
        resources["limits"]["nvidia.com/gpu"] = head.gpus
        resources["requests"]["nvidia.com/gpu"] = head.gpus

    return {
        "serviceType": "NodePort",
        "rayStartParams": {"dashboard-host": "0.0.0.0"},
        "template": {
            "spec": {
                "containers": [
                    {
                        "name": "ray-head",
                        "image": image,
                        "resources": resources,
                        "ports": [
                            {"containerPort": 6379, "name": "gcs-server"},
                            {"containerPort": 8265, "name": "dashboard"},
                            {"containerPort": 10001, "name": "client"},
                        ],
                    }
                ],
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
