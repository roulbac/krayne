from __future__ import annotations

import time
from typing import Any

import anyio

from prism.api.types import (
    ClusterDetails,
    ClusterInfo,
    HeadNodeInfo,
    WorkerGroupInfo,
)
from prism.config.models import ClusterConfig
from prism.config.settings import _async_load_prism_settings, load_prism_settings
from prism.errors import ClusterTimeoutError
from prism.kube.client import (
    AsyncKubeClient,
    DefaultAsyncKubeClient,
    DefaultKubeClient,
    KubeClient,
    _SyncToAsyncClientAdapter,
    _extract_status,
)
from prism.kube.manifest import RAY_IMAGE, build_manifest

from prism._async_utils import _run_sync


# ---------------------------------------------------------------------------
# Async internal implementations
# ---------------------------------------------------------------------------


async def _async_resolve_client(
    client: KubeClient | AsyncKubeClient | None,
    kubeconfig: str | None = None,
    context: str | None = None,
) -> AsyncKubeClient:
    if client is not None:
        if isinstance(client, (DefaultAsyncKubeClient, _SyncToAsyncClientAdapter)):
            return client
        return _SyncToAsyncClientAdapter(client)
    if kubeconfig is None:
        settings = await _async_load_prism_settings()
        kubeconfig = settings.kubeconfig
        if context is None:
            context = settings.kube_context
    return DefaultAsyncKubeClient(kubeconfig=kubeconfig, context=context)


async def _async_create_cluster(
    config: ClusterConfig,
    *,
    client: KubeClient | AsyncKubeClient | None = None,
    kubeconfig: str | None = None,
    wait: bool = False,
    timeout: int = 300,
) -> ClusterInfo:
    kube = await _async_resolve_client(client, kubeconfig)
    manifest = build_manifest(config)
    obj = await kube.create_ray_cluster(manifest)
    info = await _async_obj_to_info(obj, client=kube)
    if wait:
        return await _async_wait_until_ready(
            config.name, config.namespace, client=kube, timeout=timeout
        )
    return info


async def _async_get_cluster(
    name: str,
    namespace: str = "default",
    *,
    client: KubeClient | AsyncKubeClient | None = None,
    kubeconfig: str | None = None,
) -> ClusterInfo:
    kube = await _async_resolve_client(client, kubeconfig)
    obj: dict = {}
    pods: list[dict] = []

    async def _fetch_obj() -> None:
        nonlocal obj
        obj = await kube.get_ray_cluster(name, namespace)

    async def _fetch_pods() -> None:
        nonlocal pods
        pods = await kube.list_pods(name, namespace)

    async with anyio.create_task_group() as tg:
        tg.start_soon(_fetch_obj)
        tg.start_soon(_fetch_pods)

    return await _async_obj_to_info(obj, pods=pods, client=kube)


async def _async_list_clusters(
    namespace: str = "default",
    *,
    client: KubeClient | AsyncKubeClient | None = None,
    kubeconfig: str | None = None,
) -> list[ClusterInfo]:
    kube = await _async_resolve_client(client, kubeconfig)
    items = await kube.list_ray_clusters(namespace)
    pods_map: dict[str, list[dict]] = {}

    async with anyio.create_task_group() as tg:
        for obj in items:
            cluster_name = obj.get("metadata", {}).get("name", "")

            async def _fetch_pods(cn: str = cluster_name) -> None:
                pods_map[cn] = await kube.list_pods(cn, namespace)

            tg.start_soon(_fetch_pods)

    results: list[ClusterInfo] = []
    for obj in items:
        cn = obj.get("metadata", {}).get("name", "")
        results.append(
            await _async_obj_to_info(obj, pods=pods_map.get(cn), client=kube)
        )
    return results


async def _async_describe_cluster(
    name: str,
    namespace: str = "default",
    *,
    client: KubeClient | AsyncKubeClient | None = None,
    kubeconfig: str | None = None,
) -> ClusterDetails:
    kube = await _async_resolve_client(client, kubeconfig)
    obj: dict = {}
    pods: list[dict] = []

    async def _fetch_obj() -> None:
        nonlocal obj
        obj = await kube.get_ray_cluster(name, namespace)

    async def _fetch_pods() -> None:
        nonlocal pods
        pods = await kube.list_pods(name, namespace)

    async with anyio.create_task_group() as tg:
        tg.start_soon(_fetch_obj)
        tg.start_soon(_fetch_pods)

    return await _async_obj_to_details(obj, pods=pods, client=kube)


async def _async_scale_cluster(
    name: str,
    namespace: str,
    worker_group: str,
    replicas: int,
    *,
    client: KubeClient | AsyncKubeClient | None = None,
    kubeconfig: str | None = None,
) -> ClusterInfo:
    kube = await _async_resolve_client(client, kubeconfig)
    obj = await kube.get_ray_cluster(name, namespace)

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
    patched = await kube.patch_ray_cluster(name, namespace, patch)
    return await _async_obj_to_info(patched, client=kube)


async def _async_delete_cluster(
    name: str,
    namespace: str = "default",
    *,
    client: KubeClient | AsyncKubeClient | None = None,
    kubeconfig: str | None = None,
) -> None:
    kube = await _async_resolve_client(client, kubeconfig)
    await kube.delete_ray_cluster(name, namespace)


async def _async_wait_until_ready(
    name: str,
    namespace: str = "default",
    *,
    client: KubeClient | AsyncKubeClient | None = None,
    kubeconfig: str | None = None,
    timeout: int = 300,
    _poll_interval: float = 2.0,
) -> ClusterInfo:
    kube = await _async_resolve_client(client, kubeconfig)
    deadline = time.monotonic() + timeout
    while True:
        obj = await kube.get_ray_cluster(name, namespace)
        status = _extract_status(obj)
        if status == "ready":
            return await _async_obj_to_info(obj, client=kube)
        if time.monotonic() >= deadline:
            raise ClusterTimeoutError(name, namespace, timeout)
        await anyio.sleep(_poll_interval)


def _is_sandbox() -> bool:
    from prism.sandbox.manager import SANDBOX_KUBECONFIG

    settings = load_prism_settings()
    return settings.kubeconfig == str(SANDBOX_KUBECONFIG)


async def _async_obj_to_info(
    obj: dict,
    pods: list[dict] | None = None,
    client: AsyncKubeClient | None = None,
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

    dashboard_url = None
    client_url = None
    if head_ip:
        dashboard_url = f"http://{head_ip}:8265"
        client_url = f"ray://{head_ip}:10001"

    # On sandbox, rewrite URLs to use localhost + NodePort
    if client is not None and head_ip and _is_sandbox():
        cluster_name = metadata.get("name", "")
        namespace = metadata.get("namespace", "")
        dashboard_np: int | None = None
        client_np: int | None = None

        async def _get_dashboard() -> None:
            nonlocal dashboard_np
            dashboard_np = await client.get_head_node_port(
                cluster_name, namespace, "dashboard"
            )

        async def _get_client() -> None:
            nonlocal client_np
            client_np = await client.get_head_node_port(
                cluster_name, namespace, "client"
            )

        async with anyio.create_task_group() as tg:
            tg.start_soon(_get_dashboard)
            tg.start_soon(_get_client)

        if dashboard_np:
            dashboard_url = f"http://localhost:{dashboard_np}"
        if client_np:
            client_url = f"ray://localhost:{client_np}"

    return ClusterInfo(
        name=metadata.get("name", ""),
        namespace=metadata.get("namespace", ""),
        status=_extract_status(obj, pods=pods),
        head_ip=head_ip,
        dashboard_url=dashboard_url,
        client_url=client_url,
        notebook_url=None,
        vscode_url=None,
        num_workers=num_workers,
        created_at=metadata.get("creationTimestamp", ""),
    )


async def _async_obj_to_details(
    obj: dict,
    pods: list[dict] | None = None,
    client: AsyncKubeClient | None = None,
) -> ClusterDetails:
    info = await _async_obj_to_info(obj, pods=pods, client=client)
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

    return ClusterDetails(
        info=info,
        head=head,
        worker_groups=worker_groups,
        ray_version="unknown",
        python_version="unknown",
    )


# ---------------------------------------------------------------------------
# Public sync API (unchanged signatures)
# ---------------------------------------------------------------------------


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
    return _run_sync(
        _async_create_cluster,
        config,
        client=client,
        kubeconfig=kubeconfig,
        wait=wait,
        timeout=timeout,
    )


def get_cluster(
    name: str,
    namespace: str = "default",
    *,
    client: KubeClient | None = None,
    kubeconfig: str | None = None,
) -> ClusterInfo:
    """Return summary info for a single cluster."""
    return _run_sync(
        _async_get_cluster, name, namespace, client=client, kubeconfig=kubeconfig
    )


def list_clusters(
    namespace: str = "default",
    *,
    client: KubeClient | None = None,
    kubeconfig: str | None = None,
) -> list[ClusterInfo]:
    """List all Ray clusters in *namespace*."""
    return _run_sync(
        _async_list_clusters, namespace, client=client, kubeconfig=kubeconfig
    )


def describe_cluster(
    name: str,
    namespace: str = "default",
    *,
    client: KubeClient | None = None,
    kubeconfig: str | None = None,
) -> ClusterDetails:
    """Return extended details for a cluster."""
    return _run_sync(
        _async_describe_cluster,
        name,
        namespace,
        client=client,
        kubeconfig=kubeconfig,
    )


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
    return _run_sync(
        _async_scale_cluster,
        name,
        namespace,
        worker_group,
        replicas,
        client=client,
        kubeconfig=kubeconfig,
    )


def delete_cluster(
    name: str,
    namespace: str = "default",
    *,
    client: KubeClient | None = None,
    kubeconfig: str | None = None,
) -> None:
    """Delete a Ray cluster."""
    _run_sync(
        _async_delete_cluster, name, namespace, client=client, kubeconfig=kubeconfig
    )


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
    return _run_sync(
        _async_wait_until_ready,
        name,
        namespace,
        client=client,
        kubeconfig=kubeconfig,
        timeout=timeout,
        _poll_interval=_poll_interval,
    )
