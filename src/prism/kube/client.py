"""Kubernetes client: protocol definition and default implementation."""

from __future__ import annotations

import time
from typing import Any, Protocol, runtime_checkable

from kubernetes import client as k8s_client  # type: ignore[import-untyped]
from kubernetes import config as k8s_config  # type: ignore[import-untyped]
from kubernetes.client.exceptions import ApiException  # type: ignore[import-untyped]

from prism.errors import (
    ClusterAlreadyExistsError,
    ClusterNotFoundError,
    KubeConnectionError,
    NamespaceNotFoundError,
)

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


class DefaultKubeClient:
    """Default KubeClient backed by the official ``kubernetes`` Python client."""

    def __init__(
        self,
        kubeconfig: str | None = None,
        context: str | None = None,
    ) -> None:
        try:
            k8s_config.load_incluster_config()
        except k8s_config.ConfigException:
            try:
                k8s_config.load_kube_config(
                    config_file=kubeconfig, context=context
                )
            except k8s_config.ConfigException as exc:
                raise KubeConnectionError(
                    "Cannot load Kubernetes configuration. "
                    "Ensure a valid kubeconfig exists or run inside a cluster."
                ) from exc

        self._custom = k8s_client.CustomObjectsApi()
        self._core = k8s_client.CoreV1Api()

    # -- CRD operations -------------------------------------------------------

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
        return _extract_status(obj)

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
        """Return the NodePort for *port_name* on the head service, or None."""
        svc_name = f"{cluster_name}-head-svc"
        try:
            svc = self._core.read_namespaced_service(svc_name, namespace)
        except ApiException:
            return None
        for port in svc.spec.ports or []:
            if port.name == port_name and port.node_port:
                return int(port.node_port)
        return None

    # -- helpers --------------------------------------------------------------

    def _ensure_namespace(self, namespace: str) -> None:
        try:
            self._core.read_namespace(namespace)
        except ApiException as exc:
            if exc.status == 404:
                raise NamespaceNotFoundError(namespace) from exc
            raise KubeConnectionError(str(exc)) from exc


def _extract_status(obj: dict, pods: list[dict] | None = None) -> str:
    """Pull the high-level status string from a RayCluster object.

    When the CRD state is unavailable and *pods* are provided, a more
    granular status is derived from pod phases and container states.
    """
    status = obj.get("status", {})
    state = status.get("state", "")
    if state:
        return state.lower()
    # Fallback: older KubeRay versions use 'conditions'
    conditions = status.get("conditions", [])
    for cond in conditions:
        if cond.get("type") == "Ready" and cond.get("status") == "True":
            return "ready"
    # Derive status from pods when CRD state is missing
    if pods is not None:
        return _status_from_pods(pods)
    return "unknown"


def _status_from_pods(pods: list[dict]) -> str:
    """Derive a human-readable status from pod phases and container states."""
    if not pods:
        return "creating"

    for pod in pods:
        phase = (pod.get("status") or {}).get("phase", "")

        # Check for scheduling issues
        if phase == "Pending":
            for cond in (pod.get("status") or {}).get("conditions") or []:
                if (
                    cond.get("type") == "PodScheduled"
                    and cond.get("status") == "False"
                    and cond.get("reason") == "Unschedulable"
                ):
                    return "unschedulable"

        # Check container-level waiting reasons
        containers = (pod.get("status") or {}).get("container_statuses") or []
        for cs in containers:
            waiting = (cs.get("state") or {}).get("waiting") or {}
            reason = waiting.get("reason", "")
            if reason == "ContainerCreating":
                return "containers-creating"
            if reason in ("ImagePullBackOff", "ErrImagePull"):
                return "image-pull-error"
            if reason == "CrashLoopBackOff":
                return "crash-loop"

    # Check pod phases after container-level checks
    phases = {(p.get("status") or {}).get("phase", "") for p in pods}
    if phases == {"Running"}:
        return "running"
    if "Pending" in phases:
        return "pods-pending"
    if "Failed" in phases:
        return "pods-failed"

    return "unknown"
