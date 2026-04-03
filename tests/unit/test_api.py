from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from krayne.api import (
    create_cluster,
    delete_cluster,
    describe_cluster,
    get_cluster,
    list_clusters,
    managed_cluster,
    open_tunnel,
    scale_cluster,
    wait_until_ready,
)
from krayne.api.types import ClusterDetails, ClusterInfo, ManagedClusterResult, TunnelSession
from krayne.config import ClusterConfig, WorkerGroupConfig
from krayne.errors import ClusterTimeoutError, KrayneError


_SAMPLE_OBJ = {
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
                            "ports": [
                                {"containerPort": 6379, "name": "gcs-server"},
                                {"containerPort": 8265, "name": "dashboard"},
                                {"containerPort": 10001, "name": "client"},
                                {"containerPort": 8888, "name": "notebook"},
                                {"containerPort": 22, "name": "ssh"},
                            ],
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
    client.get_head_node_port.return_value = None
    return client


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
        assert info.client_url == "ray://10.0.0.1:10001"
        assert info.notebook_url == "http://10.0.0.1:8888"
        assert info.ssh_url == "ssh://10.0.0.1:22"
        assert info.code_server_url is None  # no code-server port in sample obj
        mock_client.get_ray_cluster.assert_called_once_with("test", "default")

    def test_service_urls_absent_when_no_ports(self, mock_client):
        """When head container has no ports, service URLs are None."""
        obj_no_ports = {
            **_SAMPLE_OBJ,
            "spec": {
                **_SAMPLE_OBJ["spec"],
                "headGroupSpec": {
                    "template": {
                        "spec": {
                            "containers": [
                                {
                                    "name": "ray-head",
                                    "image": "rayproject/ray:2.41.0",
                                    "resources": {"requests": {"cpu": 2, "memory": "2Gi"}},
                                    "ports": [
                                        {"containerPort": 6379, "name": "gcs-server"},
                                        {"containerPort": 8265, "name": "dashboard"},
                                        {"containerPort": 10001, "name": "client"},
                                    ],
                                }
                            ]
                        }
                    }
                },
            },
        }
        mock_client.get_ray_cluster.return_value = obj_no_ports
        info = get_cluster("test", "default", client=mock_client)
        assert info.notebook_url is None
        assert info.code_server_url is None
        assert info.ssh_url is None


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
        assert details.head.cpus == "2"
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
        with pytest.raises(KrayneError, match="not found"):
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


class TestManagedCluster:
    def test_creates_and_deletes(self, mock_client):
        cfg = ClusterConfig(name="test")
        with managed_cluster(cfg, client=mock_client, tunnel=False) as result:
            assert isinstance(result, ManagedClusterResult)
            assert isinstance(result.cluster, ClusterInfo)
            assert result.cluster.name == "test"
            assert result.cluster.status == "ready"
            assert result.tunnel is None
        mock_client.create_ray_cluster.assert_called_once()
        mock_client.delete_ray_cluster.assert_called_once_with("test", "default")

    def test_deletes_on_exception(self, mock_client):
        cfg = ClusterConfig(name="test")
        with pytest.raises(RuntimeError, match="boom"):
            with managed_cluster(cfg, client=mock_client, tunnel=False) as result:
                assert result.cluster.name == "test"
                raise RuntimeError("boom")
        mock_client.delete_ray_cluster.assert_called_once_with("test", "default")

    def test_custom_namespace(self, mock_client):
        obj = {**_SAMPLE_OBJ, "metadata": {**_SAMPLE_OBJ["metadata"], "namespace": "ml"}}
        mock_client.create_ray_cluster.return_value = obj
        mock_client.get_ray_cluster.return_value = obj
        cfg = ClusterConfig(name="test", namespace="ml")
        with managed_cluster(cfg, client=mock_client, tunnel=False):
            pass
        mock_client.delete_ray_cluster.assert_called_once_with("test", "ml")

    @patch("krayne.tunnel.stop_tunnels")
    @patch("krayne.tunnel.start_tunnels")
    def test_tunnel_default_opens_and_closes(self, mock_start, mock_stop, mock_client):
        from krayne.tunnel import TunnelInfo

        mock_start.return_value = [
            TunnelInfo(service="dashboard", remote_port=8265, local_port=12345, local_url="http://localhost:12345"),
            TunnelInfo(service="client", remote_port=10001, local_port=12346, local_url="ray://localhost:12346"),
        ]
        cfg = ClusterConfig(name="test")
        with managed_cluster(cfg, client=mock_client) as result:
            assert isinstance(result, ManagedClusterResult)
            assert result.tunnel is not None
            assert len(result.tunnel.tunnels) == 2
            assert result.tunnel.dashboard_url == "http://localhost:12345"
            assert result.tunnel.client_url == "ray://localhost:12346"
        mock_start.assert_called_once()
        mock_stop.assert_called_once_with("test", "default")
        mock_client.delete_ray_cluster.assert_called_once_with("test", "default")

    @patch("krayne.tunnel.stop_tunnels")
    @patch("krayne.tunnel.start_tunnels")
    def test_tunnel_false_skips_tunnels(self, mock_start, mock_stop, mock_client):
        cfg = ClusterConfig(name="test")
        with managed_cluster(cfg, client=mock_client, tunnel=False) as result:
            assert result.tunnel is None
        mock_start.assert_not_called()
        mock_stop.assert_not_called()
        mock_client.delete_ray_cluster.assert_called_once_with("test", "default")

    @patch("krayne.tunnel.stop_tunnels")
    @patch("krayne.tunnel.start_tunnels")
    def test_tunnel_cleanup_on_exception(self, mock_start, mock_stop, mock_client):
        mock_start.return_value = []
        cfg = ClusterConfig(name="test")
        with pytest.raises(RuntimeError, match="boom"):
            with managed_cluster(cfg, client=mock_client, tunnel=True) as result:
                raise RuntimeError("boom")
        mock_stop.assert_called_once_with("test", "default")
        mock_client.delete_ray_cluster.assert_called_once_with("test", "default")

    @patch("krayne.tunnel.stop_tunnels")
    @patch("krayne.tunnel.start_tunnels")
    def test_cleanup_order_tunnels_before_cluster(self, mock_start, mock_stop, mock_client):
        mock_start.return_value = []
        call_order = []
        mock_stop.side_effect = lambda *a, **kw: call_order.append("stop_tunnels")
        mock_client.delete_ray_cluster.side_effect = lambda *a, **kw: call_order.append("delete_cluster")
        cfg = ClusterConfig(name="test")
        with managed_cluster(cfg, client=mock_client, tunnel=True):
            pass
        assert call_order == ["stop_tunnels", "delete_cluster"]


class TestManagedClusterResult:
    def test_cluster_and_tunnel_accessed_separately(self):
        from krayne.tunnel import TunnelInfo

        cluster = ClusterInfo(
            name="c", namespace="ns", status="ready", head_ip="10.0.0.1",
            dashboard_url="http://10.0.0.1:8265", client_url="ray://10.0.0.1:10001",
            notebook_url=None, code_server_url=None, ssh_url=None,
            num_workers=1, created_at="now",
        )
        tunnels = [
            TunnelInfo(service="dashboard", remote_port=8265, local_port=11111, local_url="http://localhost:11111"),
            TunnelInfo(service="client", remote_port=10001, local_port=22222, local_url="ray://localhost:22222"),
        ]
        session = TunnelSession(cluster_name="c", namespace="ns", tunnels=tunnels)
        result = ManagedClusterResult(cluster=cluster, tunnel=session)
        # Tunnel URLs via result.tunnel
        assert result.tunnel.dashboard_url == "http://localhost:11111"
        assert result.tunnel.client_url == "ray://localhost:22222"
        # Cluster URLs via result.cluster
        assert result.cluster.dashboard_url == "http://10.0.0.1:8265"
        assert result.cluster.client_url == "ray://10.0.0.1:10001"

    def test_tunnel_none_when_disabled(self):
        cluster = ClusterInfo(
            name="c", namespace="ns", status="ready", head_ip="10.0.0.1",
            dashboard_url="http://10.0.0.1:8265", client_url="ray://10.0.0.1:10001",
            notebook_url=None, code_server_url=None, ssh_url=None,
            num_workers=1, created_at="now",
        )
        result = ManagedClusterResult(cluster=cluster, tunnel=None)
        assert result.tunnel is None
        assert result.cluster.dashboard_url == "http://10.0.0.1:8265"
        assert result.cluster.client_url == "ray://10.0.0.1:10001"


class TestKubeconfigPassthrough:
    """Verify that kubeconfig= reaches DefaultKubeClient when no client is provided."""

    @pytest.fixture(autouse=True)
    def _patch_client(self):
        with patch("krayne.api.clusters.DefaultKubeClient") as mock_cls:
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
        with patch("krayne.api.clusters.load_krayne_settings") as mock_settings:
            from krayne.config.settings import KrayneSettings

            mock_settings.return_value = KrayneSettings()
            yield

    def test_explicit_kubeconfig(self):
        list_clusters("default", kubeconfig="/custom/kubeconfig")
        self.mock_cls.assert_called_once_with(
            kubeconfig="/custom/kubeconfig", context=None
        )

    def test_no_kubeconfig_uses_settings(self):
        from krayne.config.settings import KrayneSettings

        with patch("krayne.api.clusters.load_krayne_settings") as mock_settings:
            mock_settings.return_value = KrayneSettings(kubeconfig="/from/settings")
            list_clusters("default")
            self.mock_cls.assert_called_with(
                kubeconfig="/from/settings", context=None
            )


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


class TestOpenTunnel:
    @patch("krayne.tunnel.stop_tunnels")
    @patch("krayne.tunnel.start_tunnels")
    def test_opens_and_closes_tunnels(self, mock_start, mock_stop, mock_client):
        from krayne.tunnel import TunnelInfo

        mock_start.return_value = [
            TunnelInfo(service="dashboard", remote_port=8265, local_port=12345, local_url="http://localhost:12345"),
            TunnelInfo(service="client", remote_port=10001, local_port=12346, local_url="ray://localhost:12346"),
        ]
        with open_tunnel("test", "default", client=mock_client) as session:
            assert isinstance(session, TunnelSession)
            assert session.cluster_name == "test"
            assert session.namespace == "default"
            assert len(session.tunnels) == 2
        mock_start.assert_called_once_with("test", "default", ["dashboard", "client", "notebook", "ssh"], kubeconfig=None)
        mock_stop.assert_called_once_with("test", "default")

    @patch("krayne.tunnel.stop_tunnels")
    @patch("krayne.tunnel.start_tunnels")
    def test_closes_on_exception(self, mock_start, mock_stop, mock_client):
        mock_start.return_value = []
        with pytest.raises(RuntimeError, match="boom"):
            with open_tunnel("test", "default", client=mock_client):
                raise RuntimeError("boom")
        mock_stop.assert_called_once_with("test", "default")


class TestTunnelSession:
    def test_url_properties(self):
        from krayne.tunnel import TunnelInfo

        tunnels = [
            TunnelInfo(service="dashboard", remote_port=8265, local_port=11111, local_url="http://localhost:11111"),
            TunnelInfo(service="client", remote_port=10001, local_port=22222, local_url="ray://localhost:22222"),
            TunnelInfo(service="notebook", remote_port=8888, local_port=33333, local_url="http://localhost:33333"),
            TunnelInfo(service="code-server", remote_port=8443, local_port=44444, local_url="http://localhost:44444"),
            TunnelInfo(service="ssh", remote_port=22, local_port=55555, local_url="ssh://localhost:55555"),
        ]
        session = TunnelSession(cluster_name="c", namespace="ns", tunnels=tunnels)
        assert session.dashboard_url == "http://localhost:11111"
        assert session.client_url == "ray://localhost:22222"
        assert session.notebook_url == "http://localhost:33333"
        assert session.code_server_url == "http://localhost:44444"
        assert session.ssh_url == "ssh://localhost:55555"

    def test_missing_service_returns_none(self):
        from krayne.tunnel import TunnelInfo

        tunnels = [
            TunnelInfo(service="dashboard", remote_port=8265, local_port=11111, local_url="http://localhost:11111"),
        ]
        session = TunnelSession(cluster_name="c", namespace="ns", tunnels=tunnels)
        assert session.dashboard_url == "http://localhost:11111"
        assert session.client_url is None
        assert session.notebook_url is None
        assert session.code_server_url is None
        assert session.ssh_url is None

    def test_empty_tunnels(self):
        session = TunnelSession(cluster_name="c", namespace="ns", tunnels=[])
        assert session.dashboard_url is None
        assert session.client_url is None
