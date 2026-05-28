from __future__ import annotations

import hashlib
from collections.abc import Iterator
from typing import Any, Protocol, runtime_checkable

from kubernetes import client as k8s_client  # type: ignore[import-untyped]
from kubernetes import config as k8s_config  # type: ignore[import-untyped]
from kubernetes import watch as k8s_watch  # type: ignore[import-untyped]
from kubernetes.client.exceptions import ApiException  # type: ignore[import-untyped]
from kubernetes.stream import portforward as k8s_portforward  # type: ignore[import-untyped]

from krayne.errors import (
    ClusterAlreadyExistsError,
    ClusterNotFoundError,
    KubeConnectionError,
    KubeRayNotInstalledError,
    NamespaceNotFoundError,
)

RAYCLUSTER_CRD_NAME = "rayclusters.ray.io"

RAYCLUSTER_GROUP = "ray.io"
RAYCLUSTER_VERSION = "v1"
RAYCLUSTER_PLURAL = "rayclusters"


@runtime_checkable
class KubeClient(Protocol):
    """Structural interface for Kubernetes operations on RayCluster CRDs."""

    def create_ray_cluster(self, manifest: dict) -> dict: ...
    def get_ray_cluster(self, name: str, namespace: str) -> dict: ...
    def list_ray_clusters(self, namespace: str) -> list[dict]: ...
    def patch_ray_cluster(self, name: str, namespace: str, patch: dict) -> dict: ...
    def delete_ray_cluster(self, name: str, namespace: str) -> None: ...
    def get_cluster_status(self, name: str, namespace: str) -> str: ...
    def list_pods(self, cluster_name: str, namespace: str) -> list[dict]: ...
    def get_head_node_port(self, cluster_name: str, namespace: str, port_name: str) -> int | None: ...
    def list_namespaces(self) -> list[str]: ...
    def head_pod_name(self, cluster_name: str, namespace: str) -> str | None: ...
    def watch_pods(
        self,
        cluster_name: str,
        namespace: str,
        *,
        resource_version: str | None = None,
        timeout_seconds: int = 0,
    ) -> Iterator[dict]: ...
    def portforward(
        self, pod_name: str, namespace: str, ports: list[int]
    ) -> Any: ...


class DefaultKubeClient:
    """Default KubeClient backed by the official ``kubernetes`` Python client."""

    def __init__(
        self,
        kubeconfig: str | None = None,
        context: str | None = None,
    ) -> None:
        _load_kube_config(
            kubeconfig,
            context,
            error_message=(
                "Cannot load Kubernetes configuration. "
                "Ensure a valid kubeconfig exists or run inside a cluster."
            ),
        )
        self._custom = k8s_client.CustomObjectsApi()
        self._core = k8s_client.CoreV1Api()

    def create_ray_cluster(self, manifest: dict) -> dict:
        namespace = manifest["metadata"]["namespace"]
        self._ensure_namespace(namespace)
        try:
            return self._custom.create_namespaced_custom_object(
                group=RAYCLUSTER_GROUP,
                version=RAYCLUSTER_VERSION,
                namespace=namespace,
                plural=RAYCLUSTER_PLURAL,
                body=manifest,
            )
        except ApiException as exc:
            if exc.status == 409:
                raise ClusterAlreadyExistsError(
                    manifest["metadata"]["name"], namespace
                ) from exc
            raise KubeConnectionError(str(exc)) from exc

    def get_ray_cluster(self, name: str, namespace: str) -> dict:
        try:
            return self._custom.get_namespaced_custom_object(
                group=RAYCLUSTER_GROUP,
                version=RAYCLUSTER_VERSION,
                namespace=namespace,
                plural=RAYCLUSTER_PLURAL,
                name=name,
            )
        except ApiException as exc:
            if exc.status == 404:
                raise ClusterNotFoundError(name, namespace) from exc
            raise KubeConnectionError(str(exc)) from exc

    def list_ray_clusters(self, namespace: str) -> list[dict]:
        try:
            resp = self._custom.list_namespaced_custom_object(
                group=RAYCLUSTER_GROUP,
                version=RAYCLUSTER_VERSION,
                namespace=namespace,
                plural=RAYCLUSTER_PLURAL,
            )
            return resp.get("items", [])
        except ApiException as exc:
            raise KubeConnectionError(str(exc)) from exc

    def patch_ray_cluster(self, name: str, namespace: str, patch: dict) -> dict:
        try:
            return self._custom.patch_namespaced_custom_object(
                group=RAYCLUSTER_GROUP,
                version=RAYCLUSTER_VERSION,
                namespace=namespace,
                plural=RAYCLUSTER_PLURAL,
                name=name,
                body=patch,
            )
        except ApiException as exc:
            if exc.status == 404:
                raise ClusterNotFoundError(name, namespace) from exc
            raise KubeConnectionError(str(exc)) from exc

    def delete_ray_cluster(self, name: str, namespace: str) -> None:
        try:
            self._custom.delete_namespaced_custom_object(
                group=RAYCLUSTER_GROUP,
                version=RAYCLUSTER_VERSION,
                namespace=namespace,
                plural=RAYCLUSTER_PLURAL,
                name=name,
            )
        except ApiException as exc:
            if exc.status == 404:
                raise ClusterNotFoundError(name, namespace) from exc
            raise KubeConnectionError(str(exc)) from exc

    def get_cluster_status(self, name: str, namespace: str) -> str:
        obj = self.get_ray_cluster(name, namespace)
        pods = self.list_pods(name, namespace)
        return _extract_status(obj, pods=pods)

    def list_pods(self, cluster_name: str, namespace: str) -> list[dict]:
        try:
            resp = self._core.list_namespaced_pod(
                namespace=namespace,
                label_selector=f"ray.io/cluster={cluster_name}",
            )
            return [pod.to_dict() for pod in (resp.items or [])]
        except ApiException as exc:
            raise KubeConnectionError(str(exc)) from exc

    def get_head_node_port(
        self, cluster_name: str, namespace: str, port_name: str
    ) -> int | None:
        svc_name = f"{cluster_name}-head-svc"
        try:
            svc = self._core.read_namespaced_service(svc_name, namespace)
        except ApiException:
            return None
        for port in svc.spec.ports or []:
            if port.name == port_name and port.node_port:
                return int(port.node_port)
        return None

    def list_namespaces(self) -> list[str]:
        try:
            resp = self._core.list_namespace()
            return sorted(ns.metadata.name for ns in (resp.items or []))
        except ApiException as exc:
            raise KubeConnectionError(str(exc)) from exc

    def head_pod_name(self, cluster_name: str, namespace: str) -> str | None:
        """Return the name of a Running head pod, or ``None`` if none exists.

        Used by the tunnel manager to pick a pod to port-forward to. Filters
        by ``ray.io/cluster=<name>`` (same selector as :meth:`list_pods`),
        further restricted to ``ray.io/node-type=head``.
        """
        try:
            resp = self._core.list_namespaced_pod(
                namespace=namespace,
                label_selector=(
                    f"ray.io/cluster={cluster_name},ray.io/node-type=head"
                ),
            )
        except ApiException as exc:
            raise KubeConnectionError(str(exc)) from exc
        for pod in resp.items or []:
            if (pod.status.phase or "") != "Running":
                continue
            for cond in pod.status.conditions or []:
                if cond.type == "Ready" and cond.status == "True":
                    return pod.metadata.name
        return None

    def watch_pods(
        self,
        cluster_name: str,
        namespace: str,
        *,
        resource_version: str | None = None,
        timeout_seconds: int = 0,
    ) -> Iterator[dict]:
        """Yield raw watch events for pods matching the cluster label.

        Blocking generator — designed to be consumed from a worker thread
        (e.g. via ``anyio.to_thread.run_sync``). Yields the raw event dicts
        produced by ``kubernetes.watch.Watch().stream(...)``: ``{"type":
        "ADDED" | "MODIFIED" | "DELETED", "object": <V1Pod>}``.
        """
        w = k8s_watch.Watch()
        kwargs: dict[str, Any] = {
            "namespace": namespace,
            "label_selector": f"ray.io/cluster={cluster_name}",
        }
        if resource_version is not None:
            kwargs["resource_version"] = resource_version
        if timeout_seconds:
            kwargs["timeout_seconds"] = timeout_seconds
        try:
            yield from w.stream(self._core.list_namespaced_pod, **kwargs)
        finally:
            w.stop()

    def portforward(
        self, pod_name: str, namespace: str, ports: list[int]
    ) -> Any:
        """Open an in-process port-forward session to *pod_name*.

        Returns the ``kubernetes.stream.ws_client.PortForward`` instance.
        The caller obtains per-port sockets via ``.socket(port)`` and is
        responsible for closing them when done.
        """
        return k8s_portforward(
            self._core.connect_get_namespaced_pod_portforward,
            pod_name,
            namespace,
            ports=ports,
        )

    def _ensure_namespace(self, namespace: str) -> None:
        try:
            self._core.read_namespace(namespace)
        except ApiException as exc:
            if exc.status == 404:
                raise NamespaceNotFoundError(namespace) from exc
            raise KubeConnectionError(str(exc)) from exc


# Client cache keyed on (kubeconfig, context, settings-file-digest).  The
# settings-file digest ensures the cache self-invalidates whenever
# ``~/.krayne/config.yaml`` changes on disk — critical because many call
# sites resolve the kubeconfig/context from that file.
_client_cache: dict[tuple[str | None, str | None, str], "DefaultKubeClient"] = {}


def _settings_file_digest() -> str:
    from krayne.config.settings import PRISM_CONFIG_FILE

    if not PRISM_CONFIG_FILE.exists():
        return ""
    return hashlib.sha256(PRISM_CONFIG_FILE.read_bytes()).hexdigest()


def get_kube_client(
    kubeconfig: str | None = None,
    context: str | None = None,
) -> "DefaultKubeClient":
    """Return a cached :class:`DefaultKubeClient`.

    When both *kubeconfig* and *context* are ``None`` (the default), the
    kubeconfig and kube_context from ``~/.krayne/config.yaml`` are used
    — and :func:`load_krayne_settings` validates that file.  The cache
    key includes a hash of the settings-file contents, so edits to the
    file (including ``krayne init``) invalidate the cache automatically.
    """
    from krayne.config.settings import load_krayne_settings

    if kubeconfig is None and context is None:
        settings = load_krayne_settings()
        kubeconfig = settings.kubeconfig
        context = settings.kube_context

    key = (kubeconfig, context, _settings_file_digest())
    cached = _client_cache.get(key)
    if cached is not None:
        return cached
    # Verify KubeRay is installed *before* constructing the client, so
    # the same friendly error surfaces regardless of entry point
    # (CLI / TUI / SDK / ``krayne init``).
    assert_kuberay_installed(kubeconfig=kubeconfig, context=context)
    client = DefaultKubeClient(kubeconfig=kubeconfig, context=context)
    _client_cache[key] = client
    return client


def clear_kube_client_cache() -> None:
    """Clear the :func:`get_kube_client` cache.  Useful in tests."""
    _client_cache.clear()


def assert_kuberay_installed(
    kubeconfig: str | None = None, context: str | None = None
) -> None:
    """Raise :class:`KubeRayNotInstalledError` when the ``rayclusters.ray.io``
    CRD is not registered on the target cluster.

    Runs before :class:`DefaultKubeClient` is constructed and uses an
    isolated ``Configuration`` so the process-wide default and the
    :func:`get_kube_client` cache are untouched.  Every code path that
    builds a kube client should go through :func:`get_kube_client`,
    which calls this first — so the error surfaces uniformly whether
    the caller is the CLI, the TUI, or the SDK.
    """
    configuration = k8s_client.Configuration()
    _load_kube_config(
        kubeconfig,
        context,
        client_configuration=configuration,
        error_message=(
            "Cannot load Kubernetes configuration for KubeRay check. "
            "Ensure a valid kubeconfig exists or run inside a cluster."
        ),
    )

    api_client = k8s_client.ApiClient(configuration=configuration)
    try:
        ext = k8s_client.ApiextensionsV1Api(api_client=api_client)
        try:
            ext.read_custom_resource_definition(RAYCLUSTER_CRD_NAME)
        except ApiException as exc:
            if exc.status == 404:
                raise KubeRayNotInstalledError(context=context) from exc
            raise KubeConnectionError(
                f"Failed to query CRDs on the target cluster: {exc}"
            ) from exc
    finally:
        api_client.close()


def _load_kube_config(
    kubeconfig: str | None,
    context: str | None,
    *,
    client_configuration: "k8s_client.Configuration | None" = None,
    error_message: str,
) -> None:
    """Load kubeconfig with the same precedence used everywhere in krayne.

    Explicit *kubeconfig*/*context* take precedence over in-cluster config,
    so the user's configured kubeconfig is never silently ignored. When
    *client_configuration* is given, the loaded config is written there
    instead of mutating the process-wide default.
    """
    try:
        if kubeconfig is not None or context is not None:
            k8s_config.load_kube_config(
                config_file=kubeconfig,
                context=context,
                client_configuration=client_configuration,
            )
        else:
            try:
                k8s_config.load_incluster_config(
                    client_configuration=client_configuration
                )
            except k8s_config.ConfigException:
                k8s_config.load_kube_config(
                    client_configuration=client_configuration
                )
    except k8s_config.ConfigException as exc:
        raise KubeConnectionError(error_message) from exc


def _safe_get(d: dict | None, *keys: str) -> dict:
    """Walk nested dicts, treating missing keys or ``None`` values as ``{}``."""
    cur: dict = d or {}
    for key in keys:
        cur = cur.get(key) or {}
    return cur


# Pod-derived statuses that override an optimistic CRD state. KubeRay sometimes
# flips RayCluster.status.state to "ready" before the head pod has actually
# transitioned from ContainerCreating → Running, so when pods are observable
# and show one of these failure/transition signals we trust the pods.
_POD_OVERRIDES: frozenset[str] = frozenset({
    "containers-creating",
    "image-pull-error",
    "crash-loop",
    "unschedulable",
    "pods-pending",
    "pods-failed",
})


def _extract_status(obj: dict, pods: list[dict] | None = None) -> str:
    """Pull the high-level status string from a RayCluster object.

    Pod-level failure or transition signals (e.g. ContainerCreating)
    take precedence over an optimistic CRD ``status.state`` so the UI
    doesn't claim the cluster is ready before the head pod is actually
    running. When the CRD state is empty, pod-derived status is used
    as a full fallback.
    """
    pod_status = _status_from_pods(pods) if pods is not None else None
    if pod_status in _POD_OVERRIDES:
        return pod_status

    status = obj.get("status", {})
    state = status.get("state", "")
    if state:
        return state.lower()
    # Fallback: older KubeRay versions use 'conditions'
    conditions = status.get("conditions", [])
    for cond in conditions:
        if cond.get("type") == "Ready" and cond.get("status") == "True":
            return "ready"
    if pod_status is not None:
        return pod_status
    return "unknown"


def _pod_problem_signal(pod: dict) -> str | None:
    """Return a status string when a pod is in a bad/transient state, else ``None``."""
    status = _safe_get(pod, "status")
    phase = status.get("phase", "")

    if phase == "Pending":
        for cond in status.get("conditions") or []:
            if (
                cond.get("type") == "PodScheduled"
                and cond.get("status") == "False"
                and cond.get("reason") == "Unschedulable"
            ):
                return "unschedulable"

    for cs in status.get("container_statuses") or []:
        reason = _safe_get(cs, "state", "waiting").get("reason", "")
        if reason == "ContainerCreating":
            return "containers-creating"
        if reason in ("ImagePullBackOff", "ErrImagePull"):
            return "image-pull-error"
        if reason == "CrashLoopBackOff":
            return "crash-loop"

    return None


def _phase_summary(pods: list[dict]) -> str:
    """Map a set of pod phases to a single status string."""
    phases = {_safe_get(p, "status").get("phase", "") for p in pods}
    if phases == {"Running"}:
        return "running"
    if "Pending" in phases:
        return "pods-pending"
    if "Failed" in phases:
        return "pods-failed"
    return "unknown"


def _status_from_pods(pods: list[dict]) -> str:
    """Derive a cluster status from raw pod dicts.

    Head pod state dominates: a worker stuck in ``Unschedulable`` while the
    autoscaler waits for capacity is normal, not a cluster failure, so we
    refuse to downgrade the whole cluster on the strength of a worker
    signal alone. The head's problem signals are still authoritative —
    those genuinely block the cluster from being usable.
    """
    if not pods:
        return "creating"

    head_pods = [
        p for p in pods
        if _safe_get(p, "metadata", "labels").get("ray.io/node-type") == "head"
    ]

    if head_pods:
        for pod in head_pods:
            problem = _pod_problem_signal(pod)
            if problem is not None:
                return problem
        return _phase_summary(head_pods)

    # No head pod is labeled (older KubeRay, or odd state) — fall back to
    # treating all pods uniformly so we still surface obvious failures.
    for pod in pods:
        problem = _pod_problem_signal(pod)
        if problem is not None:
            return problem
    return _phase_summary(pods)
