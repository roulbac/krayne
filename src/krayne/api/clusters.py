from __future__ import annotations

import contextlib
import time
from collections.abc import Generator

from krayne.api.types import (
    ClusterDetails,
    ClusterInfo,
    HeadNodeInfo,
    TunnelSession,
    WorkerGroupInfo,
)
from krayne.config.models import ClusterConfig
from krayne.errors import ClusterTimeoutError, KrayneError
from krayne.kube.client import KubeClient, _extract_status, get_kube_client
from krayne.kube.manifest import _get_ray_image, build_manifest
from krayne.tunnel import SERVICE_PORTS, detect_services, head_port_names


def _resolve_client(
    client: KubeClient | None,
    kubeconfig: str | None = None,
    context: str | None = None,
) -> KubeClient:
    if client is not None:
        return client
    return get_kube_client(kubeconfig=kubeconfig, context=context)


def create_cluster(
    config: ClusterConfig,
    *,
    client: KubeClient | None = None,
    kubeconfig: str | None = None,
    wait: bool = False,
    timeout: int = 300,
) -> ClusterInfo:
    """Create a new Ray cluster from *config* and return its info."""
    kube = _resolve_client(client, kubeconfig)
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
    kubeconfig: str | None = None,
) -> ClusterInfo:
    """Return summary info for a single cluster."""
    kube = _resolve_client(client, kubeconfig)
    obj = kube.get_ray_cluster(name, namespace)
    pods = kube.list_pods(name, namespace)
    return _obj_to_info(obj, pods=pods)


def list_clusters(
    namespace: str = "default",
    *,
    client: KubeClient | None = None,
    kubeconfig: str | None = None,
) -> list[ClusterInfo]:
    """List all Ray clusters in *namespace*."""
    kube = _resolve_client(client, kubeconfig)
    items = kube.list_ray_clusters(namespace)
    results: list[ClusterInfo] = []
    for obj in items:
        cluster_name = obj.get("metadata", {}).get("name", "")
        pods = kube.list_pods(cluster_name, namespace)
        results.append(_obj_to_info(obj, pods=pods))
    return results


def describe_cluster(
    name: str,
    namespace: str = "default",
    *,
    client: KubeClient | None = None,
    kubeconfig: str | None = None,
) -> ClusterDetails:
    """Return extended details for a cluster."""
    kube = _resolve_client(client, kubeconfig)
    obj = kube.get_ray_cluster(name, namespace)
    pods = kube.list_pods(name, namespace)
    return _obj_to_details(obj, pods=pods)


def get_cluster_services(
    name: str,
    namespace: str = "default",
    *,
    client: KubeClient | None = None,
    kubeconfig: str | None = None,
) -> list[str]:
    """Return the list of service names exposed on the cluster head node."""
    kube = _resolve_client(client, kubeconfig)
    obj = kube.get_ray_cluster(name, namespace)
    return detect_services(obj)


def scale_cluster(
    name: str,
    namespace: str,
    worker_group: str,
    replicas: int | None = None,
    *,
    min_replicas: int | None = None,
    max_replicas: int | None = None,
    client: KubeClient | None = None,
    kubeconfig: str | None = None,
) -> ClusterInfo:
    """Scale *worker_group* of a cluster.

    When autoscaling is enabled on the cluster, only the explicitly provided
    fields (``replicas``, ``min_replicas``, ``max_replicas``) are patched.
    When autoscaling is disabled, all three are pinned to *replicas*.
    """
    if replicas is None and min_replicas is None and max_replicas is None:
        raise KrayneError("At least one of replicas, min_replicas, or max_replicas is required")

    kube = _resolve_client(client, kubeconfig)
    obj = kube.get_ray_cluster(name, namespace)

    autoscaling = obj.get("spec", {}).get("enableInTreeAutoscaling", False)
    worker_specs = obj.get("spec", {}).get("workerGroupSpecs", [])
    spec = next(
        (s for s in worker_specs if s.get("groupName") == worker_group), None
    )
    if spec is None:
        raise KrayneError(
            f"Worker group '{worker_group}' not found in cluster '{name}'"
        )

    if autoscaling:
        if replicas is not None:
            spec["replicas"] = replicas
        if min_replicas is not None:
            spec["minReplicas"] = min_replicas
        if max_replicas is not None:
            spec["maxReplicas"] = max_replicas
    else:
        target = replicas if replicas is not None else spec.get("replicas", 0)
        spec["replicas"] = target
        spec["minReplicas"] = target
        spec["maxReplicas"] = target

    patch = {"spec": {"workerGroupSpecs": worker_specs}}
    patched = kube.patch_ray_cluster(name, namespace, patch)
    return _obj_to_info(patched)


def delete_cluster(
    name: str,
    namespace: str = "default",
    *,
    client: KubeClient | None = None,
    kubeconfig: str | None = None,
) -> None:
    """Delete a Ray cluster."""
    kube = _resolve_client(client, kubeconfig)
    kube.delete_ray_cluster(name, namespace)


@contextlib.contextmanager
def open_tunnel(
    name: str,
    namespace: str = "default",
    *,
    client: KubeClient | None = None,
    kubeconfig: str | None = None,
) -> Generator[TunnelSession, None, None]:
    """Context manager that opens port-forward tunnels to all cluster services.

    Tunnels are automatically closed when the context exits.

    Usage::

        with open_tunnel("my-cluster") as session:
            ray.init(session.client_url)
            print(session.dashboard_url)
        # tunnels are closed here
    """
    from krayne.tunnel import start_tunnels, stop_tunnels

    kube = _resolve_client(client, kubeconfig)
    services = get_cluster_services(name, namespace, client=kube)
    tunnels = start_tunnels(name, namespace, services, kubeconfig=kubeconfig)
    try:
        yield TunnelSession(
            cluster_name=name,
            namespace=namespace,
            tunnels=tunnels,
        )
    finally:
        stop_tunnels(name, namespace)


def wait_until_ready(
    name: str,
    namespace: str = "default",
    *,
    client: KubeClient | None = None,
    kubeconfig: str | None = None,
    timeout: int = 300,
    _poll_interval: float = 2.0,
) -> ClusterInfo:
    """Poll until the cluster reaches *ready* state or *timeout* expires."""
    kube = _resolve_client(client, kubeconfig)
    deadline = time.monotonic() + timeout
    while True:
        obj = kube.get_ray_cluster(name, namespace)
        status = _extract_status(obj)
        if status == "ready":
            return _obj_to_info(obj)
        if time.monotonic() >= deadline:
            raise ClusterTimeoutError(name, namespace, timeout)
        time.sleep(_poll_interval)


def _service_url(service: str, head_ip: str) -> str:
    remote_port, scheme = SERVICE_PORTS[service]
    return f"{scheme}://{head_ip}:{remote_port}"


def _obj_to_info(
    obj: dict,
    pods: list[dict] | None = None,
) -> ClusterInfo:
    metadata = obj.get("metadata", {})
    status_block = obj.get("status", {})
    spec = obj.get("spec", {})

    head_ip = status_block.get("head", {}).get("podIP") or status_block.get(
        "head", {}
    ).get("serviceIP")

    num_workers = sum(
        wg.get("replicas", 0) for wg in spec.get("workerGroupSpecs", [])
    )

    port_names = head_port_names(obj)

    dashboard_url = client_url = None
    notebook_url = code_server_url = ssh_url = None
    if head_ip:
        dashboard_url = _service_url("dashboard", head_ip)
        client_url = _service_url("client", head_ip)
        if "notebook" in port_names:
            notebook_url = _service_url("notebook", head_ip)
        if "code-server" in port_names:
            code_server_url = _service_url("code-server", head_ip)
        if "ssh" in port_names:
            ssh_url = _service_url("ssh", head_ip)

    autoscaling_enabled = spec.get("enableInTreeAutoscaling", False)

    return ClusterInfo(
        name=metadata.get("name", ""),
        namespace=metadata.get("namespace", ""),
        status=_extract_status(obj, pods=pods),
        head_ip=head_ip,
        dashboard_url=dashboard_url,
        client_url=client_url,
        notebook_url=notebook_url,
        code_server_url=code_server_url,
        ssh_url=ssh_url,
        num_workers=num_workers,
        autoscaling_enabled=autoscaling_enabled,
        created_at=metadata.get("creationTimestamp", ""),
    )


def _obj_to_details(
    obj: dict,
    pods: list[dict] | None = None,
) -> ClusterDetails:
    info = _obj_to_info(obj, pods=pods)
    spec = obj.get("spec", {})

    head_spec = spec.get("headGroupSpec", {})
    head_container = (
        head_spec.get("template", {})
        .get("spec", {})
        .get("containers", [{}])[0]
    )
    head_res = head_container.get("resources", {}).get("requests", {})
    head_image = head_container.get("image") or _get_ray_image()
    # Head is schedulable iff rayStartParams.num-cpus is set to a non-zero value.
    head_num_cpus = head_spec.get("rayStartParams", {}).get("num-cpus", "0")
    runs_tasks = head_num_cpus not in ("0", "0.0", "", None)

    head = HeadNodeInfo(
        cpus=str(head_res.get("cpu", "0")),
        memory=str(head_res.get("memory", "0")),
        gpus=int(head_res.get("nvidia.com/gpu", 0)),
        image=head_image,
        runs_tasks=runs_tasks,
    )

    worker_groups: list[WorkerGroupInfo] = []
    for wg_spec in spec.get("workerGroupSpecs", []):
        container = (
            wg_spec.get("template", {})
            .get("spec", {})
            .get("containers", [{}])[0]
        )
        res = container.get("resources", {}).get("requests", {})
        worker_groups.append(
            WorkerGroupInfo(
                name=wg_spec.get("groupName", ""),
                replicas=wg_spec.get("replicas", 0),
                min_replicas=wg_spec.get("minReplicas", 0),
                max_replicas=wg_spec.get("maxReplicas", 0),
                cpus=str(res.get("cpu", "0")),
                memory=str(res.get("memory", "0")),
                gpus=int(res.get("nvidia.com/gpu", 0)),
            )
        )

    # Extract ray version from image tag (e.g. "rayproject/ray:2.41.0" → "2.41.0")
    ray_version = "unknown"
    if ":" in head_image:
        tag = head_image.rsplit(":", 1)[1]
        # Strip architecture suffixes like "-aarch64"
        ray_version = tag.split("-")[0] if tag else "unknown"

    return ClusterDetails(
        info=info,
        head=head,
        worker_groups=worker_groups,
        ray_version=ray_version,
        python_version="unknown",
    )
