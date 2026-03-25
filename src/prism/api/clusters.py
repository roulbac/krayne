"""Functional SDK for Ray cluster lifecycle management."""

from __future__ import annotations

import time
from typing import Any

from prism.api.types import (
    ClusterDetails,
    ClusterInfo,
    HeadNodeInfo,
    WorkerGroupInfo,
)
from prism.config.models import ClusterConfig
from prism.errors import ClusterTimeoutError
from prism.kube.client import DefaultKubeClient, KubeClient, _extract_status
from prism.kube.manifest import RAY_IMAGE, build_manifest


def _resolve_client(client: KubeClient | None) -> KubeClient:
    if client is None:
        return DefaultKubeClient()
    return client


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def create_cluster(
    config: ClusterConfig,
    *,
    client: KubeClient | None = None,
    wait: bool = False,
    timeout: int = 300,
) -> ClusterInfo:
    """Create a new Ray cluster from *config* and return its info."""
    kube = _resolve_client(client)
    manifest = build_manifest(config)
    obj = kube.create_ray_cluster(manifest)
    info = _obj_to_info(obj)
    if wait:
        return wait_until_ready(
            config.name, config.namespace, client=kube, timeout=timeout
        )
    return info


def get_cluster(
    name: str,
    namespace: str = "default",
    *,
    client: KubeClient | None = None,
) -> ClusterInfo:
    """Return summary info for a single cluster."""
    kube = _resolve_client(client)
    obj = kube.get_ray_cluster(name, namespace)
    return _obj_to_info(obj)


def list_clusters(
    namespace: str = "default",
    *,
    client: KubeClient | None = None,
) -> list[ClusterInfo]:
    """List all Ray clusters in *namespace*."""
    kube = _resolve_client(client)
    items = kube.list_ray_clusters(namespace)
    return [_obj_to_info(obj) for obj in items]


def describe_cluster(
    name: str,
    namespace: str = "default",
    *,
    client: KubeClient | None = None,
) -> ClusterDetails:
    """Return extended details for a cluster."""
    kube = _resolve_client(client)
    obj = kube.get_ray_cluster(name, namespace)
    return _obj_to_details(obj)


def scale_cluster(
    name: str,
    namespace: str,
    worker_group: str,
    replicas: int,
    *,
    client: KubeClient | None = None,
) -> ClusterInfo:
    """Scale *worker_group* of a cluster to *replicas*."""
    kube = _resolve_client(client)
    obj = kube.get_ray_cluster(name, namespace)

    worker_specs = obj.get("spec", {}).get("workerGroupSpecs", [])
    for spec in worker_specs:
        if spec.get("groupName") == worker_group:
            spec["replicas"] = replicas
            spec["minReplicas"] = replicas
            spec["maxReplicas"] = replicas
            break
    else:
        from prism.errors import PrismError

        raise PrismError(
            f"Worker group '{worker_group}' not found in cluster '{name}'"
        )

    patch = {"spec": {"workerGroupSpecs": worker_specs}}
    patched = kube.patch_ray_cluster(name, namespace, patch)
    return _obj_to_info(patched)


def delete_cluster(
    name: str,
    namespace: str = "default",
    *,
    client: KubeClient | None = None,
) -> None:
    """Delete a Ray cluster."""
    kube = _resolve_client(client)
    kube.delete_ray_cluster(name, namespace)


def wait_until_ready(
    name: str,
    namespace: str = "default",
    *,
    client: KubeClient | None = None,
    timeout: int = 300,
    _poll_interval: float = 2.0,
) -> ClusterInfo:
    """Poll until the cluster reaches *ready* state or *timeout* expires."""
    kube = _resolve_client(client)
    deadline = time.monotonic() + timeout
    while True:
        obj = kube.get_ray_cluster(name, namespace)
        status = _extract_status(obj)
        if status == "ready":
            return _obj_to_info(obj)
        if time.monotonic() >= deadline:
            raise ClusterTimeoutError(name, namespace, timeout)
        time.sleep(_poll_interval)


# ---------------------------------------------------------------------------
# Helpers — raw K8s object → SDK types
# ---------------------------------------------------------------------------


def _obj_to_info(obj: dict) -> ClusterInfo:
    metadata = obj.get("metadata", {})
    status_block = obj.get("status", {})
    spec = obj.get("spec", {})

    head_ip = status_block.get("head", {}).get("podIP") or status_block.get(
        "head", {}
    ).get("serviceIP")

    num_workers = sum(
        wg.get("replicas", 0) for wg in spec.get("workerGroupSpecs", [])
    )

    dashboard_url = None
    if head_ip:
        dashboard_url = f"http://{head_ip}:8265"

    return ClusterInfo(
        name=metadata.get("name", ""),
        namespace=metadata.get("namespace", ""),
        status=_extract_status(obj),
        head_ip=head_ip,
        dashboard_url=dashboard_url,
        notebook_url=None,
        vscode_url=None,
        num_workers=num_workers,
        created_at=metadata.get("creationTimestamp", ""),
    )


def _obj_to_details(obj: dict) -> ClusterDetails:
    info = _obj_to_info(obj)
    spec = obj.get("spec", {})

    head_spec = spec.get("headGroupSpec", {})
    head_container = (
        head_spec.get("template", {})
        .get("spec", {})
        .get("containers", [{}])[0]
    )
    head_res = head_container.get("resources", {}).get("requests", {})
    head_image = head_container.get("image", RAY_IMAGE)

    head = HeadNodeInfo(
        cpus=int(head_res.get("cpu", 0)),
        memory=str(head_res.get("memory", "0")),
        gpus=int(head_res.get("nvidia.com/gpu", 0)),
        image=head_image,
    )

    worker_groups: list[WorkerGroupInfo] = []
    for wg_spec in spec.get("workerGroupSpecs", []):
        container = (
            wg_spec.get("template", {})
            .get("spec", {})
            .get("containers", [{}])[0]
        )
        res = container.get("resources", {}).get("requests", {})
        ns = (
            wg_spec.get("template", {}).get("spec", {}).get("nodeSelector", {})
        )
        worker_groups.append(
            WorkerGroupInfo(
                name=wg_spec.get("groupName", ""),
                replicas=wg_spec.get("replicas", 0),
                cpus=int(res.get("cpu", 0)),
                memory=str(res.get("memory", "0")),
                gpus=int(res.get("nvidia.com/gpu", 0)),
                gpu_type=ns.get("cloud.google.com/gke-accelerator"),
            )
        )

    return ClusterDetails(
        info=info,
        head=head,
        worker_groups=worker_groups,
        ray_version="unknown",
        python_version="unknown",
    )
