"""Unit tests for prism.api (SDK functional layer) with a mock KubeClient."""

from __future__ import annotations

from unittest.mock import MagicMock

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
                                "requests": {"cpu": 15, "memory": "48Gi"},
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
                                    "requests": {"cpu": 15, "memory": "48Gi"},
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
        assert details.head.cpus == 15
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
