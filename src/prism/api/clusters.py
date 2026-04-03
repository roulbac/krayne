from __future__ import annotations

import contextlib
import time
from collections.abc import Generator
from typing import Any

from prism.api.types import (
    ClusterDetails,
    ClusterInfo,
    HeadNodeInfo,
    ManagedClusterResult,
    TunnelSession,
    WorkerGroupInfo,
)
from prism.config.models import ClusterConfig
from prism.config.settings import load_prism_settings
from prism.errors import ClusterTimeoutError
from prism.kube.client import DefaultKubeClient, KubeClient, _extract_status
from prism.kube.manifest import RAY_IMAGE, build_manifest


def _resolve_client(
    client: KubeClient | None,
    kubeconfig: str | None = None,
    context: str | None = None,
) -> KubeClient:
    if client is not None:
        return client
    if kubeconfig is None:
        settings = load_prism_settings()
        kubeconfig = settings.kubeconfig
        if context is None:
            context = settings.kube_context
    return DefaultKubeClient(kubeconfig=kubeconfig, context=context)


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
    info = _obj_to_info(obj, client=kube)
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
    return _obj_to_info(obj, pods=pods, client=kube)


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
        results.append(_obj_to_info(obj, pods=pods, client=kube))
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
    return _obj_to_details(obj, pods=pods, client=kube)


def get_cluster_services(
    name: str,
    namespace: str = "default",
    *,
    client: KubeClient | None = None,
    kubeconfig: str | None = None,
) -> list[str]:
    """Return the list of service names exposed on the cluster head node."""
    from prism.tunnel import detect_services

    kube = _resolve_client(client, kubeconfig)
    obj = kube.get_ray_cluster(name, namespace)
    return detect_services(obj)


def scale_cluster(
    name: str,
    namespace: str,
    worker_group: str,
    replicas: int,
    *,
    client: KubeClient | None = None,
    kubeconfig: str | None = None,
) -> ClusterInfo:
    """Scale *worker_group* of a cluster to *replicas*."""
    kube = _resolve_client(client, kubeconfig)
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
    return _obj_to_info(patched, client=kube)


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
def managed_cluster(
    config: ClusterConfig,
    *,
    client: KubeClient | None = None,
    kubeconfig: str | None = None,
    timeout: int = 300,
    tunnel: bool = True,
) -> Generator[ManagedClusterResult, None, None]:
    """Context manager that creates a Ray cluster, waits for readiness, and deletes it on exit.

    When *tunnel* is ``True`` (the default), port-forward tunnels are
    opened to all detected services after the cluster becomes ready.
    Access cluster URLs via ``result.cluster`` and tunnel URLs via
    ``result.tunnel``.

    Usage::

        with managed_cluster(config) as managed:
            ray.init(managed.tunnel.client_url)   # ray://localhost:...
            print(managed.cluster.client_url)      # ray://10.0.0.1:10001
            ...
        # tunnels closed, then cluster deleted
    """
    from prism.tunnel import start_tunnels, stop_tunnels

    kube = _resolve_client(client, kubeconfig)
    info = create_cluster(config, client=kube, wait=True, timeout=timeout)
    session: TunnelSession | None = None
    try:
        if tunnel:
            services = get_cluster_services(
                config.name, config.namespace, client=kube
            )
            tunnel_infos = start_tunnels(
                config.name, config.namespace, services, kubeconfig=kubeconfig
            )
            session = TunnelSession(
                cluster_name=config.name,
                namespace=config.namespace,
                tunnels=tunnel_infos,
            )
        yield ManagedClusterResult(cluster=info, tunnel=session)
    finally:
        if session is not None:
            stop_tunnels(config.name, config.namespace)
        delete_cluster(config.name, config.namespace, client=kube)


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
    from prism.tunnel import start_tunnels, stop_tunnels

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
            return _obj_to_info(obj, client=kube)
        if time.monotonic() >= deadline:
            raise ClusterTimeoutError(name, namespace, timeout)
        time.sleep(_poll_interval)


def _head_port_names(obj: dict) -> set[str]:
    """Collect port names from head containers and headService spec."""
    head_spec = obj.get("spec", {}).get("headGroupSpec", {})
    containers = (
        head_spec.get("template", {}).get("spec", {}).get("containers", [])
    )
    names: set[str] = set()
    for container in containers:
        for port in container.get("ports", []):
            name = port.get("name")
            if name:
                names.add(name)
    # Also check headService.spec.ports for extra service ports
    for port in head_spec.get("headService", {}).get("spec", {}).get("ports", []):
        name = port.get("name")
        if name:
            names.add(name)
    return names


def _obj_to_info(
    obj: dict,
    pods: list[dict] | None = None,
    client: KubeClient | None = None,
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

    port_names = _head_port_names(obj)

    dashboard_url = None
    client_url = None
    notebook_url = None
    code_server_url = None
    ssh_url = None
    if head_ip:
        dashboard_url = f"http://{head_ip}:8265"
        client_url = f"ray://{head_ip}:10001"
        if "notebook" in port_names:
            notebook_url = f"http://{head_ip}:8888"
        if "code-server" in port_names:
            code_server_url = f"http://{head_ip}:8443"
        if "ssh" in port_names:
            ssh_url = f"ssh://{head_ip}:22"

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
        created_at=metadata.get("creationTimestamp", ""),
    )


def _obj_to_details(
    obj: dict,
    pods: list[dict] | None = None,
    client: KubeClient | None = None,
) -> ClusterDetails:
    info = _obj_to_info(obj, pods=pods, client=client)
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
        cpus=str(head_res.get("cpu", "0")),
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
                cpus=str(res.get("cpu", "0")),
                memory=str(res.get("memory", "0")),
                gpus=int(res.get("nvidia.com/gpu", 0)),
                gpu_type=ns.get("cloud.google.com/gke-accelerator"),
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
