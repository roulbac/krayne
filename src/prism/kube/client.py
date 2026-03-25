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


class DefaultKubeClient:
    """Default KubeClient backed by the official ``kubernetes`` Python client."""

    def __init__(self) -> None:
        try:
            k8s_config.load_incluster_config()
        except k8s_config.ConfigException:
            try:
                k8s_config.load_kube_config()
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

    # -- helpers --------------------------------------------------------------

    def _ensure_namespace(self, namespace: str) -> None:
        try:
            self._core.read_namespace(namespace)
        except ApiException as exc:
            if exc.status == 404:
                raise NamespaceNotFoundError(namespace) from exc
            raise KubeConnectionError(str(exc)) from exc


def _extract_status(obj: dict) -> str:
    """Pull the high-level status string from a RayCluster object."""
    status = obj.get("status", {})
    state = status.get("state", "")
    if state:
        return state.lower()
    # Fallback: older KubeRay versions use 'conditions'
    conditions = status.get("conditions", [])
    for cond in conditions:
        if cond.get("type") == "Ready" and cond.get("status") == "True":
            return "ready"
    return "unknown"
