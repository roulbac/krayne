from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import pytest

from krayne.tunnel import (
    PORT_RANGE_END,
    PORT_RANGE_START,
    SERVICE_PORTS,
    TunnelState,
    detect_services,
    is_tunnel_active,
    load_tunnel_state,
    local_port_for,
    start_tunnels,
    stop_tunnels,
)


class TestLocalPortFor:
    def test_deterministic(self):
        """Same inputs always produce the same port."""
        p1 = local_port_for("my-cluster", "default", "dashboard")
        p2 = local_port_for("my-cluster", "default", "dashboard")
        assert p1 == p2

    def test_in_range(self):
        port = local_port_for("my-cluster", "default", "dashboard")
        assert PORT_RANGE_START <= port < PORT_RANGE_END

    def test_different_clusters_different_ports(self):
        p1 = local_port_for("cluster-a", "default", "dashboard")
        p2 = local_port_for("cluster-b", "default", "dashboard")
        assert p1 != p2

    def test_different_services_different_ports(self):
        p1 = local_port_for("my-cluster", "default", "dashboard")
        p2 = local_port_for("my-cluster", "default", "notebook")
        assert p1 != p2

    def test_different_namespaces_different_ports(self):
        p1 = local_port_for("my-cluster", "ns-a", "dashboard")
        p2 = local_port_for("my-cluster", "ns-b", "dashboard")
        assert p1 != p2


class TestDetectServices:
    def test_all_services(self):
        obj = {
            "spec": {
                "headGroupSpec": {
                    "template": {
                        "spec": {
                            "containers": [
                                {
                                    "name": "ray-head",
                                    "ports": [
                                        {"containerPort": 6379, "name": "gcs-server"},
                                        {"containerPort": 8265, "name": "dashboard"},
                                        {"containerPort": 10001, "name": "client"},
                                    ],
                                },
                            ]
                        }
                    },
                    "headService": {
                        "spec": {
                            "ports": [
                                {"name": "notebook", "port": 8888},
                                {"name": "ssh", "port": 22},
                                {"name": "code-server", "port": 8443},
                            ]
                        }
                    },
                }
            }
        }
        services = detect_services(obj)
        assert set(services) == {"dashboard", "client", "notebook", "code-server", "ssh"}

    def test_base_only(self):
        obj = {
            "spec": {
                "headGroupSpec": {
                    "template": {
                        "spec": {
                            "containers": [
                                {
                                    "name": "ray-head",
                                    "ports": [
                                        {"containerPort": 6379, "name": "gcs-server"},
                                        {"containerPort": 8265, "name": "dashboard"},
                                        {"containerPort": 10001, "name": "client"},
                                    ],
                                }
                            ]
                        }
                    }
                }
            }
        }
        services = detect_services(obj)
        assert set(services) == {"dashboard", "client"}

    def test_empty_spec(self):
        services = detect_services({})
        assert services == []


class TestStartTunnels:
    @pytest.fixture(autouse=True)
    def _isolate_tunnel_dir(self, tmp_path, monkeypatch):
        monkeypatch.setattr("krayne.tunnel.TUNNEL_DIR", tmp_path / "tunnels")

    @patch("krayne.tunnel.subprocess.Popen")
    def test_launches_kubectl_processes(self, mock_popen):
        mock_proc = MagicMock()
        mock_proc.pid = 1234
        mock_popen.return_value = mock_proc

        tunnels = start_tunnels(
            "my-cluster", "default", ["dashboard", "notebook"]
        )

        assert len(tunnels) == 2
        assert mock_popen.call_count == 2

        # Check first call is for dashboard
        first_call_args = mock_popen.call_args_list[0][0][0]
        assert "kubectl" in first_call_args
        assert "port-forward" in first_call_args
        assert "svc/my-cluster-head-svc" in first_call_args
        assert "-n" in first_call_args
        assert "default" in first_call_args

    @patch("krayne.tunnel.subprocess.Popen")
    def test_kubeconfig_passed(self, mock_popen):
        mock_popen.return_value = MagicMock(pid=99)

        start_tunnels(
            "c", "ns", ["dashboard"], kubeconfig="/my/kubeconfig"
        )

        call_args = mock_popen.call_args_list[0][0][0]
        assert "--kubeconfig" in call_args
        assert "/my/kubeconfig" in call_args

    @patch("krayne.tunnel.subprocess.Popen")
    def test_context_passed(self, mock_popen):
        mock_popen.return_value = MagicMock(pid=99)

        start_tunnels(
            "c", "ns", ["dashboard"],
            kubeconfig="/my/kubeconfig", context="my-ctx",
        )

        call_args = mock_popen.call_args_list[0][0][0]
        assert "--context" in call_args
        assert "my-ctx" in call_args

    @patch("krayne.tunnel.subprocess.Popen")
    def test_kubeconfig_and_context_from_settings(
        self, mock_popen, tmp_path
    ):
        """When neither is supplied, both are loaded from ~/.krayne/config.yaml."""
        from krayne.config.settings import (
            KrayneSettings,
            save_krayne_settings,
        )

        kubeconfig = tmp_path / "kubeconfig"
        kubeconfig.write_text(
            "apiVersion: v1\n"
            "kind: Config\n"
            "contexts:\n"
            "- name: settings-ctx\n"
            "  context: {cluster: c, user: u}\n"
        )
        save_krayne_settings(
            KrayneSettings(
                kubeconfig=str(kubeconfig), kube_context="settings-ctx"
            )
        )

        mock_popen.return_value = MagicMock(pid=99)
        start_tunnels("c", "ns", ["dashboard"])

        call_args = mock_popen.call_args_list[0][0][0]
        assert "--kubeconfig" in call_args
        assert str(kubeconfig) in call_args
        assert "--context" in call_args
        assert "settings-ctx" in call_args

    @patch("krayne.tunnel.subprocess.Popen")
    def test_tunnel_info_fields(self, mock_popen):
        mock_popen.return_value = MagicMock(pid=42)

        tunnels = start_tunnels("c", "ns", ["dashboard"])

        t = tunnels[0]
        assert t.service == "dashboard"
        assert t.remote_port == 8265
        assert t.local_port == local_port_for("c", "ns", "dashboard")
        assert t.local_url == f"http://localhost:{t.local_port}"

    @patch("krayne.tunnel.subprocess.Popen")
    def test_unknown_service_skipped(self, mock_popen):
        mock_popen.return_value = MagicMock(pid=1)

        tunnels = start_tunnels("c", "ns", ["nonexistent"])

        assert len(tunnels) == 0


class TestIdempotency:
    @pytest.fixture(autouse=True)
    def _isolate_tunnel_dir(self, tmp_path, monkeypatch):
        monkeypatch.setattr("krayne.tunnel.TUNNEL_DIR", tmp_path / "tunnels")

    @patch("krayne.tunnel._pid_alive", return_value=True)
    @patch("krayne.tunnel.subprocess.Popen")
    def test_start_is_idempotent(self, mock_popen, mock_alive):
        mock_popen.return_value = MagicMock(pid=100)

        # First start
        tunnels1 = start_tunnels("c", "ns", ["dashboard"])
        assert mock_popen.call_count == 1

        # Second start — no new processes
        tunnels2 = start_tunnels("c", "ns", ["dashboard"])
        assert mock_popen.call_count == 1  # still 1
        assert tunnels1 == tunnels2

    @patch("krayne.tunnel.subprocess.Popen")
    def test_stop_is_idempotent(self, mock_popen):
        mock_popen.return_value = MagicMock(pid=100)

        start_tunnels("c", "ns", ["dashboard"])

        # First stop
        assert stop_tunnels("c", "ns") is True
        # Second stop — no-op
        assert stop_tunnels("c", "ns") is False

    def test_stop_without_start(self):
        assert stop_tunnels("nonexistent", "default") is False


class TestTunnelState:
    @pytest.fixture(autouse=True)
    def _isolate_tunnel_dir(self, tmp_path, monkeypatch):
        self.tunnel_dir = tmp_path / "tunnels"
        monkeypatch.setattr("krayne.tunnel.TUNNEL_DIR", self.tunnel_dir)

    @patch("krayne.tunnel.subprocess.Popen")
    def test_state_persisted_to_disk(self, mock_popen):
        mock_popen.return_value = MagicMock(pid=555)

        start_tunnels("my-cluster", "default", ["dashboard"])

        state_file = self.tunnel_dir / "default" / "my-cluster.json"
        assert state_file.exists()
        data = json.loads(state_file.read_text())
        assert data["cluster_name"] == "my-cluster"
        assert data["pids"] == [555]

    @patch("krayne.tunnel.subprocess.Popen")
    def test_state_removed_on_stop(self, mock_popen):
        mock_popen.return_value = MagicMock(pid=555)

        start_tunnels("my-cluster", "default", ["dashboard"])
        stop_tunnels("my-cluster", "default")

        state_file = self.tunnel_dir / "default" / "my-cluster.json"
        assert not state_file.exists()

    @patch("krayne.tunnel._pid_alive", return_value=True)
    @patch("krayne.tunnel.subprocess.Popen")
    def test_is_tunnel_active_true(self, mock_popen, mock_alive):
        mock_popen.return_value = MagicMock(pid=123)
        start_tunnels("c", "ns", ["dashboard"])
        assert is_tunnel_active("c", "ns") is True

    def test_is_tunnel_active_false_when_no_state(self):
        assert is_tunnel_active("c", "ns") is False

    @patch("krayne.tunnel._pid_alive", return_value=False)
    @patch("krayne.tunnel.subprocess.Popen")
    def test_stale_state_cleaned_up(self, mock_popen, mock_alive):
        mock_popen.return_value = MagicMock(pid=999)
        start_tunnels("c", "ns", ["dashboard"])

        # PIDs are dead, so is_tunnel_active should clean up
        assert is_tunnel_active("c", "ns") is False

        state_file = self.tunnel_dir / "ns" / "c.json"
        assert not state_file.exists()
