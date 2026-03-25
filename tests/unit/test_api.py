"""Unit tests for prism.api (SDK functional layer) with a mock KubeClient."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from prism.api import (
    create_cluster,
    delete_cluster,
    describe_cluster,
    get_cluster,
    list_clusters,
    scale_cluster,
    wait_until_ready,
)
from prism.api.types import ClusterDetails, ClusterInfo
from prism.config import ClusterConfig, WorkerGroupConfig
from prism.errors import ClusterTimeoutError, PrismError


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

_SAMPLE_OBJ = {
    "apiVersion": "ray.io/v1",
    "kind": "RayCluster",
    "metadata": {
        "name": "test",
        "namespace": "default",
        "creationTimestamp": "2026-01-01T00:00:00Z",
    },
    "status": {"state": "ready", "head": {"podIP": "10.0.0.1"}},
    "spec": {
        "headGroupSpec": {
            "template": {
                "spec": {
                    "containers": [
                        {
                            "name": "ray-head",
                            "image": "rayproject/ray:2.41.0",
                            "resources": {
                                "requests": {"cpu": 2, "memory": "2Gi"},
                            },
                        }
                    ]
                }
            }
        },
        "workerGroupSpecs": [
            {
                "groupName": "worker",
                "replicas": 2,
                "minReplicas": 2,
                "maxReplicas": 2,
                "template": {
                    "spec": {
                        "containers": [
                            {
                                "name": "ray-worker",
                                "image": "rayproject/ray:2.41.0",
                                "resources": {
                                    "requests": {"cpu": 2, "memory": "2Gi"},
                                },
                            }
                        ]
                    }
                },
            }
        ],
    },
}


@pytest.fixture()
def mock_client():
    client = MagicMock()
    client.create_ray_cluster.return_value = _SAMPLE_OBJ
    client.get_ray_cluster.return_value = _SAMPLE_OBJ
    client.list_ray_clusters.return_value = [_SAMPLE_OBJ]
    client.patch_ray_cluster.return_value = _SAMPLE_OBJ
    client.delete_ray_cluster.return_value = None
    client.list_pods.return_value = []
    return client


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestCreateCluster:
    def test_basic_create(self, mock_client):
        cfg = ClusterConfig(name="test")
        info = create_cluster(cfg, client=mock_client)
        assert isinstance(info, ClusterInfo)
        assert info.name == "test"
        assert info.status == "ready"
        mock_client.create_ray_cluster.assert_called_once()

    def test_create_with_wait(self, mock_client):
        cfg = ClusterConfig(name="test")
        info = create_cluster(cfg, client=mock_client, wait=True, timeout=10)
        assert info.status == "ready"


class TestGetCluster:
    def test_get(self, mock_client):
        info = get_cluster("test", "default", client=mock_client)
        assert info.name == "test"
        assert info.head_ip == "10.0.0.1"
        assert info.dashboard_url == "http://10.0.0.1:8265"
        mock_client.get_ray_cluster.assert_called_once_with("test", "default")


class TestListClusters:
    def test_list(self, mock_client):
        clusters = list_clusters("default", client=mock_client)
        assert len(clusters) == 1
        assert clusters[0].name == "test"

    def test_empty_list(self, mock_client):
        mock_client.list_ray_clusters.return_value = []
        clusters = list_clusters("default", client=mock_client)
        assert clusters == []


class TestDescribeCluster:
    def test_describe(self, mock_client):
        details = describe_cluster("test", "default", client=mock_client)
        assert isinstance(details, ClusterDetails)
        assert details.info.name == "test"
        assert details.head.cpus == 2
        assert len(details.worker_groups) == 1
        assert details.worker_groups[0].replicas == 2


class TestScaleCluster:
    def test_scale(self, mock_client):
        info = scale_cluster("test", "default", "worker", 4, client=mock_client)
        assert isinstance(info, ClusterInfo)
        mock_client.patch_ray_cluster.assert_called_once()
        patch_arg = mock_client.patch_ray_cluster.call_args[0][2]
        assert patch_arg["spec"]["workerGroupSpecs"][0]["replicas"] == 4

    def test_scale_unknown_group(self, mock_client):
        with pytest.raises(PrismError, match="not found"):
            scale_cluster("test", "default", "nonexistent", 4, client=mock_client)


class TestDeleteCluster:
    def test_delete(self, mock_client):
        delete_cluster("test", "default", client=mock_client)
        mock_client.delete_ray_cluster.assert_called_once_with("test", "default")


class TestWaitUntilReady:
    def test_already_ready(self, mock_client):
        info = wait_until_ready("test", "default", client=mock_client, timeout=5)
        assert info.status == "ready"

    def test_timeout(self, mock_client):
        not_ready_obj = {**_SAMPLE_OBJ, "status": {"state": "pending"}}
        mock_client.get_ray_cluster.return_value = not_ready_obj
        with pytest.raises(ClusterTimeoutError):
            wait_until_ready(
                "test", "default", client=mock_client, timeout=1, _poll_interval=0.1
            )


class TestKubeconfigPassthrough:
    """Verify that kubeconfig= reaches DefaultKubeClient when no client is provided."""

    @pytest.fixture(autouse=True)
    def _patch_client(self):
        with patch("prism.api.clusters.DefaultKubeClient") as mock_cls:
            mock_instance = MagicMock()
            mock_instance.list_ray_clusters.return_value = [_SAMPLE_OBJ]
            mock_instance.get_ray_cluster.return_value = _SAMPLE_OBJ
            mock_instance.create_ray_cluster.return_value = _SAMPLE_OBJ
            mock_cls.return_value = mock_instance
            self.mock_cls = mock_cls
            self.mock_instance = mock_instance
            yield

    @pytest.fixture(autouse=True)
    def _patch_settings(self):
        with patch("prism.api.clusters.load_prism_settings") as mock_settings:
            from prism.config.settings import PrismSettings

            mock_settings.return_value = PrismSettings()
            yield

    def test_explicit_kubeconfig(self):
        list_clusters("default", kubeconfig="/custom/kubeconfig")
        self.mock_cls.assert_called_once_with(kubeconfig="/custom/kubeconfig")

    def test_no_kubeconfig_uses_settings(self):
        from prism.config.settings import PrismSettings

        with patch("prism.api.clusters.load_prism_settings") as mock_settings:
            mock_settings.return_value = PrismSettings(kubeconfig="/from/settings")
            list_clusters("default")
            self.mock_cls.assert_called_with(kubeconfig="/from/settings")


class TestPodLevelStatus:
    """Verify granular status is derived from pod phases when CRD state is empty."""

    def _no_state_obj(self):
        """Return a RayCluster object with no status.state."""
        return {
            **_SAMPLE_OBJ,
            "status": {},  # no state field
        }

    def test_crd_state_takes_priority(self, mock_client):
        """When CRD has a state, it's used regardless of pods."""
        info = get_cluster("test", "default", client=mock_client)
        assert info.status == "ready"

    def test_no_pods_shows_creating(self, mock_client):
        mock_client.get_ray_cluster.return_value = self._no_state_obj()
        mock_client.list_pods.return_value = []
        info = get_cluster("test", "default", client=mock_client)
        assert info.status == "creating"

    def test_pending_pods(self, mock_client):
        mock_client.get_ray_cluster.return_value = self._no_state_obj()
        mock_client.list_pods.return_value = [
            {"status": {"phase": "Pending", "conditions": [], "container_statuses": None}},
        ]
        info = get_cluster("test", "default", client=mock_client)
        assert info.status == "pods-pending"

    def test_container_creating(self, mock_client):
        mock_client.get_ray_cluster.return_value = self._no_state_obj()
        mock_client.list_pods.return_value = [
            {
                "status": {
                    "phase": "Pending",
                    "conditions": [],
                    "container_statuses": [
                        {"state": {"waiting": {"reason": "ContainerCreating"}}}
                    ],
                },
            },
        ]
        info = get_cluster("test", "default", client=mock_client)
        assert info.status == "containers-creating"

    def test_image_pull_error(self, mock_client):
        mock_client.get_ray_cluster.return_value = self._no_state_obj()
        mock_client.list_pods.return_value = [
            {
                "status": {
                    "phase": "Pending",
                    "conditions": [],
                    "container_statuses": [
                        {"state": {"waiting": {"reason": "ImagePullBackOff"}}}
                    ],
                },
            },
        ]
        info = get_cluster("test", "default", client=mock_client)
        assert info.status == "image-pull-error"

    def test_crash_loop(self, mock_client):
        mock_client.get_ray_cluster.return_value = self._no_state_obj()
        mock_client.list_pods.return_value = [
            {
                "status": {
                    "phase": "Running",
                    "conditions": [],
                    "container_statuses": [
                        {"state": {"waiting": {"reason": "CrashLoopBackOff"}}}
                    ],
                },
            },
        ]
        info = get_cluster("test", "default", client=mock_client)
        assert info.status == "crash-loop"

    def test_unschedulable(self, mock_client):
        mock_client.get_ray_cluster.return_value = self._no_state_obj()
        mock_client.list_pods.return_value = [
            {
                "status": {
                    "phase": "Pending",
                    "conditions": [
                        {"type": "PodScheduled", "status": "False", "reason": "Unschedulable"}
                    ],
                    "container_statuses": None,
                },
            },
        ]
        info = get_cluster("test", "default", client=mock_client)
        assert info.status == "unschedulable"

    def test_all_running(self, mock_client):
        mock_client.get_ray_cluster.return_value = self._no_state_obj()
        mock_client.list_pods.return_value = [
            {"status": {"phase": "Running", "conditions": [], "container_statuses": []}},
            {"status": {"phase": "Running", "conditions": [], "container_statuses": []}},
        ]
        info = get_cluster("test", "default", client=mock_client)
        assert info.status == "running"

    def test_list_clusters_uses_pods(self, mock_client):
        mock_client.list_ray_clusters.return_value = [self._no_state_obj()]
        mock_client.list_pods.return_value = [
            {"status": {"phase": "Pending", "conditions": [], "container_statuses": None}},
        ]
        clusters = list_clusters("default", client=mock_client)
        assert clusters[0].status == "pods-pending"
